"""Wrap any HTML (remote URL or local file) so the widget can run against it.

The widget needs three things a foreign page won't give it on its own:
  * to be **same-origin** with the server (so the `fetch` POST to /ask is allowed
    and not blocked as cross-origin or mixed-content);
  * its **assets resolved** (these docs pull a relative `../.assets/dossier.css`,
    which would 404 — and render unstyled — if served naively);
  * the **widget `<script>` injected**.

`prepare_html` does all three: it rewrites `<link>/<script>/<img>/<source>/<audio>/<video>/<track>` asset
refs to absolute URLs (remote → the original site; local → a `/_fs/...` route that
serves the file from disk), strips any `<base>`/CSP that would break same-origin
anchors or block the widget, and injects the widget script before `</body>`.
Remote documents are always made inert. Local HTML can explicitly keep its scripts
so interactive artifacts behave like they do when opened directly in a browser.
"""

from __future__ import annotations

import html as _html
import ipaddress
import os
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt
from markdown_it.common import normalize_url as _normalize_url
from markdown_it.token import Token

from . import __version__

if TYPE_CHECKING:  # vault.py imports from this module; keep the runtime import one-way
    from .vault import VaultIndex

# Match a <link|script|img|source|audio|video|track ...> tag's first href/src
# value. The quote class is symmetric (group 2 = opening quote, back-referenced as
# the closing quote) so single-quoted attributes are handled too. These docs are
# generated and well-formed, so a scoped regex beats dragging in an HTML parser
# that would round-trip-mangle the markup. Media tags are here because study
# guides narrate with a bare `<audio src="audio/x.m4a">`, which would otherwise
# resolve against /view and 404.
_ASSET_RE = re.compile(
    r"""(<(?:link|script|img|source|audio|video|track)\b[^>]*?\b(?:href|src)=(["']))(.*?)\2""",
    re.IGNORECASE,
)
# Used to neutralize a viewed document's own scripts when the caller has not
# explicitly trusted a local HTML file. Re-serving foreign HTML same-origin on
# localhost would otherwise let its inline JS read /_fs and /ask and exfiltrate.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>|<script\b[^>]*/>", re.IGNORECASE | re.DOTALL)
_BASE_RE = re.compile(r"<base\b[^>]*>", re.IGNORECASE)
_CSP_RE = re.compile(
    r'<meta\b[^>]*http-equiv=["\']?content-security-policy["\']?[^>]*>',
    re.IGNORECASE,
)
_BODY_RE = re.compile(r"</body>", re.IGNORECASE)
_EMBEDDED_BASE_HREF_RE = re.compile(r'<base\b[^>]*\bhref=["\']([^"\']*)["\']', re.IGNORECASE)

_SKIP_PREFIXES = ("http://", "https://", "data:", "//", "#", "mailto:", "tel:", "javascript:")

MAX_REMOTE_BYTES = 12 * 1024 * 1024
LOCAL_DOCUMENT_EXTENSIONS = {".html", ".htm", ".md", ".markdown", ".txt", ".pdf"}

# Note: .json / .map deliberately excluded — serving them would turn /_fs into a
# reader for secret-bearing config (.docker/config.json, ~/.claude.json, …). /_fs
# is additionally locked to the exact asset files a viewed document references.
ASSET_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".vtt": "text/vtt",
}


