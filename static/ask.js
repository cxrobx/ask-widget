/* ask-widget — highlight-to-ask reading companion.
 *
 * Drop this on any local HTML page:
 *   <script src="http://localhost:8899/ask.js"></script>
 *
 * Highlight text, right-click -> ELI5 / Prove it / Ask a question. Each answer
 * streams from a `claude` CLI call on the local server with full context of a
 * folder you point it at. Single self-contained file (no CDN/deps): it injects
 * its own CSS, renders Markdown with a built-in renderer, and builds all DOM
 * under <body>.
 */
(function () {
  'use strict';
  if (window.__askWidget) return;
  window.__askWidget = true;

  // Origin of the server that served this script (standard currentScript trick).
  var SERVER = (function () {
    try { return new URL(document.currentScript.src).origin; }
    catch (e) { return window.location.origin; }
  })();
  // Replaced server-side at /ask.js serve time with the per-server random token.
  var TOKEN = '__ASK_TOKEN__';

  var MAX_SEL = 4000;
  var MAX_CTX = 600;
  var PANEL_W = 380;

  // ---- state ----
  var folder = null;
  var defaultFolder = null;
  var recentFolders = [];
  var sel = null;              // { text, context, rect }
  var abort = null;            // AbortController for the active stream
  var activeAction = null;     // 'eli5' | 'prove' | 'ask'
  var userPinned = false;      // user dragged/resized the panel → stop auto-positioning
  var isFileProto = window.location.protocol === 'file:';
  var lastAnswer = '', lastAction = null, lastQuestion = '';   // for "Open in Claude"
  var transcript = [];         // completed turns: { role:'user'|'assistant', text }
  var liveEl = null;           // the .askw-a element receiving the current stream
  var streaming = false;       // a /ask stream is in flight (defer live-reload while true)
  // ---- live reload (only on /view pages, which seed askw-src) ----
  var reloadSrc = null, reloadSig = null, reloadSeen = null, reloadPending = false;

  // ---- DOM refs ----
  var menuEl, askWrap, askInput;
  var panelEl, panelTitle, panelSel, panelTools, panelBody, claudeBtn;
  var followWrap, followInput, followGo;
  var pillEl, pillLabel, pickerEl, toastEl;

  // ============================================================ styles
  var CSS = [
    '.askw-root{all:revert;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1c1917;line-height:1.5;}',
    '.askw-root *{box-sizing:border-box;}',
    '.askw-menu{position:fixed;z-index:2147483600;display:none;min-width:190px;background:#fff;border:1px solid #e7e5e4;border-radius:10px;box-shadow:0 12px 32px rgba(0,0,0,.18);padding:6px;font-size:13px;}',
    '.askw-item{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;cursor:pointer;color:#292524;user-select:none;}',
    '.askw-item:hover{background:#f5f5f4;}',
    '.askw-item .askw-ico{width:16px;text-align:center;opacity:.75;}',
    '.askw-ask-wrap{display:none;padding:6px 6px 4px;border-top:1px solid #f0efed;margin-top:4px;}',
    '.askw-ask-wrap.open{display:block;}',
    '.askw-ask-input{width:100%;min-height:54px;resize:vertical;border:1px solid #d6d3d1;border-radius:7px;padding:7px 9px;font:inherit;font-size:13px;color:#1c1917;outline:none;}',
    '.askw-ask-input:focus{border-color:#c2410c;}',
    '.askw-ask-go{margin-top:6px;float:right;background:#c2410c;color:#fff;border:none;border-radius:7px;padding:6px 14px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;}',
    '.askw-ask-go:disabled{opacity:.45;cursor:not-allowed;}',
    '.askw-panel{position:fixed;z-index:2147483601;display:none;width:' + PANEL_W + 'px;height:auto;min-width:300px;min-height:180px;max-width:96vw;max-height:92vh;background:#fffdfb;border:1px solid #e7e5e4;border-radius:14px;box-shadow:0 18px 48px rgba(0,0,0,.22);overflow:hidden;flex-direction:column;resize:both;}',
    '.askw-panel.open{display:flex;}',
    '.askw-head{padding:13px 40px 11px 15px;border-bottom:1px solid #f0efed;position:relative;flex:0 0 auto;cursor:move;user-select:none;}',
    '.askw-eyebrow{font-size:10px;letter-spacing:.08em;text-transform:uppercase;font-weight:700;color:#c2410c;margin:0 0 3px;}',
    '.askw-selq{font-size:12.5px;color:#57534e;margin:0;max-height:46px;overflow:hidden;}',
    '.askw-x{position:absolute;top:9px;right:9px;width:26px;height:26px;border:none;background:transparent;color:#a8a29e;font-size:17px;line-height:1;border-radius:6px;cursor:pointer;}',
    '.askw-x:hover{background:#f5f5f4;color:#44403c;}',
    '.askw-tools{display:flex;flex-wrap:wrap;gap:6px;padding:9px 15px 0;flex:0 0 auto;}',
    '.askw-pillt{display:inline-flex;align-items:center;gap:5px;background:rgba(194,65,12,.09);color:#9a3412;font-size:10.5px;font-weight:600;padding:3px 9px;border-radius:999px;}',
    '.askw-dot{width:6px;height:6px;border-radius:50%;background:#ea580c;animation:askw-pulse 1.1s infinite;}',
    '@keyframes askw-pulse{0%,100%{opacity:.35}50%{opacity:1}}',
    '.askw-body{padding:12px 15px 15px;overflow-y:auto;font-size:14px;line-height:1.55;color:#292524;flex:1 1 auto;min-height:0;}',
    '.askw-q{margin:15px 0 9px;padding:7px 11px;background:#faf7f4;border:1px solid #f1e7dd;border-radius:9px;font-size:13px;color:#57534e;white-space:pre-wrap;}',
    '.askw-q:first-child{margin-top:1px;}',
    '.askw-a{font-size:14px;}',
    '.askw-body p{margin:0 0 9px;}.askw-body p:last-child{margin-bottom:0;}',
    '.askw-body ul,.askw-body ol{margin:0 0 9px;padding-left:20px;}.askw-body li{margin:2px 0;}',
    '.askw-body code{background:#f5f5f4;border-radius:4px;padding:1px 5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;}',
    '.askw-body pre{background:#1c1917;color:#fafaf9;border-radius:8px;padding:11px 13px;overflow-x:auto;font-size:12.5px;}',
    '.askw-body pre code{background:transparent;color:inherit;padding:0;}',
    '.askw-body h1,.askw-body h2,.askw-body h3{font-size:14.5px;margin:12px 0 6px;font-weight:700;}',
    '.askw-body a{color:#c2410c;}',
    '.askw-fallback{white-space:pre-wrap;}',
    '.askw-think{display:flex;align-items:center;gap:8px;color:#78716c;font-size:13px;}',
    '.askw-err{color:#b91c1c;font-size:13px;}',
    '.askw-foot{display:flex;justify-content:flex-end;gap:8px;padding:8px 13px;border-top:1px solid #f0efed;flex:0 0 auto;}',
    '.askw-foot button{background:#f5f5f4;border:1px solid #e7e5e4;border-radius:7px;padding:5px 11px;font:inherit;font-size:12px;color:#44403c;cursor:pointer;}',
    '.askw-foot button:hover{background:#eceae8;}',
    '.askw-foot .askw-claude{background:#c2410c;border-color:#c2410c;color:#fff;margin-right:auto;}',
    '.askw-foot .askw-claude:hover{background:#9a3412;}',
    '.askw-foot .askw-claude:disabled{opacity:.55;cursor:default;}',
    '.askw-followup{display:none;align-items:flex-end;gap:7px;padding:9px 13px;border-top:1px solid #f0efed;flex:0 0 auto;}',
    '.askw-follow-input{flex:1 1 auto;resize:none;max-height:96px;min-height:34px;border:1px solid #d6d3d1;border-radius:9px;padding:7px 10px;font:inherit;font-size:13px;color:#1c1917;outline:none;line-height:1.4;}',
    '.askw-follow-input:focus{border-color:#c2410c;}',
    '.askw-follow-input:disabled{opacity:.55;background:#fafaf9;}',
    '.askw-follow-go{flex:0 0 auto;width:34px;height:34px;background:#c2410c;color:#fff;border:none;border-radius:9px;font-size:15px;line-height:1;cursor:pointer;}',
    '.askw-follow-go:hover{background:#9a3412;}',
    '.askw-follow-go:disabled{opacity:.4;cursor:not-allowed;}',
    '.askw-toast{position:fixed;bottom:24px;left:50%;z-index:2147483603;background:#1c1917;color:#fafaf9;padding:9px 16px;border-radius:10px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.3);opacity:0;pointer-events:none;transform:translateX(-50%) translateY(8px);transition:opacity .15s,transform .15s;}',
    '.askw-toast.show{opacity:1;transform:translateX(-50%) translateY(0);}',
    '.askw-pill{position:fixed;top:12px;right:12px;z-index:2147483599;display:flex;align-items:center;gap:6px;max-width:240px;background:rgba(255,253,251,.95);border:1px solid #e7e5e4;border-radius:999px;box-shadow:0 4px 14px rgba(0,0,0,.12);padding:5px 11px;font-size:11.5px;color:#57534e;cursor:pointer;}',
    '.askw-pill .askw-ico{color:#c2410c;}',
    '.askw-pill b{color:#1c1917;font-weight:600;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    '.askw-picker{position:fixed;top:42px;right:12px;z-index:2147483602;display:none;width:300px;background:#fff;border:1px solid #e7e5e4;border-radius:12px;box-shadow:0 14px 38px rgba(0,0,0,.2);padding:10px;font-size:12.5px;}',
    '.askw-picker.open{display:block;}',
    '.askw-picker label{display:block;font-weight:600;color:#44403c;margin:0 0 5px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;}',
    '.askw-picker input{width:100%;border:1px solid #d6d3d1;border-radius:7px;padding:7px 9px;font:inherit;font-size:12.5px;outline:none;}',
    '.askw-picker input:focus{border-color:#c2410c;}',
    '.askw-recent{margin-top:8px;max-height:160px;overflow-y:auto;}',
    '.askw-recent-item{padding:6px 8px;border-radius:6px;cursor:pointer;color:#44403c;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    '.askw-recent-item:hover{background:#f5f5f4;}',
    '.askw-hint{margin-top:8px;color:#a16207;font-size:11px;line-height:1.4;}',
    '.askw-picker-save{margin-top:8px;width:100%;background:#c2410c;color:#fff;border:none;border-radius:7px;padding:7px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;}'
  ].join('\n');

  function injectStyle() {
    var s = document.createElement('style');
    s.id = 'askw-style';
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  // ============================================================ markdown
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  }); }

  // Collision-free placeholder sentinels (Unicode Private Use Area), built at
  // runtime so this source file stays plain ASCII and diffable.
  var MD_C0 = String.fromCharCode(0xE000), MD_C1 = String.fromCharCode(0xE001);
  var MD_F0 = String.fromCharCode(0xE002), MD_F1 = String.fromCharCode(0xE003);
  var MD_RE_CODE = new RegExp(MD_C0 + '(\\d+)' + MD_C1, 'g');
  var MD_RE_FENCE = new RegExp('^' + MD_F0 + '(\\d+)' + MD_F1 + '$');

  // Inline spans on an ALREADY-escaped string: `code`, [link](url), **bold**,
  // *italic*. Code spans are pulled out first so their contents aren't reformatted.
  function inlineMd(s) {
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) { codes.push(c); return MD_C0 + (codes.length - 1) + MD_C1; });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) {
      return /^(https?:\/\/|\/)/.test(u) ? '<a href="' + u + '" target="_blank" rel="noopener">' + t + '</a>' : m;
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(MD_RE_CODE, function (_, i) { return '<code>' + codes[+i] + '</code>'; });
    return s;
  }

  // Self-contained Markdown -> safe HTML. Escapes everything first, then only adds
  // our own tags, so reflected file contents from "Prove it" can't inject script
  // (no CDN, no DOMPurify, works offline).
  function mdToHtml(src) {
    src = String(src).replace(/\r\n/g, '\n');
    var fences = [];
    src = src.replace(/```[^\n]*\n([\s\S]*?)```/g, function (_, code) {
      fences.push('<pre><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>');
      return MD_F0 + (fences.length - 1) + MD_F1;
    });
    var out = [];
    src.split(/\n{2,}/).forEach(function (block) {
      block = block.replace(/^\n+|\n+$/g, '');
      if (!block) return;
      var fm = block.match(MD_RE_FENCE);
      if (fm) { out.push(fences[+fm[1]]); return; }
      var lines = block.split('\n');
      if (lines.every(function (l) { return /^\s*[-*]\s+/.test(l); })) {
        out.push('<ul>' + lines.map(function (l) { return '<li>' + inlineMd(esc(l.replace(/^\s*[-*]\s+/, ''))) + '</li>'; }).join('') + '</ul>');
        return;
      }
      if (lines.every(function (l) { return /^\s*\d+\.\s+/.test(l); })) {
        out.push('<ol>' + lines.map(function (l) { return '<li>' + inlineMd(esc(l.replace(/^\s*\d+\.\s+/, ''))) + '</li>'; }).join('') + '</ol>');
        return;
      }
      var hm = lines.length === 1 && block.match(/^(#{1,6})\s+(.*)$/);
      if (hm) { var lv = Math.min(hm[1].length, 6); out.push('<h' + lv + '>' + inlineMd(esc(hm[2])) + '</h' + lv + '>'); return; }
      out.push('<p>' + lines.map(function (l) { return inlineMd(esc(l)); }).join('<br>') + '</p>');
    });
    return out.join('\n');
  }

  function renderMarkdown(el, text) {
    el.classList.remove('askw-fallback');
    el.innerHTML = mdToHtml(text);
  }

  // ============================================================ helpers
  function isOurs(node) {
    if (!node) return false;
    var el = node.nodeType === 3 ? node.parentElement : node;
    return !!(el && el.closest && el.closest('.askw-root'));
  }

  function basename(p) {
    if (!p) return '(none)';
    var parts = String(p).replace(/\/+$/, '').split('/');
    return parts[parts.length - 1] || p;
  }

  function surroundingContext(range) {
    var node = range.commonAncestorContainer;
    if (node && node.nodeType === 3) node = node.parentElement;
    var block = node && node.closest
      ? node.closest('p,li,td,th,blockquote,pre,section,article,figure,h1,h2,h3,h4,h5,h6,div')
      : null;
    var txt = block ? (block.innerText || block.textContent || '') : '';
    return txt.replace(/\s+/g, ' ').trim().slice(0, MAX_CTX);
  }

  function captureFromSelection() {
    var s = window.getSelection();
    if (!s || s.isCollapsed || s.rangeCount === 0) return null;
    var text = s.toString().trim();
    if (!text) return null;
    var range = s.getRangeAt(0);
    if (isOurs(range.commonAncestorContainer)) return null;
    return {
      text: text.slice(0, MAX_SEL),
      context: surroundingContext(range),
      rect: range.getBoundingClientRect()
    };
  }

  // ============================================================ build DOM
  function build() {
    // --- context menu ---
    menuEl = document.createElement('div');
    menuEl.className = 'askw-root askw-menu';
    menuEl.innerHTML =
      '<div class="askw-item" data-act="eli5"><span class="askw-ico">○</span>ELI5</div>' +
      '<div class="askw-item" data-act="prove"><span class="askw-ico">✓</span>Prove it</div>' +
      '<div class="askw-item" data-act="ask"><span class="askw-ico">…</span>Ask a question…</div>' +
      '<div class="askw-ask-wrap"><textarea class="askw-ask-input" placeholder="Ask about the highlighted text…"></textarea>' +
      '<button class="askw-ask-go">Go</button><div style="clear:both"></div></div>';
    document.body.appendChild(menuEl);
    askWrap = menuEl.querySelector('.askw-ask-wrap');
    askInput = menuEl.querySelector('.askw-ask-input');
    var askGo = menuEl.querySelector('.askw-ask-go');

    menuEl.querySelectorAll('.askw-item').forEach(function (item) {
      item.addEventListener('click', function () {
        var act = item.getAttribute('data-act');
        if (act === 'ask') {
          askWrap.classList.add('open');
          askInput.focus();
        } else {
          start(act);
        }
      });
    });
    askGo.addEventListener('click', submitAsk);
    askInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitAsk(); }
    });

    // --- answer panel ---
    panelEl = document.createElement('div');
    panelEl.className = 'askw-root askw-panel';
    panelEl.innerHTML =
      '<div class="askw-head"><p class="askw-eyebrow"></p><p class="askw-selq"></p>' +
      '<button class="askw-x" title="Close">×</button></div>' +
      '<div class="askw-tools"></div>' +
      '<div class="askw-body"></div>' +
      '<div class="askw-followup"><textarea class="askw-follow-input" rows="1" placeholder="Ask a follow-up…"></textarea>' +
      '<button class="askw-follow-go" title="Send (Enter)">↑</button></div>' +
      '<div class="askw-foot"><button class="askw-claude" title="Open a dedicated Claude session in this folder — hold ⌥ Option to copy the prompt instead">Open in Claude</button><button class="askw-copy">Copy</button></div>';
    document.body.appendChild(panelEl);
    panelTitle = panelEl.querySelector('.askw-eyebrow');
    panelSel = panelEl.querySelector('.askw-selq');
    panelTools = panelEl.querySelector('.askw-tools');
    panelBody = panelEl.querySelector('.askw-body');
    followWrap = panelEl.querySelector('.askw-followup');
    followInput = panelEl.querySelector('.askw-follow-input');
    followGo = panelEl.querySelector('.askw-follow-go');
    claudeBtn = panelEl.querySelector('.askw-claude');
    panelEl.querySelector('.askw-x').addEventListener('click', closePanel);
    panelEl.querySelector('.askw-copy').addEventListener('click', function () {
      var t = panelBody.innerText || '';
      if (navigator.clipboard) navigator.clipboard.writeText(t).catch(function () {});
    });
    claudeBtn.addEventListener('click', function (e) { openInClaude(e.altKey); });
    followGo.addEventListener('click', submitFollowup);
    followInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitFollowup(); }
    });
    followInput.addEventListener('input', autosizeFollow);
    makeDragResize(panelEl, panelEl.querySelector('.askw-head'));

    // --- folder pill + picker ---
    pillEl = document.createElement('div');
    pillEl.className = 'askw-root askw-pill';
    pillEl.innerHTML = '<span class="askw-ico">◈</span><b class="askw-pill-label">…</b>';
    pillLabel = pillEl.querySelector('.askw-pill-label');
    pillEl.title = 'Context folder Claude reads — click to change';
    document.body.appendChild(pillEl);
    pillEl.addEventListener('click', togglePicker);

    pickerEl = document.createElement('div');
    pickerEl.className = 'askw-root askw-picker';
    pickerEl.innerHTML =
      '<label>Context folder</label><input class="askw-picker-input" spellcheck="false" />' +
      '<button class="askw-picker-save">Use this folder</button>' +
      '<div class="askw-recent"></div>' +
      (isFileProto ? '<div class="askw-hint">You opened this over file:// — if requests fail, serve the page over http (e.g. <code>python3 -m http.server</code>).</div>' : '');
    document.body.appendChild(pickerEl);
    pickerEl.querySelector('.askw-picker-save').addEventListener('click', function () {
      var v = pickerEl.querySelector('.askw-picker-input').value.trim();
      if (v) { setFolder(v); closePicker(); }
    });
  }

  // ============================================================ menu
  function showMenu(x, y) {
    askWrap.classList.remove('open');
    askInput.value = '';
    menuEl.style.display = 'block';
    var mw = menuEl.offsetWidth || 190;
    var mh = menuEl.offsetHeight || 130;
    menuEl.style.left = Math.max(6, Math.min(x, window.innerWidth - mw - 8)) + 'px';
    menuEl.style.top = Math.max(6, Math.min(y, window.innerHeight - mh - 8)) + 'px';
  }
  function hideMenu() { if (menuEl) menuEl.style.display = 'none'; }

  function submitAsk() {
    var q = askInput.value.trim();
    if (!q) return;
    start('ask', q);
  }

  // ============================================================ panel
  var EYEBROW = { eli5: 'ELI5', prove: 'Prove it', ask: 'Your question' };

  function openPanel(action) {
    activeAction = action;
    panelTitle.textContent = EYEBROW[action] || 'Answer';
    panelSel.textContent = sel ? ('“' + sel.text.slice(0, 160) + (sel.text.length > 160 ? '…' : '') + '”') : '';
    panelTools.innerHTML = '';
    panelBody.innerHTML = '';
    hideFollowup();
    panelEl.classList.add('open');
    if (!userPinned) positionPanel();
  }
  function closePanel() {
    panelEl.classList.remove('open');
    activeAction = null;
    if (abort) { abort.abort(); abort = null; }
    streaming = false;
    resetConversation();
    hideFollowup();
    maybeApplyReload();
  }

  // ---- conversation transcript (grows in the panel body as follow-ups arrive) ----
  function resetConversation() { transcript = []; liveEl = null; }

  function renderConversation() {
    panelBody.innerHTML = '';
    transcript.forEach(function (t) {
      var el = document.createElement('div');
      if (t.role === 'user') { el.className = 'askw-q'; el.textContent = t.text; }
      else { el.className = 'askw-a'; renderMarkdown(el, t.text); }
      panelBody.appendChild(el);
    });
  }
  // Append a fresh answer block (showing "Thinking…") for the stream to fill.
  function appendLive() {
    liveEl = document.createElement('div');
    liveEl.className = 'askw-a';
    liveEl.innerHTML = '<div class="askw-think"><span class="askw-dot"></span>Thinking…</div>';
    panelBody.appendChild(liveEl);
    autoscroll();
  }
  function liveError(msg) {
    if (liveEl) liveEl.innerHTML = '<div class="askw-err">' + esc(msg) + '</div>';
    else showError(msg);
  }

  // ---- follow-up composer ----
  function showFollowup(enabled) {
    if (!followWrap) return;
    followWrap.style.display = 'flex';
    followInput.disabled = followGo.disabled = !enabled;
  }
  function hideFollowup() {
    if (!followWrap) return;
    followWrap.style.display = 'none';
    followInput.value = '';
    followInput.style.height = '';
  }
  function autosizeFollow() {
    followInput.style.height = 'auto';
    followInput.style.height = Math.min(followInput.scrollHeight, 96) + 'px';
  }
  function positionPanel() {
    if (!sel || !sel.rect) return;
    var m = 12, vw = window.innerWidth, vh = window.innerHeight;
    var h = panelEl.offsetHeight || 240;
    var left = sel.rect.left + sel.rect.width / 2 - PANEL_W / 2;
    left = Math.max(m, Math.min(left, vw - PANEL_W - m));
    var top = sel.rect.bottom + 8;
    if (top + h > vh - m) top = sel.rect.top - 8 - h;
    top = Math.max(m, Math.min(top, vh - h - m));
    panelEl.style.left = left + 'px';
    panelEl.style.top = top + 'px';
  }
  function autoscroll() { panelBody.scrollTop = panelBody.scrollHeight; }

  // Drag the panel by its header; resize from the bottom-right corner (CSS
  // resize:both). Either gesture "pins" it so auto-positioning stops fighting you.
  function makeDragResize(panel, handle) {
    handle.addEventListener('mousedown', function (e) {
      if (e.target.closest('.askw-x')) return;     // close button isn't a drag grip
      e.preventDefault();
      userPinned = true;
      var r = panel.getBoundingClientRect();
      var sx = e.clientX, sy = e.clientY, ox = r.left, oy = r.top;
      function mv(ev) {
        var nx = ox + (ev.clientX - sx), ny = oy + (ev.clientY - sy);
        nx = Math.max(4, Math.min(nx, window.innerWidth - panel.offsetWidth - 4));
        ny = Math.max(4, Math.min(ny, window.innerHeight - 44));
        panel.style.left = nx + 'px';
        panel.style.top = ny + 'px';
      }
      function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
      document.addEventListener('mousemove', mv);
      document.addEventListener('mouseup', up);
    });
    // grabbing the resize corner also pins it
    panel.addEventListener('mousedown', function (e) {
      var r = panel.getBoundingClientRect();
      if (e.clientX > r.right - 22 && e.clientY > r.bottom - 22) userPinned = true;
    });
  }

  // Fallback error render into the whole body (used only when there's no live
  // answer element to target — see liveError).
  function showError(msg) {
    panelBody.innerHTML = '<div class="askw-err">' + esc(msg) + '</div>';
  }

  function toast(msg) {
    if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'askw-root askw-toast'; document.body.appendChild(toastEl); }
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(function () { toastEl.classList.remove('show'); }, 2400);
  }

  function setClaudeLabel(opt) {
    if (claudeBtn && !claudeBtn.disabled) claudeBtn.textContent = opt ? 'Copy Claude prompt' : 'Open in Claude';
  }

  // Hand off to a dedicated `claude` session in the context folder. With Option
  // held (copy=true), copy the seed prompt instead of launching a terminal.
  function openInClaude(copy) {
    if (!sel || !lastAction) { toast('Ask something first.'); return; }
    claudeBtn.disabled = true;
    claudeBtn.textContent = copy ? 'Copying…' : 'Opening…';
    fetch(SERVER + '/open-in-claude', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        folder: folder, action: lastAction, selection: sel.text,
        question: lastQuestion, answer: lastAnswer, mode: copy ? 'copy' : 'open', token: TOKEN
      })
    }).then(function (r) { return r.json(); }).then(function (d) {
      claudeBtn.disabled = false; setClaudeLabel(false);
      if (!d || !d.ok) { toast((d && d.error) || 'Open in Claude failed.'); return; }
      if (copy) {
        if (navigator.clipboard && d.prompt) {
          navigator.clipboard.writeText(d.prompt).then(function () { toast('Claude prompt copied'); }).catch(function () { toast('Copy failed'); });
        } else { toast('Copy failed'); }
      } else {
        toast('Opening a Claude session…');
      }
    }).catch(function () {
      claudeBtn.disabled = false; setClaudeLabel(false);
      toast('Could not reach the Ask server.');
    });
  }

  var toolPills = {};
  function updateTool(data) {
    if (data.status === 'calling' && !toolPills[data.tool]) {
      var span = document.createElement('span');
      span.className = 'askw-pillt';
      span.innerHTML = '<span class="askw-dot"></span>' + esc(prettyTool(data.tool));
      toolPills[data.tool] = span;
      panelTools.appendChild(span);
    } else if (data.status === 'complete' && toolPills[data.tool]) {
      panelTools.removeChild(toolPills[data.tool]);
      delete toolPills[data.tool];
    }
  }
  function clearTools() {
    toolPills = {};
    if (panelTools) panelTools.innerHTML = '';
  }
  function prettyTool(t) { return String(t).replace(/_/g, ' '); }

  // ============================================================ cache
  function djb2(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  }
  function cacheKey(action) { return 'askw:' + action + ':' + (folder || '') + ':' + djb2(sel ? sel.text : ''); }
  function cacheGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function cacheSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // ============================================================ streaming
  // A top-level action (ELI5 / Prove it / Ask) opens the panel and starts a new
  // conversation. Follow-ups reuse the same panel + selection via submitFollowup.
  function start(action, question) {
    hideMenu();
    if (!sel) return;
    lastAction = action; lastQuestion = question || ''; lastAnswer = '';

    var ck = action !== 'ask' ? cacheKey(action) : null;
    if (ck) {
      if (activeAction === action) {
        try { localStorage.removeItem(ck); } catch (e) {}   // re-click = refresh
      } else {
        var cached = cacheGet(ck);
        if (cached) {
          openPanel(action);
          resetConversation();
          transcript.push({ role: 'assistant', text: cached });
          lastAnswer = cached;
          renderConversation();
          showFollowup(true);
          return;
        }
      }
    }

    openPanel(action);
    resetConversation();
    if (action === 'ask' && question) transcript.push({ role: 'user', text: question });
    renderConversation();

    streamAnswer({
      action: action,
      selection: sel.text,
      context: sel.context,
      question: question || '',
      folder: folder,
      token: TOKEN
    }, ck);
  }

  // Ask a follow-up about the same selection, continuing the panel's thread.
  function submitFollowup() {
    if (!sel || !lastAction) return;
    var q = followInput.value.trim();
    if (!q) return;
    hideFollowup();           // clears the box; re-shown (enabled) when the answer lands
    lastAction = 'ask'; lastQuestion = q; lastAnswer = '';

    transcript.push({ role: 'user', text: q });
    renderConversation();

    // Send every completed turn before this question so Claude has the thread.
    var history = transcript.slice(0, -1).map(function (t) { return { role: t.role, text: t.text }; });
    streamAnswer({
      action: 'ask',
      selection: sel.text,
      context: sel.context,
      question: q,
      history: history,
      folder: folder,
      token: TOKEN
    }, null);
  }

  // Shared stream pump: fills the live answer block, commits it to the transcript
  // on completion. `ck` is a cache key to store the result under (top-level only).
  function streamAnswer(reqBody, ck) {
    if (abort) abort.abort();
    abort = new AbortController();
    var myAbort = abort;
    streaming = true;
    clearTools();
    appendLive();
    showFollowup(false);

    var acc = '';
    fetch(SERVER + '/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody),
      signal: myAbort.signal
    }).then(function (resp) {
      if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { finish(acc, ck); return; }
          buffer += decoder.decode(r.value, { stream: true });
          var lines = buffer.split('\n');
          buffer = lines.pop() || '';
          var evt = '';
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.indexOf('event: ') === 0) {
              evt = line.slice(7).trim();
            } else if (line.indexOf('data: ') === 0 && evt) {
              var data;
              try { data = JSON.parse(line.slice(6)); } catch (e) { evt = ''; continue; }
              if (evt === 'token') {
                acc += data.text;
                renderMarkdown(liveEl, acc);
                autoscroll();
              } else if (evt === 'tool_status') {
                updateTool(data);
              } else if (evt === 'error') {
                liveError(data.message || 'An error occurred.');
                acc = '';   // don't cache or commit an error
              } else if (evt === 'done') {
                /* terminal; finish() runs on stream close */
              }
              evt = '';
            }
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      liveError('Could not reach the Ask server at ' + SERVER + '. Is it running? (' + (err && err.message || err) + ')');
      finishMeta();
    });
  }

  function finish(acc, ck) {
    clearTools();
    if (acc) {
      transcript.push({ role: 'assistant', text: acc });
      lastAnswer = acc;
      if (ck) cacheSet(ck, acc);
    } else if (liveEl && liveEl.querySelector('.askw-think')) {
      liveEl.innerHTML = '<div class="askw-err">No response received.</div>';
    }
    finishMeta();
  }
  // Re-enable the composer and detach the live element after a turn settles.
  function finishMeta() {
    liveEl = null;
    streaming = false;
    showFollowup(true);
    maybeApplyReload();
  }

  // ============================================================ folder
  function setFolder(f) {
    folder = f;
    try { localStorage.setItem('askw:folder', f); } catch (e) {}
    if (recentFolders.indexOf(f) === -1) recentFolders.unshift(f);
    updatePill();
    renderRecent();
  }
  function updatePill() { if (pillLabel) pillLabel.textContent = basename(folder); }
  function renderRecent() {
    var box = pickerEl.querySelector('.askw-recent');
    box.innerHTML = '';
    recentFolders.slice(0, 8).forEach(function (f) {
      var d = document.createElement('div');
      d.className = 'askw-recent-item';
      d.textContent = f;
      d.title = f;
      d.addEventListener('click', function () { setFolder(f); closePicker(); });
      box.appendChild(d);
    });
  }
  function togglePicker() {
    if (pickerEl.classList.contains('open')) { closePicker(); return; }
    pickerEl.querySelector('.askw-picker-input').value = folder || '';
    renderRecent();
    pickerEl.classList.add('open');
  }
  function closePicker() { pickerEl.classList.remove('open'); }

  function metaFolder() {
    var m = document.querySelector('meta[name="askw-folder"]');
    return m ? (m.getAttribute('content') || '').trim() : '';
  }

  function initFolder() {
    // A /view page seeds the folder via <meta name="askw-folder"> (CSP blocks the
    // old inline-script seed). Meta (explicit per-open intent) wins over storage.
    var seeded = metaFolder();
    if (seeded) {
      folder = seeded;
      try { localStorage.setItem('askw:folder', seeded); } catch (e) {}
    } else {
      try { folder = localStorage.getItem('askw:folder') || folder; } catch (e) {}
    }
    updatePill();

    fetch(SERVER + '/config').then(function (r) { return r.json(); }).then(function (cfg) {
      defaultFolder = cfg.default_folder;
      recentFolders = (cfg.recent_folders || []).slice();
      if (!folder) folder = defaultFolder;
      if (folder && recentFolders.indexOf(folder) === -1) recentFolders.unshift(folder);
      updatePill();
    }).catch(function () { updatePill(); });
  }

  // ============================================================ live reload
  // Only /view pages seed <meta name="askw-src">; on those, poll the file's stat
  // signature and reload when it settles on a new value, so edits from another
  // editor/agent show up in real time. Reloads are deferred while the widget is
  // busy (a stream in flight, or the answer panel open) so we never yank content
  // mid-answer; the deferred reload fires when things go idle.
  function metaSrc() {
    var m = document.querySelector('meta[name="askw-src"]');
    return m ? (m.getAttribute('content') || '').trim() : '';
  }
  function reloadIdle() {
    return !streaming && !(panelEl && panelEl.classList.contains('open'));
  }
  function doReload() {
    try { sessionStorage.setItem('askw:reload', JSON.stringify({ src: reloadSrc, y: window.scrollY })); } catch (e) {}
    location.reload();
  }
  function maybeApplyReload() {
    if (reloadPending && reloadIdle()) { reloadPending = false; doReload(); }
  }
  function checkDoc() {
    if (!reloadSrc) return;
    fetch(SERVER + '/_mtime?src=' + encodeURIComponent(reloadSrc), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok || !d.sig) return;
        var sig = d.sig;
        if (reloadSig === null) { reloadSig = reloadSeen = sig; return; }  // baseline
        if (sig !== reloadSeen) { reloadSeen = sig; return; }              // still changing — let it settle
        if (sig === reloadSig) return;                                     // unchanged from what's rendered
        reloadSig = sig;                                                   // settled on a new version
        if (reloadIdle()) { doReload(); }
        else if (!reloadPending) { reloadPending = true; toast('Document updated — refreshes when you’re done'); }
      })
      .catch(function () {});
  }
  function initLiveReload() {
    reloadSrc = metaSrc();
    if (!reloadSrc) return;   // not a local /view page → no live reload
    // If this load is the result of a reload we triggered, restore the reading
    // position and confirm the refresh landed.
    try {
      var saved = JSON.parse(sessionStorage.getItem('askw:reload') || 'null');
      if (saved && saved.src === reloadSrc) {
        sessionStorage.removeItem('askw:reload');
        window.addEventListener('load', function () { window.scrollTo(0, saved.y || 0); });
        toast('Document updated');
      }
    } catch (e) {}
    checkDoc();   // establish the baseline immediately
    setInterval(checkDoc, 1000);
  }

  // ============================================================ global events
  function wire() {
    var debounce = null;
    document.addEventListener('mouseup', function (e) {
      if (isOurs(e.target)) return;
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(function () {
        var captured = captureFromSelection();
        if (captured) sel = captured;
      }, 300);
    });

    document.addEventListener('contextmenu', function (e) {
      if (isOurs(e.target)) return;              // allow native menu inside our UI
      var captured = captureFromSelection();
      if (!captured) { hideMenu(); return; }     // no selection -> native menu
      sel = captured;
      e.preventDefault();
      showMenu(e.clientX, e.clientY);
    });

    document.addEventListener('mousedown', function (e) {
      if (isOurs(e.target)) return;
      hideMenu();
      closePicker();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Alt') { setClaudeLabel(true); return; }
      if (e.key !== 'Escape') return;
      if (menuEl.style.display === 'block') { hideMenu(); }
      else if (pickerEl.classList.contains('open')) { closePicker(); }
      else if (panelEl.classList.contains('open')) { closePanel(); }
    });
    document.addEventListener('keyup', function (e) { if (e.key === 'Alt') setClaudeLabel(false); });
    window.addEventListener('blur', function () { setClaudeLabel(false); });

    window.addEventListener('resize', function () {
      if (panelEl.classList.contains('open') && !userPinned) positionPanel();
    });
  }

  // ============================================================ boot
  function boot() {
    injectStyle();
    build();
    wire();
    initFolder();
    initLiveReload();
  }
  if (document.body) boot();
  else document.addEventListener('DOMContentLoaded', boot);
})();
