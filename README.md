# Ask Widget

Ask Widget is a local-first reading workspace for Claude Code and Codex. Open an HTML,
Markdown, text, or PDF document; select a passage; then right-click for **ELI5**,
**Prove it**, or **Ask a question**. Answers stream into the document and can use
read-only evidence from a context folder you choose.

Version 0.5 adds a reusable local question-history workspace: inspect full saved
answers, filter by passage, document, provider, model, or date, ask a saved
question again, edit it before asking, or restore its answer and continue the
visible conversation. Version 0.4 introduced subscription-backed Claude and
Codex providers, live model catalogs, and native model selectors.

```text
selection → Ask Widget → local FastAPI service → Claude CLI (claude.ai subscription)
                ↑              │               ↘ Codex CLI (ChatGPT subscription)
                └──── answer, tool trace, validated citations ───────────────┘
```

## What it includes

- A native **Ask Widget.app** with its own frozen Python service. The installed
  app does not depend on this checkout, a project virtual environment, or a
  system Python.
- The same macOS glass material model as cxtasks: a native
  `NSVisualEffectView`, transparent WebView, alpha-aware sidebar/content/card
  layers, persistent System/Light/Dark themes, and a glass-level control. The
  document reader and floating answer UI use the same translucent materials.
- A reading library with recent documents, recent answers, search, per-document
  reading position, and Markdown note export.
- HTML, Markdown, plain-text, and text-based PDF readers. Trusted local HTML keeps
  its buttons and scripts; PDF selections retain their page number for prompts
  and evidence.
- Streaming answers with first-activity and total timeouts, heartbeats, Stop,
  Retry, follow-up questions, and visible Read/Grep/Glob traces.
- A native provider selector and CLI-discovered model selectors. Claude models
  come from the aliases advertised by the installed Claude CLI; Codex models
  and supported reasoning efforts come from its live account model catalog.
- Subscription-only execution. Claude must be signed in through claude.ai and
  Codex through ChatGPT. API-key environment variables are removed and
  non-subscription sessions are rejected to avoid metered API charges.
- Validated evidence cards. File references are resolved inside the active
  context folder, include nearby source lines, and can open in VS Code, Cursor,
  or the default app.
- A bounded, expiring local answer cache keyed by document version, model,
  context folder, selection, and page.
- Persistent SQLite history in WAL mode with schema versioning.
- A dedicated, searchable History workspace with native provider, model,
  document, date, and action filters. Saved entries can be asked again with the
  current model, edited before asking, or restored as a visible conversation.
- Settings for window glass, provider, model, reasoning effort, response detail, caching, history, timeouts,
  private URL access, and trusted context roots.
- Diagnostics for both CLI installations, both subscription sessions, the
  selected model, database, default folder, and an optional live provider probe.
- Native **File ▸ Open Document…**, file-association support, and a macOS
  **Services ▸ Ask Selection with Ask Widget** action.
- Browser-style **Command-Plus**, **Command-Minus**, and **Command-0** zoom,
  also available from the View menu.
- **Open in Claude/Codex** for continuing with the selected provider in a dedicated terminal session. Hold
  Option to copy the prepared handoff prompt instead.

## Install the macOS app

Requirements for building: macOS 13 or newer, Xcode command-line tools, and
Python 3.11 or newer. The built app needs at least one supported CLI installed:
Claude Code signed into claude.ai, or Codex signed in with ChatGPT.

```bash
git clone https://github.com/cxrobx/ask-widget.git
cd ask-widget
./launcher/build-app.sh
open -a "Ask Widget"
```

The build installs `/Applications/Ask Widget.app`, refreshes macOS Services, and
also produces:

```text
launcher/build/Ask-Widget-0.5.0-macOS.zip
launcher/build/Ask-Widget-0.5.0-macOS.zip.sha256
launcher/build/Open-in-Ask-Widget.alfredworkflow
```

Use `./launcher/build-app.sh --no-install` to build without replacing the
installed app. The prior app is staged as a backup during installation and is
restored if the replacement fails.

The launcher:

- accepts only an Ask Widget service with the expected service identity and
  protocol version;
- starts the bundled service when no compatible service is running;
- stops its owned service on normal quit and uses a parent-process watcher for
  crash/force-quit cleanup;
- waits up to 20 seconds for a healthy service and offers Retry, Open Log, and
  Quit on failure;
- writes service output to
  `~/Library/Logs/Ask Widget/ask-widget.log` and rotates it at 2 MB;
- discovers Claude and Codex through the login shell and common GUI-safe paths,
  including NVM-installed Codex binaries;
- provides **Ask Widget ▸ Check for Updates…** using published GitHub releases.

## Run from a checkout

`run.sh` creates or repairs a project-local virtual environment using the exact
versions in `requirements-runtime.lock`.

