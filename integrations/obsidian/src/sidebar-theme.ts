import { TFolder, type App } from "obsidian";

/** What Onyx's vault sidebar needs to look like this vault's file explorer (see sidebar_theme.py). */
export interface SidebarThemeSnapshot {
  mode: "light" | "dark";
  styles: Record<string, Record<string, string>>;
  folders: { name: string; color: string; guide?: string }[];
}

const TYPE = ["color", "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "line-height", "text-transform"];
const ROW = [...TYPE, "padding-top", "padding-bottom", "border-radius"];
const BORDER = ["top", "right", "bottom", "left"].flatMap((side) => ["color", "width", "style"].map((kind) => `border-${side}-${kind}`));
// Each key and property is on the server's allowlist (sidebar_theme.ELEMENTS).
const ELEMENTS: Record<string, [string, string[]]> = {
  pane: [".nav-files-container", ["color", "font-family", "font-size", "font-weight", "letter-spacing", "line-height", "background-color"]],
  folder: [".onyx-sample-folder > .nav-folder-title", ROW],
  file: [".onyx-sample-file > .nav-file-title", ROW],
  active: [".onyx-sample-active > .nav-file-title", ["color", "background-color", "font-weight", "border-radius"]],
  chevron: [".onyx-sample-folder > .nav-folder-title .collapse-icon", ["color", "opacity"]],
  guide: [".onyx-sample-folder > .nav-folder-children", ["border-left-color", "border-left-width", "border-left-style"]],
  search: [".onyx-sample-search input", ["color", "background-color", "font-family", "font-size", "border-radius", ...BORDER]],
};

// The server's own rule (markdown_theme._UNSAFE): a value that fails it would sink the whole snapshot.
const UNSAFE = /[;{}<>\\\x00-\x1f]|\/\*|\*\/|(?:url|var|env|attr|expression)\s*\(/i;
export function safeValue(value: string): string | null {
  const trimmed = value.trim();
  return trimmed && trimmed.length <= 512 && !UNSAFE.test(trimmed) ? trimmed : null;
}

const TRANSPARENT = /^(transparent|rgba\([^)]*,\s*0\))$/;

/** Top-level folders as the explorer lists them, so a positional rainbow snippet colours our copy the same way. */
function topFolders(app: App): string[] {
  const live = Array.from(document.querySelectorAll<HTMLElement>(
    ".nav-files-container .nav-folder.mod-root > .nav-folder-children > .nav-folder > .nav-folder-title",
  )).map((title) => title.dataset.path ?? "").filter(Boolean);
  if (live.length) return live;
  return app.vault.getRoot().children.filter((child): child is TFolder => child instanceof TFolder)
    .map((folder) => folder.path).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

function row(parent: HTMLElement, kind: "folder" | "file", path: string, extra = ""): HTMLElement {
  const item = parent.createDiv({ cls: `tree-item nav-${kind} ${extra}`.trim() });
  const self = item.createDiv({ cls: `tree-item-self is-clickable nav-${kind}-title${kind === "folder" ? " mod-collapsible" : ""}` });
  self.dataset.path = path;
  // createSvg adds its cls as class tokens: a space-separated string throws, so it takes a list.
  if (kind === "folder") self.createDiv({ cls: "tree-item-icon collapse-icon" }).createSvg("svg", { cls: ["svg-icon", "right-triangle"] });
  self.createDiv({ cls: `tree-item-inner nav-${kind}-title-content`, text: path.split("/").pop() ?? path });
  return item;
}

/** Measure the file explorer after the vault's theme and snippets resolve. Reads styles only — never note contents. */
export function captureSidebarTheme(app: App): SidebarThemeSnapshot {
  const host = document.createElement("div");
  host.className = "workspace-leaf-content";
  host.dataset.type = "file-explorer";
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;left:-10000px;top:0;width:320px;visibility:hidden;pointer-events:none";
  const container = host.createDiv({ cls: "nav-files-container" });
  const root = container.createDiv().createDiv({ cls: "tree-item nav-folder mod-root" });
  root.createDiv({ cls: "tree-item-self nav-folder-title" }).dataset.path = "/";
  const top = root.createDiv({ cls: "tree-item-children nav-folder-children" });
  const names = topFolders(app);
  const folderItems = (names.length ? names : ["Onyx sample"]).map((name, index) => {
    const item = row(top, "folder", name, index === 0 ? "onyx-sample-folder" : "");
    row(item.createDiv({ cls: "tree-item-children nav-folder-children" }), "file", `${name}/Onyx sample.md`);
    return item;
  });
  row(top, "file", "Onyx sample.md", "onyx-sample-file");
  row(top, "file", "Onyx active.md", "onyx-sample-active").querySelector(".nav-file-title")?.addClass("is-active");
  host.createDiv({ cls: "search-input-container onyx-sample-search" }).createEl("input", { type: "search" });
  // Inside the left sidebar, so sidebar-scoped theme variables apply as they do to the real explorer.
  (document.querySelector(".workspace-split.mod-left-split") ?? document.body).appendChild(host);
  try {
    const styles: SidebarThemeSnapshot["styles"] = {};
    for (const [key, [selector, properties]] of Object.entries(ELEMENTS)) {
      const element = host.querySelector<HTMLElement>(selector);
      if (!element) continue;
      const computed = getComputedStyle(element);
      const declarations: Record<string, string> = {};
      for (const prop of properties) {
        const value = safeValue(computed.getPropertyValue(prop));
        if (value) declarations[prop] = value;
      }
      styles[key] = declarations;
    }
    // Themes often leave the explorer transparent over the sidebar surface: use the first opaque layer behind it.
    if (!styles.pane["background-color"] || TRANSPARENT.test(styles.pane["background-color"])) {
      let layer: HTMLElement | null = container;
      let found = "";
      while (layer && !found) {
        const background = getComputedStyle(layer).backgroundColor;
        if (background && !TRANSPARENT.test(background)) found = background;
        layer = layer.parentElement;
      }
      if (found && safeValue(found)) styles.pane["background-color"] = found;
      else delete styles.pane["background-color"];
    }
    // Hover lives in the theme's variables, not on any resting row: resolve them on a probe inside the copy.
    const probe = container.createDiv();
    probe.style.cssText = "background-color:var(--nav-item-background-hover);color:var(--nav-item-color-hover)";
    const hover = getComputedStyle(probe);
    styles.hover = {};
    if (!TRANSPARENT.test(hover.backgroundColor) && safeValue(hover.backgroundColor)) styles.hover["background-color"] = hover.backgroundColor;
    if (safeValue(hover.color)) styles.hover.color = hover.color;
    if (!styles.file?.color && styles.pane.color) (styles.file ??= {}).color = styles.pane.color;
    const folders = names.map((name, index) => {
      const title = getComputedStyle(folderItems[index].querySelector<HTMLElement>(".nav-folder-title")!).color;
      const guide = getComputedStyle(folderItems[index].querySelector<HTMLElement>(".nav-folder-children")!);
      const tint: SidebarThemeSnapshot["folders"][number] = { name, color: safeValue(title) ?? "" };
      if (guide.borderLeftStyle !== "none" && parseFloat(guide.borderLeftWidth) > 0 && safeValue(guide.borderLeftColor)) {
        tint.guide = guide.borderLeftColor;
      }
      return tint;
    }).filter((tint) => tint.color && tint.name.length <= 255 && !/[\x00-\x1f]/.test(tint.name)).slice(0, 256);
    return { mode: document.body.classList.contains("theme-dark") ? "dark" : "light", styles, folders };
  } finally {
    host.remove();
  }
}
