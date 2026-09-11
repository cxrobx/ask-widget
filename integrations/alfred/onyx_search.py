#!/usr/bin/env python3
"""Alfred Script Filter: find a page in Onyx's Notes or Artifacts.

    onyx_search.py titles  QUERY    keyword onx  — a page by its title or folder
    onyx_search.py content QUERY    keyword onxc — a page by the words in it

Pages are listed the way Onyx's sidebar lists them (src/ask_widget/vault.py): a
note is named by its filename, an artifact by its <title>, and below the project
level a folder holding index.html is one page. The rules are copied rather than
imported because Alfred runs this with the system Python (/usr/bin/python3,
3.9), which cannot import the app; tests/test_alfred_search.py holds the copy to
vault.py so the two cannot drift apart unnoticed.

The workflow's next node opens the chosen page in Onyx.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from contextlib import closing

# Mirrors of vault.py and viewer.py.
NOTE_EXTENSIONS = {".html", ".htm", ".md", ".markdown", ".txt", ".pdf"}
HTML_EXTENSIONS = {".html", ".htm"}
INDEX_NAMES = ("index.html", "index.htm")
EXCLUDED_DIRS = {"node_modules", "__pycache__"}  # plus any name starting with "."
MAX_ENTRIES = 20_000
MAX_DEPTH = 24
TITLE_SCAN_BYTES = 64 * 1024
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)

# Onyx's saved folders (Settings ▸ Vaults) and the defaults it uses when none is saved.
ONYX_DB = os.path.expanduser("~/Library/Application Support/Onyx/onyx.db")
VAULT_KEYS = (
    ("html", "html_vault_root", "~/Documents/Artifacts"),
    ("notes", "vault_root", "~/Documents/CX"),
)
VAULT_NAMES = {"notes": "Notes", "html": "Artifacts"}

TITLE_RESULTS = 50
RECENT_RESULTS = 30
CONTENT_RESULTS = 40
MAX_BYTES = 4_000_000  # a bigger file is still found by its title
SNIPPET_LEN = 110

# What a reader never sees: elements whose text is code or metadata, comments,
# then every remaining tag — attributes, and the data: URIs inside them, go with it.
_HIDDEN_RE = re.compile(
    r"<(script|style|template|noscript|head|title)\b.*?</\1\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


class Page:
    __slots__ = ("path", "rel", "vault", "label", "folder", "mtime", "ident")

    def __init__(self, path, rel, vault, label, folder, mtime, ident):
        self.path = path  # the path as the vault shows it — never resolved
        self.rel = rel  # posix path below the vault root
        self.vault = vault  # "notes" | "html"
        self.label = label
        self.folder = folder  # where the entry sits, for the subtitle
        self.mtime = mtime
        self.ident = ident  # (st_dev, st_ino) of the real file


# MARK: - Listing, as Onyx does


def _ignore(_error):
    return None


def _rel(dirpath: str, root: str) -> str:
    return "" if dirpath == root else dirpath[len(root) :].lstrip("/")


def _loop_guard(root: str) -> set | None:
    """Folders already walked, seeded with the root; None when the root is gone."""
    try:
        st = os.stat(root)
    except OSError:
        return None
    return {(st.st_dev, st.st_ino)}


def _keep_dirs(dirpath: str, dirnames: list, seen: set) -> list:
    keep = []
    for name in dirnames:
        if name.startswith(".") or name in EXCLUDED_DIRS:
            continue
        try:
            st = os.stat(os.path.join(dirpath, name))
        except OSError:
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen:
            continue  # a symlink loop, or a second link to a folder already walked
        seen.add(key)
        keep.append(name)
    keep.sort(key=str.casefold)
    return keep


def walk_notes(root: str) -> list:
    """The Notes vault's documents, as its tree lists them (minus dangling links)."""
    pages: list = []
    seen = _loop_guard(root)
    if seen is None:
        return pages
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True, onerror=_ignore):
        rel_dir = _rel(dirpath, root)
        depth = rel_dir.count("/") + 1 if rel_dir else 0
        dirnames[:] = [] if depth >= MAX_DEPTH else _keep_dirs(dirpath, dirnames, seen)
        for filename in sorted(filenames, key=str.casefold):
            stem, ext = os.path.splitext(filename)
            if filename.startswith(".") or ext.lower() not in NOTE_EXTENSIONS:
                continue
            path = os.path.join(dirpath, filename)
            try:
                st = os.stat(path)
            except OSError:
                continue  # a link whose target is gone: nothing to open
            rel = f"{rel_dir}/{filename}" if rel_dir else filename
            pages.append(Page(path, rel, "notes", stem, rel_dir, st.st_mtime, (st.st_dev, st.st_ino)))
            if len(pages) >= MAX_ENTRIES:
                return pages
    return pages