```bash
./run.sh --folder ~/Projects/my-project --port 8899
open http://127.0.0.1:8899/
```

Set `ASK_WIDGET_VENV` to keep the runtime environment elsewhere. Source code is
loaded directly from `src/`, so normal edits do not trigger a reinstall.

## Use the reader

Open the app and choose a local document or paste an HTTPS URL. For a local
document, choose the context folder containing the source material the selected provider is
allowed to inspect.

Then:

1. Select a passage.
2. Right-click the selection.
3. Choose **ELI5**, **Prove it**, or **Ask a question…**.
4. Review the streamed response, tool activity, and evidence cards.
5. Ask a follow-up, open a citation, copy the answer, view prior answers for the
   selection, or hand the conversation to the selected provider in a terminal.

**Prove it** asks the selected provider to verify the passage against the selected folder and
return a Supported, Partially supported, Not supported, or No evidence verdict.

The macOS Selection Service provides the same workflow outside the reader:
select text in another app, open its Services menu, and choose **Ask Selection
with Ask Widget**. Ask Widget opens a temporary reading page and selects the
shared passage automatically.

### Vault mode

**Vault** in the sidebar (or **File ▸ Vault**, ⌘⇧V) opens a persistent folder
tree beside the reader. Set the folder under **Settings ▸ Vault**; it defaults to
`~/Documents/CX`. Saving it also adds the folder to the allowed context roots, so
answers can cite the notes themselves — remove it there and the tree keeps
working, but the reader's context folder falls back to the default.

Inside the vault the Markdown reader understands Obsidian's conventions:

| Written | Rendered |
|---|---|
| YAML frontmatter | A collapsible **Properties** block; tags become pills. |
| `[[Note]]`, `[[Note\|alias]]`, `[[Note#Heading]]` | A link resolved by Obsidian's shortest-path rules — the linking note's own folder wins, then the shallowest match. |
| `[[Missing]]` | A dotted span naming the note that does not exist. |
| `![[image.png]]` | The image, preferring the vault's attachment folder. |
| `[text](../Other.md)` | A reader link; relative and percent-encoded paths resolve. |

Clicking a link swaps the reader pane and moves the tree highlight; the browser
Back button walks the history. Press `/` to focus the filter box, Escape to clear
it. Symlinked vault folders are followed and keep their vault-visible paths, so
links between notes inside them stay in the vault.

### Supported documents

| Source | Behavior |
|---|---|
| Local HTML/HTM | Re-served from localhost with authored scripts and buttons enabled; referenced local assets receive an expiring document capability. |
| Markdown | Rendered offline by the built-in HTML-escaping renderer. |
| Plain text | Displayed in a selectable reading view. |
| PDF | Extracts selectable text per page with `pypdf`; page-aware citations are preserved. |
| HTTP/HTTPS HTML | Fetched with redirect, content-type, size, and private-network checks. |

PDFs that contain only scanned images need OCR before Ask Widget can select or
reason over their text.

### Open from Finder or Alfred

Once the app is installed, a supported document can be opened through Finder's
**Open With ▸ Ask Widget** menu. Ask Widget also provides **Services ▸ Open in
Ask Widget** for HTML, Markdown, text, and PDF files. If the Service is hidden,
enable it under **System Settings ▸ Keyboard ▸ Keyboard Shortcuts ▸ Services ▸
Files and Folders**.

For a first-class Alfred action, double-click
`launcher/build/Open-in-Ask-Widget.alfredworkflow` and approve the import. Then
select a supported file in Alfred, open Universal Actions (right arrow by
default), and choose **Open in Ask Widget**.

### Opening a document directly

```text
http://127.0.0.1:8899/view?src=/absolute/path/notes.md&folder=/absolute/path/project
http://127.0.0.1:8899/view?src=https://example.com/article
```

The reader makes the page same-origin with the local service, rewrites referenced
assets, removes the source page's CSP, and injects the widget. Trusted local HTML
retains authored scripts and inline handlers under a local-only connection policy;
remote HTML has its scripts removed. This avoids `file://` fetch and storage
restrictions while keeping downloaded web pages inert.

Because local HTML runs with the local reader origin, open interactive HTML only
when you trust its contents, just as you would before running a local script.

For a trusted page you control, the original standalone embed still works:

```html
<script src="http://127.0.0.1:8899/ask.js"></script>
```

Serve the page over HTTP rather than `file://` for predictable browser behavior.

## Library, storage, and settings

The app stores its database at:

```text
~/Library/Application Support/Ask Widget/ask-widget.db
```

Saved data includes settings, trusted roots, recent documents, reading
positions, requests, answers, citations, tool traces, errors, timing, and links
between original questions, reruns, edits, and continuations. Open **History**
to search or reuse them. Turn off **Save reading history** in Settings to stop
persisting new conversations.
Browser/WKWebView answer-cache entries live in local storage and obey the cache
TTL and maximum-entry settings.

