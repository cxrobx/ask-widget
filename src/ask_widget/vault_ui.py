"""The app's shell: a sidebar of Library, Notes and Artifacts beside a same-origin reader iframe.

**Notes** is the Obsidian vault (Markdown, wikilinks). **Artifacts** is a folder
of symlinks to HTML pages anywhere on disk: its top-level folders are projects,
pages are labelled by their ``<title>``, and the **+** panel links more in (it
only ever creates links and folders inside the vault — see
``vault.writable_folder``). **Library**, where the app opens, is both at once:
each vault's tree under its own heading, and, while no page is open, the home
page in the reader's place — open a file or URL, what you had open lately, and
your latest asks. Settings and Recent conversations are the two modals at the
sidebar's foot (``panels_ui.py``).

The page is deliberately thin. Files are plain ``<a target=reader>`` links, so
the named iframe handles navigation and history without any click JS; the
script only builds the tree, keeps the highlight in sync with whatever the
reader currently shows, drives the filter box, the rows' right-click menu
(``static/app-menu.js``: Reveal in Finder, and ⌥ Copy Path), and (Artifacts)
the add panel.

Switching views happens in place, never by loading another page: a load blanks
the glass window for a frame and leaves the tree reading "Loading…" until it is
fetched again, so the whole sidebar flickered. The page carries every view's
chrome and keeps both trees, and ``switchVault`` swaps one for another.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .launcher_ui import glass_script, theme_settings, theme_style
from .panels_ui import ICONS, panels_markup, panels_script, panels_style

# The sidebar's pin: filled while the sidebar is pinned, outlined while it floats and comes out from the left edge.
PIN_ICON = (
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path class="pin-head" d="M6.75 2.25v3.5L4.75 8.5h6.5l-2-2.75v-3.5z"/>'
    '<path d="M5.75 2.25h4.5M8 8.5v5.25"/></svg>'
)

# What each view calls itself in the shell. All ride along in the page, so a switch swaps them in place.
VAULT_LABELS = {
    "library": {"name": "Library", "unit": "item", "units": "everything", "tree": "Notes and Artifacts", "empty": ""},
    "notes": {"name": "Vault", "unit": "note", "units": "notes", "tree": "Vault notes", "empty": "Pick a note from the tree."},
    "html": {
        "name": "Artifacts",
        "unit": "page",
        "units": "pages",
        "tree": "Artifacts pages",
        "empty": "Pick a page from the sidebar.",
    },
}
# Library's headings, and where a row sits when Library shows it: the switch's words, not the vault's own name.
GROUP_LABELS = {"notes": "Notes", "html": "Artifacts"}

# The tree's glyphs: a folder that opens with its <details>, and the hover card's rows.
_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true"{cls}>{body}</svg>'
FOLDER_PATH = '<path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.6l1.5 1.5h4.9A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"/>'
TREE_ICONS = {
    "shut": _SVG.format(cls=' class="shut"', body=FOLDER_PATH),
    "open": _SVG.format(
        cls=' class="open"',
        body='<path d="M2 11.5v-7A1.5 1.5 0 0 1 3.5 3h2.6l1.5 1.5h4.4A1.5 1.5 0 0 1 13.5 6v1"/>'
        '<path d="M2 11.5l1.6-3.7A1.3 1.3 0 0 1 4.8 7h9a.8.8 0 0 1 .75 1.1l-1.5 3.9a1.5 1.5 0 0 1-1.4 1H3.5A1.5 1.5 0 0 1 2 11.5z"/>',
    ),
    "folder": _SVG.format(cls="", body=FOLDER_PATH),
    "doc": _SVG.format(
        cls="", body='<path d="M4 1.75h5.25L12.5 5v8.25c0 .55-.45 1-1 1H4c-.55 0-1-.45-1-1V2.75c0-.55.45-1 1-1z"/><path d="M9 1.75V5.25h3.5M5.5 8.5h5M5.5 11h3.5"/>'
    ),
    "link": _SVG.format(cls="", body='<path d="M6.5 9.5l3-3M7 4.5l1-1a2.5 2.5 0 0 1 3.5 3.5l-1 1M9 11.5l-1 1A2.5 2.5 0 0 1 4.5 9l1-1"/>'),
    # Only the Obsidian look shows it (sidebar_theme): a chevron that turns as its folder opens.
    "chev": '<svg class="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5"/></svg>',
}


def vault_page(
    config: AppConfig,
    settings: dict[str, Any] | None,
    *,
    src: str | None = None,
    reader_query: str | None = None,
    kind: str = "notes",
    sidebar: dict[str, Any] | None = None,
    look: dict[str, Any] | None = None,
) -> str:
    kind = kind if kind in VAULT_LABELS else "notes"
    _glass, theme = theme_settings(settings)
    # The vault's Obsidian file-explorer look (sidebar_theme), in force only when it has CSS.
    sidebar = sidebar or {}
    sidebar_css = sidebar.get("css") or ""
    sidebar_state = json.dumps({"revision": sidebar.get("revision", ""), "folders": sidebar.get("folders", [])}).replace("<", "\\u003c")
    body_class = f"kind-{kind}" + (" obsidian-tree" if sidebar_css else "")
    labels = VAULT_LABELS[kind]
    version = html.escape(__version__)
    shared_style = theme_style(settings)
    # The whole app in the vault's colours (vault_look), while Match vault appearance is on and the plugin has measured it.
    look = look or {}
    look_css = look.get("css") or ""
    look_state = json.dumps({"revision": look.get("revision", ""), "mode": look.get("mode"), "base": look.get("base")})
    html_class = ' class="vault-look"' if look_css else ""
    glass_js = glass_script(settings, vault=look if look_css else None)
    initial = html.escape(f"/view?{reader_query}", quote=True) if reader_query else "about:blank"
    initial_src = json.dumps(src or "")
    token = json.dumps(config.token)
    vault_name = labels["name"]
    title = html.escape(f"{Path(src).name} — {vault_name}" if src else vault_name)
    library_active, notes_active, html_active = (
        " class=active aria-current=page" if kind == k else "" for k in ("library", "notes", "html")
    )
    vaults_json = json.dumps(VAULT_LABELS)
    groups_json = json.dumps(GROUP_LABELS)
    icons_json = json.dumps(TREE_ICONS)
    # Library rests on its home page; the vaults on a line saying what to pick.
    home_hidden = "" if kind == "library" and not src else " hidden"
    empty_hidden = " hidden" if kind == "library" else ""
    default_folder = html.escape(str(config.default_folder), quote=True)
    short_folder = html.escape(str(config.default_folder).replace(str(Path.home()), "~", 1))
    history_icon, settings_icon = ICONS["history"], ICONS["settings"]
    panels_css, panels_html, panels_js = panels_style(), panels_markup(settings), panels_script()
    # The + and its panel ship with every view (CSS shows them only in Artifacts), so a switch needs no reload.
    add_toggle = (
        '<button id=add-toggle class=add-toggle type=button title="Add pages or a folder" '
        'aria-controls=add-panel aria-expanded=false>+</button>'
    )
    add_panel = """<div id=add-panel class=add-panel hidden><label for=add-dest>Add to</label><select id=add-dest></select>
