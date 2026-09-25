// Live Preview, as Obsidian draws it: the note is its own Markdown source, styled as it will read, with the syntax
// (`#`, `**`, `[`…`](url)`, `>`) hidden everywhere except where the cursor is, so what is being edited shows as typed.
//
// It follows the reader's renderer (viewer.py: CommonMark, tables, wikilinks) rather than Obsidian's full dialect, so
// ⌘E never shows a construct styled one way here and left as plain text on the page it toggles back to.
import { syntaxTree } from "@codemirror/language";
import { Range } from "@codemirror/state";
import { Decoration, DecorationSet, EditorView, ViewPlugin, ViewUpdate, WidgetType } from "@codemirror/view";
import type { SyntaxNode } from "@lezer/common";
import type { MarkdownConfig } from "@lezer/markdown";

// `[[Note]]`, `[[Note|Alias]]`, `[[Note#Heading]]` and `![[embed]]`, with the reader's limits: one line, not empty, no
// nested `[[`. Parsed before Link, as the reader does, so a wikilink is never taken for a reference link.
export const wikilinks: MarkdownConfig = {
  defineNodes: ["WikiLink", "WikiEmbed", "WikiLinkMark", "WikiLinkTarget", "WikiLinkAlias"],
  parseInline: [{
    name: "WikiLink",
    before: "Link",
    parse(cx, next, pos) {
      const embed = next === 33 && cx.char(pos + 1) === 91 && cx.char(pos + 2) === 91;
      if (!embed && !(next === 91 && cx.char(pos + 1) === 91)) return -1;
      const open = pos + (embed ? 3 : 2);
      const close = cx.slice(open, cx.end).indexOf("]]");
      if (close < 0) return -1;
      const inner = cx.slice(open, open + close);
      if (!inner.trim() || inner.includes("\n") || inner.includes("[[")) return -1;
      const end = open + close + 2, pipe = inner.indexOf("|");
      const children = [cx.elt("WikiLinkMark", pos, open)];
      if (pipe >= 0) {
        children.push(
          cx.elt("WikiLinkTarget", open, open + pipe),
          cx.elt("WikiLinkMark", open + pipe, open + pipe + 1),
          cx.elt("WikiLinkAlias", open + pipe + 1, open + close),
        );
      } else {
        children.push(cx.elt("WikiLinkTarget", open, open + close));
      }
      children.push(cx.elt("WikiLinkMark", open + close, end));
      return cx.addElement(cx.elt(embed ? "WikiEmbed" : "WikiLink", pos, end, children));
    },
  }],
};

class Bullet extends WidgetType {
  eq() { return true; }
  toDOM() {
    const el = document.createElement("span");
    el.className = "askw-ed-bullet";
    el.textContent = "•";
    return el;
  }
}

class Rule extends WidgetType {
  eq() { return true; }
  toDOM() {
    const el = document.createElement("span");
    el.className = "askw-ed-hr";
    return el;
  }
}

const hide = Decoration.replace({});
const dim = Decoration.mark({ class: "askw-ed-mark" });
const bullet = Decoration.replace({ widget: new Bullet() });
const rule = Decoration.replace({ widget: new Rule() });
const cls = (name: string) => Decoration.mark({ class: name });
const INLINE: Record<string, [Decoration, string]> = {
  Emphasis: [cls("askw-ed-em"), "EmphasisMark"],
  StrongEmphasis: [cls("askw-ed-strong"), "EmphasisMark"],
  InlineCode: [cls("askw-ed-code"), "CodeMark"],
};