def page_title(path: str) -> str:
    """The page's <title> with tags stripped and whitespace collapsed, or ""."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(TITLE_SCAN_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    match = _TITLE_RE.search(head)
    raw = re.sub(r"<[^>]+>", "", match.group(1)) if match else ""
    return " ".join(html.unescape(raw).split())[:300]


def _add_artifact(pages: list, path: str, rel: str, *, page_dir: bool = False) -> None:
    try:
        st = os.stat(path)
    except OSError:
        return  # Onyx lists a dangling link as missing; there is nothing to open
    label = page_title(path)
    if not label:
        label = os.path.basename(os.path.dirname(path)) if page_dir else os.path.splitext(os.path.basename(path))[0]
    entry = rel.rpartition("/")[0] if page_dir else rel  # a page folder sits where its folder does
    pages.append(Page(path, rel, "html", label, entry.rpartition("/")[0], st.st_mtime, (st.st_dev, st.st_ino)))


def walk_artifacts(root: str) -> list:
    """Artifacts' pages, as its tree lists them: by <title>, a guide folder as one page."""
    pages: list = []
    seen = _loop_guard(root)
    if seen is None:
        return pages
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True, onerror=_ignore):
        rel_dir = _rel(dirpath, root)
        depth = rel_dir.count("/") + 1 if rel_dir else 0
        # Below the project level, a folder with an index page is one page.
        index_name = next((n for n in filenames if n.lower() in INDEX_NAMES), None) if depth >= 2 else None
        if index_name is not None:
            dirnames[:] = []
            _add_artifact(pages, os.path.join(dirpath, index_name), f"{rel_dir}/{index_name}", page_dir=True)
            continue
        dirnames[:] = [] if depth >= MAX_DEPTH else _keep_dirs(dirpath, dirnames, seen)
        for filename in sorted(filenames, key=str.casefold):
            if filename.startswith(".") or os.path.splitext(filename)[1].lower() not in HTML_EXTENSIONS:
                continue
            _add_artifact(pages, os.path.join(dirpath, filename), f"{rel_dir}/{filename}" if rel_dir else filename)
        if len(pages) >= MAX_ENTRIES:
            break
    return pages


def configured_roots(environ=os.environ) -> dict:
    """{"html": folder, "notes": folder} from Onyx's settings, else its defaults.

    A folder saved as "" is hidden in Onyx, so it is left out here too.
    ONYX_DB, ONYX_NOTES_ROOT and ONYX_ARTIFACTS_ROOT override, for tests or an
    Alfred workflow variable.
    """
    saved: dict = {}
    db = environ.get("ONYX_DB") or ONYX_DB
    try:
        uri = "file:" + urllib.parse.quote(db) + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as conn:
            rows = conn.execute(
                "SELECT key, value_json FROM settings WHERE key IN ('vault_root', 'html_vault_root')"
            ).fetchall()
        for key, value_json in rows:
            value = json.loads(value_json)
            if isinstance(value, str):
                saved[key] = value
    except (sqlite3.Error, ValueError, TypeError):
        pass  # no Onyx database yet, or an older one: the defaults stand
    overrides = {"vault_root": environ.get("ONYX_NOTES_ROOT"), "html_vault_root": environ.get("ONYX_ARTIFACTS_ROOT")}
    roots = {}
    for vault, key, default in VAULT_KEYS:
        raw = overrides[key] if overrides[key] is not None else saved.get(key, default)
        if not raw.strip():
            continue
        root = os.path.normpath(os.path.expanduser(raw.strip()))
        if os.path.isabs(root) and os.path.isdir(root):
            roots[vault] = root
    return roots


def collect(roots: dict) -> list:
    """Every page in both vaults; a file reachable twice is listed once, Artifacts first."""
    pages, seen = [], set()
    for vault, walk in (("html", walk_artifacts), ("notes", walk_notes)):
        root = roots.get(vault)
        if root is None:
            continue
        for page in walk(root):
            if page.ident not in seen:
                seen.add(page.ident)
                pages.append(page)
    return pages


# MARK: - Searching


def terms_of(query: str) -> list:
    return [term for term in re.split(r"[&,\s]+", query.casefold()) if term]


