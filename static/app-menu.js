/* Onyx's rendered right-click menu, shared by the app's own pages.
 *
 * WebKit's menu can't be styled — AppKit draws it, and a page only picks light
 * or dark — so, as in cxtasks (its gotcha #9) and cxnotes, Onyx draws its menus
 * itself, from the app's theme tokens: a page that loads this must carry
 * launcher_ui.theme_style. The native app suppresses WebKit's menu wherever a
 * page did not claim the event (AskWidget.swift); OnyxMenu.open is how a page
 * claims it. The look is cxtasks' Menu.tsx, so a right-click reads the same in
 * both apps.
 *
 * Option-key variants. Finder swaps "Copy" for "Copy as Pathname" while ⌥ is
 * held; an item's `alt` is the item shown in its place. Both come from the
 * caller and the menu only picks which to show; an alt shares its item's
 * `enabled`, so the two can never disagree.
 *
 * What leaving NSMenu costs, done by hand below: staying inside the window
 * (flip and clamp), keyboard navigation, and dismissal.
 *
 *   OnyxMenu.open({items, x, y, label, onSelect(id), onClose(), returnFocus})
 *     items: [{id, label, enabled?, alt?: {id, label}} | {separator: true}]
 *   OnyxMenu.toast(text, tone)   tone 'bad' for an error
 */
