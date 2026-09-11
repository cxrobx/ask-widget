"""Vault mode shell: a persistent tree beside a same-origin reader iframe.

One shell, two vaults. **Notes** is the Obsidian vault (Markdown, wikilinks).
**HTML** is a folder of symlinks to HTML pages anywhere on disk: its top-level
folders are projects, pages are labelled by their ``<title>``, and the **+**
panel links more in (it only ever creates links and folders inside the vault —
see ``vault.writable_folder``).

The page is deliberately thin. Files are plain ``<a target=reader>`` links, so
the named iframe handles navigation and history without any click JS; the
script only builds the tree, keeps the highlight in sync with whatever the
reader currently shows, drives the filter box, the rows' right-click menu
(``static/app-menu.js``: Reveal in Finder, and ⌥ Copy Path), and (HTML) the
add panel.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .launcher_ui import glass_script, theme_settings, theme_style

# The sidebar's pin: filled while the sidebar is pinned, outlined while it floats and comes out from the left edge.
PIN_ICON = (
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path class="pin-head" d="M6.75 2.25v3.5L4.75 8.5h6.5l-2-2.75v-3.5z"/>'
    '<path d="M5.75 2.25h4.5M8 8.5v5.25"/></svg>'
)

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
    root: Path | None,
    src: str | None = None,
    reader_query: str | None = None,
    kind: str = "notes",
    sidebar: dict[str, Any] | None = None,
) -> str:
    _glass, theme = theme_settings(settings)
    # The vault's Obsidian file-explorer look (sidebar_theme), in force only when it has CSS.
    sidebar = sidebar or {}
    sidebar_css = sidebar.get("css") or ""
    sidebar_state = json.dumps({"revision": sidebar.get("revision", ""), "folders": sidebar.get("folders", [])}).replace("<", "\\u003c")
    body_class = f"kind-{kind}" + (" obsidian-tree" if sidebar_css else "")
    html_kind = kind == "html"
    version = html.escape(__version__)
    shared_style = theme_style(settings)
    glass_js = glass_script(settings)
    initial = html.escape(f"/view?{reader_query}", quote=True) if reader_query else "about:blank"
    initial_src = json.dumps(src or "")
    root_json = json.dumps(str(root) if root else "")
    token = json.dumps(config.token)
    vault_name = "Artifacts" if html_kind else "Vault"
    title = html.escape(f"{Path(src).name} — {vault_name}" if src else vault_name)
    notes_active = "" if html_kind else " class=active aria-current=page"
    html_active = " class=active aria-current=page" if html_kind else ""
    add_toggle = (
        '<button id=add-toggle class=add-toggle type=button title="Add pages or a folder" '
        'aria-controls=add-panel aria-expanded=false>+</button>'
        if html_kind
        else ""
    )
    add_panel = (
        """<div id=add-panel class=add-panel hidden><label for=add-dest>Add to</label><select id=add-dest></select>