def ago(mtime: float, now: float | None = None) -> str:
    seconds = max(0.0, (time.time() if now is None else now) - mtime)
    for unit, size in (("yr", 365 * 86400), ("mo", 30 * 86400), ("wk", 7 * 86400), ("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


def where(page: Page) -> str:
    name = VAULT_NAMES[page.vault]
    return f"{name} · {page.folder}" if page.folder else name


def alfred_item(page: Page, subtitle: str, largetype: str = "") -> dict:
    return {
        "type": "file:skipcheck",  # → on a result offers Alfred's file actions
        "title": page.label,
        "subtitle": subtitle,
        "arg": page.path,
        "icon": {"path": "icon.png"},
        "quicklookurl": page.path,
        "text": {"copy": page.path, "largetype": largetype or page.label},
    }


def search_titles(pages: list, query: str) -> list:
    """Pages whose title or folder holds every word; titles that hold them all first."""
    terms = terms_of(query)
    if not terms:
        recent = sorted(pages, key=lambda page: page.mtime, reverse=True)[:RECENT_RESULTS]
        return [alfred_item(page, f"{where(page)} · {ago(page.mtime)}") for page in recent]
    hits = []
    for page in pages:
        label, folder = page.label.casefold(), page.folder.casefold()
        if all(term in label or term in folder for term in terms):
            hits.append((not all(term in label for term in terms), label, page))
    hits.sort(key=lambda hit: hit[:2])
    return [alfred_item(page, f"{where(page)} · {ago(page.mtime)}") for _, _, page in hits[:TITLE_RESULTS]]


def page_text(page: Page) -> str:
    """What a reader of the page sees, as text; "" for a PDF or an oversized file."""
    ext = os.path.splitext(page.path)[1].lower()
    if ext == ".pdf":
        return ""
    try:
        if os.path.getsize(page.path) > MAX_BYTES:
            return ""
        with open(page.path, encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return ""
    if ext in HTML_EXTENSIONS:
        text = " ".join(html.unescape(_TAG_RE.sub(" ", _HIDDEN_RE.sub(" ", text))).split())
    return text


def snippet(text: str, term: str) -> str:
    """One cleaned line of ``text`` around the first hit of ``term``, or ""."""
    match = re.search(re.escape(term), text, re.IGNORECASE)
    if match is None:
        return ""
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    line_end = len(text) if line_end == -1 else line_end
    lo, hi = max(line_start, match.start() - 200), min(line_end, match.end() + 200)
    line = " ".join(re.sub(r"[#>*`_\[\]|]+", " ", text[lo:hi]).split())
    cut_left, cut_right = lo > line_start, hi < line_end
    if len(line) > SNIPPET_LEN:
        at = max(0, line.casefold().find(term) - 35)
        cut_left = cut_left or at > 0
        cut_right = cut_right or at + SNIPPET_LEN < len(line)
        line = line[at : at + SNIPPET_LEN].strip()
    return ("…" if cut_left else "") + line + ("…" if cut_right else "")


def search_content(pages: list, query: str) -> list:
    """Pages holding every word in their title or visible text, best matches first."""
    terms = terms_of(query)
    if not terms:
        return []
    scored = []
    for page in pages:
        label = page.label.casefold()
        text = page_text(page)
        folded = text.casefold()
        if not all(term in label or term in folded for term in terms):
            continue
        excerpt = next((s for s in (snippet(text, term) for term in terms) if s), "")
        score = (sum(term in label for term in terms), sum(folded.count(term) for term in terms), page.mtime)
        subtitle = f"{where(page)}  ·  {excerpt}" if excerpt else where(page)
        scored.append((score, alfred_item(page, subtitle, excerpt)))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _, item in scored[:CONTENT_RESULTS]]


def empty_state(mode: str, query: str, roots: dict) -> dict:
    query = query.strip()
    if not roots:
        title, subtitle = "No Onyx vaults to search", "Set the Notes and Artifacts folders in Onyx ▸ Settings ▸ Vaults"
    elif mode == "content" and query:
        title = f"No Onyx page mentions “{query}”"
        subtitle = "Every word must appear in the same page · try fewer words, or  onx  for titles"
    elif mode == "content":
        title, subtitle = "Search inside Onyx's notes and artifacts", "Type words found in a page · use  onx  to search titles"
    elif query:
        title, subtitle = f"No Onyx titles match “{query}”", "Try fewer words, or use  onxc  to search inside pages"
    else:
        title, subtitle = "Nothing in Onyx yet", "Notes and Artifacts are both empty"
    return {"title": title, "subtitle": subtitle, "valid": False, "icon": {"path": "icon.png"}}


def main(argv: list) -> None:
    mode = argv[1] if len(argv) > 1 else "titles"
    query = argv[2] if len(argv) > 2 else ""
    if query.strip() in ("…", "..."):
        query = ""  # Alfred's stand-in for an empty query
    roots = configured_roots()
    pages = collect(roots)
    items = search_content(pages, query) if mode == "content" else search_titles(pages, query)
    print(json.dumps({"items": items or [empty_state(mode, query, roots)]}))


if __name__ == "__main__":
    main(sys.argv)
