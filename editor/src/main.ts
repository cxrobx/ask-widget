// Onyx's note editor: ⌘E turns a Markdown page in the reader into its own source, drawn as Live Preview (preview.ts),
// and ⌘E again turns it back. ask.js loads this bundle on the first ⌘E and drives it through `window.OnyxEditor`.
//
// It saves as Obsidian does, by itself, a moment after typing stops, and at once on ⌘S, on leaving the editor, when
// focus leaves the page, and as the page goes away. Each save names the version of the file it was written against;
// the server refuses one whose file changed on disk meanwhile (another editor, an agent, a sync), and the editor asks
// which to keep instead of overwriting either.
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { ensureSyntaxTree, Language, LanguageSupport, syntaxTree } from "@codemirror/language";
import { commonmarkLanguage, markdownKeymap } from "@codemirror/lang-markdown";
import { yamlFrontmatter } from "@codemirror/lang-yaml";
import { EditorSelection, EditorState, Prec, StateCommand } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { parser as commonmark, Table } from "@lezer/markdown";

import { lineChanges } from "./diff";
import { livePreview, wikilinks } from "./preview";
import { CSS } from "./style";

/**
 * Where the top of the window is in a note, in terms the reading page and the editor share: the note's top-level blocks
 * (a heading, a paragraph, a whole list), which the reader's renderer and this parser split alike, both being
 * CommonMark. `index` of `count` is the block at the top; `fraction` how far down it the window's top edge sits, or,
 * when the block starts below that edge, 0 with `offset` its distance from it.
 */
export interface Landing { index: number; count: number; fraction: number; offset: number }

export interface OpenOptions {
  /** The reading page's column (`<main>`): the editor takes its place there, and the rendered note is hidden. */
  container: HTMLElement;
  server: string;
  token: string;
  src: string;
  cap: string;
  landing: Landing | null;
  toast(message: string): void;
  /** The editor has closed. `changed` says the file differs from what the page last rendered. */
  onExit(result: { changed: boolean; sig: string; landing: Landing | null }): void;
}

export interface Session {
  exit(): Promise<void>;
  /** The file's signature from the page's live-reload poll. */
  poll(sig: string): void;
}

// CommonMark with tables, as the reader renders (viewer.py), plus wikilinks. The Language is built here rather than
// through `markdown()` so the HTML and JavaScript grammars that bundles for inline HTML stay out of this file: the
// reader escapes raw HTML, so there is nothing for them to highlight. It shares `commonmarkLanguage`'s data, which is
// what the Markdown keymap checks, so Enter still continues a list and Backspace still lifts one.
const markdownSupport = new LanguageSupport(
  new Language(commonmarkLanguage.data, commonmark.configure([Table, wikilinks]), [], "markdown"),
  [Prec.high(keymap.of(markdownKeymap))],
);

// ⌘B and ⌘I, as Obsidian binds them: wrap the selection, or unwrap it when it is already wrapped.
function toggleWrap(mark: string): StateCommand {
  return ({ state, dispatch }) => {
    const n = mark.length;
    dispatch(state.update(state.changeByRange((range) => {
      const before = state.sliceDoc(range.from - n, range.from), after = state.sliceDoc(range.to, range.to + n);
      if (before === mark && after === mark) {
        return {
          changes: [{ from: range.from - n, to: range.from }, { from: range.to, to: range.to + n }],
          range: EditorSelection.range(range.from - n, range.to - n),
        };
      }
      return {
        changes: [{ from: range.from, insert: mark }, { from: range.to, insert: mark }],
        range: EditorSelection.range(range.from + n, range.to + n),
      };
    }), { userEvent: "input", scrollIntoView: true }));
    return true;
  };
}

// The note's top-level Markdown blocks, as the reader renders each one as a child of its `<main>`. A link reference
// definition renders nothing there, and the frontmatter is the Properties box, so neither is counted.
const BLOCK = /^(?:(?:ATX|Setext)Heading\d|Paragraph|BulletList|OrderedList|Blockquote|FencedCode|CodeBlock|HorizontalRule|Table|HTMLBlock|CommentBlock|ProcessingInstructionBlock)$/;
function sourceBlocks(state: EditorState): { from: number; to: number }[] {
  const tree = ensureSyntaxTree(state, state.doc.length, 250) ?? syntaxTree(state), out: { from: number; to: number }[] = [];
  tree.iterate({
    enter(n) {
      if (!BLOCK.test(n.name) || n.node.parent?.name !== "Document") return;
      out.push({ from: n.from, to: n.to });
      return false;
    },
  });
  return out;
}
// Should the two ever split a note differently, the block at the same share of the way through.
function scaleIndex(index: number, count: number, total: number): number {
  if (!total) return -1;
  if (count === total || count < 2) return Math.min(index, total - 1);
  return Math.min(total - 1, Math.round(index * (total - 1) / (count - 1)));
}

let styled = false;
function injectStyle() {
  if (styled) return;
  styled = true;
  const style = document.createElement("style");
  style.id = "askw-editor-style";
  style.textContent = CSS;
  document.head.appendChild(style);
}