<div class=add-row><button type=button class="secondary pick" id=add-pick-files>HTML files…</button><button type=button class="secondary pick" id=add-pick-folder>Folder…</button></div>
<div class=add-row><input id=add-path placeholder="Paste a path or file:// URL" spellcheck=false autocomplete=off><button type=button class=secondary id=add-path-go>Link</button></div>
<div class=add-row><input id=add-folder-name placeholder="New folder name" spellcheck=false autocomplete=off><button type=button class=secondary id=add-mkdir>Create</button></div>
<p id=add-status class=field-help>Links point at the originals; nothing is moved or copied.</p></div>"""
        if html_kind
        else ""
    )
    empty_hint = "Pick a page from the sidebar." if html_kind else "Pick a note from the tree."
    units = "pages" if html_kind else "notes"
    return f"""<!doctype html><html data-theme="{theme}"><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
{shared_style}
html,body{{height:100%;overflow:hidden}} button,input,select{{font:inherit}}
.shell{{grid-template-columns:260px minmax(0,1fr);height:100vh;min-height:0}}
aside{{display:flex;flex-direction:column;height:100vh;padding:20px 12px 14px;overflow:hidden}} body.native aside{{padding-top:48px}}
.brand{{margin:0 8px 12px}} .brand-name{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.add-toggle{{display:grid;place-items:center;width:24px;height:24px;padding:0;border:1px solid var(--line-soft);border-radius:7px;background:rgb(var(--ink)/.055);color:rgb(var(--secondary));font-size:16px;line-height:1}} .add-toggle:hover,.add-toggle[aria-expanded=true]{{background:rgb(var(--ink)/.1);color:rgb(var(--ink))}}
.vault-switch{{display:grid;grid-template-columns:1fr 1fr;gap:2px;margin:0 0 10px;padding:2px;border-radius:8px;background:rgb(var(--ink)/.06)}} .vault-switch a{{padding:4px 0;border-radius:6px;color:rgb(var(--secondary));font-size:12px;font-weight:600;text-align:center;text-decoration:none}} .vault-switch a:hover{{color:rgb(var(--ink))}} .vault-switch a.active{{background:rgb(var(--bg-elevated)/.92);color:rgb(var(--ink));box-shadow:0 1px 2px rgb(0 0 0/.12)}}
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
/* The hover card: the whole title, the page's one line, where it lives, what it is. App chrome, so it takes the app's theme. */
#peek{{position:fixed;z-index:50;width:max-content;min-width:220px;max-width:320px;padding:12px 14px;border:1px solid var(--line);border-radius:14px;background:rgb(var(--bg-elevated)/.94);color:rgb(var(--ink));box-shadow:0 18px 44px rgb(0 0 0/.26),inset 0 1px 0 rgb(255 255 255/.06);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3);pointer-events:none;opacity:0;transform:translateX(-4px);transition:opacity .12s ease,transform .12s ease}}
#peek.show{{opacity:1;transform:none}} #peek[hidden]{{display:none}} #peek p{{margin:0}} #peek .peek-title{{font-size:14px;font-weight:600;line-height:1.35}}
#peek .peek-sum{{margin-top:5px;color:rgb(var(--secondary));font-size:12.5px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
#peek .peek-row{{display:flex;align-items:center;gap:8px;margin-top:9px;color:rgb(var(--secondary));font-size:12.5px}} #peek .peek-row+.peek-row{{margin-top:5px}} #peek .peek-row svg{{flex:none;width:15px;height:15px;color:rgb(var(--muted))}} #peek .peek-row span{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
@media(prefers-reduced-motion:reduce){{#peek{{transition:none;transform:none}}}} @media(prefers-reduced-transparency:reduce){{#peek{{background:rgb(var(--bg-elevated));backdrop-filter:none;-webkit-backdrop-filter:none}}}}
.aside-foot{{position:static;margin-top:10px;padding:0 8px}} .aside-foot a{{color:inherit;text-decoration:none}} .aside-foot a:hover{{color:rgb(var(--ink))}}
main,body.native main{{position:relative;padding:0;overflow:hidden}}
#reader{{display:block;width:100%;height:100%;border:0;background:transparent}}
#reader-empty{{position:absolute;inset:0;display:grid;place-items:center;padding:24px;color:rgb(var(--muted));font-size:14px;text-align:center;pointer-events:none}} #reader-empty[hidden]{{display:none}} #reader-empty a{{pointer-events:auto;color:rgb(var(--accent))}}
/* Artifacts are named by sentence-length titles: a wider sidebar, one line each, and the whole title in the hover card. */
body.kind-html .shell{{grid-template-columns:290px minmax(0,1fr)}}
/* Sidebar: pinned, it sits in the grid; unpinned, it floats over the reader and comes out when the pointer rests on the
   left edge (#side-edge, laid over the reader because its iframe would swallow the pointer), as in Zen's compact mode. */
.side-toggle{{display:grid;place-items:center;flex:none;width:26px;height:24px;padding:0;border:0;border-radius:7px;background:transparent;color:rgb(var(--secondary))}} .side-toggle:hover{{background:rgb(var(--ink)/.08);color:rgb(var(--ink))}} .side-toggle svg{{width:16px;height:16px}} #side-pin[aria-pressed=true] .pin-head{{fill:currentColor}}
@media(max-width:800px){{.shell,body.kind-html .shell{{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}} aside,body.native aside{{position:static;height:auto;max-height:45vh;padding:12px 12px 8px}} body.native aside{{padding-top:38px}} .aside-foot{{display:block}}}}
#side-edge{{position:fixed;top:0;bottom:0;left:0;z-index:39;display:none;width:8px}} body.side-unpinned #side-edge{{display:block}}
body.side-unpinned .shell,body.side-unpinned.kind-html .shell{{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr)}}
/* Floating, it is a panel lying on the page: inset, rounded, shadowed, and on a ground thick enough to read over a page's
   text; the window's glass is too thin for that (the Obsidian look already brings an opaque ground of its own). */
body.side-unpinned #vault-side{{position:fixed;top:8px;bottom:8px;left:8px;z-index:40;width:min(260px,86vw);height:auto;max-height:none;padding-top:12px;border:1px solid var(--line);border-radius:12px;box-shadow:0 18px 50px rgb(0 0 0/.24),0 2px 8px rgb(0 0 0/.08);visibility:hidden;transform:translateX(calc(-100% - 16px))}} body.side-unpinned.kind-html #vault-side{{width:min(290px,86vw)}} body.native.side-unpinned #vault-side{{padding-top:40px}}
body.side-unpinned:not(.obsidian-tree) #vault-side{{background:rgb(var(--bg-sidebar)/.9);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3)}}
@media(prefers-reduced-transparency:reduce){{body.side-unpinned:not(.obsidian-tree) #vault-side{{background:rgb(var(--bg-sidebar));backdrop-filter:none;-webkit-backdrop-filter:none}}}}
body.side-unpinned.side-out #vault-side{{visibility:visible;transform:none}}
/* The row a menu is open for wears a ring, as Finder's does. */
#tree .menu-for{{box-shadow:inset 0 0 0 2px rgb(var(--accent))}}
</style><style id=sidebar-theme>{sidebar_css}</style></head><body class="{body_class}"><div class=shell><aside id=vault-side><div class=brand><img class=mark src=/onyx-mark.png alt=""><span class=brand-name>{vault_name}</span>{add_toggle}<button id=side-pin class=side-toggle type=button aria-pressed=true title="Unpin sidebar (⌘\\)" aria-label="Pin sidebar" aria-controls=vault-side>{PIN_ICON}</button></div>
<nav class=vault-switch aria-label="Vaults"><a href="/vault"{notes_active}>Notes</a><a href="/vault?vault=html"{html_active}>Artifacts</a></nav>
{add_panel}
<input id=vault-filter type=search placeholder="Filter {units}… (press /)" autocomplete=off spellcheck=false aria-label="Filter {units}">
<nav id=tree aria-label="{vault_name} {units}"><div class=none>Loading…</div></nav>
<div class=aside-foot><a href="/">← Launcher</a> · <span id=vault-count>v{version}</span></div></aside>
<main id=reader-pane><div id=reader-empty><div>{empty_hint}<br><small>Select any passage inside it to ask.</small></div></div>
<iframe id=reader name=reader src="{initial}" title="Reader"></iframe></main></div><div id=side-edge aria-hidden=true></div><div id=peek role=tooltip hidden></div>
<script src=/app-menu.js></script>
<script>
const KIND={json.dumps(kind)}; const TOKEN={token}; const INITIAL_SRC={initial_src}; let ROOT={root_json}; const $=s=>document.querySelector(s); const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const native=!!(window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.askwPick); if(native)document.body.classList.add('native');
{glass_js}
const HTML=KIND==='html', KEY='askw:vault:'+(HTML?'html:':''), UNIT=HTML?'page':'note';
const tree=$('#tree'),reader=$('#reader'),filter=$('#vault-filter'),empty=$('#reader-empty'); let TREE=null;
function store(k,v){{try{{localStorage.setItem(k,v)}}catch(e){{}}}} function recall(k){{try{{return localStorage.getItem(k)}}catch(e){{return null}}}}
// Notes remember which folders are OPEN (default shut, the vault is large); HTML
// remembers which are CLOSED (default open, so a project reads at a glance).
const FOLD=new Set(); try{{for(const p of JSON.parse(recall(KEY+(HTML?'closed':'open'))||'[]'))FOLD.add(p)}}catch(e){{}}
function isOpen(path){{return HTML?!FOLD.has(path):FOLD.has(path)}}
async function api(url){{const r=await fetch(url);const d=await r.json();if(!r.ok||d.ok===false)throw new Error(d.error||`HTTP ${{r.status}}`);return d}}
function viewHref(path){{return '/view?src='+encodeURIComponent(path)+(ROOT&&!HTML?'&folder='+encodeURIComponent(ROOT):'')}}
function ago(ts){{if(!ts)return'';const d=Math.max(0,Date.now()/1000-ts);if(d<3600)return Math.max(1,Math.floor(d/60))+'m';if(d<86400)return Math.floor(d/3600)+'h';if(d<86400*14)return Math.floor(d/86400)+'d';if(d<86400*120)return Math.floor(d/604800)+'w';return new Date(ts*1000).toLocaleDateString(undefined,{{month:'short',year:'2-digit'}})}}
function label(n){{return HTML?(n.title||n.name):n.name.replace(/\\.(md|markdown)$/i,'')}} function badge(ext){{return /^\\.(md|markdown)$/i.test(ext)?'':`<span class=ext>${{esc(ext.replace('.',''))}}</span>`}}
// Rows carry only a label; what a page is (title, summary, folder, kind, age) waits in NODES for the hover card.
const ICON={json.dumps(TREE_ICONS)}; const NODES=new Map();
function fileRow(n,crumbs){{NODES.set(n.path,{{n,crumbs}});if(n.missing)return `<li><span class="file missing" data-path="${{esc(n.path)}}" tabindex=0><span class=lbl>${{esc(label(n))}}</span><span class=ext>missing</span></span></li>`;return `<li><a class=file target=reader href="${{esc(viewHref(n.path))}}" data-path="${{esc(n.path)}}"><span class=lbl>${{esc(label(n))}}</span>${{HTML?'':badge(n.ext||'')}}</a></li>`}}
function dirRow(n,crumbs){{const inside=crumbs.concat(n.name);return `<li><details data-path="${{esc(n.path)}}"${{isOpen(n.path)?' open':''}}><summary title="${{esc(n.path)}}">${{ICON.chev}}<span class=fold>${{ICON.shut}}${{ICON.open}}</span><span class=lbl>${{esc(n.name)}}</span>${{n.symlink?'<span class=sym title="Linked folder">↗</span>':''}}</summary><ul>${{n.children.map(c=>render(c,inside)).join('')}}</ul></details></li>`}}
// Artifacts lists a folder only once a page sits somewhere beneath it; + still offers an empty one (destinations walks TREE).
function hasPages(n){{return n.kind!=='dir'||n.children.some(hasPages)}}
function render(n,crumbs){{return n.kind!=='dir'?fileRow(n,crumbs):(HTML&&!hasPages(n))?'':dirRow(n,crumbs)}}
function renderTree(){{if(!TREE)return;hidePeek();NODES.clear();const top=HTML?TREE.children.filter(hasPages):TREE.children;tree.innerHTML=top.length?'<ul class=root>'+top.map(c=>render(c,[])).join('')+'</ul>':`<div class=none>${{HTML?'No artifacts yet. Use + to link pages or a folder of them.':'No notes found.'}}</div>`;tree.querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{{const shut=!d.open;if(HTML?shut:!shut)FOLD.add(d.dataset.path);else FOLD.delete(d.dataset.path);store(KEY+(HTML?'closed':'open'),JSON.stringify([...FOLD]))}}));highlight(currentSrc());applyTints();if(HTML)fillDestinations()}}
function currentSrc(){{try{{const l=reader.contentWindow.location;if(!l||!l.href||l.href==='about:blank')return '';return new URLSearchParams(l.search).get('src')||''}}catch(e){{return ''}}}}
function highlight(src){{tree.querySelectorAll('a.active').forEach(a=>a.classList.remove('active'));if(!src)return;const a=tree.querySelector(`a[data-path="${{CSS.escape(src)}}"]`);if(!a)return;a.classList.add('active');let p=a.parentElement;while(p&&p!==tree){{if(p.tagName==='DETAILS'&&!p.open)p.open=true;p=p.parentElement}}a.scrollIntoView({{block:'nearest'}})}}
async function loadTree(){{try{{const d=await api('/api/vault/tree?vault='+KIND);ROOT=d.root;TREE=d.tree;$('#vault-count').textContent=d.files+' '+UNIT+(d.files===1?'':'s')+(d.missing?' · '+d.missing+' missing':'')+(d.truncated?' (truncated)':'');renderTree()}}catch(e){{tree.innerHTML=`<div class=none>${{esc(e.message)}} <a href="/#settings">Open Settings</a></div>`;$('#vault-count').textContent=HTML?'no Artifacts folder':'no vault'}}}}
// MARK: sidebar — pinned or unpinned, remembered across both vaults ("collapsed" is the old word for unpinned). Unpinned,
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
edge.addEventListener('mouseenter',()=>{{clearTimeout(sideTimer);sideTimer=setTimeout(()=>sideOut(true),120)}}); edge.addEventListener('mouseleave',e=>{{if(side.contains(e.relatedTarget))return;clearTimeout(sideTimer);if(document.body.classList.contains('side-out'))sideLater()}});
side.addEventListener('mouseenter',()=>{{sideOver=true;clearTimeout(sideTimer)}}); side.addEventListener('mouseleave',()=>{{sideOver=false;sideLater()}}); side.addEventListener('focusout',()=>{{if(!sideOver)sideLater()}});
reader.addEventListener('load',()=>{{try{{reader.contentWindow.addEventListener('keydown',sideKey)}}catch(e){{}}const src=currentSrc();empty.hidden=!!src;if(!src)return;highlight(src);history.replaceState(null,'','/vault?'+(HTML?'vault=html&':'')+'src='+encodeURIComponent(src));let t='';try{{t=reader.contentDocument.title}}catch(e){{}}document.title=(t||src.split('/').pop())+' — '+(HTML?'Artifacts':'Vault');store(KEY+'last',src)}});
let filterTimer; filter.oninput=()=>{{clearTimeout(filterTimer);filterTimer=setTimeout(applyFilter,150)}};
async function applyFilter(){{const q=filter.value.trim();if(q.length<2){{renderTree();return}}try{{const d=await api('/api/vault/search?vault='+KIND+'&q='+encodeURIComponent(q));hidePeek();NODES.clear();d.items.forEach(i=>NODES.set(i.path,{{n:i,crumbs:(i.folder||'').split('/').filter(Boolean)}}));tree.innerHTML='<ul class="root results">'+d.items.map(i=>`<li><a class=file target=reader href="${{esc(viewHref(i.path))}}" data-path="${{esc(i.path)}}"><span class=lbl>${{esc(HTML?(i.title||i.name):label(i))}}</span><small>${{esc(i.folder||'/')}}</small></a></li>`).join('')+(d.items.length?'':`<li class=none>No ${{UNIT}}s match.</li>`)+'</ul>';highlight(currentSrc())}}catch(e){{tree.innerHTML=`<div class=none>${{esc(e.message)}}</div>`}}}}
document.addEventListener('keydown',e=>{{const typing=/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement&&document.activeElement.tagName);if(e.key==='/'&&!typing&&!e.metaKey&&!e.ctrlKey){{e.preventDefault();if(!pinned())sideOut(true);filter.focus();filter.select()}}else if(e.key==='Escape'&&document.activeElement===filter){{filter.value='';applyFilter();filter.blur()}}}});
// MARK: row menu — the app's rendered menu (static/app-menu.js). The server says what a row really is (its real file, and
// the link on the way); ⌥ turns each Reveal into a Copy. A mousedown while the answer is in flight means it came too late.
let menuSeq=0; document.addEventListener('mousedown',()=>{{menuSeq++}},true);
tree.addEventListener('contextmenu',async e=>{{const row=e.target.closest('#tree .file, #tree summary');if(!row||!window.OnyxMenu)return;e.preventDefault();
// WebKit on macOS selects the word under a right-click before this event fires (for Look Up); a row isn't text to select.
const sel=getSelection();if(sel&&sel.anchorNode&&row.contains(sel.anchorNode))sel.removeAllRanges();const path=row.dataset.path||row.parentElement.dataset.path;if(!path)return;
let x=e.clientX,y=e.clientY;if(!x&&!y){{const r=row.getBoundingClientRect();x=r.left+16;y=r.bottom}}
const seq=++menuSeq;let d;try{{d=await api('/api/vault/entry?vault='+KIND+'&path='+encodeURIComponent(path))}}catch(err){{OnyxMenu.toast(err.message,'bad');return}}if(seq!==menuSeq)return;
const items=[];if(!d.is_dir)items.push({{id:'open',label:'Open',enabled:d.exists}});
items.push({{id:'reveal',label:'Reveal in Finder',enabled:!!d.real,alt:{{id:'copy',label:'Copy Path'}}}});
if(d.link)items.push({{id:'reveal-link',label:'Reveal Link in Finder',alt:{{id:'copy-link',label:'Copy Link Path'}}}});
OnyxMenu.open({{items,x,y,label:(d.is_dir?'Folder':HTML?'Page':'Note')+' actions',returnFocus:row,onClose:()=>row.classList.remove('menu-for'),onSelect:id=>rowAction(id,d,row)}});row.classList.add('menu-for')}});
function copyPath(p){{if(!navigator.clipboard)throw new Error('The clipboard is not available here.');return navigator.clipboard.writeText(p).then(()=>OnyxMenu.toast('Copied '+shortPath(p)))}}
async function rowAction(id,d,row){{try{{if(id==='open'){{if(row.tagName==='A')row.click();else reader.src=viewHref(d.path)}}else if(id==='copy')await copyPath(d.real);else if(id==='copy-link')await copyPath(d.path);else if(id==='reveal'||id==='reveal-link')await postJSON('/api/vault/reveal',{{vault:KIND,path:d.path,which:id==='reveal'?'real':'link'}})}}catch(err){{OnyxMenu.toast(err.message||String(err),'bad')}}}}
// MARK: add panel (Artifacts only)
function destinations(){{const out=[{{rel:'',label:'Top level'}}];(function walk(n,depth){{for(const c of n.children||[]){{if(c.kind==='dir'&&!c.linked){{out.push({{rel:c.rel,label:'\\u00a0'.repeat(depth*3)+c.name}});walk(c,depth+1)}}}}}})(TREE||{{children:[]}},0);return out}}
function fillDestinations(){{const sel=$('#add-dest');if(!sel)return;const keep=sel.value||recall(KEY+'dest')||'';const opts=destinations();sel.innerHTML=opts.map(o=>`<option value="${{esc(o.rel)}}">${{esc(o.label)}}</option>`).join('');sel.value=opts.some(o=>o.rel===keep)?keep:''}}
function addStatus(text,tone){{const el=$('#add-status');if(!el)return;el.textContent=text;el.className='field-help'+(tone?' '+tone:'')}}
async function postJSON(url,body){{const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:TOKEN,...body}})}});const d=await r.json().catch(()=>({{ok:false,error:'HTTP '+r.status}}));if(!r.ok||d.ok===false)throw new Error(d.error||'HTTP '+r.status);return d}}
function shortPath(p){{return String(p).replace(/^\\/Users\\/[^/]+/,'~')}}
async function linkTargets(targets){{targets=(targets||[]).filter(Boolean);if(!targets.length)return;addStatus('Linking…');try{{const d=await postJSON('/api/vault/html/link',{{parent:$('#add-dest').value,targets}});let msg='Linked '+d.linked.length+(d.linked.length===1?' item':' items');if(d.context_roots.length)msg+=' · answers can now cite '+d.context_roots.map(shortPath).join(', ');if(d.errors.length)msg+=' · skipped: '+d.errors.join('; ');addStatus(msg,d.errors.length?'bad':'ok');$('#add-path').value='';await loadTree()}}catch(e){{addStatus(e.message,'bad')}}}}
if(HTML){{const toggle=$('#add-toggle'),panel=$('#add-panel');toggle.onclick=()=>{{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)fillDestinations()}};
$('#add-dest').onchange=e=>store(KEY+'dest',e.target.value);
$('#add-pick-files').onclick=async()=>{{try{{const picked=await window.webkit.messageHandlers.askwPick.postMessage({{kind:'html',initial:''}});await linkTargets(Array.isArray(picked)?picked:[picked])}}catch(e){{addStatus(e.message,'bad')}}}};
$('#add-pick-folder').onclick=async()=>{{try{{const picked=await window.webkit.messageHandlers.askwPick.postMessage({{kind:'folder',initial:'',prompt:'Link Folder',message:'Choose a folder of HTML pages to link into Artifacts'}});await linkTargets([picked])}}catch(e){{addStatus(e.message,'bad')}}}};
$('#add-path-go').onclick=()=>linkTargets([$('#add-path').value.trim()]);$('#add-path').onkeydown=e=>{{if(e.key==='Enter'){{e.preventDefault();linkTargets([e.target.value.trim()])}}}};
async function makeFolder(){{const name=$('#add-folder-name').value.trim();if(!name)return;try{{const d=await postJSON('/api/vault/html/folder',{{parent:$('#add-dest').value,name}});$('#add-folder-name').value='';store(KEY+'dest',d.rel);addStatus('Created '+d.rel+' · it appears once it holds a page','ok');await loadTree()}}catch(e){{addStatus(e.message,'bad')}}}}
$('#add-mkdir').onclick=makeFolder;$('#add-folder-name').onkeydown=e=>{{if(e.key==='Enter'){{e.preventDefault();makeFolder()}}}}}}
// MARK: Obsidian look — while the vault's file-explorer look is in force (body.obsidian-tree, CSS from /api/sidebar-theme),
// each top-level folder takes its own Obsidian colour: by name in Notes, else by position — Artifacts always by position,
// since its folder names never match the vault's. Kept live, like the reader's Markdown styles.
let SIDE_THEME={sidebar_state};
function applyTints(){{const on=document.body.classList.contains('obsidian-tree'),list=SIDE_THEME.folders||[],byName=new Map(list.map(f=>[f.name.toLowerCase(),f]));
tree.querySelectorAll(':scope > ul.root > li > details').forEach((d,i)=>{{const li=d.parentElement,name=(d.querySelector(':scope > summary .lbl')||{{}}).textContent||'';
const f=on&&list.length?((!HTML&&byName.get(name.toLowerCase()))||list[i%list.length]):null;
if(f){{li.style.setProperty('--folder-color',f.color);li.style.setProperty('--guide-color',f.guide||f.color)}}else{{li.style.removeProperty('--folder-color');li.style.removeProperty('--guide-color')}}}})}}
async function syncSidebarTheme(){{if(document.hidden)return;try{{const d=await api('/api/sidebar-theme');if(d.revision===SIDE_THEME.revision)return;SIDE_THEME=d;$('#sidebar-theme').textContent=d.css||'';document.body.classList.toggle('obsidian-tree',!!d.css);applyTints()}}catch(e){{}}}}
setInterval(syncSidebarTheme,3000); document.addEventListener('visibilitychange',syncSidebarTheme);
// MARK: hover card — a page's whole title, its one line, where it lives, what it is. The first hover waits a beat; after
// that it follows the pointer row to row at once. A click, scroll, right-click menu, Escape, or leaving puts it away.
const peek=$('#peek'); let peekRow=null, peekTimer=0, peekWarmUntil=0;
function updated(ts){{const a=ago(ts);return !a?'':/\\d[mhdw]$/.test(a)?'Updated '+a+' ago':'Updated '+a}}
function kindOf(n){{if(HTML)return 'HTML page';const x=(n.ext||(/\\.[^.]+$/.exec(n.name||'')||[''])[0]).replace('.','').toUpperCase();return /^(MD|MARKDOWN)$/.test(x)?'Markdown note':x?x+' file':'Note'}}
function showPeek(row){{const e=NODES.get(row.dataset.path);if(!e||(window.OnyxMenu&&OnyxMenu.isOpen()))return;const n=e.n;
if(peekRow&&peekRow!==row)peekRow.removeAttribute('aria-describedby');peekRow=row;row.setAttribute('aria-describedby','peek');
const where=e.crumbs.length?e.crumbs.join(' › '):{json.dumps(vault_name)},what=n.missing?'Link target is missing: '+shortPath(n.target||n.name):[kindOf(n),updated(n.mtime)].filter(Boolean).join(' · ');
peek.innerHTML=`<p class=peek-title>${{esc(label(n))}}</p>${{n.summary?`<p class=peek-sum>${{esc(n.summary)}}</p>`:''}}<p class=peek-row>${{ICON.folder}}<span>${{esc(where)}}</span></p><p class=peek-row>${{n.missing?ICON.link:ICON.doc}}<span>${{esc(what)}}</span></p>`;
peek.hidden=false;const r=row.getBoundingClientRect(),side=$('#vault-side').getBoundingClientRect(),w=peek.offsetWidth,h=peek.offsetHeight,beside=side.right+10+w<=innerWidth-8;
peek.style.left=(beside?side.right+10:Math.max(8,Math.min(r.left,innerWidth-w-8)))+'px';peek.style.top=Math.max(8,Math.min(beside?r.top-4:r.bottom+6,innerHeight-h-8))+'px';requestAnimationFrame(()=>peek.classList.add('show'))}}
function hidePeek(){{clearTimeout(peekTimer);if(peekRow){{peekRow.removeAttribute('aria-describedby');peekRow=null}}if(!peek.hidden){{peekWarmUntil=Date.now()+350;peek.classList.remove('show');peek.hidden=true}}}}
function wantPeek(row,delay){{clearTimeout(peekTimer);if(row===peekRow)return;peekTimer=setTimeout(()=>showPeek(row),!peek.hidden||Date.now()<peekWarmUntil?0:delay)}}
tree.addEventListener('mouseover',e=>{{const row=e.target.closest('#tree .file');if(row)wantPeek(row,450)}});
tree.addEventListener('mouseout',e=>{{const row=e.target.closest('#tree .file');if(row&&!row.contains(e.relatedTarget)){{clearTimeout(peekTimer);peekTimer=setTimeout(hidePeek,90)}}}});
tree.addEventListener('focusin',e=>{{const row=e.target.closest('#tree .file');if(row&&row.matches(':focus-visible'))wantPeek(row,200)}});
for(const ev of ['focusout','scroll','click','contextmenu'])tree.addEventListener(ev,hidePeek,{{passive:true}}); document.addEventListener('keydown',e=>{{if(e.key==='Escape')hidePeek()}}); window.addEventListener('blur',hidePeek);
if(/^(unpinned|collapsed)$/.test(recall(SIDE_KEY)||''))setPinned(false);
loadTree().then(()=>{{if(!INITIAL_SRC&&ROOT){{const last=recall(KEY+'last');if(last)reader.src=viewHref(last)}}}});
</script></body></html>"""
