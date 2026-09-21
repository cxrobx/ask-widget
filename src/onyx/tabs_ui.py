"""Tabs: several notes and artifacts open at once, each in a reader frame of its own, behind a hidden, pinnable bar.

Every tab owns an iframe stacked in the reader's place (``#stage``), so a tab keeps its page, its place in it, and a
conversation still streaming while another tab shows. The tab showing is the shell's ``reader`` and wears
``id=reader``; the rest are hidden (``visibility``, never ``display:none``: a frame with no size would tell ask.js its
page scrolled to the top) and ``inert``.

The sidebar's rows stay plain ``<a target=reader>`` links. A frame keeps the name it was born with, and a click points
the link at the frame showing, so the browser still does the navigating and keeps the history. Renaming the frames
instead looked simpler and fails in the app: WebKit 17 files joint history under frame *names*, so once two frames
had swapped names, Back loaded one tab's page into the other (measured 2026-09-21, system WebKit 17.5; Playwright's
newer WebKit and Chromium both got it right). History is joint — one Back for the window, stepping whichever frame
moved last — so when that is a tab behind, it comes forward.

The bar behaves as the app's other two do. Unpinned (the default) it is away, and comes out as a card centred at the
top of the reader after a beat with the pointer in the top 24 px of the reader's middle 60 %, watched from the reader's
own pointer moves as the outline's edge is: an overlay strip would cover the context pill, the find bar and the
outline's toggle in the top-right corner. It goes a moment after the pointer leaves, unless it is in use — its list
open, or focus inside it. Pinned (⌘⌥\\ or its pin), it is a band above the reader and the reader moves down under it.
The title band of the app's window passes clicks to the page everywhere but the traffic lights (measured 2026-09-21),
so a pill there takes its click, and the band's empty space drags the window.

The script runs inside the shell's ``<script>`` and leans on it: ``$``, ``esc``, ``store``, ``recall``, ``reader``
(reassigned here), ``stage``, ``KIND``, ``VAULTS``, ``switchVault``, ``showHome``, ``empty``, ``home``, ``goHome``,
``highlight``, ``traversed``, ``readerLoaded`` and ``onReaderLoad``.
"""

from __future__ import annotations

import json

from .find_ui import DOWN_ICON
from .panels_ui import ICONS

# lucide's plus, on the panels' 24 grid.
PLUS_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M12 5v14"/></svg>'
)

