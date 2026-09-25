// Builds static/onyx-editor.js, the Live Preview editor ask.js loads on the first ⌘E. The output is committed, so the
// service runs from a checkout (and CI's browser suite) with no Node step; CI rebuilds it and fails on any difference.
import esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/main.ts"],
  bundle: true,
  outfile: "../static/onyx-editor.js",
  format: "iife",
  // The app's system WebKit is older than any browser's (Safari 17 on macOS 14).
  target: "safari16",
  minify: true,
  legalComments: "eof",
  logLevel: "info",
});
