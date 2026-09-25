# Alfred integration

The **Onyx** workflow searches Onyx from Alfred's bar and adds an **Open in
Onyx** action to Alfred's Universal Actions panel.

| Use | Does |
|---|---|
| `onx` + words | Finds a page in Notes or Artifacts by its title (a note's filename, an artifact's `<title>`) or its folder. With nothing typed, lists the 30 pages changed most recently. |
| `onxc` + words | Finds a page by the words in it: a note's text, an artifact's visible text. |
| Universal Actions ▸ **Open in Onyx** | Opens a selected HTML, Markdown, plain-text, or PDF document. |

Every word typed must match. ↩ opens the pick in Onyx, ⌘C copies its path, ⌘Y
previews it, and → offers Alfred's file actions.

Install it on any Mac by downloading
[`Open-in-Onyx.alfredworkflow`](https://github.com/cxrobx/onyx/releases/latest/download/Open-in-Onyx.alfredworkflow)
from the latest release and double-clicking it. From a checkout, build Onyx and
double-click `launcher/build/Open-in-Onyx.alfredworkflow` instead.

Everything opens by Onyx's bundle identifier, so the app must be installed but
does not have to be running.

## The search

`onyx_search.py` reads the Notes and Artifacts folders from Onyx's settings
(**Settings ▸ Vaults**, falling back to `~/Documents/CX` and
`~/Documents/Artifacts`) and lists pages the way Onyx's sidebar does: a guide
folder is one page, and a page linked into Artifacts from inside the Notes
vault shows once, under Notes, which is where Onyx files it when it opens. It
is a standalone copy of `vault.py`'s listing rules,
because Alfred runs it with the system Python (3.9), which cannot import the
app. `tests/test_alfred_search.py` checks the copy against `vault.py` and runs
it under `/usr/bin/python3`.

To try a change without rebuilding, copy `info.plist` and `onyx_search.py` into
the installed workflow's folder and relaunch Alfred.
