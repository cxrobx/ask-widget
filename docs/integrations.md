# Finder, Alfred, and Obsidian

## Open from Finder or Alfred

Once the app is installed, a supported document can be opened through Finder's
**Open With ▸ Onyx** menu; it reads in Library, beside the sidebar, and a page
that lives in a vault opens as its row there. Onyx also provides **Services ▸ Open in
Onyx** for HTML, Markdown, text, and PDF files. If the Service is hidden,
enable it under **System Settings ▸ Keyboard ▸ Keyboard Shortcuts ▸ Services ▸
Files and Folders**.

For a first-class Alfred action, download
[`Open-in-Onyx.alfredworkflow`](https://github.com/cxrobx/onyx/releases/latest/download/Open-in-Onyx.alfredworkflow)
from the latest release, double-click it, and approve the import. The link
always points at the newest copy, so it is also the way to update the workflow.
A build from source writes the same file to
`launcher/build/Open-in-Onyx.alfredworkflow`. Then
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
the Notes vault shows once, under Notes, which is where Onyx files it when it
opens. The search is `integrations/alfred/onyx_search.py`, run by the system
Python.

## Obsidian plugin

`integrations/obsidian/` is a desktop-only Obsidian plugin that runs the same
three actions on a selection inside a note and streams the answer into a
right-sidebar panel. Answers are filed under the note's absolute path, so they
appear in this app's Library and History too. Build and install it with:

```bash
VAULT="$HOME/Documents/CX" ./scripts/install-obsidian-plugin.sh
```

Enable it under **Community plugins**, then quit and relaunch Obsidian — a newly
enabled plugin is not loaded by Cmd+R. See
[`integrations/obsidian/README.md`](../integrations/obsidian/README.md) for the
development loop and the settings the plugin needs.
