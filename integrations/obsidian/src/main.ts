import {
  MarkdownView,
  Menu,
  Modal,
  Notice,
  Plugin,
  type WorkspaceLeaf,
} from "obsidian";

import {
  captureFromEditor,
  captureFromPreview,
  documentSource,
  vaultBasePath,
  type Capture,
} from "./context";
import { AskWidgetPanel, VIEW_TYPE_ASK_WIDGET } from "./panel";
import { AskService, ServiceError } from "./service";
import { AskWidgetSettingTab, DEFAULT_SETTINGS, type AskWidgetSettings } from "./settings";
import { captureMarkdownTheme } from "./markdown-theme";
import { captureSidebarTheme } from "./sidebar-theme";

type Action = "eli5" | "prove" | "ask";

const ACTION_TITLES: Record<Action, string> = {
  eli5: "Onyx: ELI5",
  prove: "Onyx: Prove it",
  ask: "Onyx: Ask…",
};

export default class AskWidgetPlugin extends Plugin {
  settings: AskWidgetSettings = { ...DEFAULT_SETTINGS };
  service!: AskService;
  private themeTimer = 0;
  private themeSync: Promise<void> | null = null;
  private themeStopped = false;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.service = new AskService(this.settings.serviceUrl);

    this.registerView(VIEW_TYPE_ASK_WIDGET, (leaf) => new AskWidgetPanel(leaf, this));
    this.addSettingTab(new AskWidgetSettingTab(this.app, this));

    this.app.workspace.onLayoutReady(() => {
      if (this.themeStopped) return;
      this.scheduleMarkdownTheme();
      this.registerEvent(this.app.workspace.on("css-change", () => this.scheduleMarkdownTheme()));
      const observer = new MutationObserver(() => this.scheduleMarkdownTheme());
      observer.observe(document.body, { attributes: true, attributeFilter: ["class", "style"] });
      this.register(() => observer.disconnect());
      // Reconnect after service restarts; also catches appearance plugins that do
      // not emit css-change. Background failures stay quiet and retry later.
      this.registerInterval(window.setInterval(() => this.scheduleMarkdownTheme(), 30_000));
    });

    for (const action of ["eli5", "prove", "ask"] as Action[]) {
      this.addCommand({
        id: action,
        name: ACTION_TITLES[action],
        callback: () => void this.start(action),
      });
    }

    this.registerEvent(
      this.app.workspace.on("editor-menu", (menu, editor, view) => {
        if (!editor.getSelection().trim()) return;
        for (const action of ["eli5", "prove", "ask"] as Action[]) {
          menu.addItem((item) =>
            item
              .setTitle(ACTION_TITLES[action])
              .setIcon("message-circle-question")
              .onClick(() => {
                const capture = captureFromEditor(editor, view instanceof MarkdownView ? view : null);
                void this.start(action, capture);
              }),
          );
        }
      }),
    );

