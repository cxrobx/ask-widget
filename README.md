# Onyx

Onyx (formerly Ask Widget) is a local-first reading workspace for Claude Code and Codex. Open an HTML,
Markdown, text, or PDF document; select a passage; then right-click for **ELI5**,
**Prove it**, or **Ask a question**. Answers stream into the document and can use
read-only evidence from a context folder you choose.

Version 0.5 adds a reusable local question-history workspace: inspect full saved
answers, filter by passage, document, provider, model, or date, ask a saved
question again, edit it before asking, or restore its answer and continue the
visible conversation. Version 0.4 introduced subscription-backed Claude and
Codex providers, live model catalogs, and native model selectors.

```text
selection → Onyx → local FastAPI service → Claude CLI (claude.ai subscription)
                ↑              │               ↘ Codex CLI (ChatGPT subscription)
                └──── answer, tool trace, validated citations ───────────────┘
```

## What it includes

- A native **Onyx.app** with its own frozen Python service. The installed
  app does not depend on this checkout, a project virtual environment, or a
  system Python.
- The same macOS glass as cxtasks and cxmail: a plain Gaussian blur of the
  desktop behind the window (`CGSSetWindowBackgroundBlurRadius`, resolved at
  runtime, with `NSVisualEffectView` as the fallback), alpha-aware
  sidebar/content/card tints over it, persistent System/Light/Dark themes, and
  one transparency slider that drives both the tints and the blur radius
  (10–48, 24 at the default). The sidebar never gets thinner than the opacity
  that keeps its labels at WCAG AA contrast over any backdrop (79% dark, 84%
  light), so a bright window behind it can't wash the list out. The window
  opens opaque and turns to glass after the first paint; macOS **Reduce
  Transparency** is honoured live.
- **Library**, where the app opens: both vaults' trees in one sidebar, and a
  home page of recently opened documents (notes, artifacts, anything else) and
  recent asks. Per-document reading position and Markdown note export.
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
- **Recent conversations**, a searchable dialog (the clock at the sidebar's
  foot, or ⌘Y) with question-type, provider, model, document, and date filters.
  Saved entries can be asked again with the current model, edited before asking,
  or restored as a visible conversation, each in the reader beside the sidebar.
- **Settings**, a dialog behind the cog at the sidebar's foot (or ⌘,), as in
  cxtasks: window glass, provider, model, reasoning effort, response detail,
  caching, history, timeouts, private URL access, and trusted context roots.
  Each setting applies as it changes; there are no Save buttons.
- Diagnostics for both CLI installations, both subscription sessions, the
  selected model, database, default folder, and an optional live provider probe.
