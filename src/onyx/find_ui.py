"""⌘F: find in the page the reader shows, as Safari and Obsidian do. A bar at the reader's top-right.

It searches that page and only it: not the sidebar, not the outline, not Onyx's own answer panel (``.askw-root``). The
page's text is read as it is drawn, so what the page hides is left out, except text folded inside a shut ``<details>``,
which a match there opens. A match ignores case and accents, and runs across a bold word or a link but never from one
paragraph into the next. Every match is tinted with the CSS Custom Highlight API and the current one more strongly, so
the page itself is never edited: an artifact's scripts, a selection, live reload and the reading position don't notice.

↩ and ⌘G go to the next match, ⇧↩ and ⇧⌘G to the one before, and bring it to the middle of the reader at once. Escape
puts the bar away and leaves that match selected, as Safari does, so a right-click can ask about it. A short passage
selected in the page becomes the query. The bar stays open from page to page and counts the new page's matches without
moving it, and counts again when a page's script adds text or a click changes what shows (the Artifact Kit's ELI switch
is CSS alone, which no observer sees).

It opens from Edit ▸ Find in the app (``window.onyxShell.find``), and from ⌘F in a browser or with focus inside the
reader. The script runs inside the shell's ``<script>`` and leans on it: ``$``, ``reader``, ``readerDoc``,
``readerPage``, ``pillRoom`` and ``hidePeek``.
"""

from __future__ import annotations

from .palette_ui import SEARCH_ICON
from .panels_ui import ICONS

# lucide's chevron-up and chevron-down, on the panels' 24 grid.
_CHEVRON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path d="{d}"/></svg>'
)
UP_ICON, DOWN_ICON = _CHEVRON.format(d="m18 15-6-6-6 6"), _CHEVRON.format(d="m6 9 6 6 6-6")

FIND_CSS = """/* Find in page (⌘F, find_ui.py): a bar at the reader's top-right, left of the outline's toggle (in the corner itself
   while the outline is docked), in the toggle's glass; the context pill steps left of it (pillRoom). Shown at once. */
.find-bar{position:absolute;top:12px;right:50px;z-index:3;display:flex;align-items:center;gap:1px;width:min(340px,calc(100% - 62px));height:32px;padding:0 4px 0 10px;border:1px solid var(--line);border-radius:10px;background:rgb(var(--bg-elevated)/.94);color:rgb(var(--ink));box-shadow:0 8px 24px rgb(0 0 0/.14),0 1px 2px rgb(0 0 0/.06);backdrop-filter:blur(14px) saturate(1.8);-webkit-backdrop-filter:blur(14px) saturate(1.8)}
.find-bar[hidden],body.reader-blank .find-bar,#home:not([hidden])~.find-bar{display:none} body.outline-docked .find-bar{right:12px;width:min(340px,calc(100% - 24px))}
.find-bar:focus-within{border-color:rgb(var(--accent)/.55);box-shadow:0 8px 24px rgb(0 0 0/.14),0 0 0 2px rgb(var(--accent)/.18)}
.find-bar>svg{flex:none;width:14px;height:14px;color:rgb(var(--faint))}
#find-input{flex:1;min-width:0;margin:0 0 0 7px;padding:0;border:0;border-radius:0;background:transparent;color:rgb(var(--ink));font-size:13px;outline:none;box-shadow:none} #find-input::placeholder{color:rgb(var(--faint))}
.find-count{flex:none;padding:0 6px 0 4px;color:rgb(var(--muted));font-size:11.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.find-btn{display:grid;flex:none;place-items:center;width:24px;height:24px;padding:0;border:0;border-radius:6px;background:transparent;color:rgb(var(--secondary));transition:background-color 75ms,color 75ms}
.find-btn:hover:not(:disabled){background:rgb(var(--ink)/.08);color:rgb(var(--ink))} .find-btn:disabled{opacity:.35;cursor:default} .find-btn svg{width:14px;height:14px} .find-btn:focus-visible{outline:2px solid rgb(var(--accent));outline-offset:-2px}
@media(prefers-reduced-transparency:reduce){.find-bar{background:rgb(var(--bg-elevated));backdrop-filter:none;-webkit-backdrop-filter:none}}"""