class RangeNotSatisfiable(ValueError):
    """A ``Range`` header that asks for bytes the file does not have (HTTP 416)."""


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse one ``Range: bytes=…`` header into an inclusive ``(start, end)``.

    WebKit will not play ``<audio>``/``<video>`` from a server that ignores
    ranges: it opens with ``bytes=0-1`` and gives up on a plain 200. So /_fs
    answers single ranges with 206. Returns None when the whole file should be
    sent — no header, a unit other than bytes, a multi-range list, or a header
    too malformed to mean anything (RFC 9110 lets a server ignore those).
    Raises :class:`RangeNotSatisfiable` for a well-formed range past the end.
    """
    if not header:
        return None
    unit, sep, spec = header.partition("=")
    if not sep or unit.strip().lower() != "bytes" or "," in spec:
        return None
    first, dash, last = spec.strip().partition("-")
    if not dash:
        return None
    first, last = first.strip(), last.strip()
    if not (first.isdigit() or first == "") or not (last.isdigit() or last == "") or (first == last == ""):
        return None
    if first == "":  # suffix: the last N bytes
        length = int(last)
        if length == 0 or size == 0:
            raise RangeNotSatisfiable(header)
        return max(0, size - length), size - 1
    start = int(first)
    if last and int(last) < start:
        return None  # backwards, so malformed rather than unsatisfiable
    if start >= size:
        raise RangeNotSatisfiable(header)
    end = int(last) if last else size - 1
    return start, min(end, size - 1)


class ViewerError(Exception):
    """Raised with a user-facing message when a source can't be loaded."""


@dataclass(frozen=True)
class LoadedDocument:
    html: str
    title: str
    kind: str
    page_count: int | None = None


@dataclass
class RenderContext:
    """What the Markdown renderer needs to turn links into reader URLs.

    ``doc_path`` is the **lexical** path of the document (a note under a
    symlinked vault folder keeps its vault-visible path here); ``vault`` is set
    only when the document lives inside the configured vault.
    """

    doc_path: Path
    folder: str | None = None
    vault: "VaultIndex | None" = None

    def view_url(self, target: Path | str) -> str:
        params = {"src": str(target)}
        if self.folder:
            params["folder"] = self.folder
        # Encode exactly once; a literal ``%`` in a filename round-trips as ``%25``.
        return "/view?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="/")

    def rewrite_href(self, href: str) -> str:
        """Point a relative/absolute/file: link at a local document to /view."""
        if not href or href.startswith("#"):
            return href
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme and parsed.scheme.lower() != "file":
            return href
        raw = urllib.parse.unquote(parsed.path)
        if not raw:
            return href
        if Path(raw).suffix.lower() not in LOCAL_DOCUMENT_EXTENSIONS:
            return href
        if raw.startswith("/"):
            target = Path(os.path.normpath(raw))
            if not target.is_file():
                return href
        else:
            target = Path(os.path.normpath(str(self.doc_path.parent / raw)))
        url = self.view_url(target)
        if parsed.fragment:
            url += "#" + parsed.fragment
        return url


def is_remote(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


def validate_remote_url(url: str, *, allow_private: bool = False) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ViewerError("Only http:// and https:// document URLs are supported.")
    if parsed.username or parsed.password:
        raise ViewerError("Document URLs containing credentials are not allowed.")
    if allow_private:
        return url
    try:
        records = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise ViewerError(f"Could not resolve {parsed.hostname}: {exc}") from exc
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ViewerError(
                f"Remote documents may not resolve to private/local addresses ({address}). "
                "This can be changed in Settings for trusted development URLs."
            )
    return url


class _ValidatedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool) -> None:
        super().__init__()
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_remote_url(newurl, allow_private=self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_remote(url: str, *, allow_private: bool = False) -> str:
    validate_remote_url(url, allow_private=allow_private)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"Onyx/{__version__} (+local reading companion)"},
    )
    try:
        opener = urllib.request.build_opener(_ValidatedRedirects(allow_private))
        with opener.open(req, timeout=20) as resp:  # noqa: S310 - validated above
            content_type = (resp.headers.get_content_type() or "").lower()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ViewerError(f"Remote URL returned unsupported content type: {content_type}")
            raw = resp.read(MAX_REMOTE_BYTES + 1)
    except ViewerError:
        raise
    except Exception as exc:  # urllib raises a zoo of error types
        raise ViewerError(f"Could not fetch {url}: {exc}") from exc
    if len(raw) > MAX_REMOTE_BYTES:
        raise ViewerError(f"Remote document is larger than {MAX_REMOTE_BYTES // (1024 * 1024)} MB.")
    return raw.decode("utf-8", errors="replace")


