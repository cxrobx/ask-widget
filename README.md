# ask-widget

Highlight any text on a local HTML page, **right-click**, and get **ELI5**,
**Prove it**, or **Ask a question…** — each answer streamed from a `claude` CLI
call that has the **full context of a folder you point it at** (its `CLAUDE.md`
+ files, read agentically via Read/Grep/Glob).

It's a single drop-in `<script>` widget plus a tiny local **FastAPI** server.
Built for the HTML artifacts produced by `~/.claude/docs/html-design/`, but it
works on any local page.

```
highlight → right-click → ELI5 / Prove it / Ask…
        │
        ▼
  ask.js (widget)  ──POST /ask──▶  FastAPI server  ──▶  claude -p --add-dir <folder>
        ▲                                                  (read-only: Read/Grep/Glob)
        └───────────────  SSE: token / tool_status / done / error  ◀──┘
```

## Install (PEP 668 — a venv is required)

Homebrew Python is "externally managed", so a bare `pip install` fails. Use a venv:

```bash
cd ~/Projects/ask-widget
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

> On Python 3.14, `uvicorn[standard]`'s native extras (httptools/uvloop) may need
> source builds. The base install above uses plain `uvicorn`, which is enough.
> For the faster event loop: `pip install -e '.[fast]'` (only if wheels exist).

## Run

```bash
./run.sh --folder ~/Projects/zora --port 8899   # binds 127.0.0.1 only
# or:  python -m ask_widget --folder ~/Projects/zora --port 8899
```

## Run it as a Mac app

A native launcher (`launcher/`) wraps the whole thing as **Ask Widget.app** — open
it like any app; it starts the server and shows the launcher in a window.

```bash
./launcher/build-app.sh        # compiles the Swift launcher + installs to /Applications
open -a "Ask Widget"
```

The app (Swift + WKWebView, pattern from `resume-platform/launcher-swift`):
- starts `~/Projects/ask-widget/.venv/bin/python -m ask_widget` via a login shell
  (so the spawned server finds the `claude` binary on PATH), reusing an
  already-running server if one is up;
- shows the launcher in a window; **File ▸ Open Document…** and the launcher's
  **Choose…** buttons use a real macOS `NSOpenPanel` (a native folder/file picker —
  only available inside the app, since browsers hide absolute paths);
- **View ▸ Open in Default Browser** hands the current page to your browser as a
  fallback.

The widget is verified to work in WebKit (WKWebView), not just Chromium.

## Open ANY HTML — no editing, no file:// (the `/view` launcher)

The widget has to be **loaded by the page and same-origin with the server**. That's
a problem for two common cases:

- a **remote https page** you don't host (you can't add the `<script>`, and an
  https page can't load `http://localhost/ask.js` — mixed content), and
- a **local file opened via `file://`** (browsers block its `fetch` POST and
  partition `localStorage`; relative assets like `../.assets/style.css` 404).

So the server can re-serve any document **from localhost** with the widget already
injected and its assets rewired. Just open the launcher:

```
http://localhost:8899/
```

Paste a URL **or** a local file path + a context folder, hit **Open**. Or go direct:

```
http://localhost:8899/view?src=<url-or-path>&folder=<context-folder>
# e.g. a local file (fully self-contained — assets served from disk via /_fs):
http://localhost:8899/view?src=/Users/you/Meetings/review.html&folder=/Users/you/Projects/clientX
# e.g. a hosted share (assets pulled from the original site):
http://localhost:8899/view?src=https://example.com/share/abc123
```

What `/view` does: rewrites `<link>/<script>/<img>` asset refs to absolute (local →
a read-only `/_fs/<path>` route, remote → the original site), strips `<base>`/CSP
that would break same-origin anchors or block the widget, injects the widget, and
(if you pass `&folder=`) pre-seeds the context folder. The page ends up same-origin
with the server, so no mixed content and no `file://` fragility. **Prefer a local
path when you have the file** — it's fully self-contained (assets from disk, works
offline); a remote `src` may drop the odd access-protected asset.

## Or embed it yourself

In a local HTML file you control, before `</body>`:

```html
<script src="http://localhost:8899/ask.js"></script>
```

(then serve that file over http — see below — not `file://`).

**Serve the page over http**, not `file://`:

```bash
cd /folder/with/your.html && python3 -m http.server 9000
# open http://localhost:9000/your.html
```

`file://` works for loading the script, but browsers may block its `fetch` POST
to localhost and partition `localStorage`. The widget shows a hint when it
detects `file://`.

### Try the bundled demo

A ready-made test page lives at `examples/testdoc.html` (it already includes the
`<script>` line). Run the server pointed at this project, then serve the example:

```bash
./run.sh --folder ~/Projects/ask-widget --port 8899        # terminal 1
cd examples && python3 -m http.server 9000                 # terminal 2
# open http://localhost:9000/testdoc.html
```

It contains a deliberately false claim ("binds to `0.0.0.0`") so **Prove it**
can grep this repo's real source and return *Not supported* with file citations.
See `docs/screenshots/` for what each action looks like.

## Using it

- **Highlight** text, then **right-click** → a small menu appears with
  **ELI5 / Prove it / Ask a question…**. (No selection → your browser's normal
  right-click menu.)
