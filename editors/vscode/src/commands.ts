// The palette commands (AC-19.3) other than Review branch (review.ts).
import { spawn } from "node:child_process";
import * as vscode from "vscode";
import { ApiError, type CadreClient, type OrgSummary } from "./api";
import { cliInvocation, describeInvocation, findOnPath, isPresetId } from "./cli";
import { brief, hasUsableProvider } from "./format";
import type { Server } from "./server";

export interface Deps {
  client: CadreClient;
  server: Server;
  port: () => number;
  openRun: (runId: string) => void;
  refreshRuns: () => void;
  out: vscode.OutputChannel;
}

function fail(e: unknown): void {
  void vscode.window.showErrorMessage(`Cadre: ${(e as Error).message}`);
}

async function pickOrg(client: CadreClient, placeHolder: string): Promise<string | undefined> {
  const orgs = (await client.orgs()).filter((o: OrgSummary) => o.valid);
  if (!orgs.length) {
    void vscode.window.showWarningMessage("Cadre: no valid organisation found.");
    return undefined;
  }
  const pick = await vscode.window.showQuickPick(
    orgs.map((o) => ({
      label: o.name,
      description: `${o.source === "yours" ? "yours" : "template"}${o.workflow ? ` · ${o.workflow}` : ""}`,
      detail: brief(o.description ?? "", 160) + (o.checks?.length ? `  Checks: ${o.checks.join(", ")}` : ""),
    })),
    { placeHolder, matchOnDetail: true });
  return pick?.label;
}

async function askGoal(prefill = ""): Promise<string | undefined> {
  const goal = await vscode.window.showInputBox({
    title: "Goal",
    prompt: "What should the organisation achieve?",
    placeHolder: "e.g. Add a --json flag to the export command, with tests",
    value: prefill,
    ignoreFocusOut: true,
    validateInput: (v) => (v.trim() ? undefined : "A run needs a goal."),
  });
  return goal?.trim() || undefined;
}

async function pickFolder(): Promise<vscode.WorkspaceFolder | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length <= 1) return folders[0];
  return vscode.window.showWorkspaceFolderPick({ placeHolder: "Which folder should the run work on?" });
}

// ------------------------------------------------------------------ Forecast
export async function forecastCommand(d: Deps): Promise<void> {
  if (!(await d.server.ensure())) return;
  try {
    const org = await pickOrg(d.client, "Forecast which organisation?");
    if (!org) return;
    const goal = await askGoal();
    if (!goal) return;
    const folder = vscode.workspace.isTrusted ? vscode.workspace.workspaceFolders?.[0] : undefined;
    const f = await d.client.forecast({ org, goal, project: folder?.uri.fsPath });
    const canStart = f.verdict !== "cannot_run" && vscode.workspace.isTrusted && !!folder;
    const buttons = canStart ? ["Start run on this folder"] : [];
    const pick = await vscode.window.showInformationMessage(
      `Cadre forecast for ${org}: ${f.headline}`, { modal: true, detail: f.lines.join("\n") }, ...buttons);
    if (pick === "Start run on this folder") await startRunWith(d, org, goal, folder!);
  } catch (e) {
    fail(e);
  }
}

// ------------------------------------------------------------------ Start run on this folder
export async function startRunCommand(d: Deps): Promise<void> {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("Cadre: starting a run on a folder needs a trusted workspace.");
    return;
  }
  const folder = await pickFolder();
  if (!folder) {
    void vscode.window.showInformationMessage("Cadre: open a folder (a git repository) first.");
    return;
  }
  if (!(await d.server.ensure())) return;
  try {
    const org = await pickOrg(d.client, `Which organisation should work on ${folder.name}?`);
    if (!org) return;
    const goal = await askGoal();
    if (!goal) return;
    await startRunWith(d, org, goal, folder);
  } catch (e) {
    fail(e);
  }
}

async function startRunWith(d: Deps, org: string, goal: string, folder: vscode.WorkspaceFolder): Promise<void> {
  let demo = false;
  const providers = await d.client.providers().catch(() => undefined);
  if (providers && !hasUsableProvider(providers)) {
    const pick = await vscode.window.showWarningMessage(
      "Cadre has no model key yet. Run the demo instead (an offline scripted model — it exercises the "
      + "workflow, not real intelligence), or add a provider first?",
      "Run demo", "Add provider");
    if (pick === "Add provider") {
      await vscode.commands.executeCommand("cadre.addProvider");
      return;
    }
    if (pick !== "Run demo") return;
    demo = true;
  }
  const body = { org, goal, project: folder.uri.fsPath, demo };
  try {
    const { id } = await d.client.startRun(body);
    d.refreshRuns();
    d.openRun(id);
  } catch (e) {
    // The engine owns the dirty-tree rule (AC-8.2); its words are shown as they are.
    const msg = (e as Error).message;
    if (e instanceof ApiError && /uncommitted changes/i.test(msg)) {
      const pick = await vscode.window.showErrorMessage(`Cadre: ${msg}`,
        { modal: true, detail: "Starting from HEAD anyway means the run does not see your uncommitted changes." },
        "Start from HEAD anyway");
      if (pick !== "Start from HEAD anyway") return;
      try {
        const { id } = await d.client.startRun({ ...body, allow_dirty: true });
        d.refreshRuns();
        d.openRun(id);
      } catch (e2) {
        fail(e2);
      }
      return;
    }
    fail(e);
  }
}

