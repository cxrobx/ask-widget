import { Notice, PluginSettingTab, Setting, type App } from "obsidian";

import type AskWidgetPlugin from "./main";

export interface AskWidgetSettings {
  serviceUrl: string;
  contextFolder: string;
}

export const DEFAULT_SETTINGS: AskWidgetSettings = {
  serviceUrl: "http://127.0.0.1:8899",
  contextFolder: "",
};

export class AskWidgetSettingTab extends PluginSettingTab {
  constructor(
    app: App,
    private plugin: AskWidgetPlugin,
  ) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("Service URL")
      .setDesc("Where the Ask Widget app is listening. Keep this on loopback.")
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.serviceUrl)
          .setValue(this.plugin.settings.serviceUrl)
          .onChange(async (value) => {
            this.plugin.settings.serviceUrl = value.trim() || DEFAULT_SETTINGS.serviceUrl;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Context folder")
      .setDesc(
        "Folder the model may read as supporting evidence. Defaults to this vault. It must be an allowed root in Ask Widget.",
      )
      .addText((text) =>
        text
          .setPlaceholder(this.plugin.vaultPath() ?? "/path/to/folder")
          .setValue(this.plugin.settings.contextFolder)
          .onChange(async (value) => {
            this.plugin.settings.contextFolder = value.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Allow this vault as context")
      .setDesc("Registers the context folder with Ask Widget so answers can cite your notes.")
      .addButton((button) =>
        button
          .setButtonText("Allow vault folder")
          .setCta()
          .onClick(async () => {
            const folder = this.plugin.contextFolder();
            if (!folder) {
              new Notice("This vault is not stored on the local filesystem.");
              return;
            }
            try {
              await this.plugin.service.ensureRoot(folder);
              new Notice(`Ask Widget can now read ${folder}`);
            } catch (error) {
              new Notice(error instanceof Error ? error.message : String(error));
            }
          }),
      );

    new Setting(containerEl)
      .setName("Connection")
      .setDesc("Check that the service is running and reports a compatible version.")
      .addButton((button) =>
        button.setButtonText("Test connection").onClick(async () => {
          try {
            const session = await this.plugin.service.ensureSession(true);
            new Notice(`Ask Widget ${session.version} · ${session.provider}/${session.model}`);
          } catch (error) {
            new Notice(error instanceof Error ? error.message : String(error));
          }
        }),
      );
  }
}
