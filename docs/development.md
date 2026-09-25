# Development and verification

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.lock
PYTHONPATH=src .venv/bin/python -W error -m unittest discover -s tests -v

.venv/bin/python -m pip install -r requirements-browser.lock
.venv/bin/python -m playwright install chromium webkit
PYTHONPATH=src .venv/bin/python -m unittest tests.browser_smoke -v

swiftc -typecheck -framework Cocoa -framework WebKit \
  -framework UniformTypeIdentifiers launcher/Onyx.swift
plutil -lint launcher/Info.plist

./launcher/build-app.sh --no-install
./scripts/smoke-bundle.sh
```

CI runs the Python suite on Python 3.11 and 3.14, type-checks the Swift launcher,
validates the plist, builds the frozen service, and smoke-tests its health and
configuration contracts.

For a distributable release, provide a Developer ID identity and a notarytool
keychain profile:

```bash
ONYX_SIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
ONYX_NOTARY_PROFILE="notary-profile" \
./launcher/build-app.sh --no-install
```

The build notarizes the ZIP, staples the app, then creates and notarizes a DMG
with an Applications shortcut. It staples the DMG and writes a SHA-256 file for
each download. Attach the DMG, the ZIP, the Alfred workflow, and their `.sha256`
files to the GitHub release. The Alfred workflow's name carries no version, so
`releases/latest/download/Open-in-Onyx.alfredworkflow` always serves the newest
copy, and the README links there. The frozen Python service uses the build machine's architecture;
the arm64 release requires an Apple Silicon Mac.

Without those variables, a local build signs with your keychain's Apple
Development identity when you have one. macOS then keeps the app's Documents
and Google Drive permissions across rebuilds. With no such identity (as in CI),
or with `ONYX_SIGN_IDENTITY=-`, it signs ad hoc. An ad-hoc signature changes
with every build, so macOS asks for those permissions again after each install,
and the vault sidebar waits until they are answered.

## Project layout

```text
onyx/
├── integrations/
│   ├── alfred/               Alfred workflow: onx/onxc search, file action
│   └── obsidian/             Obsidian plugin (TypeScript, esbuild)
├── launcher/                 native Swift app and release build
├── scripts/                  smoke tests, background daemon, plugin install
├── src/onyx/
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
