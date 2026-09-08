/**
 * The right-sidebar panel: passage, streamed answer, evidence, follow-ups.
 *
 * `MarkdownRenderer.render` needs a live `Component` for the lifetime of what it
 * renders, so each repaint gets a fresh child component: render detached, swap
 * the node in, then unload the previous child. The last one is unloaded in
 * `onClose`.
 */

import {
  Component,
  ItemView,
  MarkdownRenderer,
  Notice,
  TFile,
  setIcon,
  type WorkspaceLeaf,
} from "obsidian";

import { parseVerdict, type SseFrame } from "./sse";
import { ServiceError, type Citation } from "./service";
import type AskWidgetPlugin from "./main";

export const VIEW_TYPE_ASK_WIDGET = "ask-widget-panel";

const ACTION_LABELS: Record<string, string> = {
  eli5: "ELI5",
  prove: "Prove it",
  ask: "Question",
};

export interface PanelRequest {
  action: "eli5" | "prove" | "ask";
  selection: string;
  context: string;
  question: string;
  documentSource: string;
  documentTitle: string;
  notePath: string | null;
}

interface Turn {
  role: "user" | "assistant";
  text: string;
}

export class AskWidgetPanel extends ItemView {
  private plugin: AskWidgetPlugin;
  private request: PanelRequest | null = null;
  private controller: AbortController | null = null;
  private answer = "";
  private citations: Citation[] = [];
  private tools: string[] = [];
  private transcript: Turn[] = [];
  private parentRequestId = "";
  private startedAt = 0;
  private elapsedTimer: number | null = null;
  private renderChild: Component | null = null;
  private repaintQueued = false;

  private headerEl!: HTMLElement;
  private metaEl!: HTMLElement;
  private passageEl!: HTMLDetailsElement;
  private verdictEl!: HTMLElement;
  private toolsEl!: HTMLElement;
  private answerEl!: HTMLElement;
  private citationsEl!: HTMLElement;
  private actionsEl!: HTMLElement;
  private followUpEl!: HTMLTextAreaElement;

  constructor(leaf: WorkspaceLeaf, plugin: AskWidgetPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return VIEW_TYPE_ASK_WIDGET;
  }

  getDisplayText(): string {
    return "Ask Widget";
  }

  getIcon(): string {
    return "message-circle-question";
  }

