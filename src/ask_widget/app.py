"""FastAPI app: serves the widget and the SSE /ask endpoint.

Security model (all enforced on POST /ask):
  1. Server binds 127.0.0.1 only (see __main__.py).
  2. Host header must be 127.0.0.1:<port> or localhost:<port> — blocks
     DNS-rebinding, where a malicious page resolves its own domain to 127.0.0.1.
  3. Origin allowlist (NOT ``*``): null (file://), http(s)://localhost[:*],
     http(s)://127.0.0.1[:*], plus any origin the user added to the
     ``allowed_origins`` setting (``app://obsidian.md`` by default, for the
     Obsidian plugin). ``*`` stays only on GET /ask.js so the script tag
     loads from any page.
  4. Per-server random token, baked into ask.js at serve time, required in the
     /ask body.
  5. Folder allowlist via AppConfig.resolve_allowed (resolve() + is_relative_to).
  6. Read-only tool locks live in the Claude and Codex runner commands.

Every refusal is an ``event: error`` on a 200 SSE stream (not 403/429) so the
widget's stream reader can parse and display it.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import secrets
import sys
import time
import urllib.parse
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .runner import _sse, stream_answer
from .citations import open_source
from .config import AppConfig
from .diagnostics import build_diagnostics
from .launcher_ui import launcher_page
from .prompts import append_system_for, build_handoff_prompt, build_user_prompt
from .providers import find_claude, find_codex, provider_catalogs, provider_status
from .storage import Storage
from .vault_ui import vault_page
from . import handoff, markdown_theme, vault, viewer
from . import __version__

logger = logging.getLogger("ask_widget.app")

_FROZEN_ROOT = getattr(sys, "_MEIPASS", None)
STATIC_DIR = (
    Path(_FROZEN_ROOT) / "static"
    if _FROZEN_ROOT
    else Path(__file__).resolve().parent.parent.parent / "static"
)
ASK_JS = STATIC_DIR / "ask.js"
TOKEN_PLACEHOLDER = "__ASK_TOKEN__"
PROTOCOL_VERSION = 3

MAX_CONCURRENT = 3
MAX_SELECTION = 4000
MAX_CONTEXT = 4000
MAX_RECENT = 8
# Follow-up conversation history sent back by the widget. Bound both the number
# of turns and each turn's length so a malicious page can't blow up the prompt.
MAX_HISTORY_TURNS = 12
MAX_HISTORY_TEXT = 4000
MAX_ASSET_CAPABILITIES = 32
ASSET_CAPABILITY_TTL = 8 * 60 * 60


def _sanitize_history(raw: object) -> list[dict]:
    """Coerce the request's ``history`` into a bounded list of clean turns."""
    if not isinstance(raw, list):
        return []
    turns: list[dict] = []
    for item in raw[-MAX_HISTORY_TURNS:]:
        if not isinstance(item, dict):
            continue
        role = "assistant" if item.get("role") == "assistant" else "user"
        text = item.get("text")
        if not isinstance(text, str):
            continue
        text = text.strip()[:MAX_HISTORY_TEXT]
        if text:
            turns.append({"role": role, "text": text})
    return turns

_LOCALHOST_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _origin_allowed(origin: str | None, extra: tuple[str, ...] | list[str] = ()) -> bool:
    if origin is None:
        return True  # no Origin header (same-origin / non-browser): nothing to block
    if origin == "null":
        return True  # file://
    if origin in extra:
        return True  # explicitly allowed in Settings (e.g. the Obsidian plugin)
    return bool(_LOCALHOST_ORIGIN.match(origin))


def _host_allowed(host: str, port: int) -> bool:
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def _cors_headers(origin: str | None, extra: tuple[str, ...] | list[str] = ()) -> dict[str, str]:
    """CORS headers to echo for an allowed cross-origin request (empty otherwise)."""
    if origin is None or not _origin_allowed(origin, extra):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def _remember_folder(app: FastAPI, folder: str) -> None:
    recent: list[str] = app.state.recent_folders
    if folder in recent:
        recent.remove(folder)
    recent.insert(0, folder)
    del recent[MAX_RECENT:]


def _runtime_roots(app: FastAPI) -> tuple[Path, ...]:
    roots: list[Path] = []
    for item in app.state.storage.roots():
        try:
            path = Path(item["path"]).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if path.is_dir() and path not in roots:
            roots.append(path)
    return tuple(roots)


def _resolve_folder(app: FastAPI, raw: str | None) -> Path | None:
    config: AppConfig = app.state.config
    value = raw or str(config.default_folder)
    if config.allow_any:
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        return path if path.is_dir() else None
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not path.is_dir():
        return None
    for root in _runtime_roots(app):
        try:
            if path == root or path.is_relative_to(root):
                return path
        except ValueError:
            continue
    return None


def _vault_root(app: FastAPI, kind: str = "notes") -> Path | None:
    """A configured vault folder (lexical, normalized) or None when unset/missing.

    ``kind`` is "notes" (the Obsidian vault) or "html" (the HTML vault).
    """
    config: AppConfig = app.state.config
    key = "html_vault_root" if kind == "html" else "vault_root"
    raw = str(app.state.storage.settings(model_default=config.model).get(key) or "").strip()
    if not raw:
        return None
    root = vault.normalize(Path(raw).expanduser())
    try:
        return root if root.is_absolute() and root.is_dir() else None
    except OSError:
        return None