TABS_CSS = """/* Tabs (tabs_ui.py). The reader's pane is two rows: the bar's, empty until it is pinned, then the stage. */
#reader-pane{--tabs-h:40px;grid-template-rows:0 minmax(0,1fr);transition:grid-template-rows .15s cubic-bezier(.2,.8,.2,1)} #stage{grid-row:2}
body.tabs-pinned #reader-pane{grid-template-rows:var(--tabs-h) minmax(0,1fr)}
/* Unpinned, the bar lies over the reader's top and only its centred card takes the pointer, so it never covers the
   context pill, the find bar or the outline's toggle. Pinned, it is a band of its own whose empty space drags the window. */
#tab-bar{position:absolute;top:0;right:0;left:0;z-index:5;display:flex;justify-content:center;align-items:flex-start;height:var(--tabs-h);padding:8px 12px 0;pointer-events:none}
body.tabs-pinned #tab-bar{position:relative;grid-row:1;align-items:center;padding:0 12px;border-bottom:1px solid var(--line-soft);pointer-events:auto}
body.native.side-unpinned.tabs-pinned #tab-bar{padding-left:84px}
.tab-group{display:flex;align-items:center;gap:2px;min-width:0;max-width:60%;padding:3px;border:1px solid var(--line);border-radius:12px;background:rgb(var(--bg-sidebar)/.96);box-shadow:0 18px 50px rgb(0 0 0/.24),0 2px 8px rgb(0 0 0/.08);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3);pointer-events:auto;visibility:hidden;transform:translateY(calc(-100% - 12px))}
@media(prefers-reduced-transparency:reduce){.tab-group{background:rgb(var(--bg-sidebar));backdrop-filter:none;-webkit-backdrop-filter:none}}
body.tabs-out .tab-group,body.tabs-pinned .tab-group{visibility:visible;transform:none}
body.tabs-pinned .tab-group{max-width:100%;padding:0;border-color:transparent;background:transparent;box-shadow:none;backdrop-filter:none;-webkit-backdrop-filter:none}
/* Motion, as the sidebar's: out with an ease-out slide, away with a quicker ease-in one, hidden once it is off. */
.tab-group{transition:transform .13s cubic-bezier(.4,0,1,1),visibility 0s linear .13s} body.tabs-out .tab-group{transition:transform .15s cubic-bezier(.2,.8,.2,1),visibility 0s}
body.tabs-pinned .tab-group,body.tabs-still .tab-group,body.tabs-still #reader-pane{transition:none!important}
@media(prefers-reduced-motion:reduce){body:not(.tabs-pinned) .tab-group{transform:none;opacity:0;transition:opacity .15s linear,visibility 0s linear .15s} body.tabs-out:not(.tabs-pinned) .tab-group{opacity:1;transition:opacity .15s linear,visibility 0s} #reader-pane{transition:none}}
/* The pills: a segmented strip, as the panel's Outline/Related switch — the tab showing on the raised ground, in ink. */
#tab-strip{display:flex;align-items:center;gap:2px;min-width:0;padding:2px;border-radius:9px;background:rgb(var(--ink)/.055)}
.tab{display:flex;align-items:center;flex:0 1 180px;gap:4px;min-width:88px;height:26px;padding:0 3px 0 10px;border-radius:7px;color:rgb(var(--secondary));font-size:12px;font-weight:500;cursor:default;user-select:none;-webkit-user-select:none;transition:background-color .12s,color .12s}
.tab:hover{background:rgb(var(--ink)/.05);color:rgb(var(--ink))} .tab[aria-selected=true]{background:rgb(var(--bg-elevated));color:rgb(var(--ink));font-weight:600;box-shadow:0 1px 2px rgb(0 0 0/.1)}
.tab:focus-visible{outline:2px solid rgb(var(--accent));outline-offset:-2px} .tab-title{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.tab-x{display:grid;flex:none;place-items:center;width:18px;height:18px;padding:0;border:0;border-radius:5px;background:transparent;color:rgb(var(--faint));opacity:0;transition:opacity .12s}
.tab:hover .tab-x,.tab[aria-selected=true] .tab-x{opacity:1} .tab-x:hover{background:rgb(var(--ink)/.1);color:rgb(var(--ink))} .tab-x svg{width:12px;height:12px}
.tab-btn{display:grid;flex:none;place-items:center;width:26px;height:26px;padding:0;border:0;border-radius:7px;background:transparent;color:rgb(var(--secondary))}
.tab-btn:hover,.tab-btn[aria-expanded=true]{background:rgb(var(--ink)/.08);color:rgb(var(--ink))} .tab-btn svg{width:15px;height:15px} .tab-btn:focus-visible{outline:2px solid rgb(var(--accent));outline-offset:-2px}
#tab-pin[aria-pressed=true] .pin-head{fill:currentColor}"""

TABS_HTML = """<div id=tab-bar data-drag><div class=tab-group data-nodrag><div id=tab-strip role=tablist aria-label="Open tabs"></div><button id=tab-new class=tab-btn type=button title="New tab (⌘T)" aria-label="New tab">__PLUS__</button><button id=tab-list class=tab-btn type=button title="All tabs" aria-label="All tabs" aria-haspopup=menu aria-expanded=false>__DOWN__</button><button id=tab-pin class=side-toggle type=button aria-pressed=false title="Pin tab bar (⌘⌥\\)" aria-label="Pin tab bar">__PIN__</button></div></div>"""