  async onOpen(): Promise<void> {
    const root = this.contentEl;
    root.empty();
    root.addClass("ask-widget-panel");

    const head = root.createDiv({ cls: "askw-head" });
    this.headerEl = head.createDiv({ cls: "askw-title", text: "Ask Widget" });
    this.metaEl = head.createDiv({ cls: "askw-meta", text: "Select a passage, then run an Ask Widget command." });

    this.passageEl = root.createEl("details", { cls: "askw-passage" });
    this.passageEl.createEl("summary", { text: "Passage" });
    this.verdictEl = root.createDiv({ cls: "askw-verdict" });
    this.verdictEl.hide();
    this.toolsEl = root.createDiv({ cls: "askw-tools" });
    this.answerEl = root.createDiv({ cls: "askw-answer" });
    this.citationsEl = root.createDiv({ cls: "askw-citations" });
    this.actionsEl = root.createDiv({ cls: "askw-actions" });

    const followUp = root.createDiv({ cls: "askw-followup" });
    this.followUpEl = followUp.createEl("textarea", {
      attr: { rows: "2", placeholder: "Ask a follow-up…" },
    });
    this.followUpEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        void this.sendFollowUp();
      }
    });
    followUp.createEl("button", { text: "Send" }).addEventListener("click", () => {
      void this.sendFollowUp();
    });
  }

  async onClose(): Promise<void> {
    this.stop();
    this.clearElapsed();
    this.renderChild?.unload();
    this.renderChild = null;
  }

  // MARK: - Running a request

  async run(request: PanelRequest): Promise<void> {
    this.stop();
    this.request = request;
    this.answer = "";
    this.citations = [];
    this.tools = [];
    this.transcript = [];
    this.parentRequestId = "";
    this.startRequest(request.question || ACTION_LABELS[request.action], "generated");
  }

  private async sendFollowUp(): Promise<void> {
    const question = this.followUpEl.value.trim();
    if (!question || !this.request) return;
    this.followUpEl.value = "";
    if (this.answer) {
      this.transcript.push({ role: "user", text: this.request.question || ACTION_LABELS[this.request.action] });
      this.transcript.push({ role: "assistant", text: this.answer });
    }
    this.request = { ...this.request, action: "ask", question };
    this.answer = "";
    this.citations = [];
    this.tools = [];
    this.startRequest(question, "continue");
  }

  private retry(): void {
    if (!this.request) return;
    this.answer = "";
    this.citations = [];
    this.tools = [];
    this.startRequest(this.request.question || ACTION_LABELS[this.request.action], "rerun");
  }

  private startRequest(headline: string, mode: "generated" | "continue" | "rerun"): void {
    const request = this.request;
    if (!request) return;
    this.headerEl.setText(`${ACTION_LABELS[request.action]} · ${request.documentTitle}`);
    this.passageEl.empty();
    this.passageEl.createEl("summary", { text: headline.slice(0, 120) });
    this.passageEl.createEl("blockquote", { text: request.selection });
    this.verdictEl.hide();
    this.verdictEl.empty();
    this.toolsEl.empty();
    this.citationsEl.empty();
    this.answerEl.empty();
    this.answerEl.createDiv({ cls: "askw-waiting", text: "Waiting for the model…" });
    this.startedAt = Date.now();
    this.startElapsed();
    this.renderActions(true);

    const controller = new AbortController();
    this.controller = controller;
    void this.plugin.service
      .ask(
        {
          action: request.action,
          selection: request.selection,
          context: request.context,
          question: request.question,
          folder: this.plugin.contextFolder() ?? "",
          document_source: request.documentSource,
          document_title: request.documentTitle,
          history: this.transcript.slice(-8),
          request_mode: mode,
          parent_request_id: this.parentRequestId || undefined,
        },
        { onFrame: (frame) => this.onFrame(frame), signal: controller.signal },
      )
      .catch((error: unknown) => {
        if (error instanceof ServiceError && error.kind === "aborted") return;
        this.showError(error);
      })
      .finally(() => {
        if (this.controller === controller) this.controller = null;
        this.clearElapsed();
        this.renderActions(false);
        this.repaint(true);
      });
  }

  stop(): void {
    this.controller?.abort();
    this.controller = null;
  }

  private onFrame(frame: SseFrame): void {
    const data = frame.data;
    switch (frame.event) {
      case "meta": {
        this.parentRequestId = String(data.request_id ?? "");
        const provider = String(data.provider ?? "");
        const model = String(data.model ?? "");
        this.metaEl.setText(`${provider}${model ? ` · ${model}` : ""}`);
        break;
      }
      case "token":
        this.answer += String(data.text ?? "");
        this.repaint(false);
        break;
      case "tool_status":
      case "tool_trace": {
        const tool = String(data.tool ?? data.status ?? "").trim();
        if (tool && !this.tools.includes(tool)) {
          this.tools.push(tool);
          this.renderTools();
        }
        break;
      }
      case "citations":
        this.citations = Array.isArray(data.items) ? (data.items as Citation[]) : [];
        this.renderCitations();
        break;
      case "error":
        this.showError(new Error(String(data.message ?? "Ask Widget reported an error.")));
        break;
      default:
        break;
    }
  }

  private showError(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    this.answerEl.empty();
    const box = this.answerEl.createDiv({ cls: "askw-error" });
    box.createSpan({ text: message });
    if (error instanceof ServiceError && error.kind === "offline") {
      box.createEl("button", { text: "Open the app" }).addEventListener("click", async () => {
        this.plugin.service.openInApp();
        new Notice("Starting Ask Widget…");
        const ready = await this.plugin.service.waitForService();
        new Notice(ready ? "Ask Widget is running. Try again." : "Ask Widget did not start.");
      });
    }
    if (message.includes("folder not allowed")) {
      box.createEl("button", { text: "Allow this vault" }).addEventListener("click", async () => {
        const folder = this.plugin.contextFolder();
        if (!folder) return;
        try {
          await this.plugin.service.ensureRoot(folder);
          new Notice("Folder allowed. Try again.");
        } catch (allowError) {
          new Notice(allowError instanceof Error ? allowError.message : String(allowError));
        }
      });
    }
  }

  // MARK: - Rendering

  private startElapsed(): void {
    this.clearElapsed();
    this.elapsedTimer = window.setInterval(() => {
      const seconds = (Date.now() - this.startedAt) / 1000;
      this.headerEl.dataset.elapsed = `${seconds.toFixed(0)}s`;
    }, 500);
    this.registerInterval(this.elapsedTimer);
  }

  private clearElapsed(): void {
    if (this.elapsedTimer !== null) {
      window.clearInterval(this.elapsedTimer);
      this.elapsedTimer = null;
    }
  }

  /** Throttled markdown repaint; `force` renders the final answer immediately. */
  private repaint(force: boolean): void {
    if (!force) {
      if (this.repaintQueued) return;
      this.repaintQueued = true;
      window.setTimeout(() => {
        this.repaintQueued = false;
        this.paintAnswer();
      }, 100);
      return;
    }
    this.repaintQueued = false;
    this.paintAnswer();
  }

  private paintAnswer(): void {
    if (!this.answer) return;
    const request = this.request;
    if (request?.action === "prove") {
      const verdict = parseVerdict(this.answer);
      if (verdict) {
        this.verdictEl.empty();
        this.verdictEl.createSpan({
          cls: `askw-badge askw-badge-${verdict.toLowerCase().split(" ")[0]}`,
          text: verdict,
        });
        this.verdictEl.show();
      }
    }
    const child = new Component();
    child.load();
    const target = createDiv({ cls: "askw-markdown" });
    void MarkdownRenderer.render(this.app, this.answer, target, request?.notePath ?? "", child).then(() => {
      this.answerEl.empty();
      this.answerEl.appendChild(target);
      this.renderChild?.unload();
      this.renderChild = child;
    });
  }

  private renderTools(): void {
    this.toolsEl.empty();
    for (const tool of this.tools.slice(0, 8)) {
      this.toolsEl.createSpan({ cls: "askw-pill", text: tool });
    }
  }

  private renderCitations(): void {
    this.citationsEl.empty();
    if (this.citations.length === 0) return;
    this.citationsEl.createDiv({ cls: "askw-section-title", text: "Evidence" });
    for (const citation of this.citations) {
      const card = this.citationsEl.createDiv({ cls: "askw-citation" });
      const button = card.createEl("button", { cls: "askw-citation-open" });
      setIcon(button.createSpan(), "file-search");
      button.createSpan({ text: citation.label ?? citation.path ?? "source" });
      button.addEventListener("click", () => void this.openCitation(citation));
      if (citation.snippet) card.createEl("pre", { text: citation.snippet });
    }
  }

  private async openCitation(citation: Citation): Promise<void> {
    const path = citation.path ?? "";
    const base = this.plugin.vaultPath();
    if (base && path.startsWith(`${base}/`)) {
      const relative = path.slice(base.length + 1);
      const file = this.app.vault.getAbstractFileByPath(relative);
      if (file instanceof TFile) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
        if (citation.line) {
          const view = leaf.view;
          const editor = (view as { editor?: { setCursor(pos: { line: number; ch: number }): void } }).editor;
          editor?.setCursor({ line: Math.max(0, citation.line - 1), ch: 0 });
        }
        return;
      }
    }
    try {
      await this.plugin.service.openSource(citation, this.plugin.contextFolder() ?? "");
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
    }
  }

  private renderActions(running: boolean): void {
    this.actionsEl.empty();
    if (running) {
      this.actionsEl.createEl("button", { text: "Stop" }).addEventListener("click", () => this.stop());
      return;
    }
    if (!this.request) return;
    this.actionsEl.createEl("button", { text: "Retry" }).addEventListener("click", () => this.retry());
    this.actionsEl.createEl("button", { text: "Copy" }).addEventListener("click", async () => {
      await navigator.clipboard.writeText(this.answer);
      new Notice("Answer copied.");
    });
    this.actionsEl.createEl("button", { text: "Open in Ask Widget" }).addEventListener("click", () => {
      this.plugin.service.openInApp(this.request?.documentSource);
    });
  }
}
