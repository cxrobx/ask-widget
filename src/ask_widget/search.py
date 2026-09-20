"""Search inside Notes and Artifacts by the words in a page and by what it means: the ⌘P palette's passages, and the
related pane's neighbours.

Onyx keeps no index of its own. It reads the one the vault MCP maintains (vault-mcp's ``data/index.db``; see
``index_path``): every note and artifact cut into passages at its headings, each with an FTS5 row for its words and a
normalised embedding for its meaning. That index already covers both of Onyx's vaults, the Notes vault and Artifacts
mounted beside it as ``Artifacts/``, and every Claude session refreshes it on startup, so a second copy here would
only drift. Onyx opens it read-only and never writes to it.

A search ranks as vault-mcp's ``search_vault`` does: a words leg (BM25, every word present) and a meaning leg (cosine
against the query, embedded by the local Ollama), fused by Reciprocal Rank Fusion. Three things suit a box being typed
into: the last word matches as a prefix, meaning counts only above ``MEANING_FLOOR``, and each page is one row, at its
best passage. The frozen app carries no numpy, so the meaning leg is a plain-Python scan (``math.sumprod``: about
200 ms over 10k passages, in a worker thread), and a newer query stops an older one's scan partway.

``related`` is the same passages read the other way round: instead of a query's direction, the direction of the page
being read, averaged over its own passages, with the nearest other pages around it. It is what vault-mcp's
``related_notes`` does, and since the page's vectors are already in the index it embeds nothing and needs no Ollama.

It degrades rather than breaks. No index: no passages, and titles still come from ``/api/vault/search``. No Ollama:
words only, and related is unaffected. Every answer says which legs ran and why one didn't, for the palette's footer.
"""

from __future__ import annotations

import array
import heapq
import itertools
import json
import logging
import math
import operator
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

logger = logging.getLogger("ask_widget.search")

DEFAULT_INDEX = "~/Projects/vault-mcp/data/index.db"  # vault-mcp's own default (its VAULT_MCP_DB)
DEFAULT_OLLAMA = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"
# What vault-mcp calls the Artifacts folder in its paths: its default mount, VAULT_MCP_MOUNTS="Artifacts=~/Documents/Artifacts".
ARTIFACTS_MOUNT = "Artifacts"
# nomic-embed-text is task-prefixed: passages went in as "search_document: …", so a query goes in as this.
QUERY_PREFIX = "search_query: "
# Ollama drops a model after five idle minutes, and loading it again costs ~0.8 s; keep it for a reading session.
KEEP_ALIVE = "30m"
EMBED_TIMEOUT = 8.0
RRF_K = 60  # vault-mcp's fusion constant (Cormack et al. 2009)
CANDIDATES = 50  # passages drawn from each leg before fusion, as vault-mcp does
MAX_TERMS = 32
MIN_WORDS = 2  # characters typed before the words leg runs
MIN_MEANING = 3  # … and before a query is worth embedding
# Meaning alone must come at least this close. nomic-embed-text's cosines are compressed: on the author's index (10k
# passages) sensible queries top out at 0.65–0.74, while keyboard noise ("asdf", "xqzv flurb") still reaches 0.55–0.56
# with the whole corpus a hair below. Under the floor the meaning leg has nothing to say, so it says nothing.
MEANING_FLOOR = 0.6
RELOAD_EVERY = 5.0  # seconds between asking the index whether it has been rebuilt
SCAN_BLOCK = 1024  # passages scored between looks for a newer query
SNIPPET_LEN = 160
# The related pane. Averaged over a page's chunks these cosines sit high and close together — measured over 120 pages
# of the author's vault, a page's nearest neighbour ran 0.76 to 0.98, median 0.89 — so neither number below is a
# similarity anyone should read as a percentage. What carries the meaning is the distance between them.
#
# At or above this, the pair was the same content twice every time it came up in that sample: a note beside its own
# HTML rendering, a meeting note beside its "… 2" copy, an inbox capture beside the note it became. Worth saying so
# about rather than opening again. Below it the band is mixed (0.93 was two genuinely different notes), and a label
# that tells someone to merge two pages has to be right, so it stays where it is precise.
DUPE_AT = 0.95
# Where a page's neighbourhood ends: the largest drop in its scores, so long as the drop is a real one. Since the
# scores are compressed, this is about the shape of the list and not its height — in that sample a third of pages had
# a drop this size and the rest ran smoothly down from the top, which is a list with no neighbourhood to mark.
GAP_MIN = 0.02
GAP_WITHIN = 12  # ranks the split is looked for among: a gap at the twentieth row divides nothing

