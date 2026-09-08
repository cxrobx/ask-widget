# Ask Widget for Obsidian

ELI5, Prove it, and Ask a question on any selection in a note, answered by the
Ask Widget app already running on your Mac. Answers stream into a right-sidebar
panel and are saved under the note's absolute path, so they also appear in Ask
Widget's own Library and History.

Desktop only: the plugin talks to `http://127.0.0.1:8899` and launches the macOS
app when it isn't running.

## Install

```bash
cd integrations/obsidian && npm install && npm run build
VAULT="$HOME/Documents/CX" ../../scripts/install-obsidian-plugin.sh
```

Then in Obsidian: **Settings ▸ Community plugins ▸ Ask Widget ▸ enable**, and
**quit and relaunch Obsidian**. A newly enabled plugin is not picked up by
Cmd+R; only a full relaunch loads it the first time.

Finally, open the plugin's settings and press **Allow vault folder** once. That
registers the vault with the service so answers may cite your notes.

## Use

With a passage selected:

- Right-click in the editor (source or Live Preview) and pick an Ask Widget item.
- Or run **Ask Widget: ELI5 / Prove it / Ask…** from the command palette, which
  also works in reading view. No hotkeys are bound by default.

The panel streams the answer, shows the provider and model, lists the tools the
model used, and renders evidence cards. A citation inside the vault opens the
note at the cited line; anything outside opens in your editor through the
service. **Stop** cancels, **Retry** re-runs, and the follow-up box continues the
same conversation.

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
- Restarting the Ask Widget app mints a new token. The plugin notices and
  refetches once, so an in-flight action just works.