// Under this many bytes a save goes out with `keepalive`, so one sent as the page goes away (a sidebar click, a closed
// tab) still lands; browsers cap a keepalive body at 64 KiB.
const KEEPALIVE_BYTES = 60_000;
const SAVE_AFTER_MS = 700;

export async function open(options: OpenOptions): Promise<Session> {
  const { container, server, token, toast } = options;
  const query = new URLSearchParams({ src: options.src, cap: options.cap });
  const loaded = await fetch(`${server}/api/source?${query}`, { cache: "no-store" })
    .then((r) => r.json())
    .catch(() => ({ ok: false, error: "Onyx isn’t answering." }));
  if (!loaded.ok) throw new Error(loaded.error || "This note can’t be edited.");
  injectStyle();

  const edit: string = loaded.edit, openedSig: string = loaded.sig;
  let sig = openedSig;            // the version on disk the editor's text was last in step with
  let saved = loaded.text as string;
  let timer = 0, inflight: Promise<boolean> | null = null, again = false;
  let conflict: string | null = null, failed = false, closed = false;

  const host = document.createElement("div");
  host.className = "askw-ed-host askw-ed";
  container.append(host);
  const status = document.createElement("div");
  status.className = "askw-ed-status";
  status.setAttribute("role", "status");
  const bar = document.createElement("div");
  bar.className = "askw-ed-conflict";
  bar.hidden = true;
  host.append(bar);
  document.body.append(status);

  const dirty = () => view.state.doc.toString() !== saved;
  function paint() {
    const text = conflict ? "Changed on disk" : failed ? "Not saved" : inflight ? "Saving…" : dirty() ? "Edited" : "Saved";
    status.textContent = "Editing · " + text;
    status.dataset.state = conflict || failed ? "problem" : "ok";
  }

  function showConflict(disk: string) {
    conflict = disk;
    bar.hidden = false;
    bar.innerHTML = "";
    const text = document.createElement("span");
    text.textContent = "This note changed on disk while you were editing it.";
    const mine = document.createElement("button");
    mine.textContent = "Keep mine";
    mine.onclick = () => { sig = disk; conflict = null; bar.hidden = true; save(); };
    const theirs = document.createElement("button");
    theirs.textContent = "Use the one on disk";
    theirs.onclick = () => { conflict = null; bar.hidden = true; reloadFromDisk(true); };
    bar.append(text, mine, theirs);
    paint();
  }

  // One save at a time; a change made while one is out is sent right after it, against the version it returns.
  function save(): Promise<boolean> {
    clearTimeout(timer);
    if (conflict) return Promise.resolve(false);
    if (inflight) { again = true; return inflight; }
    const text = view.state.doc.toString();
    if (text === saved && !failed) return Promise.resolve(true);
    const body = JSON.stringify({ token, edit, text, base: sig });
    inflight = fetch(`${server}/api/source`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: body.length < KEEPALIVE_BYTES,
    })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (r.status === 409 && d.sig) { showConflict(d.sig); return false; }
        if (!r.ok || !d.ok) throw new Error(d.error || `Onyx answered ${r.status}.`);
        sig = d.sig;
        saved = text;
        failed = false;
        return true;
      })
      .catch((err: Error) => {
        if (!failed) toast("Couldn’t save this note — " + (err.message || "Onyx isn’t answering."));
        failed = true;
        return false;
      })
      .finally(() => {
        inflight = null;
        paint();
        if (again && !conflict && !closed) { again = false; save(); }
      });
    paint();
    return inflight;
  }

  // Everything typed so far on disk: waits out a save in flight, and sends what was typed while it was out. False when
  // that can't happen (a conflict, or Onyx not answering), so nothing that relies on it goes ahead.
  async function settle(): Promise<boolean> {
    for (let tries = 0; tries < 6; tries++) {
      if (inflight) { await inflight; continue; }
      if (conflict) return false;
      if (!dirty() && !failed) return true;
      if (!(await save())) return false;
    }
    return !dirty();
  }

  // Take in the file's text, changing only the lines that differ, so a cursor away from them stays put.
  async function reloadFromDisk(force: boolean) {
    const fresh = await fetch(`${server}/api/source?${query}`, { cache: "no-store" }).then((r) => r.json()).catch(() => null);
    if (!fresh || !fresh.ok || closed) return;
    if (!force && dirty()) { showConflict(fresh.sig); return; }
    const next = fresh.text as string, changes = lineChanges(view.state.doc.toString(), next);
    saved = next;
    sig = fresh.sig;
    failed = false;
    if (changes.length) view.dispatch({ changes, userEvent: "sync" });
    paint();
  }

  const view = new EditorView({
    parent: host,
    state: EditorState.create({
      doc: saved,
      extensions: [
        history(),
        EditorView.lineWrapping,
        yamlFrontmatter({ content: markdownSupport }),
        livePreview,
        keymap.of([
          { key: "Mod-s", run: () => { save(); return true; }, preventDefault: true },
          { key: "Mod-b", run: toggleWrap("**") },
          { key: "Mod-i", run: toggleWrap("*") },
          indentWithTab,
          ...defaultKeymap,
          ...historyKeymap,
        ]),
        EditorView.contentAttributes.of({ spellcheck: "true", autocorrect: "on", autocapitalize: "sentences" }),
        EditorView.updateListener.of((u) => {
          if (!u.docChanged) return;
          if (u.transactions.some((t) => t.isUserEvent("sync"))) return;
          clearTimeout(timer);
          timer = window.setTimeout(save, SAVE_AFTER_MS);
          paint();
        }),
        EditorView.domEventHandlers({
          mousedown: (event, v) => followLink(event, v),
          blur: () => { save(); return false; },
        }),
      ],
    }),
  });
  document.body.classList.add("askw-editing");

  // A link drawn as a link (its syntax hidden) follows on click, as in Obsidian; one being edited takes the cursor.
  function followLink(event: MouseEvent, v: EditorView): boolean {
    if (event.button !== 0) return false;
    const el = (event.target as Element).closest?.("[data-askw-href],[data-askw-wiki]");
    if (!el || !v.contentDOM.contains(el)) return false;
    event.preventDefault();
    const wiki = el.getAttribute("data-askw-wiki"), href = el.getAttribute("data-askw-href") || "";
    const newTab = event.metaKey || event.ctrlKey;
    if (!wiki && (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^file:/i.test(href))) {
      go(href, newTab, true);
      return true;
    }
    if (!wiki && href.startsWith("#")) return true;
    const q = new URLSearchParams({ edit, target: wiki || href, kind: wiki ? "wiki" : "md" });
    fetch(`${server}/api/source/link?${q}`, { cache: "no-store" }).then((r) => r.json()).then((d) => {
      if (d.ok && d.href) go(d.href, newTab, false);
      else toast(wiki ? `No note named “${wiki.split("#")[0]}”` : "That link doesn’t lead to a local page.");
    }).catch(() => toast("Onyx isn’t answering."));
    return true;
  }
  // Through a real link in the page, so the shell treats it as it treats the reader's own: ⌘-click for a new tab, an
  // outside address to the top window.
  async function go(href: string, newTab: boolean, outside: boolean) {
    if (!newTab && !(await settle())) return;
    const a = document.createElement("a");
    a.href = href;
    a.hidden = true;
    if (outside) { a.target = "_top"; a.rel = "noreferrer noopener"; }
    document.body.append(a);
    a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, metaKey: newTab, button: 0 }));
    a.remove();
  }

  // What the reader was showing at the top of the window, shown there again.
  if (options.landing) {
    const blocks = sourceBlocks(view.state), at = options.landing;
    const block = blocks[scaleIndex(at.index, at.count, blocks.length)];
    if (block) {
      const doc = view.state.doc, first = doc.lineAt(block.from).number, last = doc.lineAt(Math.max(block.from, block.to - 1)).number;
      const line = doc.line(Math.min(last, first + Math.floor(at.fraction * (last - first + 1))));
      view.dispatch({
        selection: { anchor: line.from },
        effects: EditorView.scrollIntoView(line.from, { y: "start", yMargin: at.fraction ? 0 : Math.max(0, at.offset) }),
      });
    }
  }
  view.focus();
  paint();

  // The page going away: whatever is unsaved goes out with keepalive.
  const flushOnHide = () => { if (dirty() && !conflict) save(); };
  window.addEventListener("pagehide", flushOnHide);
  const onVisibility = () => { if (document.hidden) flushOnHide(); };
  document.addEventListener("visibilitychange", onVisibility);

  function topLanding(): Landing | null {
    const blocks = sourceBlocks(view.state), edge = -view.documentTop;
    for (let i = 0; i < blocks.length; i++) {
      const b = blocks[i];
      const top = view.lineBlockAt(b.from).top, bottom = view.lineBlockAt(Math.max(b.from, b.to - 1)).bottom;
      if (bottom <= edge) continue;
      return top >= edge
        ? { index: i, count: blocks.length, fraction: 0, offset: top - edge }
        : { index: i, count: blocks.length, fraction: (edge - top) / Math.max(1, bottom - top), offset: 0 };
    }
    return null;
  }

  return {
    async exit() {
      if (closed) return;
      if (!(await settle())) {
        if (!conflict) toast("This note isn’t saved yet, so it stays open for editing.");
        return;
      }
      closed = true;
      const landing = topLanding();
      window.removeEventListener("pagehide", flushOnHide);
      document.removeEventListener("visibilitychange", onVisibility);
      view.destroy();
      host.remove();
      status.remove();
      document.body.classList.remove("askw-editing");
      options.onExit({ changed: sig !== openedSig, sig, landing });
    },
    poll(disk: string) {
      if (closed || inflight || conflict || disk === sig) return;
      reloadFromDisk(false);
    },
  };
}

declare global {
  interface Window { OnyxEditor?: { open: typeof open } }
}
window.OnyxEditor = { open };