# A C dot product from 3.12 (the app's Python is 3.14); 3.11, the oldest a checkout runs on, takes the slow road.
_dot: Callable[[Sequence[float], Sequence[float]], float] = getattr(math, "sumprod", None) or (
    lambda a, b: sum(map(operator.mul, a, b))
)
# Ollama is local, so never through a proxy the environment names.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def index_path() -> Path:
    """vault-mcp's index: ``ONYX_VAULT_INDEX``, else where vault-mcp keeps it by default."""
    return Path(os.environ.get("ONYX_VAULT_INDEX") or DEFAULT_INDEX).expanduser()


def ollama_url() -> str:
    return (os.environ.get("ONYX_OLLAMA") or DEFAULT_OLLAMA).rstrip("/")


def _short(path: Path) -> str:
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home) :] if text.startswith(home + os.sep) else text


# MARK: - Words and text

# A token as FTS5's unicode61 tokenizer sees one (vault-mcp's store._FTS_TOKEN_RE): ASCII letters, digits and the
# underscore, and anything beyond ASCII. Every ASCII separator, the double quote among them, falls outside.
_TOKEN_RE = re.compile(r"[0-9A-Za-z_-￿]+")


def fts_query(text: str) -> str:
    """The FTS5 MATCH expression for what was typed: vault-mcp's, with the last word as a prefix.

    Every word is quoted, so nothing typed reaches FTS5's query parser (``nas-tunnel`` is a syntax error there, ``a:b``
    a column filter), and a word joined by punctuation stays one phrase. The words are ANDed, so a passage holds them
    all or isn't a words hit. The last is still being typed, so it matches as a prefix unless a space follows it.
    "" when nothing typed is a word.
    """
    terms: list[str] = []
    for word in text.split():
        parts = _TOKEN_RE.findall(word)
        if parts:
            terms.append('"' + " ".join(parts) + '"')
            if len(terms) >= MAX_TERMS:
                break
    if terms and not text[-1:].isspace():
        terms[-1] += "*"
    return " AND ".join(terms)


_WIKILINK_RE = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_MDLINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Emphasis, code ticks, table pipes, and a line's heading, quote or list marker. An underscore inside a word stays.
_MARKS_RE = re.compile(r"\*+|~~|`+|\||(?<!\w)_+|_+(?!\w)|^[ \t]{0,3}(?:#{1,6}|>+|[-+]|\d+[.)])[ \t]+", re.MULTILINE)


def plain(text: str) -> str:
    """A passage or heading as the page shows it: Markdown's syntax out, links as their words, whitespace collapsed."""
    text = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _MDLINK_RE.sub(r"\1", text)
    text = _MARKS_RE.sub(" ", _HTML_TAG_RE.sub(" ", text))
    return " ".join(text.split())


def snippet(text: str, query: str) -> str:
    """About SNIPPET_LEN of a passage: around the first place a word of ``query`` starts a word in it, else its start.

    At a word's start, as the words leg matched it: "on" is no reason to quote the middle of "rationale".
    """
    line = plain(text)
    words = [re.escape(word) for word in query.split() if len(word) > 1]
    hit = re.search(r"(?<!\w)(?:" + "|".join(words) + ")", line, re.IGNORECASE) if words else None
    at = hit.start() if hit else 0
    start = 0 if at < 40 else line.rfind(" ", 0, at - 30) + 1
    end = start + SNIPPET_LEN
    if end < len(line):
        space = line.rfind(" ", start, end)
        end = space if space > start + SNIPPET_LEN // 2 else end
    return ("…" if start else "") + line[start:end].strip() + ("…" if end < len(line) else "")


# MARK: - Pages


