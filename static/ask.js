/* Onyx — highlight-to-ask reading companion.
 *
 * Drop this on any local HTML page:
 *   <script src="http://localhost:8899/ask.js"></script>
 *
 * Highlight text, then use the nearby Ask button or right-click for ELI5 /
 * Prove it / Ask a question. Each answer
 * streams from a subscription-backed provider CLI on the local server with full context of a
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
  var serverConfig = { version: 'unknown', provider: 'claude', model: 'sonnet', reasoning_effort: 'medium', cache_ttl_hours: 168, cache_max_entries: 100 };
  var appearanceTheme = 'system';
  var vaultLook = null;  // Match vault appearance: {mode, reader_css} while the app wears the vault
  var sel = null;              // { text, context, rect }
  var abort = null;            // AbortController for the active stream
  var activeAction = null;     // 'eli5' | 'prove' | 'ask'
  var userPinned = false;      // user dragged/resized the panel → stop auto-positioning
  var isFileProto = window.location.protocol === 'file:';
  var lastAnswer = '', lastAction = null, lastQuestion = '';   // for provider handoff
  var transcript = [];         // completed turns: { role:'user'|'assistant', text }
  var liveEl = null;           // the .askw-a element receiving the current stream
  var streaming = false;       // a /ask stream is in flight (defer live-reload while true)
  var currentRequestId = null, lastRequestBody = null, lastCacheKey = null;
  var currentRequestMode = 'generated', historyOrigin = null;
  var requestCitations = [], requestTrace = [];
  // ---- live reload (only on /view pages, which seed askw-src) ----
  var reloadSrc = null, reloadSig = null, reloadSeen = null, reloadPending = false;

  // ---- DOM refs ----
  var triggerEl, menuEl, askWrap, askInput;
  var panelEl, panelTitle, panelSel, panelTools, panelBody, claudeBtn, stopBtn, retryBtn, historyBtn;
  var followWrap, followInput, followGo;
  var pillEl, pillLabel, pickerEl, toastEl;

  // ============================================================ styles
  var CSS = [
    '.askw-root{all:revert;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Helvetica Neue",sans-serif;color:#0d0d0d;line-height:1.5;-webkit-font-smoothing:antialiased;--askw-accent:#3a83f7;--askw-accent-hover:#2c67c5;--askw-line:rgba(0,0,0,.10);--askw-soft:rgba(0,0,0,.055);}',
    '.askw-root *{box-sizing:border-box;}',
    '.askw-menu{position:fixed;z-index:2147483600;display:none;min-width:190px;background:rgba(255,255,255,.82);border:1px solid var(--askw-line);border-radius:11px;box-shadow:0 20px 55px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.65);backdrop-filter:blur(24px) saturate(1.35);-webkit-backdrop-filter:blur(24px) saturate(1.35);padding:6px;font-size:13px;}',
    '.askw-trigger{position:fixed;z-index:2147483598;display:none;align-items:center;gap:5px;padding:5px 10px;border:1px solid rgba(255,255,255,.28);border-radius:999px;background:var(--askw-accent);color:#fff;box-shadow:0 10px 28px rgba(0,0,0,.20);font:600 12px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;cursor:pointer;}',
    '.askw-trigger:hover{background:var(--askw-accent-hover);}',
    '.askw-item{display:flex;width:100%;align-items:center;gap:8px;padding:8px 10px;border:0;border-radius:7px;background:transparent;font:inherit;text-align:left;cursor:pointer;color:#0d0d0d;user-select:none;}',
    '.askw-item:hover{background:rgba(13,13,13,.07);}',
    '.askw-trigger:focus-visible,.askw-item:focus-visible,.askw-pill:focus-visible,.askw-x:focus-visible,.askw-follow-go:focus-visible,.askw-foot button:focus-visible,.askw-ask-go:focus-visible,.askw-picker button:focus-visible{outline:2px solid var(--askw-accent);outline-offset:2px;}',
    '.askw-item .askw-ico{width:16px;text-align:center;opacity:.75;}',
    '.askw-ask-wrap{display:none;padding:6px 6px 4px;border-top:1px solid var(--askw-soft);margin-top:4px;}',
    '.askw-ask-wrap.open{display:block;}',
    '.askw-ask-input{width:100%;min-height:54px;resize:vertical;border:1px solid var(--askw-line);border-radius:7px;background:#fff;padding:7px 9px;font:inherit;font-size:13px;color:#0d0d0d;outline:none;}',
    '.askw-ask-input:focus{border-color:var(--askw-accent);box-shadow:0 0 0 2px rgba(58,131,247,.18);}',
    '.askw-ask-go{margin-top:6px;float:right;background:var(--askw-accent);color:#fff;border:none;border-radius:7px;padding:6px 14px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;}',
    '.askw-ask-go:disabled{opacity:.45;cursor:not-allowed;}',
    '.askw-panel{position:fixed;z-index:2147483601;display:none;width:' + PANEL_W + 'px;height:auto;min-width:300px;min-height:180px;max-width:96vw;max-height:92vh;background:rgba(252,252,252,.88);border:1px solid var(--askw-line);border-radius:15px;box-shadow:0 26px 70px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.72);backdrop-filter:blur(28px) saturate(1.28);-webkit-backdrop-filter:blur(28px) saturate(1.28);overflow:hidden;flex-direction:column;resize:both;}',
    '.askw-panel.open{display:flex;}',
    '.askw-head{padding:13px 40px 11px 15px;border-bottom:1px solid var(--askw-soft);background:rgba(255,255,255,.28);position:relative;flex:0 0 auto;cursor:move;user-select:none;}',
    '.askw-eyebrow{font-size:10px;letter-spacing:.08em;text-transform:uppercase;font-weight:700;color:var(--askw-accent);margin:0 0 3px;}',
    '.askw-selq{font-size:12.5px;line-height:1.45;color:#5d5d5d;margin:0;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;}',
    '.askw-selq.clamped{cursor:pointer;}.askw-selq.expanded{display:block;max-height:30vh;overflow-y:auto;}',
    '.askw-x{position:absolute;top:9px;right:9px;width:26px;height:26px;border:none;background:transparent;color:#a8a29e;font-size:17px;line-height:1;border-radius:6px;cursor:pointer;}',
    '.askw-x:hover{background:rgba(13,13,13,.07);color:#0d0d0d;}',
    '.askw-tools{display:flex;flex-wrap:wrap;gap:6px;padding:9px 15px 0;flex:0 0 auto;}',
    '.askw-pillt{display:inline-flex;align-items:center;gap:5px;background:rgba(58,131,247,.11);color:#2c67c5;font-size:10.5px;font-weight:600;padding:3px 9px;border-radius:999px;}',
    '.askw-dot{width:6px;height:6px;border-radius:50%;background:var(--askw-accent);animation:askw-pulse 1.1s infinite;}',
    '@keyframes askw-pulse{0%,100%{opacity:.35}50%{opacity:1}}',
    '.askw-body{padding:12px 15px 15px;overflow-y:auto;font-size:14px;line-height:1.55;color:#0d0d0d;flex:1 1 auto;min-height:0;}',
    '.askw-q{margin:15px 0 9px;padding:7px 11px;background:rgba(255,255,255,.56);border:1px solid var(--askw-soft);border-radius:9px;font-size:13px;color:#5d5d5d;white-space:pre-wrap;}',
    '.askw-q:first-child{margin-top:1px;}',
    '.askw-a{font-size:14px;}',
    '.askw-body p{margin:0 0 9px;}.askw-body p:last-child{margin-bottom:0;}',
    '.askw-body ul,.askw-body ol{margin:0 0 9px;padding-left:20px;}.askw-body li{margin:2px 0;}',
    '.askw-body code{background:#f5f5f4;border-radius:4px;padding:1px 5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;}',
    '.askw-body pre{background:#1c1917;color:#fafaf9;border-radius:8px;padding:11px 13px;overflow-x:auto;font-size:12.5px;}',
    '.askw-body pre code{background:transparent;color:inherit;padding:0;}',
    '.askw-body h1,.askw-body h2,.askw-body h3{font-size:14.5px;margin:12px 0 6px;font-weight:700;}',
    '.askw-body a{color:var(--askw-accent);}',
    '.askw-fallback{white-space:pre-wrap;}',
    '.askw-think{display:flex;align-items:center;gap:8px;color:#78716c;font-size:13px;}',
    '.askw-err{color:#b91c1c;font-size:13px;}',
    '.askw-request-meta{color:#a8a29e;font-size:10px;margin:6px 0 0;}',
    '.askw-origin{margin:8px 0;padding:6px 9px;border-radius:7px;background:rgba(58,131,247,.09);color:#2c67c5;font-size:10.5px;font-weight:600;}',
    '.askw-history-entry{margin:0 0 13px;padding:11px;border:1px solid var(--askw-line);border-radius:10px;background:rgba(255,255,255,.28);}',
    '.askw-history-entry .askw-q{margin-top:0}.askw-history-meta{margin:8px 0 0;color:#a8a29e;font-size:10px}.askw-history-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}.askw-history-actions button{border:1px solid var(--askw-line);border-radius:7px;background:rgba(255,255,255,.76);color:#5d5d5d;padding:5px 8px;font:inherit;font-size:10.5px;cursor:pointer}.askw-history-actions button:first-child{background:var(--askw-accent);border-color:var(--askw-accent);color:#fff}',
    '.askw-citations{margin:14px 0 2px;padding-top:10px;border-top:1px solid var(--askw-soft);}',
    '.askw-citations-title{margin:0 0 7px;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#78716c;}',
    '.askw-citation{display:block;width:100%;margin:5px 0;padding:7px 9px;border:1px solid var(--askw-line);border-radius:7px;background:rgba(255,255,255,.62);color:#2c67c5;text-align:left;font:inherit;font-size:11.5px;cursor:pointer;}',
    '.askw-citation:hover{background:#fff}.askw-citation pre{display:none;margin:7px 0 0;white-space:pre-wrap;color:#5d5d5d;background:#f7f7f7;padding:7px;font-size:10px}.askw-citation.expanded pre{display:block}',
    '.askw-foot{display:flex;justify-content:flex-end;gap:8px;padding:8px 13px;border-top:1px solid var(--askw-soft);background:rgba(255,255,255,.22);flex:0 0 auto;}',
    '.askw-foot button{background:rgba(255,255,255,.78);border:1px solid var(--askw-line);border-radius:7px;padding:5px 11px;font:inherit;font-size:12px;color:#5d5d5d;cursor:pointer;}',
    '.askw-foot button:hover{background:#fff;color:#0d0d0d;}',
    '.askw-foot .askw-claude{background:var(--askw-accent);border-color:var(--askw-accent);color:#fff;margin-right:auto;}',
    '.askw-foot .askw-claude:hover{background:var(--askw-accent-hover);}',
    '.askw-foot .askw-claude:disabled{opacity:.55;cursor:default;}',
    '.askw-foot .askw-stop{display:none;color:#b91c1c}.askw-foot .askw-retry{display:none;color:#2c67c5}',
    '.askw-followup{display:none;align-items:flex-end;gap:7px;padding:9px 13px;border-top:1px solid var(--askw-soft);background:rgba(255,255,255,.2);flex:0 0 auto;}',
    '.askw-follow-input{flex:1 1 auto;resize:none;max-height:96px;min-height:34px;border:1px solid var(--askw-line);border-radius:9px;background:#fff;padding:7px 10px;font:inherit;font-size:13px;color:#0d0d0d;outline:none;line-height:1.4;}',
    '.askw-follow-input:focus{border-color:var(--askw-accent);box-shadow:0 0 0 2px rgba(58,131,247,.18);}',
    '.askw-follow-input:disabled{opacity:.55;background:#fafaf9;}',
    '.askw-follow-go{flex:0 0 auto;width:34px;height:34px;background:var(--askw-accent);color:#fff;border:none;border-radius:9px;font-size:15px;line-height:1;cursor:pointer;}',
    '.askw-follow-go:hover{background:var(--askw-accent-hover);}',
    '.askw-follow-go:disabled{opacity:.4;cursor:not-allowed;}',
    '.askw-toast{position:fixed;bottom:24px;left:50%;z-index:2147483603;background:rgba(28,28,28,.88);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);color:#fff;padding:9px 16px;border:1px solid rgba(255,255,255,.1);border-radius:10px;font-size:13px;box-shadow:0 12px 34px rgba(0,0,0,.28);opacity:0;pointer-events:none;transform:translateX(-50%) translateY(8px);transition:opacity .15s,transform .15s;}',
    '.askw-toast.show{opacity:1;transform:translateX(-50%) translateY(0);}',
    // The context folder rests as a folder icon and slides its name out on hover,
    // keyboard focus, or while the picker is open. It is glass lying on the page,
    // so it is tinted for the page under it (data-askw-page), not the app theme:
    // thin at rest, frosted to a readable floor once its name shows.
    '.askw-pill{--askw-glass:rgba(255,255,255,.2);--askw-frost:rgba(255,255,255,.74);position:fixed;top:12px;right:12px;z-index:2147483599;display:flex;align-items:center;height:30px;max-width:240px;background:var(--askw-glass);border:1px solid rgba(255,255,255,.55);border-radius:999px;box-shadow:0 6px 18px rgba(0,0,0,.1),inset 0 1px 0 rgba(255,255,255,.6);backdrop-filter:blur(14px) saturate(1.8);-webkit-backdrop-filter:blur(14px) saturate(1.8);padding:0 7px;font-size:11.5px;color:#5d5d5d;cursor:pointer;transition:padding .2s ease,background-color .2s ease;}',
    '.askw-pill .askw-ico{display:block;flex:none;width:14px;height:14px;color:var(--askw-accent);}',
    '.askw-pill b{color:#0d0d0d;font-weight:600;max-width:0;margin-left:0;opacity:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:max-width .2s ease,margin-left .2s ease,opacity .15s;}',
    '.askw-pill:hover,.askw-pill:focus-visible,.askw-pill[aria-expanded="true"]{padding-right:11px;background:var(--askw-frost);}',
    '.askw-pill:hover b,.askw-pill:focus-visible b,.askw-pill[aria-expanded="true"] b{max-width:180px;margin-left:6px;opacity:1;}',
    'html[data-askw-page="dark"] .askw-pill{--askw-glass:rgba(22,22,22,.24);--askw-frost:rgba(30,30,30,.74);border-color:rgba(255,255,255,.14);box-shadow:0 6px 18px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.1);color:#cdcdcd;}',
    'html[data-askw-page="dark"] .askw-pill b{color:#fff;}',
    '.askw-picker{position:fixed;top:42px;right:12px;z-index:2147483602;display:none;width:300px;background:rgba(255,255,255,.86);border:1px solid var(--askw-line);border-radius:12px;box-shadow:0 20px 55px rgba(0,0,0,.19),inset 0 1px 0 rgba(255,255,255,.7);backdrop-filter:blur(24px) saturate(1.32);-webkit-backdrop-filter:blur(24px) saturate(1.32);padding:10px;font-size:12.5px;}',
    '.askw-picker.open{display:block;}',
    '.askw-picker label{display:block;font-weight:600;color:#44403c;margin:0 0 5px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;}',
    '.askw-picker input{width:100%;border:1px solid var(--askw-line);border-radius:7px;background:#fff;color:#0d0d0d;padding:7px 9px;font:inherit;font-size:12.5px;outline:none;}',
    '.askw-picker input:focus{border-color:var(--askw-accent);box-shadow:0 0 0 2px rgba(58,131,247,.18);}',
    '.askw-picker-row{display:flex;gap:6px}.askw-picker-browse{display:none;white-space:nowrap;border:1px solid #d6d3d1;border-radius:7px;background:#fff;padding:0 9px;font:inherit;font-size:11px}.askw-native .askw-picker-browse{display:block}',
    '.askw-recent{margin-top:8px;max-height:160px;overflow-y:auto;}',
    '.askw-recent-item{display:block;width:100%;padding:6px 8px;border:0;border-radius:6px;background:transparent;font:inherit;text-align:left;cursor:pointer;color:#44403c;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    '.askw-recent-item:hover{background:rgba(13,13,13,.07);}',
    '.askw-hint{margin-top:8px;color:#a16207;font-size:11px;line-height:1.4;}',
    '.askw-picker-save{margin-top:8px;width:100%;background:var(--askw-accent);color:#fff;border:none;border-radius:7px;padding:7px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;}',
    'html[data-askw-color="dark"] .askw-root{color:#fff;--askw-line:rgba(255,255,255,.15);--askw-soft:rgba(255,255,255,.07);}',
    'html[data-askw-color="dark"] .askw-menu,html[data-askw-color="dark"] .askw-panel,html[data-askw-color="dark"] .askw-picker{background:rgba(35,35,35,.84);box-shadow:0 26px 70px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.10);}',
    'html[data-askw-color="dark"] .askw-item,html[data-askw-color="dark"] .askw-body{color:#fff;}',
    'html[data-askw-color="dark"] .askw-selq,html[data-askw-color="dark"] .askw-q,html[data-askw-color="dark"] .askw-foot button,html[data-askw-color="dark"] .askw-picker label,html[data-askw-color="dark"] .askw-recent-item{color:#cdcdcd;}',
    'html[data-askw-color="dark"] .askw-head,html[data-askw-color="dark"] .askw-foot,html[data-askw-color="dark"] .askw-followup{background:rgba(15,15,15,.22);}',
    'html[data-askw-color="dark"] .askw-item:hover,html[data-askw-color="dark"] .askw-x:hover,html[data-askw-color="dark"] .askw-recent-item:hover{background:rgba(255,255,255,.10);color:#fff;}',
    'html[data-askw-color="dark"] .askw-ask-input,html[data-askw-color="dark"] .askw-follow-input,html[data-askw-color="dark"] .askw-picker input,html[data-askw-color="dark"] .askw-picker-browse{background:rgba(45,45,45,.92);border-color:var(--askw-line);color:#fff;}',
    'html[data-askw-color="dark"] .askw-q,html[data-askw-color="dark"] .askw-citation,html[data-askw-color="dark"] .askw-foot button{background:rgba(45,45,45,.72);}',
    'html[data-askw-color="dark"] .askw-history-entry{background:rgba(45,45,45,.42)}html[data-askw-color="dark"] .askw-history-actions button{background:rgba(45,45,45,.78);color:#cdcdcd}',
    'html[data-askw-color="dark"] .askw-foot button:hover,html[data-askw-color="dark"] .askw-citation:hover{background:rgba(58,58,58,.94);color:#fff;}',
    'html[data-askw-color="dark"] .askw-body code{background:rgba(255,255,255,.09);}',
    'html[data-askw-color="dark"] .askw-citation pre{background:#181818;color:#cdcdcd;}',
    'html[data-askw-color="dark"] .askw-follow-input:disabled{background:#242424;}',
    '@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){.askw-menu,.askw-panel,.askw-picker{background:#fff}.askw-pill{--askw-glass:#fff;--askw-frost:#fff}}',
    '@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){html[data-askw-color="dark"] .askw-menu,html[data-askw-color="dark"] .askw-panel,html[data-askw-color="dark"] .askw-picker{background:#242424}html[data-askw-page="dark"] .askw-pill{--askw-glass:#242424;--askw-frost:#242424}}',
    '@media(prefers-reduced-transparency:reduce){.askw-menu,.askw-panel,.askw-picker{background:rgba(255,255,255,.98)}.askw-menu,.askw-panel,.askw-picker,.askw-pill{backdrop-filter:none;-webkit-backdrop-filter:none}.askw-pill{--askw-glass:rgba(255,255,255,.98);--askw-frost:rgba(255,255,255,.98)}html[data-askw-color="dark"] .askw-menu,html[data-askw-color="dark"] .askw-panel,html[data-askw-color="dark"] .askw-picker{background:rgba(36,36,36,.98)}html[data-askw-page="dark"] .askw-pill{--askw-glass:rgba(36,36,36,.98);--askw-frost:rgba(36,36,36,.98)}}',
    '@media(prefers-reduced-motion:reduce){.askw-dot{animation:none}.askw-toast,.askw-pill,.askw-pill b{transition:none}}'
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
    var selectedNode = range.commonAncestorContainer;
    if (selectedNode && selectedNode.nodeType === 3) selectedNode = selectedNode.parentElement;
    var pageEl = selectedNode && selectedNode.closest ? selectedNode.closest('[data-askw-page]') : null;
    return {
      text: text.slice(0, MAX_SEL),
      context: surroundingContext(range),
      rect: range.getBoundingClientRect(),
      page: pageEl ? Number(pageEl.getAttribute('data-askw-page')) || null : null
    };
  }

  // ============================================================ build DOM
  function build() {
    // --- automatic selection affordance ---
    triggerEl = document.createElement('button');
    triggerEl.type = 'button';
    triggerEl.className = 'askw-root askw-trigger';
    triggerEl.setAttribute('aria-label', 'Ask about the selected text');
    triggerEl.setAttribute('aria-hidden', 'true');
    triggerEl.title = 'Ask about this selection (Command or Control + Shift + A)';
    triggerEl.innerHTML = '<span aria-hidden="true">✦</span> Ask';
    document.body.appendChild(triggerEl);
    triggerEl.addEventListener('click', function () {
      var captured = captureFromSelection();
      if (captured) sel = captured;
      if (!sel) return;
      var rect = triggerEl.getBoundingClientRect();
      showMenu(rect.left, rect.bottom + 6);
    });

    // --- context menu ---
    menuEl = document.createElement('div');
    menuEl.className = 'askw-root askw-menu';
    menuEl.setAttribute('role', 'dialog');
    menuEl.setAttribute('aria-label', 'Ask about selected text');
    menuEl.setAttribute('aria-modal', 'false');
    menuEl.setAttribute('aria-hidden', 'true');
    menuEl.innerHTML =
      '<button type="button" class="askw-item" data-act="eli5"><span class="askw-ico" aria-hidden="true">○</span>ELI5</button>' +
      '<button type="button" class="askw-item" data-act="prove"><span class="askw-ico" aria-hidden="true">✓</span>Prove it</button>' +
      '<button type="button" class="askw-item" data-act="ask"><span class="askw-ico" aria-hidden="true">…</span>Ask a question…</button>' +
      '<div class="askw-ask-wrap"><textarea class="askw-ask-input" aria-label="Question about the highlighted text" placeholder="Ask about the highlighted text…"></textarea>' +
      '<button type="button" class="askw-ask-go">Go</button><div style="clear:both"></div></div>';
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
    menuEl.addEventListener('keydown', function (e) {
      var items = Array.prototype.slice.call(menuEl.querySelectorAll('.askw-item'));
      var index = items.indexOf(document.activeElement);
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation(); hideMenu();
        if (sel) { showTrigger(sel); triggerEl.focus(); }
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        if (e.key === 'Home') index = 0;
        else if (e.key === 'End') index = items.length - 1;
        else if (e.key === 'ArrowDown') index = (index + 1 + items.length) % items.length;
        else index = (index - 1 + items.length) % items.length;
        items[index].focus();
      }
    });
    askGo.addEventListener('click', submitAsk);
    askInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitAsk(); }
    });

    // --- answer panel ---
    panelEl = document.createElement('div');
    panelEl.className = 'askw-root askw-panel';
    panelEl.setAttribute('role', 'dialog');
    panelEl.setAttribute('aria-label', 'Onyx answer');
    panelEl.setAttribute('aria-modal', 'false');
    panelEl.setAttribute('aria-busy', 'false');
    panelEl.innerHTML =
      '<div class="askw-head"><p class="askw-eyebrow"></p><p class="askw-selq"></p>' +
      '<button type="button" class="askw-x" title="Close" aria-label="Close answer">×</button></div>' +
      '<div class="askw-tools" aria-live="polite"></div>' +
      '<div class="askw-body" aria-live="polite" aria-relevant="additions text"></div>' +
      '<div class="askw-followup"><textarea class="askw-follow-input" aria-label="Follow-up question" rows="1" placeholder="Ask a follow-up…"></textarea>' +
      '<button type="button" class="askw-follow-go" title="Send (Enter)" aria-label="Send follow-up">↑</button></div>' +
      '<div class="askw-foot"><button class="askw-claude" title="Open a dedicated provider session in this folder — hold ⌥ Option to copy the prompt instead">Open session</button><button class="askw-history">History</button><button class="askw-stop">Stop</button><button class="askw-retry">Retry</button><button class="askw-copy">Copy</button></div>';
    document.body.appendChild(panelEl);
    panelTitle = panelEl.querySelector('.askw-eyebrow');
    panelSel = panelEl.querySelector('.askw-selq');
    panelTools = panelEl.querySelector('.askw-tools');
    panelBody = panelEl.querySelector('.askw-body');
    followWrap = panelEl.querySelector('.askw-followup');
    followInput = panelEl.querySelector('.askw-follow-input');
    followGo = panelEl.querySelector('.askw-follow-go');
    claudeBtn = panelEl.querySelector('.askw-claude');
    historyBtn = panelEl.querySelector('.askw-history');
    stopBtn = panelEl.querySelector('.askw-stop');
    retryBtn = panelEl.querySelector('.askw-retry');
    panelEl.querySelector('.askw-x').addEventListener('click', closePanel);
    panelSel.addEventListener('click', function () { if (!panelDragged) toggleQuote(); });
    panelSel.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleQuote(); }
    });
    panelEl.querySelector('.askw-copy').addEventListener('click', function () {
      var t = panelBody.innerText || '';
      if (navigator.clipboard) navigator.clipboard.writeText(t).catch(function () {});
    });
    claudeBtn.addEventListener('click', function (e) { openInProvider(e.altKey); });
    historyBtn.addEventListener('click', loadSelectionHistory);
    stopBtn.addEventListener('click', stopRequest);
    retryBtn.addEventListener('click', retryRequest);
    followGo.addEventListener('click', submitFollowup);
    followInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitFollowup(); }
    });
    followInput.addEventListener('input', autosizeFollow);
    makeDragResize(panelEl, panelEl.querySelector('.askw-head'));

    // --- folder pill + picker ---
    pillEl = document.createElement('button');
    pillEl.type = 'button';
    pillEl.className = 'askw-root askw-pill';
    pillEl.innerHTML = '<svg class="askw-ico" aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.6l1.5 1.5h4.9A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"/></svg><b class="askw-pill-label">…</b>';
    pillLabel = pillEl.querySelector('.askw-pill-label');
    pillEl.title = 'Context folder the selected provider reads — click to change';
    pillEl.setAttribute('aria-haspopup', 'dialog');
    pillEl.setAttribute('aria-expanded', 'false');
    document.body.appendChild(pillEl);
    pillEl.addEventListener('click', togglePicker);

    pickerEl = document.createElement('div');
    pickerEl.className = 'askw-root askw-picker';
    pickerEl.setAttribute('role', 'dialog');
    pickerEl.setAttribute('aria-label', 'Choose context folder');
    pickerEl.setAttribute('aria-hidden', 'true');
    pickerEl.innerHTML =
      '<label for="askw-folder-input">Context folder</label><div class="askw-picker-row"><input id="askw-folder-input" class="askw-picker-input" spellcheck="false" /><button type="button" class="askw-picker-browse">Choose…</button></div>' +
      '<div class="askw-hint">Only files inside this folder are available to the selected provider as supporting evidence.</div>' +
      '<button type="button" class="askw-picker-save">Use this folder</button>' +
      '<div class="askw-recent"></div>' +
      (isFileProto ? '<div class="askw-hint">You opened this over file:// — if requests fail, serve the page over http (e.g. <code>python3 -m http.server</code>).</div>' : '');
    document.body.appendChild(pickerEl);
    pickerEl.querySelector('.askw-picker-save').addEventListener('click', function () {
      var v = pickerEl.querySelector('.askw-picker-input').value.trim();
      if (v) setFolder(v);
    });
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.askwPick) {
      pickerEl.classList.add('askw-native');
      pickerEl.querySelector('.askw-picker-browse').addEventListener('click', async function () {
        try {
          var input = pickerEl.querySelector('.askw-picker-input');
          var chosen = await window.webkit.messageHandlers.askwPick.postMessage({ kind: 'folder', initial: input.value });
          if (chosen) input.value = chosen;
        } catch (e) { toast('Folder picker failed.'); }
      });
    }
  }

  // ============================================================ menu
  function showTrigger(captured) {
    if (!triggerEl || !captured || panelEl.classList.contains('open')) return;
    sel = captured;
    triggerEl.style.display = 'flex';
    triggerEl.setAttribute('aria-hidden', 'false');
    var rect = captured.rect;
    var width = triggerEl.offsetWidth || 58;
    var height = triggerEl.offsetHeight || 28;
    var left = rect.left + rect.width / 2 - width / 2;
    var top = rect.bottom + 7;
    if (top + height > window.innerHeight - 8) top = rect.top - height - 7;
    triggerEl.style.left = Math.max(8, Math.min(left, window.innerWidth - width - 8)) + 'px';
    triggerEl.style.top = Math.max(8, top) + 'px';
  }
  function hideTrigger() {
    if (!triggerEl) return;
    triggerEl.style.display = 'none';
    triggerEl.setAttribute('aria-hidden', 'true');
  }
  function showMenu(x, y) {
    hideTrigger();
    askWrap.classList.remove('open');
    askInput.value = '';
    menuEl.style.display = 'block';
    menuEl.setAttribute('aria-hidden', 'false');
    var mw = menuEl.offsetWidth || 190;
    var mh = menuEl.offsetHeight || 130;
    menuEl.style.left = Math.max(6, Math.min(x, window.innerWidth - mw - 8)) + 'px';
    menuEl.style.top = Math.max(6, Math.min(y, window.innerHeight - mh - 8)) + 'px';
    var firstItem = menuEl.querySelector('.askw-item');
    if (firstItem) firstItem.focus();
  }
  function hideMenu() {
    if (!menuEl) return;
    menuEl.style.display = 'none';
    menuEl.setAttribute('aria-hidden', 'true');
  }

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
    panelSel.textContent = sel ? '“' + sel.text + '”' : '';
    panelTools.innerHTML = '';
    panelBody.innerHTML = '';
    stopBtn.style.display = 'none';
    retryBtn.style.display = 'none';
    hideFollowup();
    panelEl.classList.add('open');
    fitQuote();
    if (!userPinned) positionPanel();
    panelEl.querySelector('.askw-x').focus();
  }
  // The quoted passage shows three whole lines; a longer one ends in an ellipsis
  // and becomes a button that unfolds the rest.
  function fitQuote() {
    panelSel.classList.remove('expanded');
    var clamped = panelSel.scrollHeight > panelSel.clientHeight + 1;
    panelSel.classList.toggle('clamped', clamped);
    if (clamped) {
      panelSel.setAttribute('role', 'button');
      panelSel.setAttribute('tabindex', '0');
      panelSel.setAttribute('aria-expanded', 'false');
      panelSel.title = 'Show the whole passage';
    } else {
      ['role', 'tabindex', 'aria-expanded', 'title'].forEach(function (a) { panelSel.removeAttribute(a); });
    }
  }
  function toggleQuote() {
    if (!panelSel.classList.contains('clamped')) return;
    var open = panelSel.classList.toggle('expanded');
    panelSel.setAttribute('aria-expanded', open ? 'true' : 'false');
    panelSel.title = open ? 'Collapse the passage' : 'Show the whole passage';
  }
  function closePanel() {
    panelEl.classList.remove('open');
    panelEl.setAttribute('aria-busy', 'false');
    activeAction = null;
    if (abort) { abort.abort(); abort = null; }
    streaming = false;
    stopBtn.style.display = 'none';
    retryBtn.style.display = 'none';
    resetConversation();
    historyOrigin = null;
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

  function historyModeLabel(mode) {
    return ({ generated: 'Generated', rerun: 'Asked again', edited: 'Edited & asked', continue: 'Continued' })[mode] || 'Generated';
  }

  function setSelectionFromHistory(item) {
    var x = Math.max(20, window.innerWidth / 2 - 1);
    var y = Math.max(70, Math.min(window.innerHeight / 3, window.innerHeight - 220));
    sel = {
      text: item.selection || '',
      context: item.context || item.selection || '',
      page: item.document_page || null,
      rect: { left: x, right: x + 2, top: y, bottom: y + 2, width: 2, height: 2 }
    };
    if (item.folder) commitFolder(item.folder);
  }

  function restoreHistory(item, mode) {
    setSelectionFromHistory(item);
    historyOrigin = { request_id: item.request_id, mode: mode };
    lastAction = item.action || 'ask';
    lastQuestion = item.question || '';
    lastAnswer = item.answer || '';
    openPanel(item.action || 'ask');
    resetConversation();

    if (mode === 'edited') {
      panelTitle.textContent = 'Edit & ask';
      var note = document.createElement('div');
      note.className = 'askw-origin';
      note.textContent = 'Editing a saved question · the next answer will use your current ' + providerLabel() + ' model';
      panelBody.appendChild(note);
      showFollowup(true);
      followInput.value = item.question || (item.action === 'eli5' ? 'Explain this passage simply.' : item.action === 'prove' ? 'What evidence supports this passage?' : '');
      autosizeFollow();
      followInput.focus();
      return;
    }

    if (item.question) transcript.push({ role: 'user', text: item.question });
    transcript.push({ role: 'assistant', text: item.answer || item.error || 'No saved answer.' });
    renderConversation();
    var origin = document.createElement('div');
    origin.className = 'askw-origin';
    origin.textContent = historyModeLabel(item.request_mode) + ' · ' + (item.provider || 'claude') + ' · ' + item.model;
    panelBody.appendChild(origin);
    if (item.citations && item.citations.length) renderCitations(item.citations);
    panelTitle.textContent = mode === 'continue' ? 'Continue saved answer' : 'Saved answer';
    showFollowup(true);
    anchorTurn(panelBody.firstElementChild);
  }

  function askAgainHistory(item) {
    setSelectionFromHistory(item);
    historyOrigin = null;
    start(item.action || 'ask', item.question || '', {
      bypass_cache: true,
      request_mode: 'rerun',
      parent_request_id: item.request_id
    });
  }
  // Append a fresh answer block (showing "Thinking…") for the stream to fill.
  function appendLive() {
    var asked = panelBody.lastElementChild;
    liveEl = document.createElement('div');
    liveEl.className = 'askw-a';
    liveEl.innerHTML = '<div class="askw-think"><span class="askw-dot"></span>Thinking…</div>';
    panelBody.appendChild(liveEl);
    anchorTurn(asked && asked.classList.contains('askw-q') ? asked : liveEl);
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
    // The answer can grow dramatically after this initial placement. Cap its
    // material sheet to the remaining viewport so the footer/Stop button never
    // streams below the screen; the flex body becomes the scroll container.
    panelEl.style.maxHeight = Math.max(180, vh - top - m) + 'px';
  }
  // Follow a streaming answer down, but never past the top of the turn that asked
  // for it: the question stays in view and the answer reads from its first line.
  // Once the reader scrolls on their own, later tokens leave them where they are.
  var scrollAnchor = null, scrollPinned = -1;
  function anchorTurn(el) { scrollAnchor = el; scrollPinned = -1; autoscroll(); }
  function autoscroll() {
    var max = panelBody.scrollHeight - panelBody.clientHeight, now = panelBody.scrollTop;
    var clamped = scrollPinned > max && now >= max - 1;   // a re-render shrank the content
    if (scrollPinned >= 0 && Math.abs(now - scrollPinned) > 2 && !clamped) return;
    var top = max;
    if (scrollAnchor && panelBody.contains(scrollAnchor)) {
      top = Math.min(top, scrollAnchor.getBoundingClientRect().top - panelBody.getBoundingClientRect().top + now - 8);
    }
    panelBody.scrollTop = Math.max(0, top);
    scrollPinned = panelBody.scrollTop;
  }

  // Drag the panel by its header; resize from the bottom-right corner (CSS
  // resize:both). Either gesture "pins" it so auto-positioning stops fighting you.
  var panelDragged = false;   // a drag that ends on the quote is not a click on it
  function makeDragResize(panel, handle) {
    handle.addEventListener('mousedown', function (e) {
      // close button isn't a drag grip; an unfolded quote keeps its scrollbar
      if (e.target.closest('.askw-x, .askw-selq.expanded')) return;
      e.preventDefault();
      var r = panel.getBoundingClientRect();
      var sx = e.clientX, sy = e.clientY, ox = r.left, oy = r.top;
      panelDragged = false;
      function mv(ev) {
        if (!panelDragged && Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 4) return;
        panelDragged = userPinned = true;
        var nx = ox + (ev.clientX - sx), ny = oy + (ev.clientY - sy);
        nx = Math.max(4, Math.min(nx, window.innerWidth - panel.offsetWidth - 4));
        ny = Math.max(4, Math.min(ny, window.innerHeight - 44));
        panel.style.left = nx + 'px';
        panel.style.top = ny + 'px';
        panel.style.maxHeight = Math.max(180, window.innerHeight - ny - 4) + 'px';
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
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'askw-root askw-toast';
      toastEl.setAttribute('role', 'status');
      toastEl.setAttribute('aria-live', 'polite');
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(function () { toastEl.classList.remove('show'); }, 2400);
  }

  function providerLabel() { return serverConfig.provider === 'codex' ? 'Codex' : 'Claude'; }
  function setProviderLabel(opt) {
    if (claudeBtn && !claudeBtn.disabled) claudeBtn.textContent = opt ? 'Copy ' + providerLabel() + ' prompt' : 'Open in ' + providerLabel();
  }

  // Hand off to a dedicated provider session in the context folder. With Option
  // held (copy=true), copy the seed prompt instead of launching a terminal.
  function openInProvider(copy) {
    if (!sel || !lastAction) { toast('Ask something first.'); return; }
    claudeBtn.disabled = true;
    claudeBtn.textContent = copy ? 'Copying…' : 'Opening…';
    fetch(SERVER + '/open-in-provider', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        folder: folder, action: lastAction, selection: sel.text,
        question: lastQuestion, answer: lastAnswer, provider: serverConfig.provider, mode: copy ? 'copy' : 'open', token: TOKEN
      })
    }).then(function (r) { return r.json(); }).then(function (d) {
      claudeBtn.disabled = false; setProviderLabel(false);
      if (!d || !d.ok) { toast((d && d.error) || 'Open in ' + providerLabel() + ' failed.'); return; }
      if (copy) {
        if (navigator.clipboard && d.prompt) {
          navigator.clipboard.writeText(d.prompt).then(function () { toast(providerLabel() + ' prompt copied'); }).catch(function () { toast('Copy failed'); });
        } else { toast('Copy failed'); }
      } else {
        toast('Opening a ' + providerLabel() + ' session…');
      }
    }).catch(function () {
      claudeBtn.disabled = false; setProviderLabel(false);
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
  function metaValue(name) {
    var m = document.querySelector('meta[name="' + name + '"]');
    return m ? (m.getAttribute('content') || '').trim() : '';
  }
  function documentSource() { return metaValue('askw-src') || location.href.split('#')[0]; }
  function documentIdentity() {
    return [documentSource(), document.title || '', serverConfig.version, serverConfig.provider, serverConfig.model, serverConfig.reasoning_effort].join('|');
  }
  function cacheKey(action) {
    return 'askw:cache:' + djb2([
      action, folder || '', documentIdentity(), sel ? sel.text : '', sel ? sel.context : '', sel ? sel.page || '' : ''
    ].join('|'));
  }
  function cacheGet(k) {
    try {
      var item = JSON.parse(localStorage.getItem(k) || 'null');
      if (!item || typeof item.answer !== 'string') return null;
      var ttl = Number(serverConfig.cache_ttl_hours || 0) * 3600000;
      if (!ttl || Date.now() - Number(item.created_at || 0) > ttl) { localStorage.removeItem(k); return null; }
      return item;
    } catch (e) { return null; }
  }
  function cacheSet(k, v) {
    try {
      var max = Number(serverConfig.cache_max_entries || 0);
      if (!max) return;
      var index = JSON.parse(localStorage.getItem('askw:cache-index') || '[]');
      index = index.filter(function (item) { return item.key !== k; });
      index.unshift({ key: k, created_at: Date.now() });
      while (index.length > max) { var old = index.pop(); localStorage.removeItem(old.key); }
      localStorage.setItem(k, JSON.stringify({ answer: v, created_at: Date.now(), version: serverConfig.version }));
      localStorage.setItem('askw:cache-index', JSON.stringify(index));
    } catch (e) {}
  }

  // ============================================================ streaming
  // A top-level action (ELI5 / Prove it / Ask) opens the panel and starts a new
  // conversation. Follow-ups reuse the same panel + selection via submitFollowup.
  function start(action, question, options) {
    options = options || {};
    hideMenu();
    if (!sel) return;
    lastAction = action; lastQuestion = question || ''; lastAnswer = '';

    var ck = action !== 'ask' ? cacheKey(action) : null;
    if (ck && !options.bypass_cache) {
      if (activeAction === action) {
        try { localStorage.removeItem(ck); } catch (e) {}   // re-click = refresh
      } else {
        var cached = cacheGet(ck);
        if (cached) {
          openPanel(action);
          resetConversation();
          transcript.push({ role: 'assistant', text: cached.answer });
          lastAnswer = cached.answer;
          renderConversation();
          var cacheNote = document.createElement('div');
          cacheNote.className = 'askw-origin';
          cacheNote.textContent = 'Cached locally · ' + new Date(Number(cached.created_at || Date.now())).toLocaleString() + ' · click the same action again to refresh';
          panelBody.appendChild(cacheNote);
          showFollowup(true);
          return;
        }
      }
    }

    openPanel(action);
    resetConversation();
    if (action === 'ask' && question) transcript.push({ role: 'user', text: question });
    renderConversation();

    var request = {
      action: action,
      selection: sel.text,
      context: sel.context,
      question: question || '',
      document_source: documentSource(),
      document_title: document.title || '',
      document_page: sel.page || null,
      folder: folder,
      token: TOKEN
    };
    if (options.request_mode) request.request_mode = options.request_mode;
    if (options.parent_request_id) request.parent_request_id = options.parent_request_id;
    streamAnswer(request, ck);
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

    // Send every completed turn before this question so the selected model has the thread.
    var history = transcript.slice(0, -1).map(function (t) { return { role: t.role, text: t.text }; });
    var request = {
      action: 'ask',
      selection: sel.text,
      context: sel.context,
      question: q,
      history: history,
      document_source: documentSource(),
      document_title: document.title || '',
      document_page: sel.page || null,
      folder: folder,
      token: TOKEN
    };
    if (historyOrigin) {
      request.request_mode = historyOrigin.mode === 'edited' ? 'edited' : 'continue';
      request.parent_request_id = historyOrigin.request_id;
    } else {
      request.request_mode = 'continue';
      if (currentRequestId) request.parent_request_id = currentRequestId;
    }
    streamAnswer(request, null);
  }

  // Shared stream pump: fills the live answer block, commits it to the transcript
  // on completion. `ck` is a cache key to store the result under (top-level only).
  function streamAnswer(reqBody, ck) {
    if (abort) abort.abort();
    abort = new AbortController();
    var myAbort = abort;
    lastRequestBody = JSON.parse(JSON.stringify(reqBody));
    lastCacheKey = ck;
    currentRequestId = null;
    currentRequestMode = reqBody.request_mode || 'generated';
    requestCitations = [];
    requestTrace = [];
    streaming = true;
    panelEl.setAttribute('aria-busy', 'true');
    clearTools();
    appendLive();
    showFollowup(false);
    stopBtn.style.display = 'block';
    retryBtn.style.display = 'none';

    var acc = '', hadError = false, doneMeta = null;

    function handleFrame(frame) {
      var evt = '', data = {};
      frame.split('\n').forEach(function (line) {
        if (line.indexOf('event: ') === 0) evt = line.slice(7).trim();
        else if (line.indexOf('data: ') === 0) {
          try { data = JSON.parse(line.slice(6)); } catch (e) { data = {}; }
        }
      });
      if (evt === 'meta') {
        currentRequestId = data.request_id || null;
        if (data.provider) serverConfig.provider = data.provider;
        if (data.model) serverConfig.model = data.model;
        if (data.effort) serverConfig.reasoning_effort = data.effort;
        if (data.request_mode) currentRequestMode = data.request_mode;
        setProviderLabel(false);
      } else if (evt === 'token') {
        acc += data.text || '';
        renderMarkdown(liveEl, acc);
        autoscroll();
      } else if (evt === 'tool_status') {
        updateTool(data);
      } else if (evt === 'tool_trace') {
        requestTrace.push(data);
      } else if (evt === 'status') {
        if (!acc && liveEl) liveEl.innerHTML = '<div class="askw-think"><span class="askw-dot"></span>' + esc(data.message || providerLabel() + ' is working…') + '</div>';
      } else if (evt === 'citations') {
        requestCitations = Array.isArray(data.items) ? data.items : [];
      } else if (evt === 'error') {
        hadError = true;
        liveError(data.message || 'An error occurred.');
        retryBtn.style.display = data.retryable === false ? 'none' : 'block';
        acc = '';
      } else if (evt === 'done') {
        doneMeta = data;
      }
    }

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
          if (r.done) {
            if (buffer.trim()) handleFrame(buffer);
            if (myAbort === abort) finish(acc, ck, hadError, doneMeta);
            return;
          }
          buffer += decoder.decode(r.value, { stream: true });
          var frames = buffer.split(/\n\n/);
          buffer = frames.pop() || '';
          frames.forEach(handleFrame);
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      if (myAbort !== abort) return;
      liveError('Could not reach the Ask server at ' + SERVER + '. Is it running? (' + (err && err.message || err) + ')');
      retryBtn.style.display = 'block';
      finishMeta();
    });
  }

  function finish(acc, ck, hadError, meta) {
    clearTools();
    if (acc) {
      transcript.push({ role: 'assistant', text: acc });
      lastAnswer = acc;
      if (ck) cacheSet(ck, acc);
    } else if (!hadError && liveEl && liveEl.querySelector('.askw-think')) {
      liveEl.innerHTML = '<div class="askw-err">No response received.</div>';
      retryBtn.style.display = 'block';
    }
    if (requestCitations.length) renderCitations(requestCitations);
    if (meta && liveEl) {
      var detail = document.createElement('p'); detail.className = 'askw-request-meta';
      detail.textContent = [historyModeLabel(currentRequestMode), providerLabel(), serverConfig.model, serverConfig.reasoning_effort, meta.elapsed_ms ? (meta.elapsed_ms / 1000).toFixed(1) + 's' : '', currentRequestId ? currentRequestId.slice(0, 8) : ''].filter(Boolean).join(' · ');
      liveEl.appendChild(detail);
    }
    finishMeta();
  }
  // Re-enable the composer and detach the live element after a turn settles.
  function finishMeta() {
    liveEl = null;
    streaming = false;
    panelEl.setAttribute('aria-busy', 'false');
    stopBtn.style.display = 'none';
    abort = null;
    if (currentRequestId) historyOrigin = { request_id: currentRequestId, mode: 'continue' };
    showFollowup(true);
    if (!userPinned) positionPanel();
    maybeApplyReload();
  }

  function stopRequest() {
    if (!abort) return;
    var active = abort;
    abort = null;
    active.abort();
    clearTools();
    liveError('Stopped.');
    streaming = false;
    panelEl.setAttribute('aria-busy', 'false');
    stopBtn.style.display = 'none';
    retryBtn.style.display = 'block';
    showFollowup(true);
    maybeApplyReload();
  }

  function retryRequest() {
    if (!lastRequestBody) return;
    retryBtn.style.display = 'none';
    streamAnswer(JSON.parse(JSON.stringify(lastRequestBody)), lastCacheKey);
  }

  function renderCitations(items) {
    var box = document.createElement('div'); box.className = 'askw-citations';
    box.innerHTML = '<p class="askw-citations-title">Evidence</p>';
    items.forEach(function (item) {
      var button = document.createElement('button'); button.className = 'askw-citation';
      button.title = item.snippet ? 'Click to preview, then click again to open' : 'Open source';
      button.innerHTML = '<strong>' + esc(item.label || item.path) + '</strong>' + (item.snippet ? '<pre>' + esc(item.snippet) + '</pre>' : '');
      button.addEventListener('click', function () {
        if (item.snippet && !button.classList.contains('expanded')) { button.classList.add('expanded'); return; }
        fetch(SERVER + '/api/open-source', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: TOKEN, path: item.path, line: item.line, page: item.page, folder: folder })
        }).then(function (r) { return r.json(); }).then(function (d) { if (!d.ok) toast(d.error || 'Could not open source'); }).catch(function () { toast('Could not open source'); });
      });
      box.appendChild(button);
    });
    panelBody.appendChild(box);
  }

  function loadSelectionHistory() {
    if (!sel) return;
    fetch(SERVER + '/api/history?source=' + encodeURIComponent(documentSource()) + '&selection=' + encodeURIComponent(sel.text) + '&limit=10')
      .then(function (r) { return r.json(); }).then(function (d) {
        var items = d.conversations || [];
        if (!items.length) { toast('No saved answers for this passage yet.'); return; }
        panelTitle.textContent = 'Saved history';
        panelBody.innerHTML = '';
        items.forEach(function (item) {
          var entry = document.createElement('section'); entry.className = 'askw-history-entry';
          var q = document.createElement('div'); q.className = 'askw-q'; q.textContent = item.question || item.action;
          var a = document.createElement('div'); a.className = 'askw-a'; renderMarkdown(a, item.answer || item.error || 'No answer');
          var meta = document.createElement('div'); meta.className = 'askw-history-meta';
          meta.textContent = historyModeLabel(item.request_mode) + ' · ' + (item.provider || 'claude') + ' · ' + item.model + (item.effort ? ' · ' + item.effort : '') + ' · ' + new Date(item.started_at * 1000).toLocaleString();
          var actions = document.createElement('div'); actions.className = 'askw-history-actions';
          var again = document.createElement('button'); again.textContent = 'Ask again'; again.onclick = function () { askAgainHistory(item); };
          var edit = document.createElement('button'); edit.textContent = 'Edit & ask'; edit.onclick = function () { restoreHistory(item, 'edited'); };
          var resume = document.createElement('button'); resume.textContent = 'Continue'; resume.onclick = function () { restoreHistory(item, 'continue'); };
          actions.appendChild(again); actions.appendChild(edit); actions.appendChild(resume);
          entry.appendChild(q); entry.appendChild(a); entry.appendChild(meta); entry.appendChild(actions);
          panelBody.appendChild(entry);
        });
        toast(items.length + ' saved answer' + (items.length === 1 ? '' : 's'));
      }).catch(function () { toast('Could not load history.'); });
  }

  // ============================================================ folder
  function commitFolder(f) {
    folder = f;
    try { localStorage.setItem('askw:folder', f); } catch (e) {}
    if (recentFolders.indexOf(f) === -1) recentFolders.unshift(f);
    updatePill();
    renderRecent();
  }
  function setFolder(f) {
    fetch(SERVER + '/api/folder?path=' + encodeURIComponent(f)).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { toast(d.error || 'Folder is not allowed.'); return; }
      commitFolder(d.path);
      closePicker();
    }).catch(function () { toast('Could not validate that folder.'); });
  }
  function updatePill() {
    if (!pillLabel) return;
    pillLabel.textContent = basename(folder);
    pillEl.setAttribute('aria-label', 'Context folder: ' + (folder || 'none') + '. Activate to change.');
  }
  function renderRecent() {
    var box = pickerEl.querySelector('.askw-recent');
    box.innerHTML = '';
    recentFolders.slice(0, 8).forEach(function (f) {
      var d = document.createElement('button');
      d.type = 'button';
      d.className = 'askw-recent-item';
      d.textContent = f;
      d.title = f;
      d.addEventListener('click', function () { setFolder(f); });
      box.appendChild(d);
    });
  }
  function togglePicker() {
    if (pickerEl.classList.contains('open')) { closePicker(); return; }
    pickerEl.querySelector('.askw-picker-input').value = folder || '';
    renderRecent();
    pickerEl.classList.add('open');
    pickerEl.setAttribute('aria-hidden', 'false');
    pillEl.setAttribute('aria-expanded', 'true');
    pickerEl.querySelector('.askw-picker-input').focus();
  }
  function closePicker() {
    pickerEl.classList.remove('open');
    pickerEl.setAttribute('aria-hidden', 'true');
    pillEl.setAttribute('aria-expanded', 'false');
  }

  function metaFolder() {
    return metaValue('askw-folder');
  }

  function applyAppearance(raw) {
    appearanceTheme = raw === 'dark' || raw === 'light' ? raw : 'system';
    var systemDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    // While the app wears the vault look, the vault's mode stands in for the app theme.
    var effective = vaultLook ? vaultLook.mode
      : appearanceTheme === 'dark' || (appearanceTheme === 'system' && systemDark) ? 'dark' : 'light';
    document.documentElement.setAttribute('data-askw-color', effective);
    applyPageTone();
    var bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.askwAppearance;
    if (bridge) Promise.resolve(bridge.postMessage({ theme: vaultLook ? vaultLook.mode : appearanceTheme })).catch(function () {});
  }

  // Glass chips lie on the page, so they take the page's tone — a dark app over a
  // cream page still gets light glass. The first opaque background wins (body,
  // then html); a transparent page shows the app's pane, so the app theme stands in.
  function pageTone() {
    var layers = [document.body, document.documentElement];
    for (var i = 0; i < layers.length; i++) {
      var m = layers[i] && /^rgba?\(([^)]*)\)/.exec(getComputedStyle(layers[i]).backgroundColor);
      if (!m) continue;
      var c = m[1].split(/[\s,\/]+/).map(Number);
      if (c.length > 3 && c[3] < 0.5) continue;
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 140 ? 'light' : 'dark';
    }
    return document.documentElement.getAttribute('data-askw-color') || 'light';
  }
  function applyPageTone() {
    var tone = pageTone();
    if (document.documentElement.getAttribute('data-askw-page') !== tone) document.documentElement.setAttribute('data-askw-page', tone);
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

    return fetch(SERVER + '/config').then(function (r) { return r.json(); }).then(function (cfg) {
      serverConfig = cfg || serverConfig;
      setProviderLabel(false);
      applyAppearance(cfg.appearance_theme);
      defaultFolder = cfg.default_folder;
      recentFolders = (cfg.recent_folders || []).slice();
      if (!folder) folder = defaultFolder;
      if (folder && recentFolders.indexOf(folder) === -1) recentFolders.unshift(folder);
      updatePill();
    }).catch(function () { updatePill(); });
  }

  // Markdown appearance changes in place: preserve selection, scroll, and answers.
  function initMarkdownTheme() {
    // Notes, and the other pages Onyx lays out itself (markdown_theme.KINDS); an HTML page keeps its own look.
    if (['markdown', 'text', 'pdf', 'selection'].indexOf(document.body.getAttribute('data-askw-document-kind')) < 0 || !metaSrc()) return;
    var style = document.getElementById('askw-markdown-theme');
    if (!style) {
      style = document.createElement('style');
      style.id = 'askw-markdown-theme';
      document.head.appendChild(style);
    }
    var pending = false;
    function refresh() {
      if (pending || document.hidden) return;
      pending = true;
      fetch(SERVER + '/api/markdown-theme', { cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('Theme unavailable');
        return r.json();
      }).then(function (theme) {
        if (typeof theme.css === 'string' && style.textContent !== theme.css) { style.textContent = theme.css; applyPageTone(); }
      }).catch(function () { /* Keep the last good appearance while offline. */ })
        .finally(function () { pending = false; });
    }
    refresh();
    window.setInterval(refresh, 2000);
    document.addEventListener('visibilitychange', refresh);
  }

  // The whole app in the vault's colours (Match vault appearance): the panel's sheet from the service
  // (vault_look.reader_stylesheet), on html[data-askw-look], after this file's own. Kept live like the reading styles.
  function applyVaultLook(look) {
    var css = (look && look.reader_css) || '';
    var style = document.getElementById('askw-vault-look');
    if (!style) {
      style = document.createElement('style');
      style.id = 'askw-vault-look';
      (document.head || document.documentElement).appendChild(style);
    }
    if (style.textContent !== css) style.textContent = css;
    vaultLook = css ? look : null;
    if (vaultLook) document.documentElement.setAttribute('data-askw-look', vaultLook.mode);
    else document.documentElement.removeAttribute('data-askw-look');
    applyAppearance(appearanceTheme);
  }
  function initVaultLook() {
    var pending = false, revision = null;
    function refresh() {
      if (pending || document.hidden) return;
      pending = true;
      fetch(SERVER + '/api/vault-look', { cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('Look unavailable');
        return r.json();
      }).then(function (look) {
        if (look.revision !== revision) { revision = look.revision; applyVaultLook(look); }
      }).catch(function () { /* Keep the last good look while offline, or none on a page that may not ask. */ })
        .finally(function () { pending = false; });
    }
    refresh();
    window.setInterval(refresh, 3000);
    document.addEventListener('visibilitychange', refresh);
  }

  // ============================================================ live reload
  // Only /view pages seed <meta name="askw-src">; on those, poll the file's stat
  // signature and reload when it settles on a new value, so edits from another
  // editor/agent show up in real time. Reloads are deferred while the widget is
  // busy (a stream in flight, or the answer panel open) so we never yank content
  // mid-answer; the deferred reload fires when things go idle.
  function metaSrc() {
    return metaValue('askw-src');
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
    var docCap = metaValue('askw-doc-token');
    if (!reloadSrc || !docCap || document.hidden) return;
    fetch(SERVER + '/_mtime?src=' + encodeURIComponent(reloadSrc) + '&cap=' + encodeURIComponent(docCap), { cache: 'no-store' })
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
    if (!reloadSrc || !metaValue('askw-doc-token')) return;   // not a local /view page → no live reload
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
    setInterval(checkDoc, 3000);
  }

  // A plain HTML page that paints no background of its own used to sit on the
  // window's material, which tinted it. The window is now a raw desktop blur, so
  // such a page would put its text straight on the wallpaper. Give it the page
  // canvas a browser would. Only at the top level — inside the vault shell the
  // pane behind the reader supplies the tint — and never for Onyx's own
  // reading shells, whose translucency is deliberate.
  function guardTransparentCanvas() {
    if (window.top !== window || document.body.hasAttribute('data-askw-document-kind')) return;
    var clear = function (el) {
      var style = getComputedStyle(el);
      return style.backgroundImage === 'none' && /^(transparent|rgba\([^)]*,\s*0\))$/.test(style.backgroundColor);
    };
    if (clear(document.documentElement) && clear(document.body)) {
      document.documentElement.style.backgroundColor = 'Canvas';
    }
  }

  function initPosition() {
    var source = documentSource();
    if (!source) return;
    fetch(SERVER + '/api/document?source=' + encodeURIComponent(source)).then(function (r) { return r.json(); }).then(function (d) {
      // A #fragment (a guide's "#predict" link) is where the reader asked to
      // land; the remembered scroll position must not override it.
      if (location.hash) return;
      if (d.document && d.document.scroll_y > 0 && !sessionStorage.getItem('askw:reload')) {
        requestAnimationFrame(function () { window.scrollTo(0, d.document.scroll_y); });
      }
    }).catch(function () {});
    var timer = null;
    window.addEventListener('scroll', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        fetch(SERVER + '/api/position', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: TOKEN, source: source, scroll_y: window.scrollY })
        }).catch(function () {});
      }, 700);
    }, { passive: true });
  }

  function initAutoSelection() {
    if (new URLSearchParams(location.search).get('history')) return;
    if (!metaValue('askw-auto-selection')) return;
    var target = document.getElementById('askw-quick-selection');
    if (!target) return;
    var range = document.createRange(); range.selectNodeContents(target);
    var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    sel = captureFromSelection();
    if (sel) setTimeout(function () { showMenu(Math.max(20, window.innerWidth / 2 - 90), Math.max(80, target.getBoundingClientRect().bottom + 8)); }, 150);
  }

  function initHistoryReplay() {
    var params = new URLSearchParams(location.search);
    var id = params.get('history');
    if (!id) return;
    var mode = params.get('history_action') || 'continue';
    fetch(SERVER + '/api/conversations/' + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok || !d.conversation) throw new Error((d && d.error) || 'Saved answer not found.');
        if (mode === 'rerun') askAgainHistory(d.conversation);
        else restoreHistory(d.conversation, mode === 'edited' ? 'edited' : 'continue');
      })
      .catch(function (e) { toast(e.message || 'Could not restore saved answer.'); });
  }

  // ============================================================ global events
  function wire() {
    var debounce = null;
    document.addEventListener('mouseup', function (e) {
      if (isOurs(e.target)) return;
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(function () {
        var captured = captureFromSelection();
        if (captured) showTrigger(captured);
        else hideTrigger();
      }, 120);
    });

    document.addEventListener('contextmenu', function (e) {
      if (isOurs(e.target)) return;              // allow native menu inside our UI
      var captured = captureFromSelection();
      if (!captured) { hideMenu(); hideTrigger(); return; } // no selection -> native menu
      sel = captured;
      e.preventDefault();
      showMenu(e.clientX, e.clientY);
    });

    document.addEventListener('mousedown', function (e) {
      if (isOurs(e.target)) return;
      hideMenu();
      hideTrigger();
      closePicker();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Alt') { setProviderLabel(true); return; }
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'a') {
        var captured = captureFromSelection();
        if (captured) {
          e.preventDefault();
          sel = captured;
          showMenu(captured.rect.left + captured.rect.width / 2 - 95, captured.rect.bottom + 8);
        }
        return;
      }
      if (e.key !== 'Escape') return;
      if (menuEl.style.display === 'block') { hideMenu(); }
      else if (triggerEl.style.display === 'flex') { hideTrigger(); }
      else if (pickerEl.classList.contains('open')) { closePicker(); pillEl.focus(); }
      else if (panelEl.classList.contains('open')) { closePanel(); }
    });
    document.addEventListener('keyup', function (e) {
      if (e.key === 'Alt') { setProviderLabel(false); return; }
      if (e.shiftKey || e.key === 'Shift') {
        var captured = captureFromSelection();
        if (captured) showTrigger(captured);
      }
    });
    document.addEventListener('selectionchange', function () {
      var selection = window.getSelection();
      if (!selection || selection.isCollapsed) hideTrigger();
    });
    window.addEventListener('blur', function () { setProviderLabel(false); hideTrigger(); });
    window.addEventListener('scroll', hideTrigger, { passive: true });

    window.addEventListener('resize', function () {
      hideTrigger();
      if (panelEl.classList.contains('open') && !userPinned) positionPanel();
    });
  }

  // ============================================================ boot
  function boot() {
    injectStyle();
    guardTransparentCanvas();
    applyAppearance('system');
    if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      if (appearanceTheme === 'system') applyAppearance('system');
    });
    window.addEventListener('load', applyPageTone);
    build();
    wire();
    initFolder().then(initHistoryReplay);
    initLiveReload();
    initMarkdownTheme();
    initVaultLook();
    initPosition();
    initAutoSelection();
  }
  if (document.body) boot();
  else document.addEventListener('DOMContentLoaded', boot);
})();