(function () {
  'use strict';
  if (window.OnyxMenu) return;

  var CSS = [
    // Opaque on purpose (cxtasks gotcha #22): the window behind a menu is glass,
    // and a translucent menu at full transparency is text on raw wallpaper.
    '.onyx-menu{position:fixed;z-index:2147483000;min-width:200px;max-width:calc(100vw - 12px);margin:0;padding:4px;border:1px solid var(--line);border-radius:8px;background:rgb(var(--bg-elevated));color:rgb(var(--secondary));box-shadow:0 25px 50px -12px rgb(0 0 0/.35),0 0 0 .5px rgb(0 0 0/.12);font:13px/1.35 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;-webkit-font-smoothing:antialiased;user-select:none;-webkit-user-select:none;cursor:default}',
    '.onyx-menu[hidden]{display:none}.onyx-menu:focus{outline:none}',
    '.onyx-menu button{display:block;width:100%;margin:0;padding:6px 8px;border:0;border-radius:6px;background:transparent;color:inherit;font:inherit;text-align:left;white-space:nowrap;cursor:default}',
    // Focus is the only highlight. The pointer moves focus, so the mouse and the
    // arrow keys can never light two rows at once.
    '.onyx-menu button:focus{outline:none;background:rgb(var(--accent));color:#fff}',
    '.onyx-menu button[aria-disabled=true]{color:rgb(var(--faint));opacity:.5}',
    '.onyx-menu [role=separator]{height:1px;margin:4px 0;background:var(--line)}',
    '.onyx-toast{position:fixed;left:50%;bottom:22px;z-index:2147483001;max-width:calc(100vw - 32px);padding:7px 14px;border:1px solid var(--line);border-radius:9px;background:rgb(var(--bg-elevated));color:rgb(var(--ink));box-shadow:0 12px 34px rgb(0 0 0/.24);font:12.5px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:0;transform:translate(-50%,6px);transition:opacity .15s,transform .15s;pointer-events:none}',
    '.onyx-toast.show{opacity:1;transform:translate(-50%,0)}.onyx-toast.bad{color:rgb(var(--bad))}',
    '@media(prefers-reduced-motion:reduce){.onyx-toast{transition:none}}'
  ].join('\n');
  var MARGIN = 6;   // kept between a menu and the window's edges

  var menu = null, onSelect = null, onClose = null, returnFocus = null;
  var toastEl = null, toastTimer = 0;
  // Whether ⌥ is down. Seeded by every right-click (capture phase, ahead of any
  // page handler, so an answer that takes a round trip still opens in the right
  // variant), then kept live by the key events.
  var optionHeld = false;

  function ensure() {
    if (menu) return;
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);
    menu = document.createElement('div');
    menu.className = 'onyx-menu';
    menu.setAttribute('role', 'menu');
    menu.tabIndex = -1;
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('click', function (e) {
      var item = e.target.closest('button');
      if (item && enabled(item)) pick(item, false);
    });
    menu.addEventListener('mousemove', function (e) {
      // mousemove carries the modifier too, which corrects a keyup that went to
      // another window.
      setOption(e.altKey);
      var item = e.target.closest('button');
      if (item && enabled(item)) {
        if (document.activeElement !== item) item.focus({ preventScroll: true });
      } else if (document.activeElement !== menu) {
        menu.focus({ preventScroll: true });
      }
    });
    menu.addEventListener('mouseleave', function () {
      if (isOpen()) menu.focus({ preventScroll: true });
    });
    menu.addEventListener('keydown', onKey);
    menu.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  }

  function isOpen() { return !!menu && !menu.hidden; }
  function enabled(item) { return item.getAttribute('aria-disabled') !== 'true'; }
  function items() { return Array.prototype.filter.call(menu.querySelectorAll('button'), enabled); }

  /** Show each item's ⌥ variant, or its plain form, in place — re-rendering would drop the highlight. */
  function applyVariant() {
    menu.querySelectorAll('button[data-alt-action]').forEach(function (item) {
      item.textContent = optionHeld ? item.dataset.altLabel : item.dataset.label;
      // The click and Return paths read data-action, so this is what makes a
      // pick land on the variant actually showing.
      item.dataset.action = optionHeld ? item.dataset.altAction : item.dataset.primaryAction;
    });
  }

  function setOption(held) {
    if (held === optionHeld) return;
    optionHeld = held;
    if (isOpen()) applyVariant();
  }

  function open(opts) {
    ensure();
    close(false);
    onSelect = opts.onSelect || null;
    onClose = opts.onClose || null;
    returnFocus = opts.returnFocus || null;
    menu.setAttribute('aria-label', opts.label || 'Actions');
    menu.textContent = '';
    (opts.items || []).forEach(function (entry) {
      if (entry.separator) {
        var rule = document.createElement('div');
        rule.setAttribute('role', 'separator');
        menu.appendChild(rule);
        return;
      }
      var item = document.createElement('button');
      item.type = 'button';
      item.tabIndex = -1;
      item.setAttribute('role', 'menuitem');
      // aria-disabled, not `disabled`: a disabled control swallows mouse events,
      // and the row under the pointer has to take the highlight off the others.
      if (entry.enabled === false) item.setAttribute('aria-disabled', 'true');
      item.dataset.label = entry.label;
      item.dataset.action = item.dataset.primaryAction = entry.id;
      if (entry.alt) {
        item.dataset.altAction = entry.alt.id;
        item.dataset.altLabel = entry.alt.label;
      }
      item.textContent = entry.label;
      menu.appendChild(item);
    });
    menu.style.minWidth = '';
    menu.style.left = '0px';
    menu.style.top = '0px';
    menu.hidden = false;
    // Size for the WIDER variant, so holding ⌥ neither jiggles the menu nor
    // pushes it past the edge it was clamped to.
    var held = optionHeld, widest = 0;
    [false, true].forEach(function (variant) {
      optionHeld = variant;
      applyVariant();
      widest = Math.max(widest, menu.getBoundingClientRect().width);
    });
    optionHeld = held;
    applyVariant();
    menu.style.minWidth = Math.ceil(widest) + 'px';
    place(opts.x, opts.y);
    // Focus the menu, not its first item: a macOS menu opens with nothing
    // highlighted. The container still needs focus for the arrow keys.
    menu.focus({ preventScroll: true });
  }

  function place(x, y) {
    var box = menu.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight;
    // Past the right or bottom edge a menu opens to the other side of the
    // pointer, as AppKit's does, and only then clamps.
    var left = x + box.width + MARGIN > vw ? x - box.width : x;
    var top = y + box.height + MARGIN > vh ? y - box.height : y;
    menu.style.left = Math.round(Math.max(MARGIN, Math.min(left, vw - box.width - MARGIN))) + 'px';
    menu.style.top = Math.round(Math.max(MARGIN, Math.min(top, vh - box.height - MARGIN))) + 'px';
  }

  function close(restoreFocus) {
    if (!isOpen()) return;
    var back = returnFocus, done = onClose;
    if (menu.contains(document.activeElement)) menu.blur();
    menu.hidden = true;
    menu.textContent = '';
    onSelect = onClose = returnFocus = null;
    if (restoreFocus && back && back.isConnected) back.focus({ preventScroll: true });
    if (done) done();
  }

  function pick(item, byKeyboard) {
    var id = item.dataset.action, chosen = onSelect;
    close(byKeyboard);
    if (chosen) chosen(id);
  }

  function onKey(e) {
    var list = items(), at = list.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!list.length) return;
      var step = e.key === 'ArrowDown' ? 1 : -1;
      list[at < 0 ? (step > 0 ? 0 : list.length - 1) : (at + step + list.length) % list.length].focus();
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      if (list.length) list[e.key === 'Home' ? 0 : list.length - 1].focus();
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === 'Enter' || e.key === ' ') {
      // Activate here rather than through the button: its own Return rides on
      // keypress, and Chromium sends none while ⌥ is down (cxnotes gotcha #22),
      // so Return on a highlighted "Copy Path" would do nothing. Cancelling also
      // keeps the plain case to one fire.
      e.preventDefault();
      if (at >= 0) pick(list[at], true);
    }
  }

  function toast(text, tone) {
    ensure();
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'onyx-toast';
      toastEl.setAttribute('role', 'status');
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = text;
    toastEl.classList.toggle('bad', tone === 'bad');
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, tone === 'bad' ? 4000 : 1800);
  }

  document.addEventListener('contextmenu', function (e) { optionHeld = e.altKey; }, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Alt') setOption(true); }, true);
  document.addEventListener('keyup', function (e) { if (e.key === 'Alt') setOption(false); }, true);
  // Dismissal. mousedown, not click, so the menu is gone before the row under
  // it can take the same gesture; a click into the reader frame never reaches
  // this document, but it takes focus from the window, which blur catches.
  document.addEventListener('mousedown', function (e) {
    if (isOpen() && !menu.contains(e.target)) close(false);
  }, true);
  document.addEventListener('scroll', function (e) {
    if (isOpen() && !menu.contains(e.target)) close(false);
  }, true);
  window.addEventListener('blur', function () { close(false); });
  window.addEventListener('resize', function () { close(false); });

  window.OnyxMenu = { open: open, close: function () { close(false); }, isOpen: isOpen, toast: toast };
})();