- **ELI5** — plain-language explanation of the passage.
- **Prove it** — Claude greps the context folder for evidence and returns a
  verdict (Supported / Partially / Not supported / No evidence) with file-path
  citations. Great for fact-checking a doc against its own source.
- **Ask a question…** — free-form question about the passage.
- **Open in Claude** (button in the answer panel) — hands the passage + question +
  answer off to a **dedicated `claude` session in a terminal**, `cd`'d into the
  context folder (so it opens with that folder's `CLAUDE.md` + file access). Opens
  Ghostty if installed, else Terminal. **Hold ⌥ Option** and the button becomes
  **Copy Claude prompt** — it copies the seed prompt to the clipboard instead of
  launching, so you can paste it into a session you open yourself. (Same pattern as
  cxmail's "Open in Claude".)
- The **folder pill** (top-right) shows the active context folder. Click it to
  switch folders or pick a recent one. The choice persists in `localStorage` and
  is sent with every request.

## CLI flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--folder DIR` | `~/Projects` | Default context folder Claude reads. |
| `--port N` | `8899` | Server port. |
| `--host H` | `127.0.0.1` | Bind host. Keep it loopback. |
| `--model M` | `sonnet` | Passed to `claude --model`. |
| `--allow-root DIR` | — | Extra allowed root (repeatable). Default root: `~/Projects`. |
| `--allow-any` | off | Loud escape hatch: allow any directory (disables the folder allowlist). |

The macOS launcher app also reads optional extra roots from
`~/.config/ask-widget/allow-roots` (one path per line, `~` allowed, `#` comments)
and passes each as `--allow-root`.

## Security model

The server spawns a `claude` process and reads folders, so it defends against
drive-by pages and DNS-rebinding. All checks are on `POST /ask`:

1. **Loopback bind** — `127.0.0.1` only.
2. **Host header check** — must be `127.0.0.1:<port>`/`localhost:<port>` (blocks
   DNS-rebinding, where a malicious domain resolves to 127.0.0.1).
3. **Origin allowlist** — `null` (file://), `http(s)://localhost[:*]`,
   `http(s)://127.0.0.1[:*]`. Not `*`. (`*` stays only on `GET /ask.js` so the
   `<script>` tag can load anywhere.)
4. **Per-server token** — a random secret baked into `ask.js` at serve time and
   required in the `/ask` body. Defense-in-depth behind Origin + Host.
5. **Folder allowlist** — the requested folder is `resolve()`d (symlinks
   followed) and must be `is_relative_to` an allowed root.
6. **Read-only tools** — `--allowedTools Read Grep Glob` +
   `--disallowedTools Bash Edit Write NotebookEdit` (disallow wins).

Every refusal comes back as `event: error` on a 200 SSE stream so the widget can
show it inline.

### `/view` and `/_fs` (the launcher)

Re-serving a document same-origin is powerful, so `/view` is hardened against
opening a hostile URL (an adversarial review found — and this closes — a
secret-exfiltration chain here):

- **The viewed document's own `<script>` tags are stripped**, and `/view` sets a
  `Content-Security-Policy` (`script-src 'self'; connect-src 'self'`). So a page
  you open via `/view` can't run its own JS to
  read `/_fs` or drive `/ask`. (Trade-off: a doc loses its own interactivity —
  e.g. an auto-generated table of contents — when viewed this way. To keep a
  trusted local doc's scripts, embed `ask.js` in it directly instead.)
- **`/_fs` only serves the exact asset files a local document referenced** — it is
  not a general home-directory reader. A page opened from a remote URL registers
  no local files, so it can't pull `~/.claude.json` / `~/.docker/config.json`.
  `.json`/`.map` are not served at all.
- `/view` and `/_fs` enforce the same **Host-header check** as `/ask`, and local
  `/view` only opens `.html`/`.htm` files.

> The shell `claude` is a zsh function that injects `--dangerously-skip-permissions`.
> The server calls the **bare binary** (no function), so it runs with the
> read-only tool lock above. Test read-only behavior with `command claude`, not
> the shell `claude`.

## Notes & limits

- **Stateless** — no `--session-id` / `--resume` in v1. A follow-up "Ask" is a
  fresh call; to continue a thread, the previous answer would need to be resent
  as part of the context (not yet wired in the UI). This sidesteps plex-agent's
  "Session ID already in use" lock.
- **Markdown is rendered by a built-in renderer** (no CDN, works offline). It
  escapes all text first and then only adds its own tags, so "Prove it" reflecting
  raw file contents into the page is XSS-safe by construction — no DOMPurify needed.
- **Concurrency** — each `/ask` is its own `claude` process; an in-process
  semaphore caps it at 3 and returns a friendly `event: error` on overflow.
- **Latency** — first token ~2-3s; the panel shows "Thinking…" then tool pills.
  A 120s timeout bounds the worst case.

## Layout

```
ask-widget/
├── pyproject.toml
├── run.sh
├── README.md
├── src/ask_widget/
│   ├── __main__.py       # argparse + uvicorn.run
│   ├── app.py            # FastAPI routes + Origin/Host/token/folder security
│   ├── claude_runner.py  # subprocess spawn + stream-json → SSE parser
│   ├── prompts.py        # 3 prompt templates + per-action system prompts
│   └── config.py         # frozen AppConfig + folder allowlist
└── static/ask.js         # the entire widget (one file)
```
