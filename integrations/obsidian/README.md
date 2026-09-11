# Onyx for Obsidian

ELI5, Prove it, and Ask a question on any selection in a note, answered by the
Onyx app already running on your Mac. Answers stream into a right-sidebar
panel and are saved under the note's absolute path, so they also appear in Ask
Widget's own Library and History.

Desktop only: the plugin talks to `http://127.0.0.1:8899`. If nothing is
listening it starts the service itself and retries, so the app never has to be
open.

## Install

```bash
cd integrations/obsidian && npm install && npm run build
VAULT="$HOME/Documents/CX" ../../scripts/install-obsidian-plugin.sh
```

Then in Obsidian: **Settings ▸ Community plugins ▸ Onyx ▸ enable**, and
**quit and relaunch Obsidian**. A newly enabled plugin is not picked up by
Cmd+R; only a full relaunch loads it the first time.

Finally, open the plugin's settings and press **Allow vault folder** once. That
registers the vault with the service so answers may cite your notes.

## Run without opening the app

Install the LaunchAgent once and the service runs headless at login:

```bash
../../scripts/install-daemon.sh            # install and start
../../scripts/install-daemon.sh --status
../../scripts/install-daemon.sh --uninstall
```

The app and the daemon coexist: whichever binds the port first owns it, opening
the app attaches to a running service instead of starting a second one, and
quitting the app leaves the daemon serving. Without the agent the plugin still
works — it runs the service on demand, falling back to launching the app.

## Use

With a passage selected:

- Right-click in the editor (source or Live Preview) and pick an Onyx item.
- Or run **Onyx: ELI5 / Prove it / Ask…** from the command palette, which
  also works in reading view. No hotkeys are bound by default.

The panel streams the answer, shows the provider and model, lists the tools the
model used, and renders evidence cards. A citation inside the vault opens the
note at the cited line; anything outside opens in your editor through the
service. **Stop** cancels, **Retry** re-runs, and the follow-up box continues the
same conversation.

## Markdown appearance

The plugin automatically shares this vault's computed reading styles with Ask
Widget on startup and when the theme changes. Font settings and CSS snippets
are included. Open Markdown readers update in place within a few seconds;
the last successful snapshot is saved by the service and survives closing
Obsidian or restarting Onyx. If the service is offline, the plugin retries
every 30 seconds without opening the app.

In Onyx, **Settings → Vault** selects the source vault. Its styles apply
to Markdown documents opened in the app, including the Vault reader. Other open
vaults keep separate snapshots and cannot overwrite the selected vault's style.
**Settings → Appearance → Match vault Markdown appearance** is enabled by
default; turn it off to restore the original reader appearance. With no snapshot,
the reader keeps its default styles. The app chrome, floating answer panel,
PDFs, plain text, and authored HTML keep their existing appearance.

To sync immediately, use **Onyx plugin settings → Markdown appearance →
Sync now**. Update both the service and plugin when installing this feature.

The snapshot covers ordinary Markdown typography, colors, spacing, links,
lists, quotes, code blocks, and tables. It does not copy whole stylesheets,
embedded font files, syntax-highlighting rules, custom callout renderers, or
note-specific `cssclasses`. Fonts must be available on the computer.

## Develop

```bash
OBSIDIAN_VAULT="$HOME/Documents/CX" npm run dev   # rebuild + copy on save
npm run check                                     # tsc --noEmit
npm test                                          # SSE parser unit tests
```

With `npm run dev` watching, Cmd+R in Obsidian reloads the rebuilt `main.js`.

## Notes

- The request token is fetched from `GET /api/session` and kept in memory. The
  plugin's `data.json` holds only the service URL and context folder, so a
  synced vault never carries a credential.
- The service must list `app://obsidian.md` in its `allowed_origins` setting.
  That is the default; clearing it turns the plugin off at the server.
- Restarting the Onyx app mints a new token. The plugin notices and
  refetches once, so an in-flight action just works.
