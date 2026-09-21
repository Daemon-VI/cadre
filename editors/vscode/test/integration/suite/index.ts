// Runs inside the VS Code extension host. A dozen lines of runner instead of a test framework
// dependency. No Cadre server is running in CI: these tests check activation, contributions and
// the parts that must work without one.
import assert from "node:assert/strict";
import * as vscode from "vscode";

const EXTENSION_ID = "daemon-vi.cadre-ai";
const tests: [string, () => Promise<void>][] = [];
const test = (name: string, fn: () => Promise<void>) => tests.push([name, fn]);

async function extension(): Promise<vscode.Extension<unknown>> {
  const ext = vscode.extensions.getExtension(EXTENSION_ID);
  assert.ok(ext, `${EXTENSION_ID} is installed in the test instance`);
  if (!ext.isActive) await ext.activate();
  return ext;
}

test("activates without a Cadre server", async () => {
  const ext = await extension();
  assert.equal(ext.isActive, true);
});

test("every contributed command is registered", async () => {
  const ext = await extension();
  const all = new Set(await vscode.commands.getCommands(true));
  const contributed = (ext.packageJSON.contributes.commands as { command: string }[]).map((c) => c.command);
  for (const id of contributed) assert.ok(all.has(id), `${id} is registered`);
  assert.ok(all.has("cadre.runs.focus"), "the Runs view exists (the status bar opens it)");
});

test("settings default to port 8765 with auto-start, and hold nothing secret", async () => {
  await extension();
  const cfg = vscode.workspace.getConfiguration("cadre");
  assert.equal(cfg.get("port"), 8765);
  assert.equal(cfg.get("autoStart"), true);
  assert.equal(cfg.get("token"), undefined);
});

test("the review content provider refuses an option-like ref", async () => {
  await extension();
  const uri = vscode.Uri.from({ scheme: "cadre-git", path: "/README.md", query: JSON.stringify({ root: ".", ref: "--output=x" }) });
  const doc = await vscode.workspace.openTextDocument(uri);
  assert.equal(doc.getText(), "");
});

test("1.4.0: each panel command opens its own webview, once, even with no server", async () => {
  await extension();
  const cfg = vscode.workspace.getConfiguration("cadre");
  // No server in CI: with auto-start off the panel asks (and waits) instead of downloading one.
  await cfg.update("autoStart", false, vscode.ConfigurationTarget.Global);
  try {
    const panels: [string, string][] = [
      ["cadre.showUsage", "Cadre · Usage"], ["cadre.showApprovals", "Cadre · Approvals"],
      ["cadre.newRun", "Cadre · New run"], ["cadre.showMemory", "Cadre · Memory"],
      ["cadre.showOrgs", "Cadre · Organisations"],
    ];
    const tabs = () => vscode.window.tabGroups.all.flatMap((g) => g.tabs);
    const isPanel = (title: string) => (t: vscode.Tab) => t.label === title && t.input instanceof vscode.TabInputWebview;
    // The workbench's tab model catches up a moment after createWebviewPanel returns, so wait for
    // the tab (up to 5 s) rather than reading it at once; a panel that never opens still fails.
    const appears = async (title: string): Promise<boolean> => {
      for (let waited = 0; waited < 5000; waited += 50) {
        if (tabs().some(isPanel(title))) return true;
        await new Promise((r) => setTimeout(r, 50));
      }
      return tabs().some(isPanel(title));
    };
    for (const [command, title] of panels) {
      await vscode.commands.executeCommand(command);
      assert.ok(await appears(title),
        `${command} opened a webview titled "${title}"; tabs now: ${JSON.stringify(tabs().map((t) => t.label))}`);
    }
    await vscode.commands.executeCommand("cadre.showUsage");
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(tabs().filter((t) => t.label === "Cadre · Usage").length, 1, "a second call reveals the same panel");
  } finally {
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    await cfg.update("autoStart", undefined, vscode.ConfigurationTarget.Global);
  }
});

export async function run(): Promise<void> {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (err) {
      failed++;
      console.error(`not ok - ${name}\n${err instanceof Error ? err.stack : String(err)}`);
    }
  }
  console.log(`# ${tests.length - failed}/${tests.length} passed`);
  if (failed) throw new Error(`${failed} integration test(s) failed`);
}
