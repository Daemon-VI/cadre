// package.json and extension.ts agree, and the manifest keeps the promises in the README.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
const extensionSrc = fs.readFileSync(path.join(ROOT, "src", "extension.ts"), "utf8");

test("every contributed command is registered, and every registered command is contributed", () => {
  const contributed = new Set<string>(pkg.contributes.commands.map((c: { command: string }) => c.command));
  const registered = new Set([...extensionSrc.matchAll(/register\("([^"]+)"/g)].map((m) => m[1]));
  assert.deepEqual([...contributed].sort(), [...registered].sort());
});

test("menus only reference contributed commands", () => {
  const contributed = new Set<string>(pkg.contributes.commands.map((c: { command: string }) => c.command));
  for (const [where, items] of Object.entries(pkg.contributes.menus as Record<string, { command: string }[]>)) {
    for (const item of items) assert.ok(contributed.has(item.command), `${where}: ${item.command}`);
  }
});

test("no setting could hold a token or a key (Settings Sync would upload it)", () => {
  const props = Object.keys(pkg.contributes.configuration.properties);
  assert.deepEqual(props.sort(), ["cadre.autoStart", "cadre.port"]);
  assert.ok(!props.some((p) => /token|key|secret/i.test(p)));
});

test("marketplace identity and zero runtime dependencies", () => {
  assert.equal(pkg.publisher, "daemon-vi");
  // the Marketplace requires a globally unique `name`, and plain "cadre" is taken by another
  // publisher, so the extension ships under the PyPI distribution's name (2026-09-20)
  assert.equal(pkg.name, "cadre-ai");
  // the Marketplace also enforces a globally unique displayName, and plain "Cadre" is taken
  assert.equal(pkg.displayName, "Cadre AI");
  assert.equal(pkg.license, "Apache-2.0");
  assert.equal(pkg.icon, "media/icon.png");
  assert.ok(fs.existsSync(path.join(ROOT, "media", "icon.png")));
  assert.equal(pkg.dependencies, undefined);
  assert.match(pkg.engines.vscode, /^\^1\.\d+\.\d+$/);
  assert.ok(fs.existsSync(path.join(ROOT, "LICENSE")));
});

test("the status bar opens the runs view that the manifest declares", () => {
  assert.ok(extensionSrc.includes('bar.command = "cadre.runs.focus"'));
  assert.ok(pkg.contributes.views.cadre.some((v: { id: string }) => v.id === "cadre.runs"));
});

// ------------------------------------------------------------------ extension 1.4.0 (the panels)
const PANEL_COMMANDS = ["cadre.newRun", "cadre.showUsage", "cadre.showApprovals", "cadre.showMemory", "cadre.showOrgs"];

test("1.4.0: the five panel commands are contributed, registered, and reachable from the Runs view", () => {
  assert.equal(pkg.version, "1.4.0");
  const contributed = pkg.contributes.commands.map((c: { command: string }) => c.command);
  const menu = pkg.contributes.menus["view/title"].map((m: { command: string }) => m.command);
  for (const id of PANEL_COMMANDS) {
    assert.ok(contributed.includes(id), `${id} is contributed`);
    assert.ok(extensionSrc.includes(`register("${id}"`), `${id} is registered in extension.ts`);
    assert.ok(menu.includes(id), `${id} is in the Runs view's title menu`);
  }
});

test("1.4.0: the panel bundle and stylesheet ship in the .vsix", () => {
  const ignore = fs.readFileSync(path.join(ROOT, ".vscodeignore"), "utf8");
  assert.ok(ignore.includes("!dist/webview/panels.js"), "a bundle not named in .vscodeignore is silently left out");
  assert.ok(ignore.includes("!media/**"));
  assert.ok(fs.existsSync(path.join(ROOT, "media", "panels.css")));
  assert.ok(fs.readFileSync(path.join(ROOT, "esbuild.mjs"), "utf8").includes("src/webview/panels.ts"));
});