def place(path: str, notes: Any, artifacts: Any) -> dict[str, Any] | None:
    """Where the sidebar lists a passage's page: its row's fields, or None when neither vault lists it.

    vault-mcp names a note by its path in the Notes vault, and an artifact by ``Artifacts/`` and its path in Artifacts,
    as Onyx's two trees (``vault.VaultIndex``) list them. So a path is looked up in a tree rather than joined onto a
    folder, and only a page a tree lists (a file that exists, inside its vault) comes back. A Notes folder named
    Artifacts shadows the mount in vault-mcp too, so Notes is asked first.
    """
    if notes is not None:
        item = notes.at(path)
        if item is not None and item.kind == "note" and not item.missing:
            return {"vault": "notes", "path": str(item.path), "title": item.label, "folder": item.folder}
    mount, _, rest = path.partition("/")
    if artifacts is not None and mount == ARTIFACTS_MOUNT and rest:
        item = artifacts.at(rest)
        if item is not None and not item.missing:
            return {
                "vault": "html",
                "path": str(item.path),
                "title": item.label,
                "folder": item.entry_rel.rpartition("/")[0],
            }
    return None


def locate(path: str, kind: str, tree: Any) -> str | None:
    """vault-mcp's name for a page Onyx is showing, or None when the tree doesn't list it: ``place`` the other way.

    ``path`` is the page as Onyx names it — a row's ``path``, which is what the reader carries as its ``src``: the
    lexical path inside the vault, links unresolved. A page reached by the file it really is instead (the reading
    history keys a document by its realpath) comes back through the tree's ``by_real``.
    """
    if tree is None or not path:
        return None
    item = None
    root = str(tree.root)
    lexical = os.path.normpath(path)
    if os.path.isabs(lexical) and lexical.startswith(root + os.sep):
        item = tree.at(lexical[len(root) + 1 :].replace(os.sep, "/"))
    if item is None:
        item = tree.by_real(path)
    if item is None or item.missing:
        return None
    return f"{ARTIFACTS_MOUNT}/{item.rel}" if kind == "html" else item.rel


def sections(heading_path: str, title: Any) -> tuple[str, str]:
    """A passage's own heading, and the trail of headings above it without the page's title repeated at its head."""
    trail = [plain(part) for part in heading_path.split(" > ")]
    trail = [part for part in trail if part]
    rest = trail[1:] if trail and trail[0].casefold() == str(title or "").casefold() else trail
    return (trail[-1] if trail else ""), " › ".join(rest)


def split_at(scores: list[float]) -> int:
    """How many of ``scores`` (descending) stand above the largest drop among them — 0 when none does.

    The related pane's divider. Everything below it is the plateau every page shares with every other, so the answer
    is a count of rows to act on, not a threshold: where the neighbourhood ends is a property of this page's scores,
    and a vault of nothing but near-neighbours and a vault of strangers both read correctly.
    """
    gap, cut = GAP_MIN, 0
    for i in range(1, min(len(scores), GAP_WITHIN)):
        if scores[i - 1] - scores[i] > gap:
            gap, cut = scores[i - 1] - scores[i], i
    return cut


# MARK: - The index


class Unavailable(Exception):
    """A leg can't run. The message says why, in words for the palette's footer."""


class _Superseded(Exception):
    """A newer query arrived while this one's meaning leg was scanning."""


@dataclass
class _Passages:
    stamp: tuple
    model: str
    dim: int = 0
    pos: dict[int, int] = field(default_factory=dict)  # chunk id -> position, the words leg's way in
    pages: dict[str, list[int]] = field(default_factory=dict)  # file path -> its passages, the related pane's way in
    folded: dict[str, str] = field(default_factory=dict)  # casefolded file path -> the path as the index spells it
    paths: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    vecs: list[array.array] = field(default_factory=list)


def fuse(by_words: list[int], by_meaning: list[int], cosine: dict[int, float]) -> list[tuple[int, str]]:
    """vault-mcp's Reciprocal Rank Fusion, best first, each passage with how it was found: words, meaning or both.

    Meaning takes part only above MEANING_FLOOR: below it, a passage's rank among the meaning candidates is noise.
    On an exact tie the words hit goes first, as in vault-mcp: the words leg can abstain, and the meaning leg cannot.
    """
    by_meaning = [p for p in by_meaning if cosine.get(p, 0.0) >= MEANING_FLOOR]
    fused: dict[int, float] = {}
    for positions in (by_meaning, by_words):
        for rank, p in enumerate(positions, start=1):
            fused[p] = fused.get(p, 0.0) + 1.0 / (RRF_K + rank)
    words, meaning = set(by_words), set(by_meaning)
    order = sorted(fused, key=lambda p: (-fused[p], p not in words, p))
    return [(p, "both" if p in words and p in meaning else "words" if p in words else "meaning") for p in order]