FIND_HTML = """<div id=find-bar class=find-bar role=search aria-label="Find in page" hidden>__SEARCH__<input id=find-input type=text placeholder="Find in page" autocomplete=off spellcheck=false aria-label="Find in page" aria-describedby=find-count><span id=find-count class=find-count aria-live=polite></span><button id=find-prev class=find-btn type=button title="Previous match (⇧⌘G)" aria-label="Previous match" disabled>__UP__</button><button id=find-next class=find-btn type=button title="Next match (⌘G)" aria-label="Next match" disabled>__DOWN__</button><button id=find-close class=find-btn type=button title="Done (esc)" aria-label="Close find">__CLOSE__</button></div>"""

FIND_JS = r"""// MARK: find in page — ⌘F (find_ui.py). The page's text is read once while the bar is open (INDEX; again when the page
// changes), folded one character for one, so a match's place in that text is a place in the page. Ranges are made only
// for matches, and drawn as highlights: nothing is added to the page but two tints in an adopted sheet.
const FIND=(()=>{const bar=$('#find-bar'),input=$('#find-input'),count=$('#find-count'),prevBtn=$('#find-prev'),nextBtn=$('#find-next');
const CAP=1000,SKIP='script,style,noscript,template,textarea,select,title,desc,.askw-root',F=NodeFilter,FOLDS=new Map(),DRESSED=new WeakSet();
const MARKS='::highlight(onyx-find){background-color:rgb(255 212 0/.4)} ::highlight(onyx-find-current){background-color:rgb(255 150 0);color:rgb(20 20 20)}';
let doc=null,INDEX=null,hits=[],cur=-1,capped=false,stale=false,watcher=null,timer=0,raf=0;
// Lower case and without its accent (é → e), one character for one, so "resume" finds "Résumé".
function fold(c){const n=c.charCodeAt(0);if(n<128)return n>64&&n<91?String.fromCharCode(n+32):c;let f=FOLDS.get(c);if(f===undefined){f=c.normalize('NFD').charAt(0).toLowerCase().charAt(0)||c;FOLDS.set(c,f)}return f}
function space(n){return n===32||(n>8&&n<14)||n===160||(n>=0x2000&&n<=0x200a)||n===0x2028||n===0x2029||n===0x202f||n===0x205f||n===0x3000}
// A soft hyphen or a zero-width space is in the text but not in the word it splits.
function unseen(n){return n===0xad||(n>=0x200b&&n<=0x200d)||n===0x2060||n===0xfeff}
function ours(n){const el=n&&(n.nodeType===1?n:n.parentElement);return !!(el&&el.closest&&el.closest('.askw-root'))}
// The page's text as it is drawn. An element the page hides takes its text with it, but a shut <details> keeps its own
// (a match there opens it). A run of space is one space, and each block (a paragraph, a cell, a list item) starts on a
// line break no query can hold, so nothing matches from one into the next. at/off say where each character came from.
function collect(d){const w=d.defaultView,css=el=>w.getComputedStyle(el),blocks=new Map(),seen=new Map(),nodes=[],at=[],off=[],out=[];let last=10,block=null;
const shown=el=>{let v=seen.get(el);if(v===undefined){v=css(el).visibility==='visible';seen.set(el,v)}return v};
const blockOf=el=>{let b=blocks.get(el);if(b===undefined){b=el!==d.body&&el.parentElement&&/^(inline|contents|ruby)/.test(css(el).display)?blockOf(el.parentElement):el;blocks.set(el,b)}return b};
const put=(ch,node,i)=>{out.push(ch);at.push(node);off.push(i);last=ch.charCodeAt(0)};
const walker=d.createTreeWalker(d.body,F.SHOW_ELEMENT|F.SHOW_TEXT,{acceptNode:n=>n.nodeType===3?(n.parentElement&&shown(n.parentElement)?F.FILTER_ACCEPT:F.FILTER_SKIP):n.matches(SKIP)||css(n).display==='none'?F.FILTER_REJECT:n.tagName==='BR'?F.FILTER_ACCEPT:F.FILTER_SKIP});
for(let n=walker.nextNode();n;n=walker.nextNode()){if(n.nodeType===1){if(last!==32&&last!==10)put(' ',-1,0);continue}
const b=blockOf(n.parentElement);if(b!==block){block=b;if(last!==10)put('\n',-1,0)}const s=n.nodeValue,i=nodes.push(n)-1;
for(let k=0;k<s.length;k++){const c=s.charCodeAt(k);if(unseen(c))continue;if(space(c)){if(last!==32&&last!==10)put(' ',i,k)}else put(fold(s[k]),i,k)}}
return {doc:d,text:out.join(''),at,off,nodes}}
// The query, folded as the page's text is. Leading space is dropped; a space typed at the end still counts, as it is typed.
function queryOf(){const q=input.value,out=[];let last=32;for(let k=0;k<q.length;k++){const c=q.charCodeAt(k);if(unseen(c))continue;if(space(c)){if(last!==32)out.push(' ');last=32}else{out.push(fold(q[k]));last=c}}return out.join('')}
// Every match left to right, as a Range: at most CAP of them, past which the count says "+".
function search(q){hits=[];capped=false;if(!q.trim()||!INDEX)return;const {doc:d,text,at,off,nodes}=INDEX;
for(let i=text.indexOf(q);i>=0;i=text.indexOf(q,i+q.length)){if(hits.length===CAP){capped=true;break}let a=i,b=i+q.length-1;while(a<=b&&at[a]<0)a++;while(b>=a&&at[b]<0)b--;if(a>b)continue;
const r=d.createRange();try{r.setStart(nodes[at[a]],off[a]);r.setEnd(nodes[at[b]],off[b]+1)}catch(e){continue}hits.push(r)}}
function inSummary(d,node){const s=d.querySelector(':scope > summary');return !!s&&s.contains(node)}
// Where a match is drawn. One inside a shut <details> is not drawn, so the outermost shut one stands in for it.
function rectOf(r){const b=r.getBoundingClientRect();if(b.width||b.height)return b;let shut=null;for(let el=r.startContainer.parentElement;el;el=el.parentElement)if(el.tagName==='DETAILS'&&!el.open&&!inSummary(el,r.startContainer))shut=el;return shut?shut.getBoundingClientRect():b}
// The first match at or after `anchor` (the one the bar was on, so a refined query stays where it is), or, with none,
// the first at or below the top of the reader.
function pick(anchor){if(!hits.length)return -1;let i=-1;try{i=anchor?hits.findIndex(r=>r.compareBoundaryPoints(Range.START_TO_START,anchor)>=0):hits.findIndex(r=>rectOf(r).bottom>0)}catch(e){}return i<0?0:i}
// A page's `scroll-behavior:smooth` would glide there; find goes at once, as Obsidian's does.
function instant(el,move){const had=el.getAttribute('style'),was=el.style.scrollBehavior;el.style.scrollBehavior='auto';move();if(had===null)el.removeAttribute('style');else el.style.scrollBehavior=was}
// The current match in the middle of the reader, at once: its shut <details> opened, and any box it scrolls inside
// scrolled to it too. Left where it is when it is already in view, clear of the bar.
function reveal(){const r=hits[cur];if(!r)return;const node=r.startContainer,w=doc.defaultView;
for(let el=node.parentElement;el;el=el.parentElement)if(el.tagName==='DETAILS'&&!el.open&&!inSummary(el,node))el.open=true;let b=r.getBoundingClientRect();
for(let el=node.parentElement;el&&el!==doc.documentElement;el=el.parentElement){if(el.scrollHeight<=el.clientHeight+1&&el.scrollWidth<=el.clientWidth+1)continue;const s=w.getComputedStyle(el);if(!/auto|scroll|overlay/.test(s.overflowX+' '+s.overflowY))continue;const box=el.getBoundingClientRect();
if(b.top<box.top||b.bottom>box.bottom)instant(el,()=>{el.scrollTop+=b.top-box.top-(el.clientHeight-b.height)/2});if(b.left<box.left||b.right>box.right)instant(el,()=>{el.scrollLeft+=b.left-box.left-(el.clientWidth-b.width)/2});b=r.getBoundingClientRect()}
if(b.top<56||b.bottom>w.innerHeight-24)instant(doc.documentElement,()=>w.scrollBy(0,b.top-(w.innerHeight-b.height)/2))}
// Every match tinted, the current one stronger. An engine without highlights shows the current one as the selection.
function paint(){const w=doc&&doc.defaultView;if(!w)return;const reg=w.CSS&&w.CSS.highlights;
if(!reg||!w.Highlight){if(cur>=0){const s=doc.getSelection();s.removeAllRanges();s.addRange(hits[cur])}return}
const all=new w.Highlight(...hits);if(cur>=0)all.delete(hits[cur]);reg.set('onyx-find',all);if(cur>=0)reg.set('onyx-find-current',new w.Highlight(hits[cur]));else reg.delete('onyx-find-current')}
function unpaint(){try{const reg=doc.defaultView.CSS.highlights;reg.delete('onyx-find');reg.delete('onyx-find-current')}catch(e){}}
function show(){const n=hits.length,plus=capped?'+':'';count.textContent=!queryOf().trim()?'':!n?'No matches':cur<0?n+plus+(n===1&&!capped?' match':' matches'):`${cur+1} of ${n}${plus}`;prevBtn.disabled=nextBtn.disabled=!n}
// The two tints, in a sheet the page adopts, so no element is added to it.
function dress(d){if(DRESSED.has(d))return;DRESSED.add(d);try{const s=new d.defaultView.CSSStyleSheet();s.replaceSync(MARKS);d.adoptedStyleSheets=[...d.adoptedStyleSheets,s]}catch(e){const s=d.createElement('style');s.textContent=MARKS;(d.head||d.documentElement).appendChild(s)}}
// The bar follows one page at a time. A change in it (outside Onyx's own panel, which streams answers) counts again a beat
// later; so does a click or a changed control, since what a page shows can change with CSS alone.
function changed(r){if(ours(r.target))return false;if(r.type!=='childList')return true;for(const n of r.addedNodes)if(!ours(n))return true;for(const n of r.removedNodes)if(!ours(n))return true;return false}
function attach(d){detach();doc=d;dress(d);watcher=new MutationObserver(recs=>{if(recs.some(changed))later()});watcher.observe(d.body,{childList:true,subtree:true,characterData:true,attributeFilter:['hidden']})}
function detach(){clearTimeout(timer);if(watcher)watcher.disconnect();watcher=null;if(doc)unpaint();doc=INDEX=null;hits=[];cur=-1;stale=false}
function later(){stale=true;clearTimeout(timer);if(doc&&!bar.hidden)timer=setTimeout(()=>{if(doc&&!bar.hidden)refind('keep')},250)}
function poke(e){if(doc&&!bar.hidden&&!ours(e.target))later()}
// Find the query again, over the page's text read afresh if it has changed. 'jump' (typing, opening) puts the current
// match at the first from `from` (the one it was on, or the top of the reader) and brings it into view; 'keep' (the page
// changed) keeps it where it was, unmoved; 'new' (another page) has none until ↩: nothing moves under the reader unasked.
function refind(how,from){const anchor=from!==undefined?from:how==='new'?null:hits[cur]||null,had=cur>=0,q=queryOf();if(q.trim()&&(!INDEX||stale)){INDEX=collect(doc);stale=false}search(q);
cur=!hits.length||how==='new'||(how==='keep'&&!had)?-1:pick(anchor);paint();if(how==='jump'&&cur>=0)reveal();show()}
// The page in the reader, if it is still there: after a load the bar follows the new one; with none, it goes.
function sync(){const d=readerDoc();if(!d){close(false);return false}if(d!==doc){attach(d);refind('new')}return true}
function find(){raf=0;if(sync())refind('jump')}
function go(dir){if(raf){cancelAnimationFrame(raf);find()}if(!sync())return;if(stale)refind('keep');const n=hits.length;if(!n)return;const first=cur<0?pick(null):0;
cur=cur<0?(dir>0?first:(first+n-1)%n):(cur+dir+n)%n;paint();reveal();show()}
function selected(d){try{const t=String(d.getSelection()||'').trim();return t&&t.length<=120&&!/[\n\r]/.test(t)?t.replace(/\s+/g,' '):''}catch(e){return ''}}
// ⌘F: the bar, its field selected. A short passage selected in the page becomes the query, its current match the one
// selected. Open already, ⌘F only selects the field, unless that passage is new.
function open(seed){const d=readerDoc();if(!d||document.querySelector('dialog[open]'))return true;if(window.OnyxMenu)OnyxMenu.close();hidePeek();
const picked=seed?selected(d):'',fresh=bar.hidden||d!==doc||(!!picked&&picked!==input.value);let from=null;if(picked){input.value=picked;try{from=d.getSelection().getRangeAt(0)}catch(e){}}
if(bar.hidden){bar.hidden=false;pillRoom()}input.focus();input.select();if(fresh){if(d!==doc)attach(d);refind('jump',from)}return true}
// Escape, the ×, or the page gone: the tints go, and the match the bar was on is left selected in the reader, which has
// the focus back, as in Safari, so a right-click can ask about it.
function close(select){if(bar.hidden)return;const r=select?hits[cur]:null,d=doc;detach();bar.hidden=true;pillRoom();
try{if(d&&d===readerDoc()){reader.contentWindow.focus();if(r){const s=d.getSelection();s.removeAllRanges();s.addRange(r)}}else if(document.activeElement===input)input.blur()}catch(e){}}
// Edit ▸ Find's three items (the app menu, through window.onyxShell.find) and ⌘G: the next or previous match, the bar
// opened for it if it was away; nothing while there is no query.
function run(verb){if(verb==='open')return open(true);if(!queryOf().trim())return true;if(bar.hidden)open(false);else go(verb==='previous'?-1:1);return true}
// ⌘F and ⌘G from the shell and from inside the reader. Without a page, or under a dialog, they are left to the browser.
function key(e){if(!(e.metaKey||e.ctrlKey)||e.altKey||!readerDoc()||document.querySelector('dialog[open]'))return;const k=(e.key||'').toLowerCase();
if(k==='f'&&!e.shiftKey){e.preventDefault();open(true)}else if(k==='g'&&queryOf().trim()){e.preventDefault();run(e.shiftKey?'previous':'next')}}
function hook(w){try{w.addEventListener('keydown',key);w.addEventListener('click',poke,true);w.addEventListener('change',poke,true)}catch(e){}}
document.addEventListener('keydown',key);
reader.addEventListener('load',()=>{hook(reader.contentWindow);if(!bar.hidden)sync()});
if(readerPage())hook(reader.contentWindow);
input.addEventListener('input',()=>{cancelAnimationFrame(raf);raf=requestAnimationFrame(find)});
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();go(e.shiftKey?-1:1)}});
bar.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true)}});
// The arrows keep the focus in the field, so typing goes on after a click.
bar.addEventListener('mousedown',e=>{if(e.target.closest('button'))e.preventDefault()});
prevBtn.onclick=()=>go(-1);nextBtn.onclick=()=>go(1);$('#find-close').onclick=()=>close(true);
return {run,open,close}})();"""


def find_style() -> str:
    return FIND_CSS


def find_markup() -> str:
    return (
        FIND_HTML.replace("__SEARCH__", SEARCH_ICON)
        .replace("__UP__", UP_ICON)
        .replace("__DOWN__", DOWN_ICON)
        .replace("__CLOSE__", ICONS["close"])
    )


def find_script() -> str:
    return FIND_JS