def read_local(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise ViewerError(f"No such file: {path}")
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ViewerError(f"Could not read {path}: {exc}") from exc


# MARK: - Markdown: frontmatter, wikilinks, link rewriting

# Obsidian-style YAML frontmatter: an opening ``---`` on the very first line and
# a closing ``---``/``...`` line. Only matched at offset 0.
_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(?:(.*?)\r?\n)?(?:---|\.\.\.)[ \t]*(?:\r?\n|$)", re.DOTALL
)
_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_][\w .\-/]*?)\s*:(?:\s+(.*))?$")
_FLOW_ITEM_RE = re.compile(r'"[^"]*"|\'[^\']*\'|[^,]+')
_TAG_KEYS = {"tags", "tag"}
MAX_FRONTMATTER_LINES = 60


def _yaml_shaped(block: str) -> bool:
    """Every top-level line is ``key:`` or ``- item``; indented lines are free."""
    saw_key = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            continue
        if _YAML_KEY_RE.match(line):
            saw_key = True
            continue
        return False
    return saw_key


def split_frontmatter(raw: str) -> tuple[str | None, str]:
    """Return ``(frontmatter_block, body)``; block is None when there is none.

    A leading ``---`` that is not followed by a YAML-shaped block and a closing
    fence is an ordinary horizontal rule and never swallows content.
    """
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return None, raw
    block = match.group(1) or ""
    if block.strip() and not _yaml_shaped(block):
        return None, raw
    return block, raw[match.end():]


def _unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_flat_frontmatter(block: str) -> dict[str, str | list[str]] | None:
    """Parse ``key: value`` / flow lists / block lists. ``None`` when nested."""
    lines = block.splitlines()
    if len(lines) > MAX_FRONTMATTER_LINES:
        return None
    fields: dict[str, str | list[str]] = {}
    list_key: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            if list_key is None:
                return None
            item = _unquote_scalar(stripped[1:])
            existing = fields[list_key]
            assert isinstance(existing, list)
            if item:
                existing.append(item)
            continue
        if line[:1] in (" ", "\t"):
            return None  # nested mapping or multi-line scalar
        match = _YAML_KEY_RE.match(line)
        if not match:
            return None
        key = match.group(1).strip()
        value = (match.group(2) or "").strip()
        if not value:
            fields[key] = []
            list_key = key
            continue
        list_key = None
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fields[key] = [
                _unquote_scalar(item) for item in _FLOW_ITEM_RE.findall(inner) if item.strip()
            ] if inner else []
        else:
            fields[key] = _unquote_scalar(value)
    return fields


def _render_properties(block: str, md: MarkdownIt, env: dict[str, Any]) -> str:
    fields = parse_flat_frontmatter(block)
    if fields is None:
        return (
            '<details class="askw-properties"><summary>Properties</summary>'
            f"<pre>{_html.escape(block.strip())}</pre></details>"
        )
    if not fields:
        return ""

    def inline(text: str) -> str:
        return md.renderInline(text, env)

    rows: list[str] = []
    for key, value in fields.items():
        if isinstance(value, list):
            if not value:
                cell = '<span class="askw-empty">—</span>'
            elif key.casefold() in _TAG_KEYS:
                cell = "".join(
                    f'<span class="askw-tag">#{_html.escape(item.lstrip("#"))}</span>' for item in value
                )
            else:
                cell = ", ".join(inline(item) for item in value)
        elif key.casefold() in _TAG_KEYS:
            cell = "".join(
                f'<span class="askw-tag">#{_html.escape(item.strip().lstrip("#"))}</span>'
                for item in re.split(r"[,\s]+", value)
                if item.strip()
            )
        else:
            cell = inline(value) if value else '<span class="askw-empty">—</span>'
        rows.append(f"<dt>{_html.escape(key)}</dt><dd>{cell}</dd>")
    return (
        f'<details class="askw-properties"><summary>Properties · {len(rows)}</summary>'
        f"<dl>{''.join(rows)}</dl></details>"
    )


def _validate_link(url: str) -> bool:
    # markdown-it rejects ``file:`` outright; the reader turns those into /view
    # links, so allow the scheme while keeping javascript:/vbscript:/data: out.
    if url.strip().lower().startswith("file:"):
        return True
    return _normalize_url.validateLink(url)