TABS_JS = r"""// MARK: tabs — a frame per tab in #stage (tabs_ui.py). The first adopts the frame the server drew, named `reader`;
// the rest are named once, at birth, and never again (see the module's note on WebKit's history).
const TABS={list:[],active:null},TAB_RUN=Date.now().toString(36),TAB_X=__X__;let tabSeq=0;
function makeTab(o){const id=++tabSeq,t=Object.assign({id,name:'reader-'+TAB_RUN+'-'+id,frame:null,href:'',src:'',folder:'',kind:KIND,title:'',loaded:false,used:Date.now()},o);
if(t.href&&!t.src){const q=new URLSearchParams((t.href.split('?')[1]||'').split('#')[0]);t.src=q.get('src')||'';t.folder=q.get('folder')||''}return t}
function tabById(id){return TABS.list.find(t=>t.id===id)||null}
function wireFrame(t,f){t.frame=f;f.addEventListener('load',()=>{if(t.frame===f)frameLoaded(t)})}
// A tab's frame, made when the tab is opened or first shown. Its src is set before it joins the page, so the load that
// follows is the page's own and adds nothing to history.
function tabFrame(t,href){const f=document.createElement('iframe');f.name=t.name;f.setAttribute('aria-label','Reader');f.inert=true;f.src=href||'about:blank';
const frames=stage.querySelectorAll(':scope > iframe');(frames.length?frames[frames.length-1]:home).after(f);wireFrame(t,f);return f}
// Every frame's load: the tab learns what it holds; the one showing drives the shell, and one behind that Back or Forward
// just moved comes forward.
function frameLoaded(t){const f=t.frame;t.loaded=true;let l=null;try{l=f.contentWindow.location}catch(e){}
const blank=!l||!l.href||l.href==='about:blank',q=new URLSearchParams(blank?'':l.search);
t.href=blank?'':l.pathname+l.search+l.hash;t.src=q.get('src')||'';t.folder=q.get('folder')||'';
let title='';try{title=f.contentDocument.title}catch(e){}t.title=title||(t.src?t.src.split('/').pop():'');
if(f===reader)readerLoaded();else if(!blank&&traversed(f)){activateTab(t);return}tabsChanged()}
// Bring a tab forward: its frame becomes the reader, the sidebar goes back to the view it was read in, and everything
// that follows the reader follows it (readerLoaded), as a load would. A tab not shown since a restore gets its frame now.
function activateTab(t){if(!t||t===TABS.active||!TABS.list.includes(t))return;const was=TABS.active;if(was){was.kind=KIND;was.used=Date.now()}
if(!t.frame)tabFrame(t,t.href);
if(reader&&reader!==t.frame)reader.removeAttribute('id');for(const x of TABS.list)if(x.frame)x.frame.inert=x!==t;t.frame.id='reader';
reader=t.frame;TABS.active=t;t.used=Date.now();
if(t.kind&&t.kind!==KIND&&VAULTS[t.kind])switchVault(t.kind,true);
if(t.loaded||!t.href)readerLoaded();else{showHome(false);empty.hidden=true;highlight(t.src)}
tabsChanged()}
// A new tab beside the one showing, reading `href` (a blank one is Library's home page), brought forward unless told not to.
function openTab(href,o){o=o||{};const at=TABS.list.indexOf(TABS.active),t=makeTab({href:href||'',kind:o.kind||KIND});TABS.list.splice(at+1,0,t);
tabFrame(t,href);if(o.background)tabsChanged();else activateTab(t);return t}
// Closing the tab showing brings its right-hand neighbour forward, or its left. The last tab is never closed: it goes
// home instead, and a lone home tab says no, so the window can close in its place.
function closeTab(t){t=t||TABS.active;const i=TABS.list.indexOf(t);if(i<0)return false;
if(TABS.list.length===1){if(KIND==='library'&&!home.hidden)return false;if(KIND==='library')goHome();else switchVault('library');return true}
if(t===TABS.active)activateTab(TABS.list[i+1]||TABS.list[i-1]);TABS.list.splice(i,1);if(t.frame)t.frame.remove();t.frame=null;tabsChanged();return true}
function closeOtherTabs(){for(const t of TABS.list.slice())if(t!==TABS.active)closeTab(t)}
// A plain `target=reader` link goes to the tab showing: pointed there as it is clicked (Return on a focused link clicks it too).
document.addEventListener('click',e=>{const a=e.target instanceof Element?e.target.closest('a[target]'):null;if(a&&reader&&/^reader(-|$)/.test(a.target))a.target=reader.name},true);
{const t=makeTab({name:'reader'});TABS.list.push(t);TABS.active=t;wireFrame(t,reader)}
// MARK: tab bar — the pills, one per tab, the tab showing marked; + for a new one and ⌄ for the whole list.
const tabBar=$('#tab-bar'),tabGroup=tabBar.querySelector('.tab-group'),tabStrip=$('#tab-strip'),tabPin=$('#tab-pin'),tabList=$('#tab-list');
function tabLabel(t){return t.title||(t.src?t.src.split('/').pop():t.href?'Loading…':'New Tab')}
function drawTabs(){const had=tabStrip.contains(document.activeElement);tabStrip.innerHTML=TABS.list.map(t=>{const on=t===TABS.active,l=esc(tabLabel(t));
return `<div class=tab role=tab id=tab-${t.id} data-id=${t.id} aria-selected=${on} tabindex=${on?0:-1} title="${l}"><span class=tab-title>${l}</span><button class=tab-x type=button tabindex=-1 title="Close tab (⌘W)" aria-label="Close ${l}">${TAB_X}</button></div>`}).join('');
if(had){const p=TABS.active&&document.getElementById('tab-'+TABS.active.id);if(p)p.focus()}}
function tabsChanged(){drawTabs()}
tabStrip.addEventListener('click',e=>{const p=e.target.closest('.tab');if(!p)return;const t=tabById(+p.dataset.id);if(e.target.closest('.tab-x'))closeTab(t);else activateTab(t)});
// The middle button closes a pill, as in every browser; its mousedown is kept from starting the page's autoscroll.
tabStrip.addEventListener('mousedown',e=>{if(e.button===1)e.preventDefault()});
tabStrip.addEventListener('auxclick',e=>{const p=e.button===1&&e.target.closest('.tab');if(p){e.preventDefault();closeTab(tabById(+p.dataset.id))}});
tabStrip.addEventListener('keydown',e=>{const p=e.target.closest('.tab');if(!p)return;const i=TABS.list.indexOf(tabById(+p.dataset.id)),n=TABS.list.length;
const to=e.key==='ArrowRight'?(i+1)%n:e.key==='ArrowLeft'?(i+n-1)%n:e.key==='Home'?0:e.key==='End'?n-1:-1;
if(to>=0){e.preventDefault();activateTab(TABS.list[to]);const q=document.getElementById('tab-'+TABS.list[to].id);if(q)q.focus()}
else if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();closeTab(TABS.list[i])}});
$('#tab-new').onclick=()=>openTab('',{kind:'library'});
// ⌄: every tab, the one showing checked, and Close Other Tabs. The bar stays out while the list is open.
let tabMenuOpen=false;
tabList.onclick=()=>{if(!window.OnyxMenu)return;const r=tabList.getBoundingClientRect(),items=TABS.list.map(t=>({id:'tab:'+t.id,label:tabLabel(t),checked:t===TABS.active}));
items.push({separator:true},{id:'close-others',label:'Close Other Tabs',enabled:TABS.list.length>1});tabMenuOpen=true;tabList.setAttribute('aria-expanded','true');
OnyxMenu.open({items,x:r.left,y:r.bottom+4,label:'Tabs',returnFocus:tabList,onClose:()=>{tabMenuOpen=false;tabList.setAttribute('aria-expanded','false');if(!tabsOver)tabsLater()},
onSelect:id=>{if(id==='close-others')closeOtherTabs();else activateTab(tabById(+id.slice(4)))}})};
// MARK: tab bar reveal and pin — as the sidebar's (a 40 ms beat to come out, 400 ms of grace to go, polled again while in
// use), watched from the reader's own pointer moves as the outline's edge is; on the home page, from the stage's.
const TABBAR_KEY='askw:vault:tabbar';let tabsOver=false,tabsTimer=0,tabsAtEdge=false;
function tabsPinned(){return document.body.classList.contains('tabs-pinned')}
function tabsOut(out){document.body.classList.toggle('tabs-out',out);tabGroup.inert=!tabsPinned()&&!out}
function tabsInUse(){const a=document.activeElement;return !!((tabMenuOpen&&window.OnyxMenu&&OnyxMenu.isOpen())||(a&&tabBar.contains(a)))}
function tabsLater(){clearTimeout(tabsTimer);if(tabsPinned())return;tabsTimer=setTimeout(function check(){if(tabsPinned()||tabsOver)return;if(tabsInUse()){tabsTimer=setTimeout(check,400);return}tabsOut(false)},400)}
function setTabsPinned(on){document.body.classList.toggle('tabs-pinned',on);store(TABBAR_KEY,on?'pinned':'');tabPin.setAttribute('aria-pressed',String(on));const verb=on?'Unpin':'Pin';tabPin.title=verb+' tab bar (⌘⌥\\)';tabPin.setAttribute('aria-label',verb+' tab bar');
clearTimeout(tabsTimer);tabsAtEdge=false;tabsOut(!on&&(tabsOver||tabsInUse()));if(!on&&!tabsOver)tabsLater()}
// The top 24 px of the reader's middle 60 %: clear of the traffic lights on the left and the corner's floaters on the right.
function tabsZone(x,y,w){return y>=0&&y<=24&&Math.abs(x-w/2)<=w*.3}
function tabsAt(at){if(at===tabsAtEdge)return;tabsAtEdge=at;clearTimeout(tabsTimer);if(at)tabsTimer=setTimeout(()=>tabsOut(true),40);else if(document.body.classList.contains('tabs-out')&&!tabsOver)tabsLater()}
function tabsEdgeMove(e){if(tabsPinned())return;const w=e.view||window;if(w===window){const r=stage.getBoundingClientRect(),t=e.target;tabsAt(!(t.closest&&t.closest('.find-bar,.outline-toggle'))&&tabsZone(e.clientX-r.left,e.clientY-r.top,r.width));return}tabsAt(tabsZone(e.clientX,e.clientY,w.innerWidth))}
function tabsEdgeLeave(){if(!tabsPinned())tabsAt(false)}
function tabsKey(e){if(e.code==='Backslash'&&e.altKey&&(e.metaKey||e.ctrlKey)&&!e.shiftKey){e.preventDefault();setTabsPinned(!tabsPinned())}}
stage.addEventListener('mousemove',tabsEdgeMove,{passive:true});stage.addEventListener('mouseleave',tabsEdgeLeave);
tabGroup.addEventListener('mouseenter',()=>{tabsOver=true;clearTimeout(tabsTimer)});tabGroup.addEventListener('mouseleave',()=>{tabsOver=false;tabsLater()});
tabGroup.addEventListener('focusout',()=>{if(!tabsOver)tabsLater()});
tabGroup.addEventListener('keydown',e=>{if(e.key==='Escape'&&!tabsPinned()){document.activeElement.blur();tabsOut(false)}});
tabPin.onclick=()=>setTabsPinned(!tabsPinned());document.addEventListener('keydown',tabsKey);
onReaderLoad(()=>{tabsAtEdge=false;try{const w=reader.contentWindow;w.addEventListener('mousemove',tabsEdgeMove,{passive:true});w.addEventListener('keydown',tabsKey);w.document.documentElement.addEventListener('mouseleave',tabsEdgeLeave)}catch(e){}});
// Put back as it was left without a glide: pinned under tabs-still, lifted once the first frame has painted.
if(recall(TABBAR_KEY)==='pinned'){document.body.classList.add('tabs-still');setTabsPinned(true);requestAnimationFrame(()=>requestAnimationFrame(()=>document.body.classList.remove('tabs-still')))}else tabsOut(false);
drawTabs();"""


def tabs_style() -> str:
    return TABS_CSS


def tabs_markup(pin_icon: str) -> str:
    return TABS_HTML.replace("__PLUS__", PLUS_ICON).replace("__DOWN__", DOWN_ICON).replace("__PIN__", pin_icon)


def tabs_script() -> str:
    return TABS_JS.replace("__X__", json.dumps(ICONS["close"]))