class PassageIndex:
    """vault-mcp's index, read-only: loaded on first use, and again whenever it has been rebuilt.

    ``embed(model, text)`` turns a query into a vector; by default it asks Ollama. Tests hand in their own.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        ollama: str = DEFAULT_OLLAMA,
        embed: Callable[[str, str], Sequence[float]] | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.ollama = ollama.rstrip("/")
        self._embed = embed or self._ollama_embed
        self._lock = threading.Lock()
        self._data: _Passages | None = None
        self._checked = 0.0
        self._queries = itertools.count(1)
        self._latest = 0
        # The related pane counts its own scans: it and the palette scan the same passages, and neither is a newer
        # version of the other, so a page opening behind the palette must not cancel the query being typed into it.
        self._relates = itertools.count(1)
        self._latest_related = 0

    def _connect(self) -> sqlite3.Connection:
        uri = "file:" + urllib.parse.quote(str(self.db_path)) + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)

    @staticmethod
    def _stamp(conn: sqlite3.Connection) -> tuple:
        # vault-mcp writes last_index_time when a reindex finishes, and chunk ids only grow, so a new passage moves the
        # max before then. (A removed one waits for the reindex to finish; the words leg drops ids it doesn't know.)
        row = conn.execute("SELECT value FROM meta WHERE key = 'last_index_time'").fetchone()
        return (row[0] if row else "", conn.execute("SELECT max(id) FROM chunks").fetchone()[0])

    @staticmethod
    def _load(conn: sqlite3.Connection, stamp: tuple) -> _Passages:
        row = conn.execute("SELECT value FROM meta WHERE key = 'model'").fetchone()
        data = _Passages(stamp=stamp, model=(row[0] if row else "") or DEFAULT_MODEL)
        rows = conn.execute("SELECT id, file_path, heading_path, text, embedding FROM chunks ORDER BY id")
        for chunk_id, path, heading, text, blob in rows:
            if not isinstance(blob, bytes) or not blob or len(blob) % 4:
                continue
            vec = array.array("f")
            vec.frombytes(blob)  # float32, as vault-mcp stores them: already normalised
            data.dim = data.dim or len(vec)
            if len(vec) != data.dim:
                continue
            data.pos[chunk_id] = len(data.paths)
            data.pages.setdefault(path, []).append(len(data.paths))
            data.folded.setdefault(path.casefold(), path)
            data.paths.append(path)
            data.headings.append(heading or "")
            data.texts.append(text or "")
            data.vecs.append(vec)
        return data

    def _passages(self) -> _Passages:
        with self._lock:
            now = time.monotonic()
            if self._data is not None and now - self._checked < RELOAD_EVERY:
                return self._data
            if not self.db_path.is_file():
                self._data = None
                raise Unavailable(f"no vault index at {_short(self.db_path)}")
            try:
                with closing(self._connect()) as conn:
                    stamp = self._stamp(conn)
                    if self._data is None or self._data.stamp != stamp:
                        started = time.perf_counter()
                        self._data = self._load(conn, stamp)
                        logger.info(
                            "Read %d passages from %s in %.0f ms",
                            len(self._data.paths), self.db_path, (time.perf_counter() - started) * 1000,
                        )
            except sqlite3.Error as exc:
                self._data = None
                raise Unavailable(f"the vault index can't be read ({exc})") from exc
            self._checked = now
            return self._data

    # MARK: the two legs

    def _words(self, data: _Passages, query: str) -> list[int]:
        match = fts_query(query)
        if not match:
            return []
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 1.0, 0.5) LIMIT ?",
                    (match, CANDIDATES),
                ).fetchall()
        except sqlite3.Error as exc:
            raise Unavailable(f"the vault index has no words to search ({exc})") from exc
        return [data.pos[row[0]] for row in rows if row[0] in data.pos]

    def _ollama_embed(self, model: str, text: str) -> Sequence[float]:
        body = json.dumps({"model": model, "input": [QUERY_PREFIX + text], "keep_alive": KEEP_ALIVE}).encode()
        request = urllib.request.Request(
            self.ollama + "/api/embed", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with _OPENER.open(request, timeout=EMBED_TIMEOUT) as response:
                return json.load(response)["embeddings"][0]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise Unavailable(f"Ollama doesn't have {model} (ollama pull {model})") from exc
            raise Unavailable(f"Ollama refused the query (HTTP {exc.code})") from exc
        except (urllib.error.URLError, OSError) as exc:
            slow = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
            raise Unavailable("Ollama took too long to answer" if slow else "Ollama isn't running") from exc
        except (ValueError, LookupError, TypeError) as exc:
            raise Unavailable("Ollama sent back no embedding") from exc

    def _query_vector(self, data: _Passages, text: str) -> array.array:
        raw = self._embed(data.model, text)
        norm = math.sqrt(_dot(raw, raw)) if len(raw) == data.dim else 0.0
        if not norm:
            raise Unavailable(f"{data.model} gave a vector this index can't use")
        return array.array("f", (x / norm for x in raw))

    def _meaning(self, data: _Passages, qvec: array.array, query_id: int) -> tuple[list[int], dict[int, float]]:
        scores: list[float] = []
        for start in range(0, len(data.vecs), SCAN_BLOCK):
            if self._latest != query_id:
                raise _Superseded
            scores.extend(_dot(qvec, vec) for vec in data.vecs[start : start + SCAN_BLOCK])
        top = heapq.nlargest(CANDIDATES, range(len(scores)), key=scores.__getitem__)
        return top, {p: scores[p] for p in top}

    # MARK: what the palette asks

    def search(
        self, query: str, *, place: Callable[[str], dict[str, Any] | None], limit: int = 12
    ) -> dict[str, Any]:
        """Pages whose passages match ``query``, best first, one row each.

        ``place`` turns vault-mcp's path into the row's fields (``search.place`` over Onyx's trees), or None for a page
        neither vault lists. Each row adds its passage: the heading it sits under, the trail of headings above it, a
        snippet, and how it matched. ``words`` and ``meaning`` say whether each leg could run, and why not.
        ``superseded`` means a newer query came in first and this one was dropped.
        """
        query_id = next(self._queries)
        self._latest = query_id
        words: dict[str, Any] = {"ok": True, "reason": ""}
        meaning: dict[str, Any] = {"ok": True, "reason": ""}
        result: dict[str, Any] = {"items": [], "words": words, "meaning": meaning}
        text = query.strip()
        try:
            data = self._passages()
        except Unavailable as exc:
            words.update(ok=False, reason=str(exc))
            meaning.update(ok=False, reason=str(exc))
            return result
        by_words: list[int] = []
        if len(text) >= MIN_WORDS:
            try:
                by_words = self._words(data, query)
            except Unavailable as exc:
                words.update(ok=False, reason=str(exc))
        by_meaning: list[int] = []
        cosine: dict[int, float] = {}
        if len(text) >= MIN_MEANING and data.vecs:
            try:
                by_meaning, cosine = self._meaning(data, self._query_vector(data, text), query_id)
            except Unavailable as exc:
                meaning.update(ok=False, reason=str(exc))
            except _Superseded:
                result["superseded"] = True
                return result
        seen: set[str] = set()
        for p, how in fuse(by_words, by_meaning, cosine):
            path = data.paths[p]
            if path in seen:
                continue  # a page's later passages: it already has its row, at its best one
            seen.add(path)
            row = place(path)
            if row is None:
                continue
            heading, section = sections(data.headings[p], row.get("title", ""))
            result["items"].append(
                {
                    **row,
                    "heading": heading,
                    "section": section,
                    # Found by meaning alone, it holds no word typed worth quoting around: from its start.
                    "snippet": snippet(data.texts[p], "" if how == "meaning" else text),
                    "match": how,
                }
            )
            if len(result["items"]) >= limit:
                break
        return result

    def related(self, path: str, *, place: Callable[[str], dict[str, Any] | None], limit: int = 20) -> dict[str, Any]:
        """The pages nearest ``path`` in meaning, best first: the neighbours of the page being read.

        ``path`` is vault-mcp's name for the page (``locate``). Its passages average into one direction, and every
        other page is scored against it at its best passage — vault-mcp's ``related_notes``, over the index Onyx
        already has open. Nothing is embedded, since the page's own vectors are in the index, so this asks nothing of
        Ollama and works while it is off.

        ``cut`` is how many rows stand above the largest drop in their scores (``split_at``), 0 when none does, and
        ``dupe`` marks a neighbour close enough to be the same page twice. ``reason`` says why there are no rows: a
        page too short to have been indexed, or one that hasn't been indexed yet, has no direction to search from.
        """
        scan_id = next(self._relates)
        self._latest_related = scan_id
        result: dict[str, Any] = {"note": path, "items": [], "cut": 0, "reason": ""}
        try:
            data = self._passages()
        except Unavailable as exc:
            result["reason"] = str(exc)
            return result
        own = data.pages.get(path) or data.pages.get(data.folded.get(path.casefold(), ""), [])
        if not own or not data.dim:
            result["reason"] = "this page isn't in the vault index yet"
            return result
        mean = [0.0] * data.dim
        for p in own:
            for i, value in enumerate(data.vecs[p]):
                mean[i] += value
        norm = math.sqrt(_dot(mean, mean))
        if not norm:
            result["reason"] = "this page's passages point nowhere"
            return result
        centre = array.array("f", (value / norm for value in mean))
        # Each page at its best passage, as the palette lists one: the whole scan, in blocks, so a page opened while
        # an older one is still being scored drops the older scan rather than queue behind it.
        skip, best = set(own), {}
        for start in range(0, len(data.vecs), SCAN_BLOCK):
            if self._latest_related != scan_id:
                result["superseded"] = True
                return result
            for p in range(start, min(start + SCAN_BLOCK, len(data.vecs))):
                if p in skip:
                    continue
                score = _dot(centre, data.vecs[p])
                page = data.paths[p]
                if score > best.get(page, (-2.0, 0))[0]:
                    best[page] = (score, p)
        for page in sorted(best, key=lambda page: (-best[page][0], page)):
            row = place(page)
            if row is None:
                continue  # indexed, but neither tree lists it: a note outside the vaults Onyx is showing
            score, p = best[page]
            heading, section = sections(data.headings[p], row.get("title", ""))
            result["items"].append(
                {
                    **row,
                    "score": round(score, 3),
                    "dupe": score >= DUPE_AT,
                    "heading": heading,
                    "section": section,
                    "snippet": snippet(data.texts[p], ""),
                }
            )
            if len(result["items"]) >= limit:
                break
        result["cut"] = split_at([item["score"] for item in result["items"]])
        return result

    def knows(self, path: str) -> bool:
        """Whether the index holds passages under this name.

        One file can have more than one name here: an Artifacts page is usually a link, and when it points into the
        Notes vault the same bytes are both ``Artifacts/…`` and a note. vault-mcp indexes one of them — it leaves out
        a vault HTML file that is the rendering of a same-name ``.md`` — so which name to ask about is a question only
        the index can answer, and guessing it from the vault the reader happens to be in gets it wrong.
        """
        try:
            data = self._passages()
        except Unavailable:
            return False
        return path in data.pages or path.casefold() in data.folded

    def status(self, *, warm: bool = False) -> dict[str, Any]:
        """Which legs a search would run, and why one wouldn't.

        ``warm`` (the palette opening) reads the index in and wakes the embedding model, so the first query typed
        doesn't wait on either.
        """
        words: dict[str, Any] = {"ok": True, "reason": ""}
        meaning: dict[str, Any] = {"ok": True, "reason": ""}
        try:
            data = self._passages()
        except Unavailable as exc:
            words.update(ok=False, reason=str(exc))
            meaning.update(ok=False, reason=str(exc))
            return {"words": words, "meaning": meaning, "passages": 0}
        if warm:
            try:
                self._query_vector(data, "")
            except Unavailable as exc:
                meaning.update(ok=False, reason=str(exc))
        return {"words": words, "meaning": meaning, "passages": len(data.paths)}