def _wikilink_rule(state: Any, silent: bool) -> bool:
    """``[[Note]]``, ``[[Note|Alias]]``, ``[[Note#Heading]]``, ``![[image.png]]``."""
    ctx = (state.env or {}).get("askw") if state.env is not None else None
    if ctx is None or ctx.vault is None:
        return False  # non-vault markdown keeps the literal text
    src: str = state.src
    pos: int = state.pos
    embed = False
    if src.startswith("![[", pos):
        embed = True
        start = pos + 3
    elif src.startswith("[[", pos):
        start = pos + 2
    else:
        return False
    end = src.find("]]", start, state.posMax)
    if end < 0:
        return False
    inner = src[start:end]
    if not inner.strip() or "\n" in inner or "[[" in inner:
        return False
    if silent:
        return True
    target, _, alias = inner.partition("|")
    target = target.strip()
    alias = alias.strip()
    base, _, heading = target.partition("#")
    base = base.strip()
    label = alias or target
    vault = ctx.vault
    resolved = None
    if base:
        resolved = vault.resolve_embed(base, source=ctx.doc_path) if embed else vault.resolve_wikilink(base, source=ctx.doc_path)

    from .vault import IMAGE_EXTENSIONS  # local import keeps module dependency one-way

    if resolved is not None and embed and resolved.path.suffix.lower() in IMAGE_EXTENSIONS:
        token = state.push("image", "img", 0)
        token.attrSet("src", urllib.parse.quote(str(resolved.path)))
        if alias.isdigit():
            token.attrSet("width", alias)
        child = Token("text", "", 0)
        child.content = "" if alias.isdigit() else (alias or resolved.name)
        token.children = [child]
    elif resolved is not None and resolved.path.suffix.lower() in LOCAL_DOCUMENT_EXTENSIONS:
        token = state.push("link_open", "a", 1)
        href = ctx.view_url(resolved.path)
        if heading and not embed:
            href += "#" + urllib.parse.quote(heading.strip())
        token.attrSet("href", href)
        token.attrSet("class", "askw-wikilink askw-embed" if embed else "askw-wikilink")
        token.attrSet("title", resolved.rel)
        token.meta["askw_local"] = True
        text = state.push("text", "", 0)
        text.content = label if (alias or not embed) else resolved.name
        state.push("link_close", "a", -1)
    else:
        token = state.push("wikilink_missing_open", "span", 1)
        token.attrSet("class", "askw-wikilink-missing")
        token.attrSet(
            "title",
            "Unsupported embed" if resolved is not None else f"No note named “{base or target}”",
        )
        text = state.push("text", "", 0)
        text.content = label
        state.push("wikilink_missing_close", "span", -1)
    state.pos = end + 2
    return True


def _render_link_open(self: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
    token = tokens[idx]
    ctx = (env or {}).get("askw") if env is not None else None
    href = token.attrGet("href")
    if isinstance(href, str) and ctx is not None and not token.meta.get("askw_local"):
        href = ctx.rewrite_href(href)
        token.attrSet("href", href)
    token.attrSet("rel", "noreferrer noopener")
    if isinstance(href, str) and href.lower().startswith(("http://", "https://")):
        token.attrSet("target", "_top")  # leave the reader frame; a no-op at top level
    return self.renderToken(tokens, idx, options, env)


def _build_markdown() -> MarkdownIt:
    # markdown-it escapes raw HTML and rejects unsafe URL schemes under the
    # CommonMark preset. Tables are the one GitHub-style extension readers need
    # most often; everything remains local and deterministic.
    md = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": False,
        },
    ).enable("table")
    md.validateLink = _validate_link  # type: ignore[method-assign]
    # Before ``link`` so ``[[Note]]`` is never mistaken for a reference link.
    # ``backticks`` runs earlier, so wikilinks inside code spans stay literal.
    md.inline.ruler.before("link", "wikilink", _wikilink_rule)
    md.add_render_rule("link_open", _render_link_open)
    return md


_MARKDOWN = _build_markdown()