- Native **File ▸ Open Document…**, file-association support, and a macOS
  **Services ▸ Ask Selection with Onyx** action.
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
open -a "Onyx"
```

The build installs `/Applications/Onyx.app`, refreshes macOS Services, and
also produces:

```text
launcher/build/Onyx-0.5.0-macOS.zip
launcher/build/Onyx-0.5.0-macOS.zip.sha256
launcher/build/Open-in-Onyx.alfredworkflow
```

Use `./launcher/build-app.sh --no-install` to build without replacing the
installed app. The prior app is staged as a backup during installation and is
restored if the replacement fails.

The launcher:

- accepts only an Onyx service with the expected service identity and
  protocol version;
- starts the bundled service when no compatible service is running;
- stops its owned service on normal quit and uses a parent-process watcher for
  crash/force-quit cleanup;
- waits up to 20 seconds for a healthy service and offers Retry, Open Log, and
  Quit on failure;
- writes service output to
  `~/Library/Logs/Onyx/onyx.log` and rotates it at 2 MB;
- discovers Claude and Codex through the login shell and common GUI-safe paths,
  including NVM-installed Codex binaries;
- provides **Onyx ▸ Check for Updates…** using published GitHub releases.

## Run from a checkout

`run.sh` creates or repairs a project-local virtual environment using the exact
versions in `requirements-runtime.lock`.

```bash
./run.sh --folder ~/Projects/my-project --port 8899
open http://127.0.0.1:8899/
```

Set `ONYX_VENV` to keep the runtime environment elsewhere. Source code is
loaded directly from `src/`, so normal edits do not trigger a reinstall.

## Use the reader

The app opens on **Library**. Pick a note or page from the sidebar or a card on
the home page, or type a local path or HTTPS URL into its **Open** field. For a
document outside your vaults, set the context folder under that field to the
source material the selected provider is allowed to inspect; notes and artifacts
bring their own.

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
with Onyx**. Onyx opens a temporary reading page and selects the
shared passage automatically.

### Vault mode

The **Library · Notes · Artifacts** switch at the top of the sidebar (or **File ▸
Library**, ⌘N; **File ▸ Vault**, ⌘⇧V; **File ▸ Artifacts**, ⌘⇧H) changes view in
place. **Notes** is a persistent folder tree of your vault beside the reader;
**Library** shows it and Artifacts together, each under its own heading. Set the
folder under **Settings ▸ Vaults**; it defaults to `~/Documents/CX`. Saving it also adds the folder to the allowed context roots, so
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
it. The pin beside the vault's name (or ⌘\, even with focus inside the page)
unpins the sidebar, as in Zen's compact mode: the reader gets the whole window,
and the sidebar floats out over it when the pointer rests on the window's left
edge, going again once the pointer leaves. It stays out while it is in use — a
row's menu or the **+** panel open, or typing in the filter — and `/` brings it
out to type in. Pin it to dock it again; the choice is remembered.
Symlinked vault folders are followed and keep their vault-visible paths, so
links between notes inside them stay in the vault.

Right-click a note or folder for its menu: **Open**, **Reveal in Finder** (the
real file), and — for anything reached through a symlink — **Reveal Link in
Finder**, which shows the link itself in the vault folder. Hold ⌥ and they read
**Copy Path** and **Copy Link Path**: the resolved absolute path, or the path
through the link. Like cxtasks, Onyx draws its menus in its own theme; in the app,
WebKit's stock menu appears only in text fields and on a document's own links,
media, and selected text.

Hover a page (or Tab to it) and a card beside the sidebar shows its whole title,
its one-line summary (a page's description, else its subtitle, else its first
paragraph), where it lives, and when it last changed. With the Obsidian plugin
running, the sidebar can also wear your vault's file explorer — its font,
colors, chevrons, indent guides and per-folder colors — under **Settings →
Appearance → Match vault sidebar appearance**; see the plugin's README.

### Artifacts

**Artifacts** (on the switch at the top of the sidebar, or **File ▸ Artifacts**,
⌘⇧H) browses a folder of **symlinks to HTML pages anywhere on your Mac** — the HTML counterpart of an Obsidian vault. It defaults to
`~/Documents/Artifacts`; change it under **Settings ▸ Vaults**.

- **Top-level folders are projects.** A folder is listed once it holds a page,
  so an empty one stays out of the way; **+** still offers it as a place to add
  to. Link a whole folder (say a topic in `~/learnings`) and every page added to
  it later shows up on its own.
- **Pages are listed by their `<title>`**, with how long ago each changed. Below
  the project level a folder containing `index.html` is one page, so a guide
  folder reads as a single entry; its `index.inline.html` twin, audio, and notes
  stay out of the list.
- **A link whose target is gone stays listed, struck through, as *missing*.**
- **+** links more in: HTML files or a folder through the native picker, or a
  pasted path or `file://` URL, into a project you choose; it can also create
  folders. A linked `index.html` is named after its folder.
- Questions use **the real folder behind the link** as their context — a page
  under a linked topic folder can cite that topic's notes. Linking something adds
  that folder to the allowed context roots (never your home folder or `/`);
  remove it in Settings to take the access back.

Guides that narrate with `<audio src="audio/….m4a">` play in the reader: media
tags are rewritten to document capabilities, and `/_fs` answers byte ranges,
which WebKit requires before it will play media. A `file://…/index.html#section`
link keeps its fragment, and the fragment wins over the remembered scroll
position.

### Supported documents

| Source | Behavior |
|---|---|
| Local HTML/HTM | Re-served from localhost with authored scripts and buttons enabled; referenced local assets receive an expiring document capability. |
| Markdown | Rendered offline by the built-in HTML-escaping renderer. |
| Plain text | Displayed in a selectable reading view. |
| PDF | Extracts selectable text per page with `pypdf`; page-aware citations are preserved. |
| HTTP/HTTPS HTML | Fetched with redirect, content-type, size, and private-network checks. |

PDFs that contain only scanned images need OCR before Onyx can select or
reason over their text.

### Open from Finder or Alfred

Once the app is installed, a supported document can be opened through Finder's
**Open With ▸ Onyx** menu; it reads in Library, beside the sidebar, and a page
that lives in a vault opens as its row there. Onyx also provides **Services ▸ Open in
Onyx** for HTML, Markdown, text, and PDF files. If the Service is hidden,
enable it under **System Settings ▸ Keyboard ▸ Keyboard Shortcuts ▸ Services ▸
Files and Folders**.

For a first-class Alfred action, double-click
`launcher/build/Open-in-Onyx.alfredworkflow` and approve the import. Then
select a supported file in Alfred, open Universal Actions (right arrow by
default), and choose **Open in Onyx**.

The same workflow searches Onyx from Alfred's bar and opens the pick in Onyx:

| Keyword | Finds a page by | With nothing typed |
|---|---|---|
| `onx` | its title (a note's filename, an artifact's `<title>`) or its folder | the 30 pages changed most recently |
| `onxc` | the words in it: a note's text, an artifact's visible text, never its markup or scripts | a hint |

Every word typed must match. Both search Notes and Artifacts together, from the
folders set under **Settings ▸ Vaults**, and list pages the way the sidebar
does: a guide folder is one page, and a page linked into Artifacts from inside
the Notes vault shows once. The search is `integrations/alfred/onyx_search.py`,
run by the system Python.

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