def _vault_missing_error(app: FastAPI, kind: str) -> str:
    if kind != "html":
        return "No vault folder is configured."
    config: AppConfig = app.state.config
    raw = str(app.state.storage.settings(model_default=config.model).get("html_vault_root") or "").strip()
    if not raw:
        return "No HTML vault folder is configured."
    return f"The HTML vault folder does not exist yet: {raw}"


def _register_context_root(app: FastAPI, folder: Path) -> Path | None:
    """Allow ``folder`` as a context root — unless it is the whole disk or home.

    Linking a page into the HTML vault is the user saying "I want to read this
    with Ask", and its folder is where the evidence lives, so it joins the
    allowed roots (visible and removable in Settings). Home and ``/`` would
    quietly open everything, so a link from there keeps its default context.
    """
    try:
        resolved = folder.resolve()
    except (OSError, RuntimeError):
        return None
    if not resolved.is_dir() or resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        return None
    app.state.storage.add_root(resolved)
    return resolved


def _register_vault_root(app: FastAPI) -> None:
    # The vault is browsed through /view with folder=<vault>, so it must also be
    # an allowed context root or the reader's folder seed silently falls back.
    root = _vault_root(app)
    if root is not None:
        app.state.storage.add_root(root)


def _decode_sse(chunk: str) -> tuple[str | None, dict]:
    event = None
    payload: dict = {}
    for line in chunk.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            try:
                decoded = json.loads(line[6:])
                payload = decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError:
                payload = {}
    return event, payload


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _error_page(message: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><title>Onyx — can't open</title>"
        "<body style='font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;"
        "margin:80px auto;padding:0 24px;color:#1c1917'>"
        "<h1 style='color:#c2410c'>Couldn't open that document</h1>"
        f"<p>{_esc(message)}</p>"
        "<p><a href='/' style='color:#c2410c'>&larr; back to the launcher</a></p>"
    )


