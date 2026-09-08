import { copyFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import process from "node:process";

import builtins from "builtin-modules";
import esbuild from "esbuild";

const watch = process.argv.includes("--watch");
const vault = process.env.OBSIDIAN_VAULT;
const target = vault ? join(vault, ".obsidian", "plugins", "ask-widget") : null;

/** Copy the three shipped files into the vault so Cmd+R picks up the build. */
const installPlugin = {
  name: "install-plugin",
  setup(build) {
    build.onEnd(async (result) => {
      if (!target || result.errors.length > 0) return;
      await mkdir(target, { recursive: true });
      for (const file of ["manifest.json", "main.js", "styles.css"]) {
        await copyFile(file, join(target, file)).catch(() => {});
      }
      console.log(`installed → ${target}`);
    });
  },
};

const context = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  outfile: "main.js",
  format: "cjs",
  target: "es2018",
  platform: "node",
  logLevel: "info",
  sourcemap: watch ? "inline" : false,
  treeShaking: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
    ...builtins,
  ],
  plugins: target ? [installPlugin] : [],
});

if (watch) {
  await context.watch();
} else {
  await context.rebuild();
  await context.dispose();
}
