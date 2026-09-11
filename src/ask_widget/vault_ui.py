"""Vault mode shell: a persistent tree beside a same-origin reader iframe.

One shell, two vaults. **Notes** is the Obsidian vault (Markdown, wikilinks).
**HTML** is a folder of symlinks to HTML pages anywhere on disk: its top-level
folders are projects, pages are labelled by their ``<title>``, and the **+**
panel links more in (it only ever creates links and folders inside the vault —
see ``vault.writable_folder``).

The page is deliberately thin. Files are plain ``<a target=reader>`` links, so
the named iframe handles navigation and history without any click JS; the
script only builds the tree, keeps the highlight in sync with whatever the
reader currently shows, drives the filter box, and (HTML) the add panel.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .launcher_ui import glass_script, theme_settings, theme_style

# SF Symbols' sidebar.left: the one glyph both sidebar toggles share.
SIDEBAR_ICON = (
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true">'
    '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2.25"/><path d="M6.25 2.75v10.5"/></svg>'
)


def vault_page(
    config: AppConfig,
    settings: dict[str, Any] | None,
    *,
    root: Path | None,
    src: str | None = None,
    reader_query: str | None = None,
    kind: str = "notes",
) -> str:
    _glass, theme = theme_settings(settings)
    html_kind = kind == "html"
    version = html.escape(__version__)
    shared_style = theme_style(settings)
    glass_js = glass_script(settings)
    initial = html.escape(f"/view?{reader_query}", quote=True) if reader_query else "about:blank"
    initial_src = json.dumps(src or "")
    root_json = json.dumps(str(root) if root else "")
    token = json.dumps(config.token)
    vault_name = "HTML Vault" if html_kind else "Vault"
    title = html.escape(Path(src).name if src else vault_name)
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
<meta name=viewport content="width=device-width,initial-scale=1"><title>{title} — {vault_name}</title>
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
#tree{{flex:1;min-height:0;overflow:auto;margin:0 -4px;padding:0 4px;font-size:12.5px}} #tree ul{{list-style:none;margin:0;padding:0}} #tree ul ul{{padding-left:12px}}
#tree summary{{display:flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;color:rgb(var(--secondary));font-weight:600;cursor:default;list-style:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;user-select:none}} #tree summary::-webkit-details-marker{{display:none}}
#tree summary::before{{content:"▸";flex:none;width:10px;color:rgb(var(--faint));font-size:10px;transition:transform .12s}} #tree details[open]>summary::before{{transform:rotate(90deg)}}
#tree summary:hover,#tree a.file:hover{{background:rgb(var(--ink)/.06);color:rgb(var(--ink))}}
#tree .file{{display:flex;align-items:center;gap:6px;padding:4px 8px 4px 23px;border-radius:6px;color:rgb(var(--secondary));text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} #tree .file span{{overflow:hidden;text-overflow:ellipsis}} #tree a.file.active{{background:var(--selected);color:rgb(var(--ink));font-weight:600}}
#tree .ext{{flex:none;margin-left:auto;padding:0 5px;border-radius:4px;background:rgb(var(--ink)/.07);color:rgb(var(--muted));font-size:9px;font-weight:700;text-transform:uppercase}} .sym{{color:rgb(var(--faint));font-size:10px}}
#tree .age{{flex:none;margin-left:auto;color:rgb(var(--faint));font-size:10px;font-variant-numeric:tabular-nums}} #tree .file.missing{{color:rgb(var(--faint));cursor:help}} #tree .file.missing span:first-child{{text-decoration:line-through}} #tree .file.missing .ext{{background:rgb(var(--bad)/.1);color:rgb(var(--bad))}}
#tree .results .file{{flex-direction:column;align-items:flex-start;gap:1px;padding-left:8px}} #tree .results small{{max-width:100%;overflow:hidden;color:rgb(var(--muted));font-size:10.5px;text-overflow:ellipsis}} #tree .none{{padding:10px 8px;color:rgb(var(--muted))}}
.aside-foot{{position:static;margin-top:10px;padding:0 8px}} .aside-foot a{{color:inherit;text-decoration:none}} .aside-foot a:hover{{color:rgb(var(--ink))}}
main,body.native main{{position:relative;padding:0;overflow:hidden}}
#reader{{display:block;width:100%;height:100%;border:0;background:transparent}}
#reader-empty{{position:absolute;inset:0;display:grid;place-items:center;padding:24px;color:rgb(var(--muted));font-size:14px;text-align:center;pointer-events:none}} #reader-empty[hidden]{{display:none}} #reader-empty a{{pointer-events:auto;color:rgb(var(--accent))}}
/* HTML pages are named by sentence-length titles: give them room and two lines. */
body.kind-html .shell{{grid-template-columns:290px minmax(0,1fr)}} body.kind-html #tree .file{{align-items:flex-start;white-space:normal}} body.kind-html #tree .file>span:first-child{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;line-height:1.35}} body.kind-html #tree .age{{padding-top:1px}}
/* Collapsible sidebar: hide from the brand row, bring back from the reader's corner, or ⌘\\ anywhere. */
.side-toggle{{display:grid;place-items:center;flex:none;width:26px;height:24px;padding:0;border:0;border-radius:7px;background:transparent;color:rgb(var(--secondary))}} .side-toggle:hover{{background:rgb(var(--ink)/.08);color:rgb(var(--ink))}} .side-toggle svg{{width:16px;height:16px}}
#side-show{{position:absolute;top:12px;left:12px;z-index:2;display:none;border:1px solid var(--line-soft);background:rgb(var(--bg-elevated)/.82);box-shadow:0 4px 14px rgb(0 0 0/.12);backdrop-filter:blur(16px) saturate(1.3);-webkit-backdrop-filter:blur(16px) saturate(1.3)}} body.native #side-show{{top:36px}}
body.side-collapsed .shell{{grid-template-columns:minmax(0,1fr)}} body.side-collapsed aside{{display:none}} body.side-collapsed #side-show{{display:grid}}
@media(max-width:800px){{.shell,body.kind-html .shell{{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}} aside,body.native aside{{position:static;height:auto;max-height:45vh;padding:12px 12px 8px}} body.native aside{{padding-top:38px}} .aside-foot{{display:block}} body.side-collapsed .shell{{grid-template-rows:minmax(0,1fr)}}}}
</style></head><body class="kind-{kind}"><div class=shell><aside id=vault-side><div class=brand><img class=mark src=/onyx-mark.png alt=""><span class=brand-name>{vault_name}</span>{add_toggle}<button id=side-hide class=side-toggle type=button title="Hide sidebar (⌘\\)" aria-label="Hide sidebar" aria-controls=vault-side>{SIDEBAR_ICON}</button></div>
<nav class=vault-switch aria-label="Vaults"><a href="/vault"{notes_active}>Notes</a><a href="/vault?vault=html"{html_active}>HTML</a></nav>
{add_panel}
<input id=vault-filter type=search placeholder="Filter {units}… (press /)" autocomplete=off spellcheck=false aria-label="Filter {units}">
<nav id=tree aria-label="{vault_name} {units}"><div class=none>Loading…</div></nav>
<div class=aside-foot><a href="/">← Launcher</a> · <span id=vault-count>v{version}</span></div></aside>
<main id=reader-pane><button id=side-show class=side-toggle type=button title="Show sidebar (⌘\\)" aria-label="Show sidebar" aria-controls=vault-side>{SIDEBAR_ICON}</button><div id=reader-empty><div>{empty_hint}<br><small>Select any passage inside it to ask.</small></div></div>
<iframe id=reader name=reader src="{initial}" title="Reader"></iframe></main></div>
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
function fileRow(n){{if(n.missing)return `<li><span class="file missing" title="${{esc('Link target is missing: '+(n.target||n.name))}}"><span>${{esc(label(n))}}</span><span class=ext>missing</span></span></li>`;const tail=HTML?`<small class=age title="Updated ${{esc(new Date(n.mtime*1000).toLocaleString())}}">${{esc(ago(n.mtime))}}</small>`:badge(n.ext||'');return `<li><a class=file target=reader href="${{esc(viewHref(n.path))}}" data-path="${{esc(n.path)}}" title="${{esc(HTML&&n.title?n.title+' — '+n.name:n.name)}}"><span>${{esc(label(n))}}</span>${{tail}}</a></li>`}}
function dirRow(n){{return `<li><details data-path="${{esc(n.path)}}"${{isOpen(n.path)?' open':''}}><summary title="${{esc(n.path)}}">${{esc(n.name)}}${{n.symlink?' <span class=sym title="Linked folder">↗</span>':''}}</summary><ul>${{n.children.length?n.children.map(render).join(''):(HTML?'<li class=none>No pages yet.</li>':'')}}</ul></details></li>`}}
function render(n){{return n.kind==='dir'?dirRow(n):fileRow(n)}}
function renderTree(){{if(!TREE)return;tree.innerHTML=TREE.children.length?'<ul class=root>'+TREE.children.map(render).join('')+'</ul>':`<div class=none>${{HTML?'Your HTML vault is empty. Use + to link pages or folders, or add a project folder.':'No notes found.'}}</div>`;tree.querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{{const shut=!d.open;if(HTML?shut:!shut)FOLD.add(d.dataset.path);else FOLD.delete(d.dataset.path);store(KEY+(HTML?'closed':'open'),JSON.stringify([...FOLD]))}}));highlight(currentSrc());if(HTML)fillDestinations()}}
function currentSrc(){{try{{const l=reader.contentWindow.location;if(!l||!l.href||l.href==='about:blank')return '';return new URLSearchParams(l.search).get('src')||''}}catch(e){{return ''}}}}
function highlight(src){{tree.querySelectorAll('a.active').forEach(a=>a.classList.remove('active'));if(!src)return;const a=tree.querySelector(`a[data-path="${{CSS.escape(src)}}"]`);if(!a)return;a.classList.add('active');let p=a.parentElement;while(p&&p!==tree){{if(p.tagName==='DETAILS'&&!p.open)p.open=true;p=p.parentElement}}a.scrollIntoView({{block:'nearest'}})}}
async function loadTree(){{try{{const d=await api('/api/vault/tree?vault='+KIND);ROOT=d.root;TREE=d.tree;$('#vault-count').textContent=d.files+' '+UNIT+(d.files===1?'':'s')+(d.missing?' · '+d.missing+' missing':'')+(d.truncated?' (truncated)':'');renderTree()}}catch(e){{tree.innerHTML=`<div class=none>${{esc(e.message)}} <a href="/#settings">Open Settings</a></div>`;$('#vault-count').textContent=HTML?'no HTML vault':'no vault'}}}}
// MARK: sidebar — remembered across both vaults; ⌘\\ also works while focus is inside the reader.
const SIDE_KEY='askw:vault:sidebar'; if(recall(SIDE_KEY)==='collapsed')document.body.classList.add('side-collapsed');
function setSide(collapsed,focus){{document.body.classList.toggle('side-collapsed',collapsed);store(SIDE_KEY,collapsed?'collapsed':'');if(focus)$(collapsed?'#side-show':'#side-hide').focus()}}
function sideKey(e){{if(e.key==='\\\\'&&(e.metaKey||e.ctrlKey)&&!e.altKey&&!e.shiftKey){{e.preventDefault();setSide(!document.body.classList.contains('side-collapsed'),false)}}}}
$('#side-hide').onclick=()=>setSide(true,true); $('#side-show').onclick=()=>setSide(false,true); document.addEventListener('keydown',sideKey);
reader.addEventListener('load',()=>{{try{{reader.contentWindow.addEventListener('keydown',sideKey)}}catch(e){{}}const src=currentSrc();empty.hidden=!!src;if(!src)return;highlight(src);history.replaceState(null,'','/vault?'+(HTML?'vault=html&':'')+'src='+encodeURIComponent(src));let t='';try{{t=reader.contentDocument.title}}catch(e){{}}document.title=(t||src.split('/').pop())+' — '+(HTML?'HTML Vault':'Vault');store(KEY+'last',src)}});
let filterTimer; filter.oninput=()=>{{clearTimeout(filterTimer);filterTimer=setTimeout(applyFilter,150)}};
async function applyFilter(){{const q=filter.value.trim();if(q.length<2){{renderTree();return}}try{{const d=await api('/api/vault/search?vault='+KIND+'&q='+encodeURIComponent(q));tree.innerHTML='<ul class="root results">'+d.items.map(i=>`<li><a class=file target=reader href="${{esc(viewHref(i.path))}}" data-path="${{esc(i.path)}}" title="${{esc(i.path)}}"><span>${{esc(HTML?(i.title||i.name):label(i))}}</span><small>${{esc(i.folder||'/')}}</small></a></li>`).join('')+(d.items.length?'':`<li class=none>No ${{UNIT}}s match.</li>`)+'</ul>';highlight(currentSrc())}}catch(e){{tree.innerHTML=`<div class=none>${{esc(e.message)}}</div>`}}}}
document.addEventListener('keydown',e=>{{const typing=/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement&&document.activeElement.tagName);if(e.key==='/'&&!typing&&!e.metaKey&&!e.ctrlKey){{e.preventDefault();filter.focus();filter.select()}}else if(e.key==='Escape'&&document.activeElement===filter){{filter.value='';applyFilter();filter.blur()}}}});
// MARK: add panel (HTML vault only)
function destinations(){{const out=[{{rel:'',label:'Top level'}}];(function walk(n,depth){{for(const c of n.children||[]){{if(c.kind==='dir'&&!c.linked){{out.push({{rel:c.rel,label:'\\u00a0'.repeat(depth*3)+c.name}});walk(c,depth+1)}}}}}})(TREE||{{children:[]}},0);return out}}
function fillDestinations(){{const sel=$('#add-dest');if(!sel)return;const keep=sel.value||recall(KEY+'dest')||'';const opts=destinations();sel.innerHTML=opts.map(o=>`<option value="${{esc(o.rel)}}">${{esc(o.label)}}</option>`).join('');sel.value=opts.some(o=>o.rel===keep)?keep:''}}
function addStatus(text,tone){{const el=$('#add-status');if(!el)return;el.textContent=text;el.className='field-help'+(tone?' '+tone:'')}}
async function postJSON(url,body){{const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:TOKEN,...body}})}});const d=await r.json().catch(()=>({{ok:false,error:'HTTP '+r.status}}));if(!r.ok||d.ok===false)throw new Error(d.error||'HTTP '+r.status);return d}}
function shortPath(p){{return String(p).replace(/^\\/Users\\/[^/]+/,'~')}}
async function linkTargets(targets){{targets=(targets||[]).filter(Boolean);if(!targets.length)return;addStatus('Linking…');try{{const d=await postJSON('/api/vault/html/link',{{parent:$('#add-dest').value,targets}});let msg='Linked '+d.linked.length+(d.linked.length===1?' item':' items');if(d.context_roots.length)msg+=' · answers can now cite '+d.context_roots.map(shortPath).join(', ');if(d.errors.length)msg+=' · skipped: '+d.errors.join('; ');addStatus(msg,d.errors.length?'bad':'ok');$('#add-path').value='';await loadTree()}}catch(e){{addStatus(e.message,'bad')}}}}
if(HTML){{const toggle=$('#add-toggle'),panel=$('#add-panel');toggle.onclick=()=>{{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)fillDestinations()}};
$('#add-dest').onchange=e=>store(KEY+'dest',e.target.value);
$('#add-pick-files').onclick=async()=>{{try{{const picked=await window.webkit.messageHandlers.askwPick.postMessage({{kind:'html',initial:''}});await linkTargets(Array.isArray(picked)?picked:[picked])}}catch(e){{addStatus(e.message,'bad')}}}};
$('#add-pick-folder').onclick=async()=>{{try{{const picked=await window.webkit.messageHandlers.askwPick.postMessage({{kind:'folder',initial:'',prompt:'Link Folder',message:'Choose a folder of HTML pages to link into the HTML Vault'}});await linkTargets([picked])}}catch(e){{addStatus(e.message,'bad')}}}};
$('#add-path-go').onclick=()=>linkTargets([$('#add-path').value.trim()]);$('#add-path').onkeydown=e=>{{if(e.key==='Enter'){{e.preventDefault();linkTargets([e.target.value.trim()])}}}};
async function makeFolder(){{const name=$('#add-folder-name').value.trim();if(!name)return;try{{const d=await postJSON('/api/vault/html/folder',{{parent:$('#add-dest').value,name}});$('#add-folder-name').value='';store(KEY+'dest',d.rel);addStatus('Created '+d.rel,'ok');await loadTree()}}catch(e){{addStatus(e.message,'bad')}}}}
$('#add-mkdir').onclick=makeFolder;$('#add-folder-name').onkeydown=e=>{{if(e.key==='Enter'){{e.preventDefault();makeFolder()}}}}}}
loadTree().then(()=>{{if(!INITIAL_SRC&&ROOT){{const last=recall(KEY+'last');if(last)reader.src=viewHref(last)}}}});
</script></body></html>"""