<div class=add-row><button type=button class="secondary pick" id=add-pick-files>HTML files…</button><button type=button class="secondary pick" id=add-pick-folder>Folder…</button></div>
<div class=add-row><input id=add-path placeholder="Paste a path or file:// URL" spellcheck=false autocomplete=off><button type=button class=secondary id=add-path-go>Link</button></div>
<div class=add-row><input id=add-folder-name placeholder="New folder name" spellcheck=false autocomplete=off><button type=button class=secondary id=add-mkdir>Create</button></div>
<p id=add-status class=field-help>Links point at the originals; nothing is moved or copied.</p></div>"""
    empty_hint = labels["empty"]
    units = labels["units"]
    tree_label = labels["tree"]
    return f"""<!doctype html><html data-theme="{theme}"{html_class}><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
{shared_style}
html,body{{height:100%;overflow:hidden}} button,input,select{{font:inherit}}
.shell{{grid-template-columns:260px minmax(0,1fr);height:100vh;min-height:0;transition:grid-template-columns .15s cubic-bezier(.2,.8,.2,1)}}
aside{{display:flex;flex-direction:column;height:100vh;padding:20px 12px 14px;overflow:hidden}} body.native aside{{padding-top:48px}}
.brand{{margin:0 8px 12px}} .brand-name{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.add-toggle{{display:grid;place-items:center;width:24px;height:24px;padding:0;border:1px solid var(--line-soft);border-radius:7px;background:rgb(var(--ink)/.055);color:rgb(var(--secondary));font-size:16px;line-height:1}} .add-toggle:hover,.add-toggle[aria-expanded=true]{{background:rgb(var(--ink)/.1);color:rgb(var(--ink))}}
.vault-switch{{position:relative;display:grid;grid-template-columns:repeat(3,1fr);gap:2px;margin:0 0 10px;padding:2px;border-radius:8px;background:rgb(var(--ink)/.06)}} .vault-switch a{{position:relative;padding:4px 0;border-radius:6px;color:rgb(var(--secondary));font-size:12px;font-weight:600;text-align:center;text-decoration:none;transition:color .15s}} .vault-switch a:hover,.vault-switch a.active{{color:rgb(var(--ink))}}
/* Switching views: the pill is one piece that slides to the active view (a column wide: the padding box less its 2 px
   padding each side and the two 2 px gaps, in thirds), the sidebar's width glides with it (.shell above, the floating
   panel below), and the + comes and goes. */
.vault-switch::before{{content:"";position:absolute;top:2px;bottom:2px;left:2px;width:calc((100% - 8px) / 3);border-radius:6px;background:rgb(var(--bg-elevated)/.92);box-shadow:0 1px 2px rgb(0 0 0/.12);transition:transform .15s cubic-bezier(.2,.8,.2,1)}} body.kind-notes .vault-switch::before{{transform:translateX(calc(100% + 2px))}} body.kind-html .vault-switch::before{{transform:translateX(calc(200% + 4px))}}
body:not(.kind-html) #add-toggle,body:not(.kind-html) #add-panel{{display:none}}
@media(prefers-reduced-motion:reduce){{.shell,.vault-switch::before,.vault-switch a{{transition:none}}}}
.add-panel{{margin:0 0 10px;padding:10px;border:1px solid var(--line-soft);border-radius:10px;background:rgb(var(--bg-surface)/var(--surface-alpha))}} .add-panel label{{display:block;margin:0 0 4px;color:rgb(var(--secondary));font-size:11px;font-weight:650}} .add-panel select{{width:100%;margin:0 0 8px}}
.add-row{{display:flex;gap:6px;margin:0 0 6px}} .add-row button{{flex:1;padding:5px 8px;border-radius:7px;font-size:12px;font-weight:600;white-space:nowrap}} .add-row input{{flex:1;min-width:0;padding:5px 8px;border:1px solid var(--line);border-radius:7px;background:rgb(var(--bg-input)/.88);color:rgb(var(--ink));font-size:12px}} .add-row input+button{{flex:none}}
.secondary{{border:1px solid var(--line-soft);background:rgb(var(--ink)/.055);color:rgb(var(--ink))}} .secondary:hover{{border-color:var(--line);background:rgb(var(--ink)/.09)}} .pick{{display:none}} body.native .pick{{display:block}}
.field-help{{margin:4px 1px 0;color:rgb(var(--muted));font-size:10.5px;line-height:1.4}} .field-help.ok{{color:rgb(var(--good))}} .field-help.bad{{color:rgb(var(--bad))}}
#vault-filter{{width:100%;margin:0 0 10px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:rgb(var(--bg-input)/.88);color:rgb(var(--ink));font-size:12.5px;box-shadow:inset 0 1px 0 rgb(255 255 255/.025)}} #vault-filter:focus,.add-row input:focus{{outline:2px solid rgb(var(--accent)/.26);outline-offset:0;border-color:rgb(var(--accent))}}
/* The tree: one quiet line per row, a folder that opens with its <details>, the rest in the hover card. */
#tree{{flex:1;min-height:0;overflow:auto;margin:0 -6px;padding:2px 6px;font-size:13.5px}} #tree ul{{list-style:none;margin:0;padding:0}} #tree ul ul{{padding-left:25px}}
#tree summary,#tree .file{{display:flex;align-items:center;gap:9px;min-height:30px;margin:1px 0;padding:5px 10px;border-radius:9px;color:rgb(var(--ink)/.84);text-decoration:none;white-space:nowrap;user-select:none;transition:background-color .12s,color .12s}}
#tree summary{{cursor:default;list-style:none}} #tree summary::-webkit-details-marker{{display:none}} #tree .lbl{{min-width:0;overflow:hidden;text-overflow:ellipsis}}
#tree .chev{{display:none;flex:none;width:14px;height:14px;transition:transform .12s}} #tree details[open]>summary .chev{{transform:rotate(90deg)}}
#tree .fold{{display:grid;flex:none;width:16px;height:16px;color:rgb(var(--ink)/.6)}} #tree .fold svg{{grid-area:1/1;width:16px;height:16px}} #tree .fold .open,#tree details[open]>summary .fold .shut{{display:none}} #tree details[open]>summary .fold .open{{display:block}}
#tree summary:hover,#tree .file:hover{{background:rgb(var(--ink)/.045);color:rgb(var(--ink))}} #tree a.file.active{{background:rgb(var(--ink)/.12);color:rgb(var(--ink));font-weight:500}}
#tree summary:focus-visible,#tree .file:focus-visible{{outline:2px solid rgb(var(--accent));outline-offset:-2px}}
#tree .sym{{flex:none;margin-left:auto;color:rgb(var(--faint));font-size:11px;opacity:0;transition:opacity .12s}} #tree summary:hover .sym{{opacity:1}}
#tree .ext{{flex:none;margin-left:auto;padding:0 5px;border-radius:4px;background:rgb(var(--ink)/.07);color:rgb(var(--muted));font-size:9px;font-weight:700;text-transform:uppercase}}
#tree .file.missing{{color:rgb(var(--faint));cursor:help}} #tree .file.missing .lbl{{text-decoration:line-through}} #tree .file.missing .ext{{background:rgb(var(--bad)/.1);color:rgb(var(--bad))}}
#tree .results .file{{flex-direction:column;align-items:flex-start;justify-content:center;gap:0}} #tree .results .lbl{{max-width:100%}} #tree .results small{{max-width:100%;overflow:hidden;color:rgb(var(--muted));font-size:11px;text-overflow:ellipsis}} #tree .none{{padding:6px 10px;color:rgb(var(--muted));font-size:12.5px}}
/* Library: each vault's tree under a quiet heading of its own, its folders at the tree's usual depth beneath it. The
   heading is chrome, not a folder, so it keeps this look under the Obsidian one too: that look styles every row through
   `body.obsidian-tree #tree summary`, and these selectors are more specific. */
#tree li.group+li.group{{margin-top:8px}} #tree .group>details>ul{{padding-left:0}}
#tree li.group>details>summary.group-head{{gap:6px;min-height:26px;margin:0;padding:4px 10px;border-radius:7px;background:transparent;color:rgb(var(--muted));font:600 11px/1.3 var(--ui-font);letter-spacing:.06em;text-transform:uppercase}} #tree li.group>details>summary.group-head:hover{{background:transparent;color:rgb(var(--secondary))}}
#tree li.group>details>summary.group-head::before{{content:"";flex:none;width:5px;height:5px;margin:0 3px 0 1px;border:solid currentColor;border-width:0 1.4px 1.4px 0;transform:rotate(-45deg);transition:transform .12s}} #tree li.group>details[open]>summary.group-head::before{{transform:rotate(45deg)}}
#tree li.group>details>summary.group-head .count{{margin-left:auto;font-weight:500;letter-spacing:0;text-transform:none;opacity:0;transition:opacity .12s}} #tree li.group>details>summary.group-head:hover .count{{opacity:1}}
/* The hover card: the whole title, the page's one line, where it lives, what it is. App chrome, so it takes the app's theme. */
#peek{{position:fixed;z-index:50;width:max-content;min-width:220px;max-width:320px;padding:12px 14px;border:1px solid var(--line);border-radius:14px;background:rgb(var(--bg-elevated)/.94);color:rgb(var(--ink));box-shadow:0 18px 44px rgb(0 0 0/.26),inset 0 1px 0 rgb(255 255 255/.06);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3);pointer-events:none;opacity:0;transform:translateX(-4px);transition:opacity .12s ease,transform .12s ease}}
#peek.show{{opacity:1;transform:none}} #peek[hidden]{{display:none}} #peek p{{margin:0}} #peek .peek-title{{font-size:14px;font-weight:600;line-height:1.35}}
#peek .peek-sum{{margin-top:5px;color:rgb(var(--secondary));font-size:12.5px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
#peek .peek-row{{display:flex;align-items:center;gap:8px;margin-top:9px;color:rgb(var(--secondary));font-size:12.5px}} #peek .peek-row+.peek-row{{margin-top:5px}} #peek .peek-row svg{{flex:none;width:15px;height:15px;color:rgb(var(--muted))}} #peek .peek-row span{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
@media(prefers-reduced-motion:reduce){{#peek{{transition:none;transform:none}}}} @media(prefers-reduced-transparency:reduce){{#peek{{background:rgb(var(--bg-elevated));backdrop-filter:none;-webkit-backdrop-filter:none}}}}
/* The foot: how much is here, then the two modals — Recent conversations and Settings — as cxtasks parks its cog. */
.aside-foot{{position:static;display:flex;align-items:center;gap:2px;margin-top:8px;padding:0 2px 0 8px}} #vault-count{{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}}
.foot-btn{{display:grid;flex:none;place-items:center;width:24px;height:24px;padding:0;border:0;border-radius:6px;background:transparent;color:rgb(var(--faint));transition:background-color 75ms,color 75ms}} .foot-btn:hover{{background:rgb(var(--ink)/.08);color:rgb(var(--ink))}} .foot-btn svg{{width:14px;height:14px}}
main,body.native main{{position:relative;padding:0;overflow:hidden}}
#reader{{display:block;width:100%;height:100%;border:0;background:transparent}}
#reader-empty{{position:absolute;inset:0;display:grid;place-items:center;padding:24px;color:rgb(var(--muted));font-size:14px;text-align:center;pointer-events:none}} #reader-empty[hidden]{{display:none}} #reader-empty a{{pointer-events:auto;color:rgb(var(--accent))}}
/* Library's home, in the reader's place while no page is open: open something, what you had open, what you asked. */
#home{{position:absolute;inset:0;z-index:1;overflow:auto;padding:40px 44px 56px}} body.native #home{{padding-top:52px}} #home[hidden]{{display:none}}
.home-inner{{max-width:900px;margin:0 auto}} #home h2{{margin:0;color:rgb(var(--faint));font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}}
.home-h{{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:30px 2px 10px}} .home-h .link{{padding:0;border:0;background:none;color:rgb(var(--accent));font-size:12px}} .home-h .link:hover{{text-decoration:underline}}
.open-row{{display:flex;gap:8px}} .open-row input{{flex:1;min-width:0;padding:8px 11px;border:1px solid var(--line);border-radius:8px;background:rgb(var(--bg-input)/.88);color:rgb(var(--ink));font-size:13px}} .open-row button,.open-context button{{flex:none;padding:7px 13px;border-radius:8px;font-weight:600}}
.open-row input:focus,.open-context input:focus{{outline:2px solid rgb(var(--accent)/.26);outline-offset:0;border-color:rgb(var(--accent))}}
.open-context{{margin:7px 2px 0;color:rgb(var(--muted));font-size:11.5px}} .open-context summary{{display:inline;cursor:default;list-style:none}} .open-context summary::-webkit-details-marker{{display:none}} .open-context summary:hover{{color:rgb(var(--ink))}}
.open-context .row{{display:flex;gap:6px;margin-top:6px}} .open-context input{{flex:1;min-width:0;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:rgb(var(--bg-input)/.88);color:rgb(var(--ink));font-size:12px}}
.home-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}}
.home-card{{display:flex;flex-direction:column;gap:5px;min-width:0;padding:11px 13px;border:1px solid var(--line-soft);border-radius:10px;background:rgb(var(--bg-surface)/var(--surface-alpha));color:inherit;text-decoration:none;transition:border-color 75ms,background-color 75ms}} .home-card:hover{{border-color:var(--line);background:rgb(var(--bg-surface)/.98)}}
.home-card .t{{overflow:hidden;font-weight:600;white-space:nowrap;text-overflow:ellipsis}} .home-card .m{{display:flex;align-items:center;gap:7px;min-width:0;color:rgb(var(--muted));font-size:11px}} .home-card .w{{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}} .home-card .a{{flex:none;font-variant-numeric:tabular-nums}}
.tag{{flex:none;padding:1px 6px;border-radius:4px;background:rgb(var(--ink)/.07);color:rgb(var(--secondary));font-size:9.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase}} .tag.notes{{background:rgb(var(--good)/.12);color:rgb(var(--good))}} .tag.html{{background:rgb(var(--accent)/.13);color:rgb(var(--accent))}}
.home-list{{display:flex;flex-direction:column;gap:1px}} .home-ask{{display:flex;flex-direction:column;gap:2px;width:100%;padding:8px 11px;border:0;border-radius:8px;background:transparent;color:inherit;text-align:left;transition:background-color 75ms}} .home-ask:hover,.home-ask:focus-visible{{background:rgb(var(--ink)/.05);outline:none}}
.home-ask .t{{display:flex;align-items:baseline;gap:8px;min-width:0}} .home-ask strong{{flex:1;min-width:0;overflow:hidden;font-weight:600;white-space:nowrap;text-overflow:ellipsis}} .home-ask .a{{flex:none;color:rgb(var(--muted));font-size:11px}} .home-ask .q{{overflow:hidden;color:rgb(var(--secondary));white-space:nowrap;text-overflow:ellipsis}}
.home-empty{{padding:20px;border:1px dashed var(--line);border-radius:10px;color:rgb(var(--muted));text-align:center}}
/* Artifacts are named by sentence-length titles, and Library shows them too: a wider sidebar, one line each, and the whole
   title in the hover card. */
body.kind-html .shell,body.kind-library .shell{{grid-template-columns:290px minmax(0,1fr)}}
/* Sidebar: pinned, it sits in the grid; unpinned, it floats over the reader and comes out when the pointer rests on the
   left edge, as in Zen's compact mode: #side-edge, 24 px deep so the pointer needn't find a sliver, laid over the reader
   because its iframe would swallow the pointer (a page's first 24 px are margin; they can't be clicked while unpinned). */
.side-toggle{{display:grid;place-items:center;flex:none;width:26px;height:24px;padding:0;border:0;border-radius:7px;background:transparent;color:rgb(var(--secondary))}} .side-toggle:hover{{background:rgb(var(--ink)/.08);color:rgb(var(--ink))}} .side-toggle svg{{width:16px;height:16px}} #side-pin[aria-pressed=true] .pin-head{{fill:currentColor}}
@media(max-width:800px){{.shell,body.kind-html .shell,body.kind-library .shell{{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}} aside,body.native aside{{position:static;height:auto;max-height:45vh;padding:12px 12px 8px}} body.native aside{{padding-top:38px}} .aside-foot{{display:flex}} #home{{padding:24px 18px 40px}}}}
#side-edge{{position:fixed;top:0;bottom:0;left:0;z-index:39;display:none;width:24px}} body.side-unpinned #side-edge{{display:block}}
body.side-unpinned .shell,body.side-unpinned.kind-html .shell,body.side-unpinned.kind-library .shell{{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr)}}
/* Floating, it is a panel lying on the page: inset, rounded, shadowed, and on a ground thick enough to read over a page's
   text even where the blur is not drawn; the window's glass is too thin (the Obsidian look brings an opaque ground of its own). */
body.side-unpinned #vault-side{{position:fixed;top:8px;bottom:8px;left:8px;z-index:40;width:min(260px,86vw);height:auto;max-height:none;padding-top:12px;border:1px solid var(--line);border-radius:12px;box-shadow:0 18px 50px rgb(0 0 0/.24),0 2px 8px rgb(0 0 0/.08);visibility:hidden;transform:translateX(calc(-100% - 16px))}} body.side-unpinned.kind-html #vault-side,body.side-unpinned.kind-library #vault-side{{width:min(290px,86vw)}} body.native.side-unpinned #vault-side{{padding-top:40px}}
body.side-unpinned:not(.obsidian-tree) #vault-side{{background:rgb(var(--bg-sidebar)/.96);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3)}}
@media(prefers-reduced-transparency:reduce){{body.side-unpinned:not(.obsidian-tree) #vault-side{{background:rgb(var(--bg-sidebar));backdrop-filter:none;-webkit-backdrop-filter:none}}}}
body.side-unpinned.side-out #vault-side{{visibility:visible;transform:none}}
/* Motion: out with an ease-out slide; away with a quicker ease-in one, hidden only once it is off. Reduce Motion fades in place. */
body.side-unpinned #vault-side{{transition:transform .13s cubic-bezier(.4,0,1,1),visibility 0s linear .13s}} body.side-unpinned.side-out #vault-side{{transition:transform .15s cubic-bezier(.2,.8,.2,1),visibility 0s,width .15s cubic-bezier(.2,.8,.2,1)}} body.side-still #vault-side{{transition:none!important}}
@media(prefers-reduced-motion:reduce){{body.side-unpinned #vault-side{{transform:none;opacity:0;transition:opacity .15s linear,visibility 0s linear .15s}} body.side-unpinned.side-out #vault-side{{opacity:1;transition:opacity .15s linear,visibility 0s}}}}
/* The row a menu is open for wears a ring, as Finder's does. */
#tree .menu-for{{box-shadow:inset 0 0 0 2px rgb(var(--accent))}}
{panels_css}
</style><style id=sidebar-theme>{sidebar_css}</style><style id=vault-look>{look_css}</style></head><body class="{body_class}"><div class=shell><aside id=vault-side><div class=brand><img class=mark src=/onyx-mark.png alt=""><span class=brand-name>{vault_name}</span>{add_toggle}<button id=side-pin class=side-toggle type=button aria-pressed=true title="Unpin sidebar (⌘\\)" aria-label="Pin sidebar" aria-controls=vault-side>{PIN_ICON}</button></div>
<nav class=vault-switch aria-label="Library and vaults"><a href="/"{library_active} data-kind=library>Library</a><a href="/vault"{notes_active} data-kind=notes>Notes</a><a href="/vault?vault=html"{html_active} data-kind=html>Artifacts</a></nav>
{add_panel}
<input id=vault-filter type=search placeholder="Filter {units}… (press /)" autocomplete=off spellcheck=false aria-label="Filter {units}">
<nav id=tree aria-label="{tree_label}"><div class=none>Loading…</div></nav>
<div class=aside-foot><span id=vault-count>v{version}</span><button id=open-history class=foot-btn type=button title="Recent conversations (⌘Y)" aria-label="Recent conversations">{history_icon}</button><button id=open-settings class=foot-btn type=button title="Settings (⌘,)" aria-label="Settings">{settings_icon}</button></div></aside>
<main id=reader-pane><div id=reader-empty{empty_hidden}><div><span id=empty-hint>{empty_hint}</span><br><small>Select any passage inside it to ask.</small></div></div>
<section id=home aria-label="Library"{home_hidden}><div class=home-inner>
<form id=open-form class=open-row><input id=open-src placeholder="Open a file or URL — HTML, Markdown, text, PDF, or https://…" spellcheck=false autocomplete=off aria-label="Document URL or local file"><button type=button class="secondary pick" data-pick=file data-target=open-src>Choose…</button><button class=primary>Open</button></form>
<details class=open-context><summary>Context folder: <span id=open-folder-label>{short_folder}</span></summary><div class=row><input id=open-folder value="{default_folder}" spellcheck=false autocomplete=off aria-label="Context folder"><button type=button class="secondary pick" data-pick=folder data-target=open-folder>Choose…</button></div><p class=field-help>Only files inside this folder are available to the provider as evidence. Notes and Artifacts bring their own.</p></details>
<div class=home-h><h2>Recently opened</h2></div><div id=home-docs class=home-grid></div>
<div class=home-h><h2>Recent asks</h2><button type=button id=home-all class=link>See all</button></div><div id=home-asks class=home-list></div>
</div></section>
<iframe id=reader name=reader src="{initial}" aria-label="Reader"></iframe></main></div><div id=side-edge aria-hidden=true></div><div id=peek role=tooltip hidden></div>
{panels_html}
<script src=/app-menu.js></script>
<script>
let KIND={json.dumps(kind)}; const TOKEN={token}; const INITIAL_SRC={initial_src}; const $=s=>document.querySelector(s); const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const native=!!(window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.askwPick); if(native)document.body.classList.add('native');
{glass_js}
// What each view calls itself; a switch (switchVault, below) moves KIND and everything that hangs off it in place. Library
// is both vaults at once, each tree under its own heading, so every row says which vault it belongs to (data-vault).
const VAULTS={vaults_json},GROUPS={groups_json},BOTH=['notes','html']; let HTML,KEY,UNIT,VAULT;
function keyOf(k){{return 'askw:vault:'+(k==='notes'?'':k+':')}}
function setKind(k){{KIND=k;HTML=k==='html';KEY=keyOf(k);VAULT=VAULTS[k];UNIT=VAULT.unit}} setKind(KIND);
const tree=$('#tree'),reader=$('#reader'),filter=$('#vault-filter'),empty=$('#reader-empty'),home=$('#home'); const TREES={{}};
function store(k,v){{try{{localStorage.setItem(k,v)}}catch(e){{}}}} function recall(k){{try{{return localStorage.getItem(k)}}catch(e){{return null}}}}
// Notes remember which folders are OPEN (default shut, the vault is large); Artifacts remembers which are CLOSED (default
// open, so a project reads at a glance). Library shows those same two trees, and remembers which of its headings is shut.
const FOLDS={{notes:new Set(),html:new Set(),library:new Set()}};
function foldKey(k){{return keyOf(k)+(k==='notes'?'open':'closed')}} for(const k in FOLDS){{try{{for(const p of JSON.parse(recall(foldKey(k))||'[]'))FOLDS[k].add(p)}}catch(e){{}}}}
function isOpen(k,path){{return k==='notes'?FOLDS.notes.has(path):!FOLDS[k].has(path)}}
function setOpen(k,path,open){{const f=FOLDS[k];if(k==='notes'?open:!open)f.add(path);else f.delete(path);store(foldKey(k),JSON.stringify([...f]))}}
async function api(url){{const r=await fetch(url);const d=await r.json();if(!r.ok||d.ok===false)throw new Error(d.error||`HTTP ${{r.status}}`);return d}}
function rootOf(k){{const d=TREES[k];return d&&!d.error?d.root:''}}
function viewHref(path,k){{const r=k==='notes'&&rootOf('notes');return '/view?src='+encodeURIComponent(path)+(r?'&folder='+encodeURIComponent(r):'')}}
function ago(ts){{if(!ts)return'';const d=Math.max(0,Date.now()/1000-ts);if(d<3600)return Math.max(1,Math.floor(d/60))+'m';if(d<86400)return Math.floor(d/3600)+'h';if(d<86400*14)return Math.floor(d/86400)+'d';if(d<86400*120)return Math.floor(d/604800)+'w';return new Date(ts*1000).toLocaleDateString(undefined,{{month:'short',year:'2-digit'}})}}
function label(n,k){{return k==='html'?(n.title||n.name):n.name.replace(/\\.(md|markdown)$/i,'')}} function badge(ext){{return /^\\.(md|markdown)$/i.test(ext)?'':`<span class=ext>${{esc(ext.replace('.',''))}}</span>`}}
// Rows carry only a label and their vault; what a page is (title, summary, folder, kind, age) waits in NODES for the hover card.
const ICON={icons_json}; const NODES=new Map();
function fileRow(n,crumbs,k){{NODES.set(n.path,{{n,crumbs,k}});if(n.missing)return `<li><span class="file missing" data-path="${{esc(n.path)}}" data-vault=${{k}} tabindex=0><span class=lbl>${{esc(label(n,k))}}</span><span class=ext>missing</span></span></li>`;return `<li><a class=file target=reader href="${{esc(viewHref(n.path,k))}}" data-path="${{esc(n.path)}}" data-vault=${{k}}><span class=lbl>${{esc(label(n,k))}}</span>${{k==='html'?'':badge(n.ext||'')}}</a></li>`}}
function dirRow(n,crumbs,k){{const inside=crumbs.concat(n.name);return `<li><details data-path="${{esc(n.path)}}" data-vault=${{k}}${{isOpen(k,n.path)?' open':''}}><summary title="${{esc(n.path)}}">${{ICON.chev}}<span class=fold>${{ICON.shut}}${{ICON.open}}</span><span class=lbl>${{esc(n.name)}}</span>${{n.symlink?'<span class=sym title="Linked folder">↗</span>':''}}</summary><ul>${{n.children.map(c=>render(c,inside,k)).join('')}}</ul></details></li>`}}
// Artifacts lists a folder only once a page sits somewhere beneath it; + still offers an empty one (destinations walks the tree).
function hasPages(n){{return n.kind!=='dir'||n.children.some(hasPages)}}
function render(n,crumbs,k){{return n.kind!=='dir'?fileRow(n,crumbs,k):(k==='html'&&!hasPages(n))?'':dirRow(n,crumbs,k)}}
function topRows(k){{const d=TREES[k],top=k==='html'?d.tree.children.filter(hasPages):d.tree.children;return top.map(c=>render(c,[],k)).join('')}}
// Library: each vault under its own heading; a vault not set up says so under its heading rather than vanishing.
function groupRow(k){{const d=TREES[k];let body='';if(d&&d.error)body=`<li class=none>${{esc(d.error)}} <a href="#settings" data-settings=set-vaults>Open Settings</a></li>`;else if(d)body=topRows(k)||`<li class=none>${{k==='html'?'No artifacts yet.':'No notes found.'}}</li>`;return `<li class=group><details data-group=${{k}}${{isOpen('library',k)?' open':''}}><summary class=group-head><span class=lbl>${{GROUPS[k]}}</span>${{d&&!d.error?`<span class=count>${{d.files}}</span>`:''}}</summary><ul>${{body}}</ul></details></li>`}}
function renderTree(){{hidePeek();NODES.clear();if(KIND==='library')tree.innerHTML='<ul class=root>'+BOTH.map(groupRow).join('')+'</ul>';else{{const d=TREES[KIND];if(!d||d.error)return;const rows=topRows(KIND);tree.innerHTML=rows?'<ul class=root>'+rows+'</ul>':`<div class=none>${{HTML?'No artifacts yet. Use + to link pages or a folder of them.':'No notes found.'}}</div>`}}
tree.querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{{if(d.dataset.group)setOpen('library',d.dataset.group,d.open);else setOpen(d.dataset.vault,d.dataset.path,d.open)}}));highlight(currentSrc());applyTints();if(HTML)fillDestinations()}}
function currentSrc(){{try{{const l=reader.contentWindow.location;if(!l||!l.href||l.href==='about:blank')return '';return new URLSearchParams(l.search).get('src')||''}}catch(e){{return ''}}}}
function readerPage(){{try{{const h=reader.contentWindow.location.href;return h&&h!=='about:blank'?h:''}}catch(e){{return ''}}}}
function highlight(src){{tree.querySelectorAll('a.active').forEach(a=>a.classList.remove('active'));if(!src)return;const a=tree.querySelector(`a[data-path="${{CSS.escape(src)}}"]`);if(!a)return;a.classList.add('active');let p=a.parentElement;while(p&&p!==tree){{if(p.tagName==='DETAILS'&&!p.open)p.open=true;p=p.parentElement}}a.scrollIntoView({{block:'nearest'}})}}
// Both vaults' trees are kept (TREES), so a switch shows the other at once; each is fetched again behind it, and the
// list is redrawn only if that brought something new. Library shows the two together.
async function fetchTree(k){{try{{TREES[k]=await api('/api/vault/tree?vault='+k)}}catch(e){{TREES[k]={{error:e.message}}}}return TREES[k]}}
function countText(){{if(KIND==='library')return BOTH.map(k=>{{const d=TREES[k];return d&&!d.error?d.files+' '+VAULTS[k].unit+(d.files===1?'':'s'):''}}).filter(Boolean).join(' · ')||'No vaults set up';const d=TREES[KIND];return d.files+' '+UNIT+(d.files===1?'':'s')+(d.missing?' · '+d.missing+' missing':'')+(d.truncated?' (truncated)':'')}}
function showTree(){{if(KIND!=='library'){{const d=TREES[KIND];if(!d)return;if(d.error){{tree.innerHTML=`<div class=none>${{esc(d.error)}} <a href="#settings" data-settings=set-vaults>Open Settings</a></div>`;$('#vault-count').textContent=HTML?'no Artifacts folder':'no vault';return}}}}$('#vault-count').textContent=countText();renderTree()}}
function sameTree(a,b){{return !!a&&!!b&&a.error===b.error&&a.files===b.files&&a.missing===b.missing&&a.truncated===b.truncated&&JSON.stringify(a.tree)===JSON.stringify(b.tree)}}
async function loadTree(){{const k=KIND,ks=k==='library'?BOTH:[k],was=ks.map(x=>TREES[x]),got=await Promise.all(ks.map(fetchTree));if(k===KIND&&got.some((d,i)=>!sameTree(was[i],d)))showTree()}}
// After a vault folder changes in Settings: both trees again, and the home page, whose tags come from them.
async function reloadTrees(){{await Promise.all(BOTH.map(fetchTree));showTree();if(!home.hidden)loadHome()}}
// The vault a page lives in: the one whose root it sits under (the deeper, should one hold the other); '' for neither.
function vaultOf(src){{let best='',depth=0;for(const k in TREES){{const r=TREES[k].root;if(r&&r.length>depth&&src.startsWith(r+'/')){{best=k;depth=r.length}}}}return best}}
// MARK: sidebar — pinned or unpinned, remembered across every view ("collapsed" is the old word for unpinned). Unpinned,
// it comes out after a beat on the left edge and goes a moment after the pointer leaves, unless it is in use: a row menu
// open, the + panel open, or typing in one of its fields. ⌘\\ pins and unpins, also while focus is inside the reader.
const SIDE_KEY='askw:vault:sidebar', side=$('#vault-side'), pin=$('#side-pin'), edge=$('#side-edge'); let sideOver=false, sideTimer=0;
function pinned(){{return !document.body.classList.contains('side-unpinned')}}
function sideOut(out){{document.body.classList.toggle('side-out',out);side.inert=!pinned()&&!out;if(!out)hidePeek()}}
function inUse(){{const a=document.activeElement,p=$('#add-panel');return !!((window.OnyxMenu&&OnyxMenu.isOpen())||(p&&!p.hidden)||(a&&side.contains(a)&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)))}}
function sideLater(){{clearTimeout(sideTimer);if(pinned())return;sideTimer=setTimeout(function check(){{if(pinned()||sideOver)return;if(inUse()){{sideTimer=setTimeout(check,400);return}}sideOut(false)}},400)}}
function setPinned(on){{document.body.classList.toggle('side-unpinned',!on);store(SIDE_KEY,on?'':'unpinned');pin.setAttribute('aria-pressed',String(on));pin.title=(on?'Unpin':'Pin')+' sidebar (⌘\\\\)';clearTimeout(sideTimer);sideOut(!on&&(sideOver||inUse()));if(!on&&!sideOver)sideLater()}}
function sideKey(e){{if(e.key==='\\\\'&&(e.metaKey||e.ctrlKey)&&!e.altKey&&!e.shiftKey){{e.preventDefault();setPinned(!pinned())}}}}
pin.onclick=()=>setPinned(!pinned()); document.addEventListener('keydown',sideKey);
edge.addEventListener('mouseenter',()=>{{clearTimeout(sideTimer);sideTimer=setTimeout(()=>sideOut(true),40)}}); edge.addEventListener('mouseleave',e=>{{if(side.contains(e.relatedTarget))return;clearTimeout(sideTimer);if(document.body.classList.contains('side-out'))sideLater()}});
side.addEventListener('mouseenter',()=>{{sideOver=true;clearTimeout(sideTimer)}}); side.addEventListener('mouseleave',()=>{{sideOver=false;sideLater()}}); side.addEventListener('focusout',()=>{{if(!sideOver)sideLater()}});
// MARK: reader — what shows in the reader's place, and the window's URL and title, follow whatever the reader loads.
// History that crosses into the other vault (back past a switch) brings the sidebar along; a link inside a page doesn't.
function traversed(){{try{{const n=reader.contentWindow.performance.getEntriesByType('navigation')[0];return !!n&&n.type==='back_forward'}}catch(e){{return false}}}}
// Library rests on its home page, in the reader's place; Notes and Artifacts say what to pick instead.
function syncOverlays(){{const page=!!readerPage(),lib=KIND==='library',show=lib&&!page;empty.hidden=lib||page;if(show&&home.hidden)loadHome();home.hidden=!show}}
function shellUrl(k,src,folder){{const p=new URLSearchParams();if(k==='html')p.set('vault','html');if(src)p.set('src',src);if(folder)p.set('folder',folder);const q=p.toString();return (k==='library'?'/':'/vault')+(q?'?'+q:'')}}
function readerFolder(){{try{{return new URLSearchParams(reader.contentWindow.location.search).get('folder')||''}}catch(e){{return ''}}}}
reader.addEventListener('load',()=>{{try{{reader.contentWindow.addEventListener('keydown',sideKey)}}catch(e){{}}syncOverlays();const src=currentSrc();if(!src){{if(!readerPage()){{history.replaceState(null,'',shellUrl(KIND,''));document.title=VAULT.name}}return}}
const k=vaultOf(src);if(k&&KIND!=='library'&&k!==KIND&&traversed())switchVault(k,true);highlight(src);history.replaceState(null,'',shellUrl(KIND,src,KIND==='library'&&!k?readerFolder():''));let t='';try{{t=reader.contentDocument.title}}catch(e){{}}document.title=(t||src.split('/').pop())+' — '+VAULT.name;if(k)store(keyOf(k)+'last',src)}});
// A saved document or conversation, into the reader: by its vault path when it lives in a vault (the tree highlights it,
// and a page in Artifacts keeps its link's context), else as it was read, with the folder it was read with.
function itemHref(item,action){{const src=item.source||item.document_source||'',p=new URLSearchParams();let base='/view';
if(src.startsWith('service://selection/')){{base='/quick';p.set('text',item.selection||'');if(item.folder)p.set('folder',item.folder)}}else{{p.set('src',item.vault_path||src);const folder=item.vault==='notes'?(rootOf('notes')||item.folder):item.vault==='html'?'':item.folder;if(folder)p.set('folder',folder)}}
if(action){{p.set('history',item.request_id);p.set('history_action',action)}}return base+'?'+p}}
function openItem(item,action){{if(KIND!=='library'&&(item.vault||'')!==KIND)switchVault('library',true);home.hidden=true;empty.hidden=true;reader.src=itemHref(item,action)}}
let filterTimer; filter.oninput=()=>{{clearTimeout(filterTimer);filterTimer=setTimeout(applyFilter,150)}};
async function applyFilter(){{const q=filter.value.trim(),k=KIND;if(q.length<2){{renderTree();return}}const lib=k==='library',kinds=lib?BOTH.filter(rootOf):[k];
try{{const found=await Promise.all(kinds.map(vk=>api('/api/vault/search?vault='+vk+'&q='+encodeURIComponent(q)+(lib?'&limit=25':'')).then(d=>d.items.map(i=>({{...i,k:vk}})))));if(k!==KIND)return;const items=found.flat();hidePeek();NODES.clear();items.forEach(i=>NODES.set(i.path,{{n:i,crumbs:(i.folder||'').split('/').filter(Boolean),k:i.k}}));
tree.innerHTML='<ul class="root results">'+items.map(i=>`<li><a class=file target=reader href="${{esc(viewHref(i.path,i.k))}}" data-path="${{esc(i.path)}}" data-vault=${{i.k}}><span class=lbl>${{esc(i.k==='html'?(i.title||i.name):label(i,i.k))}}</span><small>${{esc(lib?GROUPS[i.k]+(i.folder?' › '+i.folder:''):(i.folder||'/'))}}</small></a></li>`).join('')+(items.length?'':`<li class=none>${{lib?'Nothing matches.':'No '+UNIT+'s match.'}}</li>`)+'</ul>';highlight(currentSrc())}}catch(e){{if(k===KIND)tree.innerHTML=`<div class=none>${{esc(e.message)}}</div>`}}}}
document.addEventListener('keydown',e=>{{const typing=/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement&&document.activeElement.tagName);if(e.key==='/'&&!typing&&!e.metaKey&&!e.ctrlKey){{e.preventDefault();if(!pinned())sideOut(true);filter.focus();filter.select()}}else if(e.key==='Escape'&&document.activeElement===filter){{filter.value='';applyFilter();filter.blur()}}}});
// MARK: row menu — the app's rendered menu (static/app-menu.js). The server says what a row really is (its real file, and
// the link on the way); ⌥ turns each Reveal into a Copy. A mousedown while the answer is in flight means it came too late.
let menuSeq=0; document.addEventListener('mousedown',()=>{{menuSeq++}},true);
tree.addEventListener('contextmenu',async e=>{{const row=e.target.closest('#tree .file, #tree summary');if(!row||!window.OnyxMenu)return;e.preventDefault();
// WebKit on macOS selects the word under a right-click before this event fires (for Look Up); a row isn't text to select.
const sel=getSelection();if(sel&&sel.anchorNode&&row.contains(sel.anchorNode))sel.removeAllRanges();const holder=row.closest('[data-vault]'),vk=holder&&holder.dataset.vault,path=row.dataset.path||row.parentElement.dataset.path;if(!path||!vk)return;
let x=e.clientX,y=e.clientY;if(!x&&!y){{const r=row.getBoundingClientRect();x=r.left+16;y=r.bottom}}
const seq=++menuSeq;let d;try{{d=await api('/api/vault/entry?vault='+vk+'&path='+encodeURIComponent(path))}}catch(err){{OnyxMenu.toast(err.message,'bad');return}}if(seq!==menuSeq)return;
const items=[];if(!d.is_dir)items.push({{id:'open',label:'Open',enabled:d.exists}});
items.push({{id:'reveal',label:'Reveal in Finder',enabled:!!d.real,alt:{{id:'copy',label:'Copy Path'}}}});
if(d.link)items.push({{id:'reveal-link',label:'Reveal Link in Finder',alt:{{id:'copy-link',label:'Copy Link Path'}}}});
OnyxMenu.open({{items,x,y,label:(d.is_dir?'Folder':vk==='html'?'Page':'Note')+' actions',returnFocus:row,onClose:()=>row.classList.remove('menu-for'),onSelect:id=>rowAction(id,d,row,vk)}});row.classList.add('menu-for')}});
function copyPath(p){{if(!navigator.clipboard)throw new Error('The clipboard is not available here.');return navigator.clipboard.writeText(p).then(()=>OnyxMenu.toast('Copied '+shortPath(p)))}}
async function rowAction(id,d,row,vk){{try{{if(id==='open'){{if(row.tagName==='A')row.click();else reader.src=viewHref(d.path,vk)}}else if(id==='copy')await copyPath(d.real);else if(id==='copy-link')await copyPath(d.path);else if(id==='reveal'||id==='reveal-link')await postJSON('/api/vault/reveal',{{vault:vk,path:d.path,which:id==='reveal'?'real':'link'}})}}catch(err){{OnyxMenu.toast(err.message||String(err),'bad')}}}}
// MARK: add panel (Artifacts only; CSS keeps it out of the other views)
const DEST_KEY=keyOf('html')+'dest';
function destinations(){{const out=[{{rel:'',label:'Top level'}}],d=TREES.html;(function walk(n,depth){{for(const c of n.children||[]){{if(c.kind==='dir'&&!c.linked){{out.push({{rel:c.rel,label:'\\u00a0'.repeat(depth*3)+c.name}});walk(c,depth+1)}}}}}})(d&&d.tree||{{children:[]}},0);return out}}
function fillDestinations(){{const sel=$('#add-dest');if(!sel)return;const keep=sel.value||recall(DEST_KEY)||'';const opts=destinations();sel.innerHTML=opts.map(o=>`<option value="${{esc(o.rel)}}">${{esc(o.label)}}</option>`).join('');sel.value=opts.some(o=>o.rel===keep)?keep:''}}
function addStatus(text,tone){{const el=$('#add-status');if(!el)return;el.textContent=text;el.className='field-help'+(tone?' '+tone:'')}}
async function postJSON(url,body){{const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:TOKEN,...body}})}});const d=await r.json().catch(()=>({{ok:false,error:'HTTP '+r.status}}));if(!r.ok||d.ok===false)throw new Error(d.error||'HTTP '+r.status);return d}}
function shortPath(p){{return String(p).replace(/^\\/Users\\/[^/]+/,'~')}}
async function linkTargets(targets){{targets=(targets||[]).filter(Boolean);if(!targets.length)return;addStatus('Linking…');try{{const d=await postJSON('/api/vault/html/link',{{parent:$('#add-dest').value,targets}});let msg='Linked '+d.linked.length+(d.linked.length===1?' item':' items');if(d.context_roots.length)msg+=' · answers can now cite '+d.context_roots.map(shortPath).join(', ');if(d.errors.length)msg+=' · skipped: '+d.errors.join('; ');addStatus(msg,d.errors.length?'bad':'ok');$('#add-path').value='';await loadTree()}}catch(e){{addStatus(e.message,'bad')}}}}
{{const toggle=$('#add-toggle'),panel=$('#add-panel');toggle.onclick=()=>{{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)fillDestinations()}};
$('#add-dest').onchange=e=>store(DEST_KEY,e.target.value);
$('#add-pick-files').onclick=async()=>{{try{{const picked=await window.webkit.messageHandlers.askwPick.postMessage({{kind:'html',initial:''}});await linkTargets(Array.isArray(picked)?picked:[picked])}}catch(e){{addStatus(e.message,'bad')}}}};
$('#add-pick-folder').onclick=async()=>{{try{{const picked=await window.webkit.messageHandlers.askwPick.postMessage({{kind:'folder',initial:'',prompt:'Link Folder',message:'Choose a folder of HTML pages to link into Artifacts'}});await linkTargets([picked])}}catch(e){{addStatus(e.message,'bad')}}}};
$('#add-path-go').onclick=()=>linkTargets([$('#add-path').value.trim()]);$('#add-path').onkeydown=e=>{{if(e.key==='Enter'){{e.preventDefault();linkTargets([e.target.value.trim()])}}}};
async function makeFolder(){{const name=$('#add-folder-name').value.trim();if(!name)return;try{{const d=await postJSON('/api/vault/html/folder',{{parent:$('#add-dest').value,name}});$('#add-folder-name').value='';store(DEST_KEY,d.rel);addStatus('Created '+d.rel+' · it appears once it holds a page','ok');await loadTree()}}catch(e){{addStatus(e.message,'bad')}}}}
$('#add-mkdir').onclick=makeFolder;$('#add-folder-name').onkeydown=e=>{{if(e.key==='Enter'){{e.preventDefault();makeFolder()}}}}}}
// MARK: Obsidian look — while the vault's file-explorer look is in force (body.obsidian-tree, CSS from /api/sidebar-theme),
// each top-level folder takes its own Obsidian colour: by name in Notes, else by position — Artifacts always by position,
// since its folder names never match the vault's. Kept live, like the reader's Markdown styles.
let SIDE_THEME={sidebar_state};
function applyTints(){{const on=document.body.classList.contains('obsidian-tree'),list=SIDE_THEME.folders||[],byName=new Map(list.map(f=>[f.name.toLowerCase(),f]));
const tops=KIND==='library'?BOTH.map(k=>[k,tree.querySelectorAll(`:scope > ul.root > li.group > details[data-group=${{k}}] > ul > li > details`)]):[[KIND,tree.querySelectorAll(':scope > ul.root > li > details')]];
for(const [k,rows] of tops)rows.forEach((d,i)=>{{const li=d.parentElement,name=(d.querySelector(':scope > summary .lbl')||{{}}).textContent||'';
const f=on&&list.length?((k==='notes'&&byName.get(name.toLowerCase()))||list[i%list.length]):null;
for(const [prop,value] of [['--folder-color',f&&f.color],['--guide-color',f&&(f.guide||f.color)],['--folder-hover',f&&f.hover]]){{if(value)li.style.setProperty(prop,value);else li.style.removeProperty(prop)}}}})}}
// MARK: vault look — the whole app in the vault's colours (vault_look) while Match vault appearance is on: its tokens on
// :root.vault-look, and the glass and the native window in the vault's mode and ground. Kept live with the sidebar's look;
// `force` is Settings turning it on or off, when the revision last drawn may be the one there is now.
let LOOK={look_state};
function syncAppearance(){{const h=native&&window.webkit.messageHandlers.askwAppearance;if(h)Promise.resolve(h.postMessage({{theme:GLASS.vault?GLASS.vault.mode:(document.documentElement.dataset.theme||'system')}})).catch(()=>{{}})}}
function applyLook(d){{LOOK=d;$('#vault-look').textContent=d.css||'';document.documentElement.classList.toggle('vault-look',!!d.css);setGlassVault(d.css?d:null);syncAppearance();document.dispatchEvent(new Event('onyx:look'))}}
async function syncSidebarTheme(force){{if(document.hidden&&!force)return;try{{const [d,l]=await Promise.all([api('/api/sidebar-theme'),api('/api/vault-look')]);if(force||l.revision!==LOOK.revision)applyLook(l);if(!force&&d.revision===SIDE_THEME.revision)return;SIDE_THEME=d;$('#sidebar-theme').textContent=d.css||'';document.body.classList.toggle('obsidian-tree',!!d.css);applyTints()}}catch(e){{}}}}
setInterval(syncSidebarTheme,3000); document.addEventListener('visibilitychange',()=>syncSidebarTheme());
// MARK: hover card — a page's whole title, its one line, where it lives, what it is. The first hover waits a beat; after
// that it follows the pointer row to row at once. A click, scroll, right-click menu, Escape, or leaving puts it away.
const peek=$('#peek'); let peekRow=null, peekTimer=0, peekWarmUntil=0;
function updated(ts){{const a=ago(ts);return !a?'':/\\d[mhdw]$/.test(a)?'Updated '+a+' ago':'Updated '+a}}
function kindOf(n,k){{if(k==='html')return 'HTML page';const x=(n.ext||(/\\.[^.]+$/.exec(n.name||'')||[''])[0]).replace('.','').toUpperCase();return /^(MD|MARKDOWN)$/.test(x)?'Markdown note':x?x+' file':'Note'}}
function showPeek(row){{const e=NODES.get(row.dataset.path);if(!e||(window.OnyxMenu&&OnyxMenu.isOpen()))return;const n=e.n;
if(peekRow&&peekRow!==row)peekRow.removeAttribute('aria-describedby');peekRow=row;row.setAttribute('aria-describedby','peek');
const where=(KIND==='library'?[GROUPS[e.k]]:[]).concat(e.crumbs).join(' › ')||VAULTS[e.k].name,what=n.missing?'Link target is missing: '+shortPath(n.target||n.name):[kindOf(n,e.k),updated(n.mtime)].filter(Boolean).join(' · ');
peek.innerHTML=`<p class=peek-title>${{esc(label(n,e.k))}}</p>${{n.summary?`<p class=peek-sum>${{esc(n.summary)}}</p>`:''}}<p class=peek-row>${{ICON.folder}}<span>${{esc(where)}}</span></p><p class=peek-row>${{n.missing?ICON.link:ICON.doc}}<span>${{esc(what)}}</span></p>`;
peek.hidden=false;const r=row.getBoundingClientRect(),side=$('#vault-side').getBoundingClientRect(),w=peek.offsetWidth,h=peek.offsetHeight,beside=side.right+10+w<=innerWidth-8;
peek.style.left=(beside?side.right+10:Math.max(8,Math.min(r.left,innerWidth-w-8)))+'px';peek.style.top=Math.max(8,Math.min(beside?r.top-4:r.bottom+6,innerHeight-h-8))+'px';requestAnimationFrame(()=>peek.classList.add('show'))}}
function hidePeek(){{clearTimeout(peekTimer);if(peekRow){{peekRow.removeAttribute('aria-describedby');peekRow=null}}if(!peek.hidden){{peekWarmUntil=Date.now()+350;peek.classList.remove('show');peek.hidden=true}}}}
function wantPeek(row,delay){{clearTimeout(peekTimer);if(row===peekRow)return;peekTimer=setTimeout(()=>showPeek(row),!peek.hidden||Date.now()<peekWarmUntil?0:delay)}}
tree.addEventListener('mouseover',e=>{{const row=e.target.closest('#tree .file');if(row)wantPeek(row,450)}});
tree.addEventListener('mouseout',e=>{{const row=e.target.closest('#tree .file');if(row&&!row.contains(e.relatedTarget)){{clearTimeout(peekTimer);peekTimer=setTimeout(hidePeek,90)}}}});
tree.addEventListener('focusin',e=>{{const row=e.target.closest('#tree .file');if(row&&row.matches(':focus-visible'))wantPeek(row,200)}});
for(const ev of ['focusout','scroll','click','contextmenu'])tree.addEventListener(ev,hidePeek,{{passive:true}}); document.addEventListener('keydown',e=>{{if(e.key==='Escape')hidePeek()}}); window.addEventListener('blur',hidePeek);
{panels_js}
// MARK: home — Library's page while nothing is open: open a file or URL, what you had open lately (whatever it was), and
// your latest asks. Fetched fresh each time it shows; a card is a plain link into the reader, an ask opens its conversation.
function docTag(d){{return d.vault==='notes'?'Note':d.vault==='html'?'Artifact':({{pdf:'PDF',markdown:'Markdown',text:'Text',html:'HTML','remote-html':'Web'}})[d.kind]||'File'}}
function docWhere(d){{if(d.vault)return [GROUPS[d.vault]].concat(d.vault_folder?d.vault_folder.split('/'):[]).join(' › ');if(/^https?:/i.test(d.source))try{{return new URL(d.source).host}}catch(e){{}}return shortPath(d.source.replace(/\\/[^/]*$/,''))}}
function docCard(d){{return `<a class=home-card target=reader href="${{esc(itemHref(d,''))}}" title="${{esc(d.vault_path||d.source)}}"><span class=t>${{esc(d.title)}}</span><span class=m><span class="tag ${{esc(d.vault||'')}}">${{esc(docTag(d))}}</span><span class=w>${{esc(docWhere(d))}}</span><span class=a>${{esc(ago(d.last_opened_at))}}</span></span></a>`}}
function askRow(c){{return `<button type=button class=home-ask data-id="${{esc(c.request_id)}}"><span class=t><strong>${{esc(c.document_title||'Untitled')}}</strong><span class=a>${{esc(ago(c.started_at))}}</span></span><span class=q>${{esc(c.question||({{ask:'Question',eli5:'ELI5',prove:'Prove it'}})[c.action]||c.action)}}</span></button>`}}
async function loadHome(){{try{{const d=await api('/api/library'),docs=(d.documents||[]).slice(0,9),asks=(d.conversations||[]).slice(0,6);PANELS.remember(d.conversations);
$('#home-docs').innerHTML=docs.length?docs.map(docCard).join(''):'<div class=home-empty>Documents you open will appear here.</div>';
$('#home-asks').innerHTML=asks.length?asks.map(askRow).join(''):'<div class=home-empty>Your completed answers will be saved here.</div>'}}catch(e){{$('#home-docs').innerHTML=`<div class=home-empty>${{esc(e.message)}}</div>`}}}}
$('#home-asks').addEventListener('click',e=>{{const b=e.target.closest('[data-id]');if(b)PANELS.showConversation(b.dataset.id)}}); $('#home-all').onclick=()=>PANELS.openHistory();
$('#open-form').onsubmit=e=>{{e.preventDefault();let s=$('#open-src').value.trim(),hash='';if(!s)return;const m=s.match(new RegExp('^((?:file://|/|~).*[.](?:html?|md|markdown|txt|pdf))(#[^/]*)$','i'));if(m){{s=m[1];hash=m[2]}}const vk=vaultOf(s),f=$('#open-folder').value.trim();home.hidden=true;reader.src=(vk?viewHref(s,vk):'/view?src='+encodeURIComponent(s)+(f?'&folder='+encodeURIComponent(f):''))+hash;$('#open-src').value=''}};
$('#open-folder').oninput=e=>{{$('#open-folder-label').textContent=shortPath(e.target.value.trim())||'the default folder'}};
home.querySelectorAll('[data-pick]').forEach(b=>b.onclick=async()=>{{const el=$('#'+b.dataset.target);try{{const p=await window.webkit.messageHandlers.askwPick.postMessage({{kind:b.dataset.pick,initial:el.value}});if(p){{el.value=p;el.dispatchEvent(new Event('input'))}}}}catch(e){{OnyxMenu.toast(e.message,'bad')}}}});
// Library shown again while already there goes home: the reader empties and the home page comes back.
function goHome(){{if(readerPage())reader.src='about:blank';highlight('');empty.hidden=true;home.hidden=false;loadHome();history.replaceState(null,'','/');document.title=VAULT.name}}
$('#open-settings').onclick=()=>PANELS.openSettings(); $('#open-history').onclick=()=>PANELS.openHistory();
tree.addEventListener('click',e=>{{const a=e.target.closest('[data-settings]');if(a){{e.preventDefault();PANELS.openSettings(a.dataset.settings)}}}});
// MARK: switch — Library ⇄ Notes ⇄ Artifacts in place. The pill slides and the width glides (CSS, off the body's kind
// class), the list swaps from TREES with a quick fade and is fetched again behind it, and the reader brings back that
// vault's last page, or, for Library, the home page. `follow` means the reader has already moved (history, or a saved
// conversation opening): then only the sidebar does. A modified click, or a page without script, still loads the link.
const switchLinks=[...document.querySelectorAll('.vault-switch a')],treeScroll={{}},stillMotion=matchMedia('(prefers-reduced-motion:reduce)');
function switchVault(k,follow){{if(!VAULTS[k])return;if(k===KIND){{if(k==='library'&&!follow)goHome();return}}
if(window.OnyxMenu)OnyxMenu.close();menuSeq++;hidePeek();clearTimeout(filterTimer);filter.value='';treeScroll[KIND]=tree.scrollTop;
setKind(k);document.body.classList.remove('kind-library','kind-notes','kind-html');document.body.classList.add('kind-'+k);
for(const a of switchLinks){{const on=a.dataset.kind===k;a.classList.toggle('active',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')}}
$('.brand-name').textContent=VAULT.name;filter.placeholder='Filter '+VAULT.units+'… (press /)';filter.setAttribute('aria-label','Filter '+VAULT.units);tree.setAttribute('aria-label',VAULT.tree);$('#empty-hint').textContent=VAULT.empty;
$('#add-panel').hidden=true;$('#add-toggle').setAttribute('aria-expanded','false');
const cached=(k==='library'?BOTH:[k]).every(x=>TREES[x]);if(cached){{showTree();tree.scrollTop=treeScroll[k]||0;highlight(currentSrc())}}else tree.innerHTML='<div class=none>Loading…</div>';
if(!stillMotion.matches)tree.animate([{{opacity:.4}},{{opacity:1}}],{{duration:120,easing:'ease-out'}});
const fresh=loadTree();if(follow){{syncOverlays();return}}if(k==='library'){{goHome();return}}
home.hidden=true;history.replaceState(null,'',shellUrl(k,''));document.title=VAULT.name;
(cached?Promise.resolve():fresh).then(()=>{{if(k!==KIND)return;const last=rootOf(k)?recall(KEY+'last'):null;empty.hidden=!!last;if(last)reader.src=viewHref(last,k);else if(readerPage())reader.src='about:blank'}})}}
for(const a of switchLinks)a.addEventListener('click',e=>{{if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();switchVault(a.dataset.kind)}});
// The app menu comes through these: File ▸ Library / Vault / Artifacts switch in place (and load the page when it isn't
// this one), and Settings… (⌘,) and Recent Conversations (⌘Y) open their dialogs.
window.onyxVault={{switchTo:k=>{{if(!VAULTS[k])return false;switchVault(k);return true}}}};
window.onyxShell={{openSettings:section=>PANELS.openSettings(section),openHistory:()=>PANELS.openHistory()}};
// Put back as it was left without a slide: the page opens with the sidebar already away.
if(/^(unpinned|collapsed)$/.test(recall(SIDE_KEY)||'')){{document.body.classList.add('side-still');setPinned(false);requestAnimationFrame(()=>requestAnimationFrame(()=>document.body.classList.remove('side-still')))}}
// Both trees load up front: Library draws them together, and the first switch is as instant as the rest.
const FIRST=KIND; if(FIRST==='library'&&!INITIAL_SRC)loadHome();
(FIRST==='library'?loadTree():fetchTree(FIRST).then(()=>{{if(KIND===FIRST)showTree()}})).then(()=>{{for(const k of BOTH)if(!TREES[k])fetchTree(k);if(KIND!==FIRST||FIRST==='library'||INITIAL_SRC||!rootOf(FIRST))return;const last=recall(KEY+'last');if(last)reader.src=viewHref(last,FIRST)}});
syncAppearance();
// A fragment names a dialog to open: #settings, #diagnostics, #history — which is where the old launcher's links land.
{{const h=location.hash.slice(1);if(/^(settings|diagnostics|history)$/.test(h)){{history.replaceState(null,'',location.pathname+location.search);if(h==='history')PANELS.openHistory();else PANELS.openSettings(h==='diagnostics'?'diagnostics':'')}}}}
</script></body></html>"""
