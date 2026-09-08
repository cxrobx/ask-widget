"""Vault mode shell: a persistent note tree beside a same-origin reader iframe.

The page is deliberately thin. Files are plain ``<a target=reader>`` links, so
the named iframe handles navigation and history without any click JS; the
script only builds the tree, keeps the highlight in sync with whatever the
reader currently shows, and drives the filter box.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .launcher_ui import theme_settings, theme_style


def vault_page(
    config: AppConfig,
    settings: dict[str, Any] | None,
    *,
    root: Path | None,
    src: str | None = None,
    reader_query: str | None = None,
) -> str:
    _glass, theme = theme_settings(settings)
    version = html.escape(__version__)
    shared_style = theme_style(settings)
    initial = html.escape(f"/view?{reader_query}", quote=True) if reader_query else "about:blank"
    initial_src = json.dumps(src or "")
    root_json = json.dumps(str(root) if root else "")
    title = html.escape(Path(src).name if src else "Vault")
    return f"""<!doctype html><html data-theme="{theme}"><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{title} — Vault</title>
<style>
{shared_style}
html,body{{height:100%;overflow:hidden}} button,input{{font:inherit}}
.shell{{grid-template-columns:260px minmax(0,1fr);height:100vh;min-height:0}}
aside{{display:flex;flex-direction:column;height:100vh;padding:20px 12px 14px;overflow:hidden}} body.native aside{{padding-top:48px}}
.brand{{margin:0 8px 14px}}
#vault-filter{{width:100%;margin:0 0 10px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:rgb(var(--bg-input)/.88);color:rgb(var(--ink));font-size:12.5px;box-shadow:inset 0 1px 0 rgb(255 255 255/.025)}} #vault-filter:focus{{outline:2px solid rgb(var(--accent)/.26);outline-offset:0;border-color:rgb(var(--accent))}}
#tree{{flex:1;min-height:0;overflow:auto;margin:0 -4px;padding:0 4px;font-size:12.5px}} #tree ul{{list-style:none;margin:0;padding:0}} #tree ul ul{{padding-left:12px}}
#tree summary{{display:flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;color:rgb(var(--secondary));font-weight:600;cursor:default;list-style:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;user-select:none}} #tree summary::-webkit-details-marker{{display:none}}
#tree summary::before{{content:"▸";flex:none;width:10px;color:rgb(var(--faint));font-size:10px;transition:transform .12s}} #tree details[open]>summary::before{{transform:rotate(90deg)}}
#tree summary:hover,#tree a.file:hover{{background:rgb(var(--ink)/.06);color:rgb(var(--ink))}}
#tree a.file{{display:flex;align-items:center;gap:6px;padding:4px 8px 4px 23px;border-radius:6px;color:rgb(var(--secondary));text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} #tree a.file span{{overflow:hidden;text-overflow:ellipsis}} #tree a.file.active{{background:rgb(var(--ink)/.09);color:rgb(var(--ink));font-weight:600}}
#tree .ext{{flex:none;margin-left:auto;padding:0 5px;border-radius:4px;background:rgb(var(--ink)/.07);color:rgb(var(--muted));font-size:9px;font-weight:700;text-transform:uppercase}} .sym{{color:rgb(var(--faint));font-size:10px}}
#tree .results a.file{{flex-direction:column;align-items:flex-start;gap:1px;padding-left:8px}} #tree .results small{{max-width:100%;overflow:hidden;color:rgb(var(--muted));font-size:10.5px;text-overflow:ellipsis}} #tree .none{{padding:10px 8px;color:rgb(var(--muted))}}
.aside-foot{{position:static;margin-top:10px;padding:0 8px}} .aside-foot a{{color:inherit;text-decoration:none}} .aside-foot a:hover{{color:rgb(var(--ink))}}
main,body.native main{{position:relative;padding:0;overflow:hidden}}
#reader{{display:block;width:100%;height:100%;border:0;background:transparent}}
#reader-empty{{position:absolute;inset:0;display:grid;place-items:center;padding:24px;color:rgb(var(--muted));font-size:14px;text-align:center;pointer-events:none}} #reader-empty[hidden]{{display:none}} #reader-empty a{{pointer-events:auto;color:rgb(var(--accent))}}
@media(max-width:800px){{.shell{{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}} aside,body.native aside{{position:static;height:auto;max-height:45vh;padding:12px 12px 8px}} body.native aside{{padding-top:38px}} .aside-foot{{display:block}}}}
</style></head><body><div class=shell><aside id=vault-side><div class=brand><span class=mark>✦</span>Vault</div>
<input id=vault-filter type=search placeholder="Filter notes… (press /)" autocomplete=off spellcheck=false aria-label="Filter notes">
<nav id=tree aria-label="Vault notes"><div class=none>Loading…</div></nav>
<div class=aside-foot><a href="/">← Launcher</a> · <span id=vault-count>v{version}</span></div></aside>
<main id=reader-pane><div id=reader-empty><div>Pick a note from the tree.<br><small>Select any passage inside it to ask.</small></div></div>
<iframe id=reader name=reader src="{initial}" title="Reader"></iframe></main></div>
<script>
const INITIAL_SRC={initial_src}; let ROOT={root_json}; const $=s=>document.querySelector(s); const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const native=!!(window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.askwPick); if(native)document.body.classList.add('native');
const tree=$('#tree'),reader=$('#reader'),filter=$('#vault-filter'),empty=$('#reader-empty'); let TREE=null; const OPEN=new Set(); try{{for(const p of JSON.parse(localStorage.getItem('askw:vault:open')||'[]'))OPEN.add(p)}}catch(e){{}}
function store(k,v){{try{{localStorage.setItem(k,v)}}catch(e){{}}}} function recall(k){{try{{return localStorage.getItem(k)}}catch(e){{return null}}}}
async function api(url){{const r=await fetch(url);const d=await r.json();if(!r.ok||d.ok===false)throw new Error(d.error||`HTTP ${{r.status}}`);return d}}
function viewHref(path){{return '/view?src='+encodeURIComponent(path)+(ROOT?'&folder='+encodeURIComponent(ROOT):'')}}
function label(name){{return name.replace(/\\.(md|markdown)$/i,'')}} function badge(ext){{return /^\\.(md|markdown)$/i.test(ext)?'':`<span class=ext>${{esc(ext.replace('.',''))}}</span>`}}
function fileRow(n){{return `<li><a class=file target=reader href="${{esc(viewHref(n.path))}}" data-path="${{esc(n.path)}}" title="${{esc(n.name)}}"><span>${{esc(label(n.name))}}</span>${{badge(n.ext||'')}}</a></li>`}}
function dirRow(n){{return `<li><details data-path="${{esc(n.path)}}"${{OPEN.has(n.path)?' open':''}}><summary title="${{esc(n.path)}}">${{esc(n.name)}}${{n.symlink?' <span class=sym title="Symlinked folder">↗</span>':''}}</summary><ul>${{n.children.map(render).join('')}}</ul></details></li>`}}
function render(n){{return n.kind==='dir'?dirRow(n):fileRow(n)}}
function renderTree(){{if(!TREE)return;tree.innerHTML='<ul class=root>'+TREE.children.map(render).join('')+'</ul>';tree.querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{{if(d.open)OPEN.add(d.dataset.path);else OPEN.delete(d.dataset.path);store('askw:vault:open',JSON.stringify([...OPEN]))}}));highlight(currentSrc())}}
function currentSrc(){{try{{const l=reader.contentWindow.location;if(!l||!l.href||l.href==='about:blank')return '';return new URLSearchParams(l.search).get('src')||''}}catch(e){{return ''}}}}
function highlight(src){{tree.querySelectorAll('a.active').forEach(a=>a.classList.remove('active'));if(!src)return;const a=tree.querySelector(`a[data-path="${{CSS.escape(src)}}"]`);if(!a)return;a.classList.add('active');let p=a.parentElement;while(p&&p!==tree){{if(p.tagName==='DETAILS'&&!p.open)p.open=true;p=p.parentElement}}a.scrollIntoView({{block:'nearest'}})}}
async function loadTree(){{try{{const d=await api('/api/vault/tree');ROOT=d.root;TREE=d.tree;$('#vault-count').textContent=d.files+' notes'+(d.truncated?' (truncated)':'');renderTree()}}catch(e){{tree.innerHTML=`<div class=none>${{esc(e.message)}} <a href="/#settings">Open Settings</a></div>`;$('#vault-count').textContent='no vault'}}}}
reader.addEventListener('load',()=>{{const src=currentSrc();empty.hidden=!!src;if(!src)return;highlight(src);history.replaceState(null,'','/vault?src='+encodeURIComponent(src));let t='';try{{t=reader.contentDocument.title}}catch(e){{}}document.title=(t||src.split('/').pop())+' — Vault';store('askw:vault:last',src)}});
let filterTimer; filter.oninput=()=>{{clearTimeout(filterTimer);filterTimer=setTimeout(applyFilter,150)}};
async function applyFilter(){{const q=filter.value.trim();if(q.length<2){{renderTree();return}}try{{const d=await api('/api/vault/search?q='+encodeURIComponent(q));tree.innerHTML='<ul class="root results">'+d.items.map(i=>`<li><a class=file target=reader href="${{esc(viewHref(i.path))}}" data-path="${{esc(i.path)}}" title="${{esc(i.path)}}"><span>${{esc(label(i.name))}}</span><small>${{esc(i.folder||'/')}}</small></a></li>`).join('')+(d.items.length?'':'<li class=none>No notes match.</li>')+'</ul>';highlight(currentSrc())}}catch(e){{tree.innerHTML=`<div class=none>${{esc(e.message)}}</div>`}}}}
document.addEventListener('keydown',e=>{{if(e.key==='/'&&document.activeElement!==filter&&!e.metaKey&&!e.ctrlKey){{e.preventDefault();filter.focus();filter.select()}}else if(e.key==='Escape'&&document.activeElement===filter){{filter.value='';applyFilter();filter.blur()}}}});
loadTree().then(()=>{{if(!INITIAL_SRC&&ROOT){{const last=recall('askw:vault:last');if(last)reader.src=viewHref(last)}}}});
</script></body></html>"""