**Library** is where the app opens. Its sidebar holds both vaults' trees, and
until you open something the reader's place holds its home page: an **Open**
field, **Recently opened** (each tagged Note, Artifact, or by kind, with where it
lives), and **Recent asks**, each of which opens its conversation. Choosing
Library again from Library returns to the home page. **Settings** (the cog at the
sidebar's foot, or ⌘,) and **Recent conversations** (the clock beside it, or ⌘Y)
are dialogs; the old launcher links `/#settings`, `/#diagnostics`, and
`/#history` open them.

The app stores its database at:

```text
~/Library/Application Support/Onyx/onyx.db
```

On its first start, Onyx copies the database it kept as Ask Widget
(`~/Library/Application Support/Ask Widget/ask-widget.db`) into place with
SQLite's backup API and leaves the original untouched.

Saved data includes settings, trusted roots, recent documents, reading
positions, requests, answers, citations, tool traces, errors, timing, and links
between original questions, reruns, edits, and continuations. Open **Recent
conversations** to search or reuse them. Turn off **Save reading history** in Settings to stop
persisting new conversations.
Browser/WKWebView answer-cache entries live in local storage and obey the cache
TTL and maximum-entry settings.

Saving a **Vault folder** in Settings also registers it as an allowed context
root, which is what lets a question asked inside a note cite that note's
neighbours. Clearing the field hides Vault mode and leaves the root in place.
The **Artifacts folder** is not registered itself — it holds only links — but
each folder linked into it is (see Artifacts above).

Additional trusted roots can be managed in Settings. The launcher also reads
`~/.config/onyx/allow-roots` at startup for compatibility; use one path per
line, with `~` expansion and `#` comments supported.

### Run headless

The service normally starts with the app, but it can also run on its own so the
Obsidian plugin and any browser page work with nothing open:

```bash
./scripts/install-daemon.sh              # LaunchAgent, starts at login
./scripts/install-daemon.sh --status
./scripts/install-daemon.sh --uninstall
```

The agent runs the same bundled server the app would spawn, logging to
`~/Library/Logs/onyx-daemon.log`. The two coexist: the launcher adopts a
healthy service rather than starting a second one, and only terminates a server
it spawned itself, so quitting the app leaves the daemon serving. If the app
happens to own the port, the daemon idles and takes over when the app quits.

### Obsidian plugin

`integrations/obsidian/` is a desktop-only Obsidian plugin that runs the same
three actions on a selection inside a note and streams the answer into a
right-sidebar panel. Answers are filed under the note's absolute path, so they
appear in this app's Library and History too. Build and install it with:

```bash
VAULT="$HOME/Documents/CX" ./scripts/install-obsidian-plugin.sh
```

Enable it under **Community plugins**, then quit and relaunch Obsidian — a newly
enabled plugin is not loaded by Cmd+R. See
[`integrations/obsidian/README.md`](integrations/obsidian/README.md) for the
development loop and the settings the plugin needs.

## Security model

Onyx can invoke Claude or Codex against local files, so the local HTTP boundary is
deliberately narrow:

0. Browser origins are allowlisted, never `*`: `null` (file://), localhost, and
   `127.0.0.1`, plus anything listed in the `allowed_origins` setting. It holds
   `app://obsidian.md` by default so the Obsidian plugin can reach the API;
   clearing it shuts the plugin out at the server.
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
11. Artifacts writes only symlinks and folders, and only inside its own
    folders: never through a linked folder (that would write into the tree it
    points at), never over an existing entry, never a link to something that
    contains the Artifacts folder. Link targets are never modified.

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
ONYX_SIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
ONYX_NOTARY_PROFILE="onyx-notary" \
./launcher/build-app.sh --no-install
```

Without those variables, the script creates a verified ad-hoc-signed local
build.

## Project layout

```text
ask-widget/
├── integrations/
│   ├── alfred/               Alfred workflow: onx/onxc search, file action
│   └── obsidian/             Obsidian plugin (TypeScript, esbuild)
├── launcher/                 native Swift app and release build
├── scripts/                  smoke tests, background daemon, plugin install
├── src/ask_widget/
│   ├── app.py                HTTP API, capabilities, persistence orchestration
│   ├── claude_runner.py      Claude process lifecycle and SSE translation
│   ├── codex_runner.py       Codex headless JSONL lifecycle and SSE translation
│   ├── citations.py          evidence validation and source opening
│   ├── diagnostics.py        provider/runtime/database diagnostics
│   ├── launcher_ui.py        shared glass, theme tokens, and the sidebar grid
│   ├── panels_ui.py          Settings and Recent conversations dialogs
│   ├── providers.py          subscription auth and live model discovery
│   ├── runner.py             subscription-only provider dispatch
│   ├── storage.py            SQLite schema and queries
│   ├── vault.py              vault index (notes + HTML): tree, wikilinks, titles, links
│   ├── vault_ui.py           the app's shell: Library · Notes · Artifacts beside the reader
│   └── viewer.py             secure HTML/Markdown/text/PDF readers
├── static/ask.js             selection UI and streamed answer panel
├── tests/                    API, security, storage, viewer, and runner tests
└── requirements-*.lock       exact runtime, build, and test environments
```