// ------------------------------------------------------------------ Add provider
/**
 * Opens the integrated terminal *running* `cadre provider add <id>` (or `uvx cadre-ai provider add
 * <id>`) directly — no shell, so nothing is interpolated. The CLI asks for the key with a hidden
 * prompt and stores it in the OS credential store; the key never passes through the extension.
 */
export async function addProviderCommand(d: Deps): Promise<void> {
  const probe = cliInvocation([], (n) => findOnPath(n));
  if (!probe) {
    const pick = await vscode.window.showErrorMessage(
      "Cadre: adding a provider needs `cadre` or `uvx` on PATH.", "Install uv");
    if (pick) await vscode.env.openExternal(vscode.Uri.parse("https://docs.astral.sh/uv/"));
    return;
  }
  let id: string | undefined;
  const presets = (await d.server.isUp()) ? await d.client.presets().then((p) => p.presets).catch(() => []) : [];
  if (presets.length) {
    const pick = await vscode.window.showQuickPick(
      presets.map((p) => ({
        label: p.id,
        description: `${p.label}${p.free ? "" : " (paid)"}${p.local ? " · local" : ""}`,
        detail: p.note ? brief(p.note, 160) : undefined,
      })),
      { placeHolder: "Which provider? The key is typed into the terminal, not here.", matchOnDescription: true });
    id = pick?.label;
  } else {
    id = (await vscode.window.showInputBox({
      title: "Provider preset",
      prompt: "A preset id such as groq, gemini, openrouter, mistral (see `cadre presets`)",
      validateInput: (v) => (isPresetId(v.trim()) ? undefined : "lowercase letters, digits, - or _"),
    }))?.trim();
  }
  if (!id || !isPresetId(id)) return;
  const inv = cliInvocation(["provider", "add", id], (n) => findOnPath(n))!;
  const terminal = vscode.window.createTerminal({
    name: `Cadre: add ${id}`,
    shellPath: inv.command,
    shellArgs: inv.args,
    iconPath: new vscode.ThemeIcon("key"),
  });
  terminal.show();
  d.out.appendLine(`[provider] opened a terminal running: ${describeInvocation(inv)}`);
  const sub = vscode.window.onDidCloseTerminal((t) => {
    if (t !== terminal) return;
    sub.dispose();
    if (t.exitStatus?.code === 0) {
      void vscode.window.showInformationMessage(
        `Cadre: provider ${id} added. A server that was already running uses it after a restart.`);
    }
  });
}

// ------------------------------------------------------------------ Open dashboard
/**
 * The dashboard needs the token, and the extension never shows or forwards it. So this runs
 * `cadre ui --port N` (or `uvx cadre-ai ui …`), which reads the token itself and opens the browser
 * with it in the URL fragment — never sent to any server (ADR-010). Without the CLI it opens the
 * bare URL, and the dashboard asks for the token.
 */
export async function openDashboardCommand(d: Deps): Promise<void> {
  if (!(await d.server.ensure())) return;
  const port = d.port();
  const inv = cliInvocation(["ui", "--port", String(port)], (n) => findOnPath(n));
  if (inv) {
    const ok = await new Promise<boolean>((resolve) => {
      const child = spawn(inv.command, inv.args, { stdio: "ignore", windowsHide: true });
      child.on("error", () => resolve(false));
      child.on("exit", (code) => resolve(code === 0));
    });
    if (ok) return;
    d.out.appendLine(`[dashboard] ${describeInvocation(inv)} failed; opening the plain URL`);
  }
  await vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${port}/`));
  void vscode.window.showInformationMessage(
    "Cadre: the dashboard asks for the access token. Run `cadre ui` in a terminal for a signed-in link.");
}

// ------------------------------------------------------------------ Open / cancel / resume
export async function pickRun(d: Deps, placeHolder: string): Promise<string | undefined> {
  if (!(await d.server.ensure())) return undefined;
  try {
    const rows = await d.client.runs(50);
    const pick = await vscode.window.showQuickPick(
      rows.map((r) => ({ label: brief(r.goal, 90), description: `${r.org} · ${r.status}`, detail: r.id, id: r.id })),
      { placeHolder, matchOnDescription: true, matchOnDetail: true });
    return pick?.id;
  } catch (e) {
    fail(e);
    return undefined;
  }
}

export async function cancelRun(d: Deps, runId: string): Promise<void> {
  const pick = await vscode.window.showWarningMessage(`Cancel Cadre run ${runId}?`, { modal: true }, "Cancel run");
  if (pick !== "Cancel run") return;
  try {
    await d.client.cancel(runId);
    d.refreshRuns();
  } catch (e) {
    fail(e);
  }
}

export async function resumeRun(d: Deps, runId: string): Promise<void> {
  try {
    await d.client.resume(runId);
    void vscode.window.showInformationMessage("Cadre: resumed — finished steps are reused, not re-billed.");
    d.refreshRuns();
  } catch (e) {
    fail(e);
  }
}