function build(view: EditorView): DecorationSet {
  const { state } = view, doc = state.doc, out: Range<Decoration>[] = [];
  const ranges = state.selection.ranges;
  // Markup shows while the selection touches its construct (inclusive, so a cursor just after `**bold**` still sees
  // the stars it would delete); a line's own markers (`#`, `>`) show while the selection is anywhere on that line.
  const touches = (from: number, to: number) => ranges.some((r) => r.from <= to && r.to >= from);
  const onLine = (from: number, to: number) => touches(doc.lineAt(from).from, doc.lineAt(to).to);
  // A block's last line: one that ends in its newline (the frontmatter does) ends on the line before `to`.
  const lastLine = (from: number, to: number) => doc.lineAt(Math.max(from, to - 1)).number;
  const lines = (from: number, to: number, name: string) => {
    for (let n = doc.lineAt(from).number, last = lastLine(from, to); n <= last; n++) {
      out.push(Decoration.line({ class: name }).range(doc.line(n).from));
    }
  };
  const marks = (node: SyntaxNode, name: string, shown: boolean) => {
    for (const m of node.getChildren(name)) out.push((shown ? dim : hide).range(m.from, m.to));
  };
  const seen = new Set<number>();  // a block reached from two visible ranges is decorated once

  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from, to,
      enter: (ref) => {
        const node = ref.node, name = node.name;
        const heading = /^(ATX|Setext)Heading(\d)$/.exec(name);
        if (heading) {
          if (seen.has(node.from)) return;
          seen.add(node.from);
          const level = heading[2];
          if (heading[1] === "ATX") {
            out.push(Decoration.line({ class: `askw-ed-h askw-ed-h${level}` }).range(doc.lineAt(node.from).from));
            const shown = onLine(node.from, node.to);
            for (const m of node.getChildren("HeaderMark")) {
              if (shown) { out.push(dim.range(m.from, m.to)); continue; }
              // The space after an opening `#` and before a closing one goes with it.
              let a = m.from, b = m.to;
              if (a === doc.lineAt(a).from) { while (b < node.to && doc.sliceString(b, b + 1) === " ") b++; }
              else { while (a > node.from && doc.sliceString(a - 1, a) === " ") a--; }
              out.push(hide.range(a, b));
            }
          } else {
            const underline = node.getChild("HeaderMark");
            const textEnd = underline ? doc.lineAt(underline.from).from - 1 : node.to;
            lines(node.from, Math.max(node.from, textEnd), `askw-ed-h askw-ed-h${level}`);
            if (underline) out.push(dim.range(underline.from, underline.to));
          }
          return;
        }
        if (name in INLINE) {
          const [style, mark] = INLINE[name];
          if (node.to > node.from) out.push(style.range(node.from, node.to));
          marks(node, mark, touches(node.from, node.to));
          return;
        }
        switch (name) {
          case "Link": {
            const linkMarks = node.getChildren("LinkMark");
            if (linkMarks.length < 2) return;
            const [open, close] = linkMarks, url = node.getChild("URL");
            const textFrom = open.to, textTo = close.from;
            if (textTo <= textFrom) return;  // `[](url)` has nothing to show in its place
            const shown = touches(node.from, node.to);
            const href = url ? doc.sliceString(url.from, url.to) : "";
            out.push(Decoration.mark({
              class: "askw-ed-link",
              attributes: shown || !href ? {} : { "data-askw-href": href },
            }).range(textFrom, textTo));
            if (shown) {
              out.push(dim.range(node.from, textFrom), dim.range(textTo, node.to));
            } else {
              out.push(hide.range(node.from, textFrom), hide.range(textTo, node.to));
            }
            return;  // on into the text, which may be bold or code itself
          }
          case "Autolink": {
            const url = node.getChild("URL");
            if (!url) return;
            const shown = touches(node.from, node.to);
            out.push(Decoration.mark({
              class: "askw-ed-link",
              attributes: shown ? {} : { "data-askw-href": doc.sliceString(url.from, url.to) },
            }).range(url.from, url.to));
            out.push((shown ? dim : hide).range(node.from, url.from), (shown ? dim : hide).range(url.to, node.to));
            return false;
          }
          case "WikiLink": {
            const target = node.getChild("WikiLinkTarget"), alias = node.getChild("WikiLinkAlias");
            if (!target) return;
            const shown = touches(node.from, node.to), label = alias || target;
            out.push(Decoration.mark({
              class: "askw-ed-link askw-ed-wikilink",
              attributes: shown ? {} : { "data-askw-wiki": doc.sliceString(target.from, target.to) },
            }).range(label.from, label.to));
            if (shown) {
              out.push(dim.range(node.from, label.from), dim.range(label.to, node.to));
            } else {
              out.push(hide.range(node.from, label.from), hide.range(label.to, node.to));
            }
            return false;
          }
          case "WikiEmbed":
          case "Image":
            out.push(cls("askw-ed-embed").range(node.from, node.to));
            return false;
          case "Blockquote": {
            lines(node.from, node.to, "askw-ed-quote");
            return;
          }
          case "QuoteMark": {
            if (onLine(node.from, node.to)) { out.push(dim.range(node.from, node.to)); return; }
            const after = doc.sliceString(node.to, node.to + 1) === " " ? node.to + 1 : node.to;
            out.push(hide.range(node.from, after));
            return;
          }
          case "ListMark": {
            const list = node.parent?.parent;
            if (list?.name !== "BulletList") return;
            out.push((touches(node.from, node.to) ? dim : bullet).range(node.from, node.to));
            return;
          }
          case "FencedCode":
          case "CodeBlock": {
            const first = doc.lineAt(node.from).number, last = lastLine(node.from, node.to);
            for (let n = first; n <= last; n++) {
              const edge = (n === first ? " askw-ed-pre-first" : "") + (n === last ? " askw-ed-pre-last" : "");
              out.push(Decoration.line({ class: "askw-ed-pre" + edge }).range(doc.line(n).from));
            }
            if (name === "FencedCode") {
              for (const m of node.getChildren("CodeMark")) out.push(dim.range(m.from, m.to));
              const info = node.getChild("CodeInfo");
              if (info) out.push(dim.range(info.from, info.to));
            }
            return false;
          }
          case "HorizontalRule": {
            out.push((onLine(node.from, node.to) ? dim : rule).range(node.from, node.to));
            return false;
          }
          case "Table":
            lines(node.from, node.to, "askw-ed-table");
            return false;
          case "Frontmatter":
            lines(node.from, node.to, "askw-ed-frontmatter");
            return false;
        }
      },
    });
  }
  return Decoration.set(out, true);
}

export const livePreview = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = build(view); }
  update(u: ViewUpdate) {
    if (u.docChanged || u.viewportChanged || u.selectionSet || syntaxTree(u.startState) !== syntaxTree(u.state)) {
      this.decorations = build(u.view);
    }
  }
}, { decorations: (v) => v.decorations });