    // Reading view has no editor menu, so the same three items are offered on a
    // right-click over a selection in the rendered pane.
    this.registerDomEvent(document, "contextmenu", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.closest(".markdown-reading-view")) return;
      const capture = captureFromPreview(this.app);
      if (!capture) return;
      const menu = new Menu();
      for (const action of ["eli5", "prove", "ask"] as Action[]) {
        menu.addItem((item) =>
          item
            .setTitle(ACTION_TITLES[action])
            .setIcon("message-circle-question")
            .onClick(() => void this.start(action, capture)),
        );
      }
      menu.showAtMouseEvent(event);
    });
  }

  onunload(): void {
    this.themeStopped = true;
    window.clearTimeout(this.themeTimer);
    // Leaves are left in place; Obsidian detaches views of an unloaded plugin.
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, (await this.loadData()) as Partial<AskWidgetSettings>);
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
    this.service.setBaseUrl(this.settings.serviceUrl);
    this.scheduleMarkdownTheme();
  }

  private scheduleMarkdownTheme(): void {
    window.clearTimeout(this.themeTimer);
    if (this.themeStopped) return;
    this.themeTimer = window.setTimeout(() => {
      if (this.themeSync) { this.scheduleMarkdownTheme(); return; }
      void this.syncMarkdownTheme().catch(() => { /* Reconnect on the next sync. */ });
    }, 350);
  }

  async syncMarkdownTheme(): Promise<void> {
    if (this.themeStopped) return;
    if (this.themeSync) return this.themeSync;
    const root = this.vaultPath();
    if (!root) throw new Error("Markdown appearance sync requires a local vault.");
    this.themeSync = (async () => {
      const snapshot = await captureMarkdownTheme(this.app);
      // The file explorer's look rides the same triggers; an Onyx too old for it just skips it.
      const sidebar = captureSidebarTheme(this.app);
      if (this.themeStopped) return;
      await this.service.syncMarkdownTheme(root, snapshot);
      await this.service.syncSidebarTheme(root, sidebar).catch(() => { /* Older Onyx: no sidebar route. */ });
    })();
    try {
      await this.themeSync;
    } finally {
      this.themeSync = null;
    }
  }

  vaultPath(): string | null {
    return vaultBasePath(this.app);
  }

  contextFolder(): string | null {
    return this.settings.contextFolder || this.vaultPath();
  }

  private capture(): Capture | null {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (view && view.getMode() === "source") {
      const capture = captureFromEditor(view.editor, view);
      if (capture) return capture;
    }
    return captureFromPreview(this.app);
  }

  /** Capture the selection, ask for a question if needed, then stream. */
  private async start(action: Action, preCaptured?: Capture | null): Promise<void> {
    const capture = preCaptured ?? this.capture();
    if (!capture) {
      new Notice("Select a passage first.");
      return;
    }
    if (capture.truncated) {
      new Notice(`Selection trimmed to the first 4000 characters.`);
    }
    const source = documentSource(this.app, capture.file);
    if (!source) {
      new Notice("Open a note stored on disk to ask about it.");
      return;
    }
    let question = "";
    if (action === "ask") {
      question = await this.promptForQuestion(capture.selection);
      if (!question) return;
    }
    const folder = this.contextFolder();
    if (!folder) {
      new Notice("Set a context folder in Onyx settings.");
      return;
    }
    try {
      await this.service.ensureSession();
    } catch (error) {
      this.reportServiceError(error);
      return;
    }
    const panel = await this.revealPanel();
    await panel.run({
      action,
      selection: capture.selection,
      context: capture.context,
      question,
      documentSource: source,
      documentTitle: capture.file?.basename ?? "Note",
      notePath: capture.file?.path ?? null,
    });
  }

  private reportServiceError(error: unknown): void {
    if (error instanceof ServiceError && error.kind === "offline") {
      // ensureSession already tried to start the background service.
      const message = this.service.hasDaemon()
        ? "The Onyx background service did not start."
        : "Onyx isn't running. Install the background service to skip this.";
      const notice = new Notice(message, 8000);
      const button = notice.noticeEl.createEl("button", { text: "Open the app" });
      button.addEventListener("click", async () => {
        this.service.openInApp();
        const ready = await this.service.waitForService();
        new Notice(ready ? "Onyx is running. Try again." : "Onyx did not start.");
      });
      return;
    }
    if (error instanceof ServiceError && error.kind === "incompatible") {
      new Notice("Update Onyx: this version has no /api/session.");
      return;
    }
    new Notice(error instanceof Error ? error.message : String(error));
  }

  private async revealPanel(): Promise<AskWidgetPanel> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE_ASK_WIDGET);
    let leaf: WorkspaceLeaf | null = existing[0] ?? null;
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? this.app.workspace.getLeaf("split");
      await leaf.setViewState({ type: VIEW_TYPE_ASK_WIDGET, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    return leaf.view as AskWidgetPanel;
  }

  private promptForQuestion(selection: string): Promise<string> {
    return new Promise((resolve) => {
      const modal = new QuestionModal(this.app, selection, resolve);
      modal.open();
    });
  }
}

class QuestionModal extends Modal {
  private value = "";
  private settled = false;

  constructor(
    app: AskWidgetPlugin["app"],
    private selection: string,
    private done: (question: string) => void,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.addClass("ask-widget-modal");
    contentEl.createEl("h3", { text: "Ask about this passage" });
    contentEl.createEl("blockquote", { text: this.selection.slice(0, 300) });
    const input = contentEl.createEl("input", {
      type: "text",
      attr: { placeholder: "What do you want to know?" },
    });
    input.addEventListener("input", () => {
      this.value = input.value;
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        this.submit();
      }
    });
    const row = contentEl.createDiv({ cls: "askw-modal-actions" });
    row.createEl("button", { text: "Cancel" }).addEventListener("click", () => this.close());
    const ask = row.createEl("button", { text: "Ask", cls: "mod-cta" });
    ask.addEventListener("click", () => this.submit());
    window.setTimeout(() => input.focus(), 0);
  }

  private submit(): void {
    const question = this.value.trim();
    if (!question) return;
    this.settled = true;
    this.done(question);
    this.close();
  }

  onClose(): void {
    this.contentEl.empty();
    if (!this.settled) this.done("");
  }
}