def _markdown_html(raw: str, title: str, *, ctx: RenderContext) -> str:
    block, body = split_frontmatter(raw)
    env: dict[str, Any] = {"askw": ctx}
    properties = _render_properties(block, _MARKDOWN, env) if block is not None else ""
    rendered = _MARKDOWN.render(body, env)
    return _reading_shell(title, properties + rendered, kind="markdown")


def _reading_shell(title: str, body: str, *, kind: str) -> str:
    # ``main`` is relative so WebKit takes it for a selection root: a selection across blocks fills the gaps
    # between them out to the root's edges, which were the window's while the root was ``body``.
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{_html.escape(title)}</title>
<style>
:root{{color-scheme:light dark;--reader-bg:247 247 247;--reader-pane:255 255 255;--reader-ink:38 36 33;--reader-muted:87 83 78;--reader-faint:168 162 158;--reader-line:0 0 0;--reader-code:243 242 239;--reader-accent:58 131 247}}
html,body{{min-height:100%;background:transparent}} body{{margin:0;background:rgb(var(--reader-bg)/.76);color:rgb(var(--reader-ink));font:17px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;backdrop-filter:saturate(1.08)}}
main{{position:relative;box-sizing:border-box;max-width:860px;min-height:100vh;margin:0 auto;padding:64px 72px 110px;background:rgb(var(--reader-pane)/.86);border-inline:1px solid rgb(var(--reader-line)/.09);box-shadow:0 18px 55px rgb(0 0 0/.06);backdrop-filter:blur(22px) saturate(1.16)}}
h1,h2,h3{{line-height:1.2;letter-spacing:-.02em}} h1{{font-size:2.35rem}} h2{{margin-top:2.2em}}
pre{{overflow:auto;padding:18px;border-radius:10px;background:rgb(var(--reader-code)/.88);font:14px/1.55 ui-monospace,SFMono-Regular,monospace}}
code{{background:rgb(var(--reader-code)/.88);padding:.12em .32em;border-radius:4px}} pre code{{padding:0}}
a{{color:rgb(var(--reader-accent))}} blockquote{{margin-left:0;padding-left:20px;border-left:3px solid rgb(var(--reader-line)/.16);color:rgb(var(--reader-muted))}}
table{{width:100%;margin:1.4em 0;border-collapse:collapse;font-size:.92em}} th,td{{padding:9px 11px;border:1px solid rgb(var(--reader-line)/.14);text-align:left;vertical-align:top}} th{{background:rgb(var(--reader-code)/.72);font-weight:650}} tbody tr:nth-child(even){{background:rgb(var(--reader-code)/.34)}}
hr{{border:0;border-top:1px solid rgb(var(--reader-line)/.14);margin:2em 0}} img{{max-width:100%;height:auto}}
.askw-pdf-page{{position:relative;margin:0 0 32px;padding:36px 44px;border:1px solid rgb(var(--reader-line)/.11);background:rgb(var(--reader-pane)/.64);box-shadow:0 8px 24px rgb(0 0 0/.07);backdrop-filter:blur(12px)}}
.askw-page-label{{margin:0 0 24px;color:rgb(var(--reader-faint));font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
.askw-properties{{margin:0 0 1.6em;padding:9px 14px;border:1px solid rgb(var(--reader-line)/.12);border-radius:10px;background:rgb(var(--reader-code)/.5);font-size:.88em}} .askw-properties summary{{cursor:pointer;color:rgb(var(--reader-muted));font-weight:600;letter-spacing:.02em}}
.askw-properties dl{{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 18px;margin:10px 0 2px}} .askw-properties dt{{color:rgb(var(--reader-muted));font-weight:600}} .askw-properties dd{{margin:0;overflow-wrap:anywhere}} .askw-properties pre{{margin:8px 0 0}} .askw-empty{{color:rgb(var(--reader-faint))}}
.askw-tag{{display:inline-block;margin:0 4px 2px 0;padding:1px 8px;border-radius:999px;background:rgb(var(--reader-accent)/.12);color:rgb(var(--reader-accent));font-size:.85em}}
.askw-wikilink-missing{{border-bottom:1px dotted rgb(var(--reader-faint));color:rgb(var(--reader-muted));cursor:help}} a.askw-embed{{display:inline-block;padding:1px 8px;border:1px dashed rgb(var(--reader-line)/.25);border-radius:6px;text-decoration:none}} a.askw-embed::before{{content:"⧉ ";opacity:.6}}
@media(prefers-color-scheme:dark){{:root{{--reader-bg:24 24 24;--reader-pane:31 31 31;--reader-ink:245 245 245;--reader-muted:205 205 205;--reader-faint:143 143 143;--reader-line:255 255 255;--reader-code:48 48 48}} body{{background:rgb(var(--reader-bg)/.70)}} main{{background:rgb(var(--reader-pane)/.78);box-shadow:0 18px 60px rgb(0 0 0/.28)}}}}
@media(max-width:720px){{main{{padding:36px 24px}}}}
@media(prefers-reduced-transparency:reduce){{body,main,.askw-pdf-page{{backdrop-filter:none}} body{{background:rgb(var(--reader-bg))}} main{{background:rgb(var(--reader-pane))}}}}
</style></head><body data-askw-document-kind="{kind}"><main>{body}</main></body></html>"""


def _pdf_html(path: Path) -> LoadedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise ViewerError("PDF support is unavailable in this build.") from exc
    try:
        reader = PdfReader(str(path))
        title = str((reader.metadata or {}).get("/Title") or path.stem)
        pages: list[str] = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            paragraphs = "".join(
                f"<p>{_html.escape(chunk.strip())}</p>"
                for chunk in re.split(r"\n\s*\n", text)
                if chunk.strip()
            )
            if not paragraphs:
                paragraphs = "<p><em>No extractable text was found on this page.</em></p>"
            pages.append(
                f'<section class="askw-pdf-page" data-askw-page="{number}">'
                f'<p class="askw-page-label">Page {number}</p>{paragraphs}</section>'
            )
    except Exception as exc:
        raise ViewerError(f"Could not read PDF {path}: {exc}") from exc
    return LoadedDocument(
        html=_reading_shell(title, "".join(pages), kind="pdf"),
        title=title,
        kind="pdf",
        page_count=len(reader.pages),
    )


def load_local_document(
    path: Path,
    *,
    display_path: Path | None = None,
    folder: str | None = None,
    vault: "VaultIndex | None" = None,
) -> LoadedDocument:
    """Load a local document for the reader.

    ``path`` is read from disk; ``display_path`` (default ``path``) is the lexical
    path relative links and wikilinks resolve against — they differ for a note
    under a symlinked vault folder.
    """
    suffix = path.suffix.lower()
    if suffix not in LOCAL_DOCUMENT_EXTENSIONS:
        raise ViewerError("Supported local documents: HTML, Markdown, text, and PDF.")
    if suffix == ".pdf":
        return _pdf_html(path)
    raw = read_local(path)
    if suffix in {".html", ".htm"}:
        match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
        title = _html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() if match else path.stem
        return LoadedDocument(raw, title or path.stem, "html")
    if suffix in {".md", ".markdown"}:
        block, body = split_frontmatter(raw)
        heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = heading.group(1).strip() if heading else ""
        if not title and block:
            fields = parse_flat_frontmatter(block) or {}
            front_title = fields.get("title")
            title = front_title.strip() if isinstance(front_title, str) else ""
        title = title or path.stem
        ctx = RenderContext(doc_path=display_path or path, folder=folder, vault=vault)
        return LoadedDocument(_markdown_html(raw, title, ctx=ctx), title, "markdown")
    title = path.stem
    body = f"<h1>{_html.escape(title)}</h1><pre>{_html.escape(raw)}</pre>"
    return LoadedDocument(_reading_shell(title, body, kind="text"), title, "text")


def _rewrite_asset(
    ref: str, *, remote: bool, base: str, doc_dir: Path | None, fs_prefix: str, sink: set[str]
) -> str:
    ref = ref.strip()
    if not ref or ref.lower().startswith(_SKIP_PREFIXES):
        return ref
    if remote:
        return urllib.parse.urljoin(base, ref)
    # Local: resolve against the document's on-disk directory, register the exact
    # file in `sink` (the /_fs allowlist), and rewrite to a /_fs URL.
    assert doc_dir is not None
    target = (doc_dir / urllib.parse.unquote(ref)).resolve()
    sink.add(str(target))
    return fs_prefix + urllib.parse.quote(str(target))


def prepare_html(
    src: str,
    *,
    html_text: str,
    server_origin: str,
    folder: str | None,
    asset_token: str | None = None,
    allow_document_scripts: bool = False,
) -> tuple[str, set[str]]:
    """Rewrite ``html_text`` so the widget can run against it under ``server_origin``.

    Returns ``(html, local_assets)`` — ``local_assets`` is the exact set of on-disk
    files this (local) document references, to be added to the /_fs allowlist. It is
    empty for remote documents (whose assets stay absolute on the original site).
    """
    remote = is_remote(src)
    fs_prefix = f"{server_origin}/_fs"
    if asset_token:
        fs_prefix += f"/{asset_token}"
    assets: set[str] = set()

    if remote:
        embedded = _EMBEDDED_BASE_HREF_RE.search(html_text)
        base = urllib.parse.urljoin(src, embedded.group(1)) if embedded else src
        doc_dir: Path | None = None
    else:
        base = ""
        doc_dir = Path(src).expanduser().resolve().parent

    def _sub(m: re.Match) -> str:
        # group(1)=prefix incl. opening quote, group(2)=quote char, group(3)=the URL
        rewritten = _rewrite_asset(
            m.group(3), remote=remote, base=base, doc_dir=doc_dir, fs_prefix=fs_prefix, sink=assets
        )
        return m.group(1) + rewritten + m.group(2)

    out = _ASSET_RE.sub(_sub, html_text)

    # Remote and untrusted documents stay inert. The /view route opts trusted local
    # HTML into scripts so buttons, tabs, diagrams, and other authored interactions
    # continue to work. Script assets were already rewritten through the exact-file
    # capability above; remote documents can never opt in.
    if remote or not allow_document_scripts:
        out = _SCRIPT_RE.sub("", out)
    # Drop <base> (so in-page #anchors resolve against the /view URL) and any
    # page-set CSP (we set our own, stricter one as a response header).
    out = _BASE_RE.sub("", out)
    out = _CSP_RE.sub("", out)

    # Seed the widget's folder via a <meta>. The widget reads it on boot.
    seed = ""
    if folder:
        seed = f'<meta name="askw-folder" content="{_html.escape(folder, quote=True)}">'
    # Seed the source path for local docs so the widget can poll /_mtime and
    # live-reload when an external editor (e.g. another agent) rewrites the file.
    # Remote docs have no mtime, so they get no seed and never poll.
    if not remote:
        seed += f'<meta name="askw-src" content="{_html.escape(src, quote=True)}">'
    if asset_token:
        seed += f'<meta name="askw-doc-token" content="{_html.escape(asset_token, quote=True)}">'
    inject = f'{seed}<script src="{server_origin}/ask.js"></script>'

    if _BODY_RE.search(out):
        out = _BODY_RE.sub(inject + "</body>", out, count=1)
    else:
        out += inject
    return out, assets


def resolve_fs_path(path: str, *, allowed: set[str], home: Path) -> tuple[Path, str] | None:
    """Validate a /_fs request. Returns (abspath, content_type) or None if refused.

    /_fs is locked to the **exact files a viewed local document referenced**
    (``allowed``) — it is not a general home-directory reader. A drive-by page
    opened via /view (remote ``src``) registers no local assets, so it cannot pull
    ``~/.claude.json`` etc. The home check + extension allowlist are extra floors.
    """
    try:
        resolved = Path("/" + path.lstrip("/")).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if str(resolved) not in allowed:
        return None
    if not resolved.is_file():
        return None
    ctype = ASSET_CONTENT_TYPES.get(resolved.suffix.lower())
    if ctype is None:
        return None
    try:
        if not (resolved == home or resolved.is_relative_to(home)):
            return None
    except ValueError:
        return None
    return resolved, ctype
