"""Tabs: several notes and artifacts open at once, each in a reader frame of its own.

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

The script runs inside the shell's ``<script>`` and leans on it: ``$``, ``reader`` (reassigned here), ``stage``,
``KIND``, ``VAULTS``, ``switchVault``, ``showHome``, ``empty``, ``home``, ``goHome``, ``highlight``, ``readerPage``,
``traversed`` and ``readerLoaded``.
"""

from __future__ import annotations

TABS_JS = r"""// MARK: tabs — a frame per tab in #stage (tabs_ui.py). The first adopts the frame the server drew, named `reader`;
// the rest are named once, at birth, and never again (see the module's note on WebKit's history).
const TABS={list:[],active:null},TAB_RUN=Date.now().toString(36);let tabSeq=0;
function makeTab(o){const id=++tabSeq;return Object.assign({id,name:'reader-'+TAB_RUN+'-'+id,frame:null,href:'',src:'',folder:'',kind:KIND,title:'',loaded:false,used:Date.now()},o)}
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
function tabsChanged(){}
// A plain `target=reader` link goes to the tab showing: pointed there as it is clicked (Return on a focused link clicks it too).
document.addEventListener('click',e=>{const a=e.target instanceof Element?e.target.closest('a[target]'):null;if(a&&reader&&/^reader(-|$)/.test(a.target))a.target=reader.name},true);
{const t=makeTab({name:'reader'});TABS.list.push(t);TABS.active=t;wireFrame(t,reader)}"""


def tabs_script() -> str:
    return TABS_JS
