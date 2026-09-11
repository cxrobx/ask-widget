/**
 * Capturing what the reader has selected, in either of Obsidian's two panes.
 *
 * Source mode and Live Preview expose a CodeMirror `Editor`, which gives exact
 * selection offsets and the surrounding paragraph by line. Reading view has no
 * editor, so the DOM selection is used and the paragraph comes from the nearest
 * block element, matching what the browser widget does on a /view page.
 */

import { FileSystemAdapter, MarkdownView, type App, type Editor, type TFile } from "obsidian";

export const MAX_SELECTION = 4000;
export const MAX_CONTEXT = 600;

export interface Capture {
  selection: string;
  context: string;
  file: TFile | null;
  truncated: boolean;
}

const BLOCK_SELECTOR =
  "p,li,td,th,blockquote,pre,section,article,figure,h1,h2,h3,h4,h5,h6,div";

function clean(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function finish(selection: string, context: string, file: TFile | null): Capture | null {
  const trimmed = selection.trim();
  if (!trimmed) return null;
  return {
    selection: trimmed.slice(0, MAX_SELECTION),
    context: clean(context).slice(0, MAX_CONTEXT),
    file,
    truncated: trimmed.length > MAX_SELECTION,
  };
}

/** Selection plus its paragraph, read from the editor's own line buffer. */
export function captureFromEditor(editor: Editor, view: MarkdownView | null): Capture | null {
  const selection = editor.getSelection();
  if (!selection.trim()) return null;
  const lastLine = editor.lastLine();
  let start = editor.getCursor("from").line;
  let end = editor.getCursor("to").line;
  while (start > 0 && editor.getLine(start - 1).trim() !== "") start -= 1;
  while (end < lastLine && editor.getLine(end + 1).trim() !== "") end += 1;
  const lines: string[] = [];
  for (let line = start; line <= end; line += 1) lines.push(editor.getLine(line));
  return finish(selection, lines.join(" "), view?.file ?? null);
}

/** Reading-view fallback: the DOM selection, ignoring text inside our panel. */
export function captureFromPreview(app: App): Capture | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const text = selection.toString();
  if (!text.trim()) return null;
  const range = selection.getRangeAt(0);
  let node: Node | null = range.commonAncestorContainer;
  if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
  const element = node instanceof Element ? node : null;
  if (element?.closest(".ask-widget-panel")) return null;
  const block = element?.closest(BLOCK_SELECTOR) ?? null;
  const context = block ? block.textContent ?? "" : "";
  const view = app.workspace.getActiveViewOfType(MarkdownView);
  return finish(text, context, view?.file ?? null);
}

/** Absolute path of the vault on disk, or null on a non-filesystem vault. */
export function vaultBasePath(app: App): string | null {
  const adapter = app.vault.adapter;
  return adapter instanceof FileSystemAdapter ? adapter.getBasePath() : null;
}

/**
 * The absolute path Onyx files this note's answers under. It must match
 * what `/view?src=` stores, so Library and History key on the same document.
 */
export function documentSource(app: App, file: TFile | null): string | null {
  if (!file) return null;
  const base = vaultBasePath(app);
  return base ? `${base}/${file.path}` : null;
}
