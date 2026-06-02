"""Wrap any HTML (remote URL or local file) so the widget can run against it.

The widget needs three things a foreign page won't give it on its own:
  * to be **same-origin** with the server (so the `fetch` POST to /ask is allowed
    and not blocked as cross-origin or mixed-content);
  * its **assets resolved** (these docs pull a relative `../.assets/dossier.css`,
    which would 404 — and render unstyled — if served naively);
  * the **widget `<script>` injected**.

`prepare_html` does all three: it rewrites `<link>/<script>/<img>/<source>` asset
refs to absolute URLs (remote → the original site; local → a `/_fs/...` route that
serves the file from disk), strips any `<base>`/CSP that would break same-origin
anchors or block the widget, and injects the widget script before `</body>`.
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
import urllib.request
from pathlib import Path

# Match a <link|script|img|source ...> tag's first href/src value. The quote class
# is symmetric (group 2 = opening quote, back-referenced as the closing quote) so
# single-quoted attributes are handled too. These docs are generated and
# well-formed, so a scoped regex beats dragging in an HTML parser that would
# round-trip-mangle the markup.
_ASSET_RE = re.compile(
    r"""(<(?:link|script|img|source)\b[^>]*?\b(?:href|src)=(["']))(.*?)\2""",
    re.IGNORECASE,
)
# Neutralize the viewed document's OWN scripts: re-serving foreign HTML same-origin
# on localhost would otherwise let its inline JS read /_fs and /ask and exfiltrate.
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
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


class ViewerError(Exception):
    """Raised with a user-facing message when a source can't be loaded."""


def is_remote(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


def fetch_remote(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ask-widget/0.1 (+local reading companion)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - user-supplied, loopback tool
            raw = resp.read(MAX_REMOTE_BYTES + 1)
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
    target = (doc_dir / ref).resolve()
    sink.add(str(target))
    return fs_prefix + urllib.parse.quote(str(target))


def prepare_html(
    src: str, *, html_text: str, server_origin: str, folder: str | None
) -> tuple[str, set[str]]:
    """Rewrite ``html_text`` so the widget can run against it under ``server_origin``.

    Returns ``(html, local_assets)`` — ``local_assets`` is the exact set of on-disk
    files this (local) document references, to be added to the /_fs allowlist. It is
    empty for remote documents (whose assets stay absolute on the original site).
    """
    remote = is_remote(src)
    fs_prefix = f"{server_origin}/_fs"
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

    # Neutralize the document's own scripts (after asset rewriting, before we inject
    # ours): a foreign document re-served same-origin must not run its own JS.
    out = _SCRIPT_RE.sub("", out)
    # Drop <base> (so in-page #anchors resolve against the /view URL) and any
    # page-set CSP (we set our own, stricter one as a response header).
    out = _BASE_RE.sub("", out)
    out = _CSP_RE.sub("", out)

    # Seed the widget's folder via a <meta> (not an inline script — those are
    # stripped above and blocked by our CSP). The widget reads it on boot.
    seed = ""
    if folder:
        seed = f'<meta name="askw-folder" content="{_html.escape(folder, quote=True)}">'
    # Seed the source path for local docs so the widget can poll /_mtime and
    # live-reload when an external editor (e.g. another agent) rewrites the file.
    # Remote docs have no mtime, so they get no seed and never poll.
    if not remote:
        seed += f'<meta name="askw-src" content="{_html.escape(src, quote=True)}">'
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