Saving a **Vault folder** in Settings also registers it as an allowed context
root, which is what lets a question asked inside a note cite that note's
neighbours. Clearing the field hides Vault mode and leaves the root in place.

Additional trusted roots can be managed in Settings. The launcher also reads
`~/.config/ask-widget/allow-roots` at startup for compatibility; use one path per
line, with `~` expansion and `#` comments supported.

## Security model

Ask Widget can invoke Claude or Codex against local files, so the local HTTP boundary is
deliberately narrow:

1. The service binds to `127.0.0.1` by default.
2. Host headers must be `127.0.0.1:<port>` or `localhost:<port>` to block DNS
   rebinding.
3. Browser origins are limited to local origins and `file://`'s `null` origin.
4. A random per-process token is embedded in `ask.js` and required by all
   mutations and provider actions.
5. Context folders are resolved through symlinks and must remain under a trusted
   root unless the explicit `--allow-any` escape hatch is used.
6. Claude is restricted to Read, Grep, and Glob. Codex runs headlessly with a
   read-only sandbox, no approvals, no user rules, and optional tool/plugin
   features disabled.
7. Remote fetches reject credentials, non-HTML content, responses over 12 MB,
   and private, loopback, link-local, reserved, or multicast addresses unless
   private URLs are explicitly enabled.
8. Local document assets use unguessable capabilities that expire after eight
   hours and are bounded to the 32 most recently opened documents. JSON and
   source-map assets are never served.
9. Remote HTML scripts and inline handlers are removed. Trusted local HTML may
   run its authored scripts, but script connections stay restricted to the local
   service; plugins and base-URL changes remain blocked.
10. File citations are displayed only after canonicalizing them and proving they
    exist inside the active context folder.

Both executables are launched directly, not through an interactive shell.
Before launch, Anthropic/OpenAI API-key and alternate-provider environment
variables are removed so the verified subscription session is used.

## CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--folder DIR` | `~/Projects` | Default context folder. |
| `--port N` | `8899` | Local service port. |
| `--host H` | `127.0.0.1` | Bind address; keep this loopback. |
| `--model M` | `sonnet` | Initial Claude model before saved settings override it. |
| `--allow-root DIR` | — | Add a trusted context root; repeatable. |
| `--allow-any` | off | Allow any readable directory. This disables the root boundary. |
| `--data-dir DIR` | app support | Override database location for development/tests. |

## Development and verification

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.lock
PYTHONPATH=src .venv/bin/python -W error -m unittest discover -s tests -v

.venv/bin/python -m pip install -r requirements-browser.lock
.venv/bin/python -m playwright install chromium
PYTHONPATH=src .venv/bin/python -m unittest tests.browser_smoke -v

swiftc -typecheck -framework Cocoa -framework WebKit \
  -framework UniformTypeIdentifiers launcher/AskWidget.swift
plutil -lint launcher/Info.plist

./launcher/build-app.sh --no-install
./scripts/smoke-bundle.sh
```

CI runs the Python suite on Python 3.11 and 3.14, type-checks the Swift launcher,
validates the plist, builds the frozen service, and smoke-tests its health and
configuration contracts.

For a distributable release, provide a Developer ID identity and optional
notarytool keychain profile:

```bash
ASK_WIDGET_SIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
ASK_WIDGET_NOTARY_PROFILE="ask-widget-notary" \
./launcher/build-app.sh --no-install
```

Without those variables, the script creates a verified ad-hoc-signed local
build.

## Project layout

```text
ask-widget/
├── launcher/                 native Swift app and release build
├── scripts/                  bundled-service smoke test
├── src/ask_widget/
│   ├── app.py                HTTP API, capabilities, persistence orchestration
│   ├── claude_runner.py      Claude process lifecycle and SSE translation
│   ├── codex_runner.py       Codex headless JSONL lifecycle and SSE translation
│   ├── citations.py          evidence validation and source opening
│   ├── diagnostics.py        provider/runtime/database diagnostics
│   ├── launcher_ui.py        library, settings, and diagnostics UI
│   ├── providers.py          subscription auth and live model discovery
│   ├── runner.py             subscription-only provider dispatch
│   ├── storage.py            SQLite schema and queries
│   ├── vault.py              vault index: tree, wikilink resolution, containment
│   ├── vault_ui.py           vault shell: note tree beside the reader iframe
│   └── viewer.py             secure HTML/Markdown/text/PDF readers
├── static/ask.js             selection UI and streamed answer panel
├── tests/                    API, security, storage, viewer, and runner tests
└── requirements-*.lock       exact runtime, build, and test environments
```