def create_app(config: AppConfig) -> FastAPI:
    storage = Storage(config.data_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        storage.close()

    app = FastAPI(title="Onyx", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.storage = storage
    app.state.storage.sync_builtin_roots(config.allowed_roots)
    app.state.vault = vault.VaultCache(ttl=5.0)
    _register_vault_root(app)
    app.state.sem = asyncio.Semaphore(MAX_CONCURRENT)
    app.state.recent_folders = [str(config.default_folder)]
    # Per-document expiring capabilities replace the old process-wide asset set.
    app.state.asset_caps = OrderedDict()

    def allowed_origins() -> list[str]:
        raw = storage.settings(model_default=config.model).get("allowed_origins")
        return [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []

    def origin_ok(origin: str | None) -> bool:
        return _origin_allowed(origin, allowed_origins())

    def cors(origin: str | None) -> dict[str, str]:
        return _cors_headers(origin, allowed_origins())

    def err_stream(message: str, origin: str | None) -> StreamingResponse:
        async def gen():
            yield _sse("error", {"message": message})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={**cors(origin), **_SSE_HEADERS},
        )

    @app.get("/health")
    async def health(request: Request):
        # The native launcher validates this identity instead of assuming that
        # any process returning HTTP 200 on port 8899 is safe to embed.
        return JSONResponse(
            {
                "status": "ok",
                "service": "onyx",
                "version": __version__,
                "protocol": PROTOCOL_VERSION,
                "runtime": f"python-{sys.version_info.major}.{sys.version_info.minor}",
                "claude_available": find_claude() is not None,
                "codex_available": find_codex() is not None,
            },
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/ask.js")
    async def ask_js():
        try:
            text = ASK_JS.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Response("// ask.js missing", status_code=500, media_type="application/javascript")
        text = text.replace(TOKEN_PLACEHOLDER, config.token)
        return Response(
            content=text,
            media_type="application/javascript",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
        )

    @app.get("/config")
    async def get_config(request: Request):
        origin = request.headers.get("origin")
        settings = app.state.storage.settings(model_default=config.model)
        body = {
            "default_folder": str(config.default_folder),
            "allowed_roots": ["(any)"] if config.allow_any else [str(r) for r in _runtime_roots(app)],
            "recent_folders": list(app.state.recent_folders),
            "model": settings["model"],
            "provider": settings["provider"],
            "reasoning_effort": settings["reasoning_effort"],
            "version": __version__,
            "cache_ttl_hours": settings["cache_ttl_hours"],
            "cache_max_entries": settings["cache_max_entries"],
            "appearance_theme": settings["appearance_theme"],
        }
        return JSONResponse(body, headers=cors(origin))

    @app.get("/", response_class=HTMLResponse)
    async def launcher():
        settings = app.state.storage.settings(model_default=config.model)
        return HTMLResponse(launcher_page(config, settings))

    @app.get("/quick", response_class=HTMLResponse)
    async def quick_read(request: Request, text: str, folder: str | None = None):
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return HTMLResponse(_error_page("Refused: host not allowed."), status_code=403)
        passage = text.strip()[:20_000]
        if not passage:
            return HTMLResponse(_error_page("No selected text was provided."), status_code=400)
        origin = str(request.base_url).rstrip("/")
        seed_folder = _resolve_folder(app, folder) if folder else config.default_folder
        if seed_folder is None:
            return HTMLResponse(_error_page("The saved context folder is no longer allowed."), status_code=400)
        source = f"service://selection/{uuid.uuid4().hex[:12]}"
        body = (
            "<h1>Shared selection</h1><p>Select the passage or right-click it to ask.</p>"
            f'<blockquote id="askw-quick-selection">{_esc(passage)}</blockquote>'
        )
        html_text = viewer._reading_shell("Shared selection", body, kind="selection")
        seed = (
            f'<meta name="askw-folder" content="{_esc(str(seed_folder))}">'
            f'<meta name="askw-src" content="{_esc(source)}">'
            '<meta name="askw-auto-selection" content="1">'
            f'<script src="{origin}/ask.js"></script>'
        )
        html_text = html_text.replace("</body>", seed + "</body>")
        app.state.storage.upsert_document(
            source=source,
            title="Shared selection",
            kind="selection",
            folder=str(seed_folder),
        )
        return HTMLResponse(
            html_text,
            headers={
                "Content-Security-Policy": "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'"
            },
        )

    @app.get("/view", response_class=HTMLResponse)
    async def view(request: Request, src: str, folder: str | None = None):
        # Host check (also makes request.base_url safe to interpolate, and blocks
        # DNS-rebinding to this route).
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return HTMLResponse(_error_page("Refused: host not allowed."), status_code=403)
        # Same-origin as the page the user navigated to (localhost vs 127.0.0.1
        # must match, or the injected ask.js would be cross-origin).
        origin = str(request.base_url).rstrip("/")
        settings = app.state.storage.settings(model_default=config.model)
        seed_path = _resolve_folder(app, folder) if folder else config.default_folder
        seed = str(seed_path) if seed_path else None
        try:
            if viewer.is_remote(src):
                html_text = await asyncio.to_thread(
                    viewer.fetch_remote,
                    src,
                    allow_private=bool(settings["allow_private_remote"]),
                )
                doc_src = src
                title_match = re.search(
                    r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL
                )
                title = (
                    html_lib.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
                    if title_match
                    else src
                )
                kind = "remote-html"
                page_count = None
            else:
                raw = src.strip()
                if raw.lower().startswith("file://"):
                    # file:///Users/...  or  file://localhost/Users/...  → /Users/...
                    raw = urllib.parse.unquote(urllib.parse.urlparse(raw).path)
                candidate = Path(raw).expanduser()
                if not candidate.is_absolute():
                    return HTMLResponse(
                        _error_page(
                            "Please paste an absolute path (starting with /) or a file:// URL — "
                            "or use “Choose file…”."
                        ),
                        status_code=400,
                    )
                # ``lexical`` is the path as the user sees it (a note under a
                # symlinked vault folder stays vault-visible); ``path`` is the
                # realpath used for history, positions, and live reload.
                lexical = vault.normalize(candidate)
                path = candidate.resolve()
                html_root = _vault_root(app, "html")
                if not folder and html_root is not None and vault.is_inside(lexical, html_root):
                    # An HTML-vault page reads with the real folder behind its
                    # link as context; the vault itself holds only symlinks.
                    context = vault.html_context_folder(lexical, html_root)
                    context_path = _resolve_folder(app, str(context)) if context else None
                    if context_path is not None:
                        seed = str(context_path)
                root = _vault_root(app)
                index = None
                if root is not None and vault.is_inside(lexical, root):
                    index = await asyncio.to_thread(app.state.vault.get, root)
                loaded = await asyncio.to_thread(
                    viewer.load_local_document,
                    path,
                    display_path=lexical,
                    folder=seed,
                    vault=index,
                )
                html_text = loaded.html
                if loaded.kind == "markdown":
                    css = current_markdown_theme()["css"]
                    html_text = html_text.replace(
                        "</head>", f'<style id="askw-markdown-theme">{css}</style></head>', 1
                    )
                doc_src = str(path)
                title = loaded.title
                kind = loaded.kind
                page_count = loaded.page_count
        except viewer.ViewerError as exc:
            return HTMLResponse(_error_page(str(exc)), status_code=400)
        capability = secrets.token_urlsafe(18)
        interactive_local_html = kind == "html" and not viewer.is_remote(doc_src)
        out, assets = viewer.prepare_html(
            doc_src,
            html_text=html_text,
            server_origin=origin,
            folder=seed,
            asset_token=capability,
            allow_document_scripts=interactive_local_html,
        )
        caps: OrderedDict = app.state.asset_caps
        caps[capability] = {
            "assets": assets,
            "source": doc_src,
            "expires": time.time() + ASSET_CAPABILITY_TTL,
        }
        caps.move_to_end(capability)
        while len(caps) > MAX_ASSET_CAPABILITIES:
            caps.popitem(last=False)
        app.state.storage.upsert_document(
            source=doc_src,
            title=title,
            kind=kind,
            folder=seed,
            page_count=page_count,
        )
        # Trusted local HTML retains its authored scripts and inline button handlers.
        # Remote HTML remains inert. In both cases, scripts cannot connect away from
        # the local service, embed plugins, or change the document base URL.
        script_src = (
            "script-src 'self' 'unsafe-inline'"
            if interactive_local_html
            else "script-src 'self'"
        )
        csp = f"{script_src}; connect-src 'self'; object-src 'none'; base-uri 'none'"
        return HTMLResponse(out, headers={"Content-Security-Policy": csp})

    @app.get("/vault", response_class=HTMLResponse)
    async def vault_view(
        request: Request,
        src: str | None = None,
        history: str | None = None,
        history_action: str | None = None,
        vault_kind: str = Query("notes", alias="vault"),
    ):
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return HTMLResponse(_error_page("Refused: host not allowed."), status_code=403)
        kind = "html" if vault_kind == "html" else "notes"
        settings = app.state.storage.settings(model_default=config.model)
        root = _vault_root(app, kind)
        reader_query = None
        if src and root is not None:
            # The Obsidian vault is its own context; an HTML-vault page gets the
            # real folder behind its link, which /view works out from the path.
            params = {"src": src} if kind == "html" else {"src": src, "folder": str(root)}
            if history:
                params["history"] = history
            if history_action:
                params["history_action"] = history_action
            reader_query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="/")
        return HTMLResponse(
            vault_page(
                config,
                settings,
                root=root,
                src=src if root is not None else None,
                reader_query=reader_query,
                kind=kind,
            )
        )

    @app.get("/_fs/{capability}/{path:path}")
    async def fs_asset(request: Request, capability: str, path: str):
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return Response("Forbidden", status_code=403)
        cap = app.state.asset_caps.get(capability)
        if not cap or cap["expires"] < time.time():
            app.state.asset_caps.pop(capability, None)
            return Response("Expired", status_code=404)
        result = viewer.resolve_fs_path(path, allowed=cap["assets"], home=Path.home())
        if result is None:
            return Response("Not found", status_code=404)
        abspath, ctype = result
        headers = {"Cache-Control": "no-cache", "Accept-Ranges": "bytes"}
        try:
            size = (await asyncio.to_thread(abspath.stat)).st_size
            span = viewer.byte_range(request.headers.get("range"), size)
        except viewer.RangeNotSatisfiable:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        except OSError:
            return Response("Not found", status_code=404)
        if span is None:
            data = await asyncio.to_thread(abspath.read_bytes)
            return Response(content=data, media_type=ctype, headers=headers)
        start, end = span

        def read_span() -> bytes:
            with open(abspath, "rb") as handle:
                handle.seek(start)
                return handle.read(end - start + 1)

        data = await asyncio.to_thread(read_span)
        headers["Content-Range"] = f"bytes {start}-{start + len(data) - 1}/{size}"
        return Response(content=data, status_code=206, media_type=ctype, headers=headers)

    @app.get("/_mtime")
    async def doc_mtime(request: Request, src: str, cap: str):
        # Cheap change-detection for live reload: returns a stat signature the
        # /view page polls. Host-checked like /_fs; returns strictly less than
        # /view already does (which serves the file's full contents). Live reload
        # is local-only — remote URLs have no mtime.
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return Response("Forbidden", status_code=403)
        capability = app.state.asset_caps.get(cap)
        if (
            not capability
            or capability["expires"] < time.time()
            or capability["source"] != src
        ):
            return JSONResponse({"ok": False})
        if viewer.is_remote(src):
            return JSONResponse({"ok": False})
        raw = src.strip()
        if raw.lower().startswith("file://"):
            raw = urllib.parse.unquote(urllib.parse.urlparse(raw).path)
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return JSONResponse({"ok": False})
        if path.suffix.lower() not in viewer.LOCAL_DOCUMENT_EXTENSIONS:
            return JSONResponse({"ok": False})
        try:
            st = await asyncio.to_thread(path.stat)
        except (OSError, ValueError):
            return JSONResponse({"ok": False})
        return JSONResponse(
            {"ok": True, "sig": f"{st.st_mtime_ns}:{st.st_size}"},
            headers={"Cache-Control": "no-store"},
        )

    # MARK: - Library, settings, and diagnostics APIs

    def api_forbidden(request: Request) -> JSONResponse | None:
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return JSONResponse({"ok": False, "error": "host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if not origin_ok(origin):
            return JSONResponse({"ok": False, "error": "origin not allowed"}, status_code=403)
        return None

    @app.get("/api/library")
    async def library(
        request: Request,
        q: str = "",
        provider: str = "",
        model: str = "",
        source: str = "",
        action: str = "",
        days: int = 0,
    ):
        if denied := api_forbidden(request):
            return denied
        storage: Storage = app.state.storage
        since = time.time() - min(max(days, 0), 3650) * 86_400 if days else None
        data = storage.search(
            q[:200],
            limit=100,
            provider=provider if provider in {"claude", "codex"} else None,
            model=model[:100] or None,
            source=source[:4000] or None,
            action=action if action in {"ask", "eli5", "prove"} else None,
            since=since,
        )
        return JSONResponse({"ok": True, **data}, headers=cors(request.headers.get("origin")))

    @app.get("/api/history")
    async def history(request: Request, source: str, selection: str = "", limit: int = 20):
        if denied := api_forbidden(request):
            return denied
        items = app.state.storage.recent_conversations(
            limit=limit, source=source[:4000], selection=selection[:MAX_SELECTION] or None
        )
        return JSONResponse(
            {"ok": True, "conversations": items, "document": app.state.storage.document(source)},
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/api/conversations/{request_id}")
    async def conversation_api(request: Request, request_id: str):
        if denied := api_forbidden(request):
            return denied
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            return JSONResponse({"ok": False, "error": "invalid request id"}, status_code=400)
        item = app.state.storage.conversation(request_id)
        if item is None:
            return JSONResponse({"ok": False, "error": "history entry not found"}, status_code=404)
        return JSONResponse(
            {"ok": True, "conversation": item},
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/api/document")
    async def document_api(request: Request, source: str):
        if denied := api_forbidden(request):
            return denied
        return JSONResponse(
            {"ok": True, "document": app.state.storage.document(source[:4000])},
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/api/folder")
    async def validate_folder_api(request: Request, path: str):
        if denied := api_forbidden(request):
            return denied
        resolved = _resolve_folder(app, path)
        if resolved is None:
            return JSONResponse(
                {"ok": False, "error": "Folder is missing or outside the allowed roots."},
                status_code=400,
                headers=cors(request.headers.get("origin")),
            )
        return JSONResponse(
            {"ok": True, "path": str(resolved)},
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/api/vault/tree")
    async def vault_tree_api(request: Request, vault_kind: str = Query("notes", alias="vault")):
        if denied := api_forbidden(request):
            return denied
        headers = cors(request.headers.get("origin"))
        kind = "html" if vault_kind == "html" else "notes"
        root = _vault_root(app, kind)
        if root is None:
            return JSONResponse(
                {"ok": False, "error": _vault_missing_error(app, kind)}, status_code=400, headers=headers
            )
        index = await asyncio.to_thread(app.state.vault.get, root, kind)
        return JSONResponse(
            {
                "ok": True,
                "root": str(root),
                "vault": kind,
                "built_at": index.built_at,
                "files": len([item for item in index.notes if not item.missing]),
                "missing": len([item for item in index.notes if item.missing]),
                "truncated": index.truncated,
                "tree": index.tree_json(),
            },
            headers=headers,
        )

    @app.get("/api/vault/search")
    async def vault_search_api(
        request: Request, q: str = "", limit: int = 50, vault_kind: str = Query("notes", alias="vault")
    ):
        if denied := api_forbidden(request):
            return denied
        headers = cors(request.headers.get("origin"))
        kind = "html" if vault_kind == "html" else "notes"
        root = _vault_root(app, kind)
        if root is None:
            return JSONResponse(
                {"ok": False, "error": _vault_missing_error(app, kind)}, status_code=400, headers=headers
            )
        q = q[:200]
        limit = max(1, min(limit, 200))
        index = await asyncio.to_thread(app.state.vault.get, root, kind)
        items, truncated = index.search(q, limit=limit)
        return JSONResponse(
            {
                "ok": True,
                "q": q,
                "items": [
                    {
                        "name": item.name,
                        "title": item.label if kind == "html" else "",
                        "path": str(item.path),
                        "folder": item.entry_rel.rpartition("/")[0] if kind == "html" else item.folder,
                    }
                    for item in items
                ],
                "truncated": truncated,
            },
            headers=headers,
        )

    async def _html_vault_body(request: Request) -> tuple[dict, Path] | JSONResponse:
        """Shared guard for the two HTML-vault write routes: token, JSON, root."""
        if denied := api_forbidden(request):
            return denied
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "invalid JSON object"}, status_code=400)
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        root = _vault_root(app, "html")
        if root is None:
            return JSONResponse({"ok": False, "error": _vault_missing_error(app, "html")}, status_code=400)
        return body, root

    @app.post("/api/vault/html/folder")
    async def html_vault_folder_api(request: Request):
        guarded = await _html_vault_body(request)
        if isinstance(guarded, JSONResponse):
            return guarded
        body, root = guarded
        try:
            folder = vault.create_folder(root, str(body.get("parent") or ""), str(body.get("name") or ""))
        except (OSError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        app.state.vault.invalidate()
        return JSONResponse(
            {"ok": True, "path": str(folder), "rel": folder.relative_to(root).as_posix()},
            headers=cors(request.headers.get("origin")),
        )

    @app.post("/api/vault/html/link")
    async def html_vault_link_api(request: Request):
        guarded = await _html_vault_body(request)
        if isinstance(guarded, JSONResponse):
            return guarded
        body, root = guarded
        targets = body.get("targets")
        if not isinstance(targets, list):
            targets = [body.get("target")]
        if not targets or len(targets) > 50 or not all(isinstance(t, str) and t for t in targets):
            return JSONResponse({"ok": False, "error": "Choose one to fifty HTML files or folders."}, status_code=400)
        name = body.get("name") if len(targets) == 1 and isinstance(body.get("name"), str) else None
        linked: list[str] = []
        errors: list[str] = []
        context_roots: list[str] = []
        for target in targets:
            try:
                link = vault.create_link(root, str(body.get("parent") or ""), target, name or None)
            except (OSError, ValueError) as exc:
                errors.append(f"{Path(target).name}: {exc}")
                continue
            linked.append(str(link))
            context = vault.html_context_folder(link, root)
            added = _register_context_root(app, context) if context else None
            if added is not None and str(added) not in context_roots:
                context_roots.append(str(added))
        app.state.vault.invalidate()
        status = 200 if linked else 400
        return JSONResponse(
            {
                "ok": bool(linked),
                "linked": linked,
                "context_roots": context_roots,
                "errors": errors,
                "error": "; ".join(errors) if errors and not linked else None,
            },
            status_code=status,
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/api/settings")
    async def settings_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        storage: Storage = app.state.storage
        return JSONResponse(
            {
                "ok": True,
                "settings": storage.settings(model_default=config.model),
                "roots": storage.roots(),
            },
            headers=cors(request.headers.get("origin")),
        )

    @app.get("/api/session")
    async def session_api(request: Request):
        """Hand a trusted client the request token and the current runtime shape.

        This is no weaker than GET /ask.js, which already serves the same token
        to any origin (``Access-Control-Allow-Origin: *``) so the widget script
        tag works from a file:// page. Tightening /ask.js to the allowlist is a
        separate change; this route is gated on host + origin like the rest of
        the JSON API.
        """
        if denied := api_forbidden(request):
            return denied
        settings = app.state.storage.settings(model_default=config.model)
        return JSONResponse(
            {
                "ok": True,
                "service": "onyx",
                "protocol": PROTOCOL_VERSION,
                "version": __version__,
                "token": config.token,
                "provider": settings["provider"],
                "model": settings["model"],
                "reasoning_effort": settings["reasoning_effort"],
                "first_activity_timeout": settings["first_activity_timeout"],
                "request_timeout": settings["request_timeout"],
                "cache_ttl_hours": settings["cache_ttl_hours"],
                "cache_max_entries": settings["cache_max_entries"],
            },
            headers={**cors(request.headers.get("origin")), "Cache-Control": "no-store"},
        )

    @app.get("/api/models")
    async def models_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        settings = app.state.storage.settings(model_default=config.model)
        catalogs = await asyncio.to_thread(provider_catalogs)
        for catalog in catalogs:
            provider = str(catalog.get("id"))
            selected = str(settings.get(f"{provider}_model") or "")
            models = catalog.get("models") if isinstance(catalog.get("models"), list) else []
            if selected and not any(item.get("id") == selected for item in models):
                models.append(
                    {
                        "id": selected,
                        "label": f"{selected} (saved; unavailable)",
                        "description": "This saved model is not in the installed CLI's current catalog.",
                        "efforts": [],
                        "default_effort": settings.get(f"{provider}_effort", "medium"),
                        "unavailable": True,
                    }
                )
            catalog["models"] = models
            catalog["selected_model"] = selected
            catalog["selected_effort"] = settings.get(f"{provider}_effort", "medium")
        return JSONResponse(
            {"ok": True, "selected_provider": settings["provider"], "providers": catalogs},
            headers=cors(request.headers.get("origin")),
        )

    def current_markdown_theme() -> dict:
        settings = app.state.storage.settings(model_default=config.model)
        root = _vault_root(app)
        snapshot = app.state.storage.markdown_theme(root) if root else None
        enabled = bool(settings.get("markdown_follow_obsidian", True))
        css = markdown_theme.stylesheet(snapshot) if enabled else ""
        return {"ok": True, "enabled": enabled, "available": snapshot is not None,
                "css": css, "revision": markdown_theme.revision(css)}

    @app.get("/api/markdown-theme")
    async def markdown_theme_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        return JSONResponse(current_markdown_theme(), headers={
            **cors(request.headers.get("origin")), "Cache-Control": "no-store",
        })

    @app.post("/api/markdown-theme")
    async def sync_markdown_theme_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        # Bound the body before decoding JSON, including chunked requests.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > markdown_theme.MAX_SNAPSHOT_BYTES + 8192:
                return JSONResponse({"ok": False, "error": "Reading theme is too large."}, status_code=413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "invalid JSON object"}, status_code=400)
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        try:
            raw_root = body.get("vault_root")
            if not isinstance(raw_root, str) or len(raw_root) > 4096:
                raise ValueError("Invalid vault folder.")
            root = Path(raw_root).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise ValueError("Vault folder does not exist.")
            snapshot = markdown_theme.validate_snapshot(body.get("snapshot"))
        except (OSError, ValueError, RuntimeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        app.state.storage.save_markdown_theme(root, snapshot)
        return JSONResponse({"ok": True}, headers=cors(request.headers.get("origin")))

    @app.post("/api/settings")
    async def update_settings_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        patch = body.get("settings") if isinstance(body.get("settings"), dict) else {}
        if any(
            key in patch
            for key in ("provider", "model", "claude_model", "codex_model", "claude_effort", "codex_effort")
        ):
            current = app.state.storage.settings(model_default=config.model)
            provider = str(patch.get("provider") or current["provider"])
            model = str(
                patch.get(f"{provider}_model")
                or patch.get("model")
                or current.get(f"{provider}_model")
                or ""
            )
            effort = str(patch.get(f"{provider}_effort") or current.get(f"{provider}_effort") or "medium")
            catalogs = await asyncio.to_thread(provider_catalogs)
            catalog = next((item for item in catalogs if item.get("id") == provider), None)
            available = catalog.get("models", []) if catalog else []
            if available and model not in {item.get("id") for item in available}:
                return JSONResponse(
                    {"ok": False, "error": f"{model!r} is not available for {provider}. Refresh the model catalog."},
                    status_code=400,
                )
            selected = next((item for item in available if item.get("id") == model), None)
            supported_efforts = selected.get("efforts", []) if selected else []
            if supported_efforts and effort not in supported_efforts:
                return JSONResponse(
                    {"ok": False, "error": f"{effort!r} effort is not supported by {model}."},
                    status_code=400,
                )
        try:
            settings = app.state.storage.update_settings(
                patch,
                model_default=config.model,
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if "vault_root" in patch and settings.get("vault_root"):
            app.state.storage.add_root(Path(str(settings["vault_root"])))
        app.state.vault.invalidate()
        return {"ok": True, "settings": settings}

    @app.post("/api/roots")
    async def add_root_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        body = await request.json()
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        try:
            path = Path(str(body.get("path") or "")).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return JSONResponse({"ok": False, "error": "invalid folder"}, status_code=400)
        if not path.is_dir():
            return JSONResponse({"ok": False, "error": "folder does not exist"}, status_code=400)
        app.state.storage.add_root(path)
        return JSONResponse(
            {"ok": True, "roots": app.state.storage.roots()},
            headers=cors(request.headers.get("origin")),
        )

    @app.delete("/api/roots")
    async def remove_root_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        body = await request.json()
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        try:
            path = Path(str(body.get("path") or "")).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return JSONResponse({"ok": False, "error": "invalid folder"}, status_code=400)
        if not app.state.storage.remove_root(path):
            return JSONResponse({"ok": False, "error": "built-in roots cannot be removed"}, status_code=400)
        return {"ok": True, "roots": app.state.storage.roots()}

    @app.get("/api/diagnostics")
    async def diagnostics_api(request: Request, probe: bool = False):
        if denied := api_forbidden(request):
            return denied
        settings = app.state.storage.settings(model_default=config.model)
        result = await asyncio.to_thread(
            build_diagnostics,
            app.state.storage,
            provider=settings["provider"],
            model=settings["model"],
            effort=settings["reasoning_effort"],
            roots=app.state.storage.roots(),
            default_folder=config.default_folder,
            probe=probe,
        )
        return JSONResponse(result)

    @app.post("/api/position")
    async def update_position_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        body = await request.json()
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        source = str(body.get("source") or "")[:4000]
        if not app.state.storage.document(source):
            return JSONResponse({"ok": False, "error": "unknown document"}, status_code=404)
        app.state.storage.update_position(source, float(body.get("scroll_y") or 0))
        return {"ok": True}

    @app.get("/api/export")
    async def export_api(request: Request, src: str):
        if denied := api_forbidden(request):
            return denied
        try:
            markdown = app.state.storage.export_markdown(src)
        except KeyError:
            return Response("Document not found", status_code=404)
        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(src).stem or "reading-notes")
        return Response(
            markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}-notes.md"'},
        )

    @app.post("/api/open-source")
    async def open_source_api(request: Request):
        if denied := api_forbidden(request):
            return denied
        body = await request.json()
        if body.get("token") != config.token:
            return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
        try:
            path = Path(str(body.get("path") or "")).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return JSONResponse({"ok": False, "error": "invalid source path"}, status_code=400)
        folder = _resolve_folder(app, body.get("folder"))
        inside_folder = bool(folder and (path == folder or path.is_relative_to(folder)))
        known_document = app.state.storage.document(str(path)) is not None
        if not inside_folder and not known_document:
            return JSONResponse({"ok": False, "error": "source is outside the active context"}, status_code=403)
        try:
            await asyncio.to_thread(
                open_source,
                path,
                line=int(body["line"]) if body.get("line") else None,
                page=int(body["page"]) if body.get("page") else None,
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True}, headers=cors(request.headers.get("origin")))

    @app.options("/ask")
    async def ask_preflight(request: Request):
        return Response(status_code=204, headers=cors(request.headers.get("origin")))

    @app.options("/api/{path:path}")
    async def api_preflight(request: Request, path: str):
        return Response(status_code=204, headers=cors(request.headers.get("origin")))

    @app.options("/open-in-provider")
    @app.options("/open-in-claude")
    async def open_in_provider_preflight(request: Request):
        return Response(status_code=204, headers=cors(request.headers.get("origin")))

    @app.post("/open-in-provider")
    @app.post("/open-in-claude")
    async def open_in_provider(request: Request):
        # Same gating as /ask — this spawns a terminal agent session, a real
        # side effect. All app-level outcomes return 200 + {ok,...} so the widget
        # (cross-origin or same-origin) can read them.
        origin = request.headers.get("origin")
        headers = cors(origin)

        def reply(payload, status=200):
            return JSONResponse(payload, status_code=status, headers=headers)

        if not origin_ok(origin):
            return reply({"ok": False, "error": "origin not allowed"})
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return reply({"ok": False, "error": "host not allowed"})
        try:
            body = await request.json()
        except Exception:
            return reply({"ok": False, "error": "invalid JSON"})
        if body.get("token") != config.token:
            return reply({"ok": False, "error": "invalid token"})

        settings = app.state.storage.settings(model_default=config.model)
        provider = str(body.get("provider") or settings["provider"])
        if provider not in {"claude", "codex"}:
            return reply({"ok": False, "error": "unknown provider"})
        runtime = await asyncio.to_thread(provider_status, provider)
        if not runtime.get("subscription"):
            return reply({"ok": False, "error": runtime.get("repair") or "subscription login required"})

        folder = _resolve_folder(app, body.get("folder"))
        if folder is None:
            return reply({"ok": False, "error": f"folder not allowed: {body.get('folder')!r}"})

        prompt = build_handoff_prompt(
            folder,
            body.get("action"),
            body.get("selection") or "",
            body.get("question") or "",
            body.get("answer") or "",
        )
        if body.get("mode") == "copy":
            return reply({"ok": True, "prompt": prompt})

        try:
            await asyncio.to_thread(handoff.open_in_provider, provider, folder, prompt)
        except Exception as exc:
            return reply({"ok": False, "error": str(exc), "prompt": prompt})
        return reply({"ok": True, "opened": True, "prompt": prompt})

    @app.post("/ask")
    async def ask(request: Request):
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")

        if not origin_ok(origin):
            return err_stream("Refused: origin not allowed.", origin)
        if not _host_allowed(host, config.port):
            return err_stream("Refused: host not allowed (possible DNS-rebinding).", origin)

        try:
            body = await request.json()
        except Exception:
            return err_stream("Invalid JSON body.", origin)

        if body.get("token") != config.token:
            return err_stream("Refused: invalid or missing token.", origin)

        action = body.get("action")
        if action not in ("eli5", "prove", "ask"):
            return err_stream("Unknown action.", origin)

        selection = (body.get("selection") or "").strip()
        if not selection:
            return err_stream("No text was selected.", origin)
        selection = selection[:MAX_SELECTION]
        context = (body.get("context") or "")[:MAX_CONTEXT]

        question = (body.get("question") or "").strip()
        if action == "ask" and not question:
            return err_stream("No question was provided.", origin)

        request_mode = str(body.get("request_mode") or "generated")
        if request_mode not in {"generated", "rerun", "edited", "continue"}:
            request_mode = "generated"
        parent_request_id = str(body.get("parent_request_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parent_request_id):
            parent_request_id = ""

        folder = _resolve_folder(app, body.get("folder"))
        if folder is None:
            return err_stream(
                f"Refused: folder not allowed or not a directory: {body.get('folder')!r}", origin
            )
        _remember_folder(app, str(folder))

        history = _sanitize_history(body.get("history"))
        document_source = str(body.get("document_source") or "")[:4000] or None
        document_title = str(body.get("document_title") or "")[:500] or None
        try:
            document_page = int(body.get("document_page")) if body.get("document_page") else None
        except (TypeError, ValueError):
            document_page = None
        settings = app.state.storage.settings(model_default=config.model)
        provider = settings["provider"]
        model = settings["model"]
        effort = settings["reasoning_effort"]
        prompt = build_user_prompt(
            action,
            selection,
            context,
            question,
            history,
            document_source=document_source,
            document_page=document_page,
        )
        append_system = append_system_for(action, settings["response_style"])
        sem: asyncio.Semaphore = app.state.sem
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        document_id = None
        if document_source:
            existing = app.state.storage.document(document_source)
            if existing:
                document_id = existing["id"]
            else:
                kind = "remote" if viewer.is_remote(document_source) else Path(document_source).suffix.lstrip(".") or "web"
                document_id = app.state.storage.upsert_document(
                    source=document_source,
                    title=document_title or Path(document_source).name or document_source,
                    kind=kind,
                    folder=str(folder),
                )
        if settings["history_enabled"]:
            app.state.storage.start_conversation(
                request_id=request_id,
                document_id=document_id,
                document_source=document_source,
                document_title=document_title,
                document_page=document_page,
                selection=selection,
                context=context,
                action=action,
                question=question,
                folder=str(folder),
                provider=provider,
                model=model,
                effort=effort,
                request_mode=request_mode,
                parent_request_id=parent_request_id or None,
            )

        async def gen():
            acquired = False
            answer = ""
            error = ""
            citations: list[dict] = []
            trace: list[dict] = []
            status = "error"
            try:
                yield _sse(
                    "meta",
                    {
                        "request_id": request_id,
                        "provider": provider,
                        "model": model,
                        "effort": effort,
                        "request_mode": request_mode,
                        "parent_request_id": parent_request_id or None,
                    },
                )
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=0.05)
                    acquired = True
                except asyncio.TimeoutError:
                    error = "Server busy (too many concurrent requests). Try again in a moment."
                    yield _sse(
                        "error",
                        {"message": error, "retryable": True},
                    )
                    return
                async for chunk in stream_answer(
                    provider,
                    prompt,
                    folder,
                    model,
                    append_system,
                    effort=effort,
                    document_source=document_source,
                    first_activity_timeout=float(settings["first_activity_timeout"]),
                    stream_timeout=float(settings["request_timeout"]),
                ):
                    event, data = _decode_sse(chunk)
                    if event == "token":
                        answer += str(data.get("text") or "")
                    elif event == "tool_trace":
                        trace.append(data)
                    elif event == "citations":
                        citations = data.get("items") if isinstance(data.get("items"), list) else []
                    elif event == "error":
                        error = str(data.get("message") or f"{provider.title()} reported an error.")
                    elif event == "done":
                        status = "complete"
                    yield chunk
            except asyncio.CancelledError:
                status = "cancelled"
                error = "Request cancelled by the reader."
                raise
            except Exception as exc:
                error = f"Unexpected request failure: {exc}"
                yield _sse("error", {"message": error, "retryable": True})
            finally:
                if acquired:
                    sem.release()
                if settings["history_enabled"]:
                    app.state.storage.finish_conversation(
                        request_id,
                        status=status,
                        answer=answer,
                        error=error,
                        citations=citations,
                        trace=trace,
                        latency_ms=int((time.monotonic() - started) * 1000),
                    )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={**cors(origin), **_SSE_HEADERS, "X-Request-ID": request_id},
        )

    return app
