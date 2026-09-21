// Three bundles: the extension host (Node, CommonJS), the run-detail webview and the panel
// webviews (browser, IIFE).
// Nothing but our own code is bundled; `vscode` is provided by the editor at runtime.
import * as fs from "node:fs";
import * as esbuild from "esbuild";

const production = process.argv.includes("--production");
const watch = process.argv.includes("--watch");

// A production build starts clean, so no development source map is left beside it.
if (production) fs.rmSync("dist", { recursive: true, force: true });

const common = {
  bundle: true,
  minify: production,
  sourcemap: !production,
  sourcesContent: false,
  logLevel: "info",
  legalComments: "none",
};

const targets = [
  {
    ...common,
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    platform: "node",
    format: "cjs",
    target: "node20",
    external: ["vscode"],
  },
  {
    ...common,
    entryPoints: ["src/webview/run.ts"],
    outfile: "dist/webview/run.js",
    platform: "browser",
    format: "iife",
    target: "es2022",
  },
  {
    ...common,
    entryPoints: ["src/webview/panels.ts"],
    outfile: "dist/webview/panels.js",
    platform: "browser",
    format: "iife",
    target: "es2022",
  },
];

if (watch) {
  for (const t of targets) {
    const ctx = await esbuild.context(t);
    await ctx.watch();
  }
} else {
  await Promise.all(targets.map((t) => esbuild.build(t)));
}
