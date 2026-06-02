"""FastAPI app: serves the widget and the SSE /ask endpoint.

Security model (all enforced on POST /ask):
  1. Server binds 127.0.0.1 only (see __main__.py).
  2. Host header must be 127.0.0.1:<port> or localhost:<port> — blocks
     DNS-rebinding, where a malicious page resolves its own domain to 127.0.0.1.
  3. Origin allowlist (NOT ``*``): null (file://), http(s)://localhost[:*],
     http(s)://127.0.0.1[:*]. ``*`` stays only on GET /ask.js so the script tag
     loads from any page.
  4. Per-server random token, baked into ask.js at serve time, required in the
     /ask body.
  5. Folder allowlist via AppConfig.resolve_allowed (resolve() + is_relative_to).
  6. Read-only tool lock lives in claude_runner.build_cmd.

Every refusal is an ``event: error`` on a 200 SSE stream (not 403/429) so the
widget's stream reader can parse and display it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .claude_runner import _sse, stream_answer
from .config import AppConfig
from .prompts import append_system_for, build_handoff_prompt, build_user_prompt
from . import handoff, viewer

logger = logging.getLogger("ask_widget.app")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
ASK_JS = STATIC_DIR / "ask.js"
TOKEN_PLACEHOLDER = "__ASK_TOKEN__"

MAX_CONCURRENT = 3
MAX_SELECTION = 4000
MAX_CONTEXT = 4000
MAX_RECENT = 8
# Follow-up conversation history sent back by the widget. Bound both the number
# of turns and each turn's length so a malicious page can't blow up the prompt.
MAX_HISTORY_TURNS = 12
MAX_HISTORY_TEXT = 4000


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


def _origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return True  # no Origin header (same-origin / non-browser): nothing to block
    if origin == "null":
        return True  # file://
    return bool(_LOCALHOST_ORIGIN.match(origin))


def _host_allowed(host: str, port: int) -> bool:
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def _cors_headers(origin: str | None) -> dict[str, str]:
    """CORS headers to echo for an allowed cross-origin request (empty otherwise)."""
    if origin is None or not _origin_allowed(origin):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }


def _remember_folder(app: FastAPI, folder: str) -> None:
    recent: list[str] = app.state.recent_folders
    if folder in recent:
        recent.remove(folder)
    recent.insert(0, folder)
    del recent[MAX_RECENT:]


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
        "<!doctype html><meta charset=utf-8><title>Ask Widget — can't open</title>"
        "<body style='font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;"
        "margin:80px auto;padding:0 24px;color:#1c1917'>"
        "<h1 style='color:#c2410c'>Couldn't open that document</h1>"
        f"<p>{_esc(message)}</p>"
        "<p><a href='/' style='color:#c2410c'>&larr; back to the launcher</a></p>"
    )


def _launcher_page(config: AppConfig) -> str:
    default_folder = _esc(str(config.default_folder))
    roots = "(any folder)" if config.allow_any else _esc(", ".join(str(r) for r in config.allowed_roots))
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Ask Widget</title>
<style>
  body{{font:16px/1.6 -apple-system,system-ui,sans-serif;color:#1c1917;background:#fdfcfa;
    max-width:640px;margin:0 auto;padding:56px 24px 80px}}
  h1{{font-size:26px;margin:0 0 6px}} .sub{{color:#57534e;margin:0 0 28px}}
  label{{display:block;font-weight:600;font-size:13px;margin:18px 0 6px}}
  input{{width:100%;padding:11px 13px;border:1px solid #d6d3d1;border-radius:9px;font:inherit}}
  input:focus{{outline:none;border-color:#c2410c}}
  .hint{{color:#78716c;font-size:13px;margin:5px 0 0}}
  button{{margin-top:22px;background:#c2410c;color:#fff;border:none;border-radius:9px;
    padding:12px 22px;font:inherit;font-weight:600;cursor:pointer}}
  button:hover{{background:#9a3412}}
  code{{background:#f5f5f4;padding:1px 6px;border-radius:5px;font-size:13px}}
  .meta{{margin-top:34px;padding-top:18px;border-top:1px solid #e7e5e4;color:#57534e;font-size:13.5px}}
  .row{{display:flex;gap:8px;align-items:stretch}} .row input{{flex:1}}
  .browse{{display:none;margin:0;white-space:nowrap;background:#fff;color:#44403c;
    border:1px solid #d6d3d1;border-radius:9px;padding:0 16px;font-size:13px;font-weight:500}}
  .browse:hover{{background:#f5f5f4}}
  body.native .browse{{display:inline-flex;align-items:center}}
</style></head><body>
<h1>Ask Widget</h1>
<p class=sub>Open any HTML — a URL or a local file — with the highlight-to-ask widget on top of it.</p>
<form onsubmit="go(event)">
  <label for=src>Document URL or local file path</label>
  <div class=row>
    <input id=src autofocus spellcheck=false placeholder="https://… or choose a file →">
    <button type=button class=browse data-pick=file data-target=src>Choose file…</button>
  </div>
  <p class=hint>The page is re-served from this server so the widget runs same-origin (no file://, no mixed content).</p>
  <label for=folder>Context folder for Claude (its CLAUDE.md + files)</label>
  <div class=row>
    <input id=folder spellcheck=false value="{default_folder}">
    <button type=button class=browse data-pick=folder data-target=folder>Choose…</button>
  </div>
  <p class=hint>Must be inside an allowed root: <code>{roots}</code>. You can also change it later from the folder pill.</p>
  <button type=submit>Open with Ask Widget &rarr;</button>
</form>
<div class=meta>
  <p>Prefer to embed manually in a page you control? Add before <code>&lt;/body&gt;</code>:<br>
  <code>&lt;script src="{_esc(config.host)}:{config.port}/ask.js" …&gt;</code> (served at <code>/ask.js</code>).</p>
</div>
<script>
function go(e){{
  e.preventDefault();
  var src=document.getElementById('src').value.trim();
  var folder=document.getElementById('folder').value.trim();
  if(!src) return;
  var u='/view?src='+encodeURIComponent(src);
  if(folder) u+='&folder='+encodeURIComponent(folder);
  location.href=u;
}}
// Native macOS picker — only available inside the Ask Widget app (WKWebView).
var NATIVE = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.askwPick);
if(NATIVE) document.body.classList.add('native');
async function pick(kind, targetId){{
  try{{
    var el=document.getElementById(targetId);
    var p=await window.webkit.messageHandlers.askwPick.postMessage({{kind:kind, initial:el.value}});
    if(p){{ el.value=p; el.focus(); }}
  }}catch(e){{}}
}}
document.querySelectorAll('.browse').forEach(function(b){{
  b.addEventListener('click', function(){{ pick(b.getAttribute('data-pick'), b.getAttribute('data-target')); }});
}});
</script>
</body></html>"""


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="ask-widget", version="0.1.0")
    app.state.config = config
    app.state.sem = asyncio.Semaphore(MAX_CONCURRENT)
    app.state.recent_folders = [str(config.default_folder)]
    # Exact files /_fs may serve — populated as local docs are opened via /view.
    app.state.allowed_assets = set()

    def err_stream(message: str, origin: str | None) -> StreamingResponse:
        async def gen():
            yield _sse("error", {"message": message})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={**_cors_headers(origin), **_SSE_HEADERS},
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

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
        body = {
            "default_folder": str(config.default_folder),
            "allowed_roots": ["(any)"] if config.allow_any else [str(r) for r in config.allowed_roots],
            "recent_folders": list(app.state.recent_folders),
            "model": config.model,
        }
        return JSONResponse(body, headers=_cors_headers(origin))

    @app.get("/", response_class=HTMLResponse)
    async def launcher():
        return HTMLResponse(_launcher_page(config))

    @app.get("/view", response_class=HTMLResponse)
    async def view(request: Request, src: str, folder: str | None = None):
        # Host check (also makes request.base_url safe to interpolate, and blocks
        # DNS-rebinding to this route).
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return HTMLResponse(_error_page("Refused: host not allowed."), status_code=403)
        # Same-origin as the page the user navigated to (localhost vs 127.0.0.1
        # must match, or the injected ask.js would be cross-origin).
        origin = str(request.base_url).rstrip("/")
        try:
            if viewer.is_remote(src):
                html_text = await asyncio.to_thread(viewer.fetch_remote, src)
                doc_src = src
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
                path = candidate.resolve()
                if path.suffix.lower() not in (".html", ".htm"):
                    return HTMLResponse(
                        _error_page("Only .html/.htm files can be opened locally."), status_code=400
                    )
                html_text = await asyncio.to_thread(viewer.read_local, path)
                doc_src = str(path)
        except viewer.ViewerError as exc:
            return HTMLResponse(_error_page(str(exc)), status_code=404)
        seed = folder if (folder and config.resolve_allowed(folder)) else None
        out, assets = viewer.prepare_html(
            doc_src, html_text=html_text, server_origin=origin, folder=seed
        )
        app.state.allowed_assets.update(assets)
        # The viewed document's own scripts are stripped; this CSP is the floor that
        # also blocks inline handlers and any beacon to a non-self origin. The widget
        # is fully self-contained (no CDN), so 'self' is all it needs.
        csp = "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'"
        return HTMLResponse(out, headers={"Content-Security-Policy": csp})

    @app.get("/_fs/{path:path}")
    async def fs_asset(request: Request, path: str):
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return Response("Forbidden", status_code=403)
        result = viewer.resolve_fs_path(path, allowed=app.state.allowed_assets, home=Path.home())
        if result is None:
            return Response("Not found", status_code=404)
        abspath, ctype = result
        data = await asyncio.to_thread(abspath.read_bytes)
        return Response(content=data, media_type=ctype, headers={"Cache-Control": "no-cache"})

    @app.get("/_mtime")
    async def doc_mtime(request: Request, src: str):
        # Cheap change-detection for live reload: returns a stat signature the
        # /view page polls. Host-checked like /_fs; returns strictly less than
        # /view already does (which serves the file's full contents). Live reload
        # is local-only — remote URLs have no mtime.
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return Response("Forbidden", status_code=403)
        if viewer.is_remote(src):
            return JSONResponse({"ok": False})
        raw = src.strip()
        if raw.lower().startswith("file://"):
            raw = urllib.parse.unquote(urllib.parse.urlparse(raw).path)
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return JSONResponse({"ok": False})
        if path.suffix.lower() not in (".html", ".htm"):
            return JSONResponse({"ok": False})
        try:
            st = await asyncio.to_thread(path.stat)
        except (OSError, ValueError):
            return JSONResponse({"ok": False})
        return JSONResponse(
            {"ok": True, "sig": f"{st.st_mtime_ns}:{st.st_size}"},
            headers={"Cache-Control": "no-store"},
        )

    @app.options("/ask")
    async def ask_preflight(request: Request):
        return Response(status_code=204, headers=_cors_headers(request.headers.get("origin")))

    @app.options("/open-in-claude")
    async def open_in_claude_preflight(request: Request):
        return Response(status_code=204, headers=_cors_headers(request.headers.get("origin")))

    @app.post("/open-in-claude")
    async def open_in_claude(request: Request):
        # Same gating as /ask — this spawns a terminal running `claude`, a real
        # side effect. All app-level outcomes return 200 + {ok,...} so the widget
        # (cross-origin or same-origin) can read them.
        origin = request.headers.get("origin")
        cors = _cors_headers(origin)

        def reply(payload, status=200):
            return JSONResponse(payload, status_code=status, headers=cors)

        if not _origin_allowed(origin):
            return reply({"ok": False, "error": "origin not allowed"})
        if not _host_allowed(request.headers.get("host", ""), config.port):
            return reply({"ok": False, "error": "host not allowed"})
        try:
            body = await request.json()
        except Exception:
            return reply({"ok": False, "error": "invalid JSON"})
        if body.get("token") != config.token:
            return reply({"ok": False, "error": "invalid token"})

        folder = config.resolve_allowed(body.get("folder") or str(config.default_folder))
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
            await asyncio.to_thread(handoff.open_in_claude, folder, prompt)
        except Exception as exc:
            return reply({"ok": False, "error": str(exc), "prompt": prompt})
        return reply({"ok": True, "opened": True, "prompt": prompt})

    @app.post("/ask")
    async def ask(request: Request):
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")

        if not _origin_allowed(origin):
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

        folder = config.resolve_allowed(body.get("folder") or str(config.default_folder))
        if folder is None:
            return err_stream(
                f"Refused: folder not allowed or not a directory: {body.get('folder')!r}", origin
            )
        _remember_folder(app, str(folder))

        history = _sanitize_history(body.get("history"))
        prompt = build_user_prompt(action, selection, context, question, history)
        append_system = append_system_for(action)
        sem: asyncio.Semaphore = app.state.sem

        async def gen():
            acquired = False
            try:
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=0.05)
                    acquired = True
                except asyncio.TimeoutError:
                    yield _sse(
                        "error",
                        {"message": "Server busy (too many concurrent requests). Try again in a moment."},
                    )
                    return
                async for chunk in stream_answer(prompt, folder, config.model, append_system):
                    yield chunk
            finally:
                if acquired:
                    sem.release()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={**_cors_headers(origin), **_SSE_HEADERS},
        )

    return app
