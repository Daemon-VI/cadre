// Cadre for VS Code — a thin client of the local Cadre API (ADR-024). Routing, quota, runs and
// approvals all live in the Python engine; this file wires views and commands to its API.
//
// Security (ADR-029): the token is read from CADRE_HOME/token for each request inside the
// extension host (api.ts) and goes nowhere else — not to settings, SecretStorage, webviews, logs
// or UI. At activation nothing is started; the server is started only when a view or command
// needs it.
import * as vscode from "vscode";
import { ApiError, CadreClient, readTokenFile, ServerUnreachable, TokenMissing } from "./api";
import { Approvals } from "./approvals";
import { tokenPath, validPort } from "./cli";
import * as cmd from "./commands";
import { OrgContent, ORG_SCHEME } from "./orgDocs";
import { Panels } from "./panels";
import { RunPanels } from "./runPanel";
import { GitContent, reviewBranch, SCHEME } from "./review";
import { RunNode, RunsProvider } from "./runsView";
import { Server } from "./server";
import { statusText, tightestDailyUsage } from "./usage";

const ONLINE_EVERY_MS = 5_000;
const OFFLINE_EVERY_MS = 15_000;
const QUOTA_EVERY_TICKS = 3;

function port(): number {
  return validPort(vscode.workspace.getConfiguration("cadre").get("port")) ?? 8765;
}

function runIdOf(arg: unknown): string | undefined {
  if (typeof arg === "string" && arg) return arg;
  if (arg instanceof RunNode) return arg.run.id;
  return undefined;
}

export function activate(context: vscode.ExtensionContext): void {
  const out = vscode.window.createOutputChannel("Cadre", { log: true });
  const client = new CadreClient({
    baseUrl: () => `http://127.0.0.1:${port()}`,
    readToken: () => readTokenFile(tokenPath()),
  });
  const server = new Server(client, port, context.globalStorageUri.fsPath, out);
  const runs = new RunsProvider(client, server, out);
  const tree = vscode.window.createTreeView("cadre.runs", { treeDataProvider: runs });

  // deps, panels and approvals refer to each other only inside callbacks, which run after all
  // three exist.
  const deps: cmd.Deps = {
    client,
    server,
    port,
    openRun: (id) => panels.show(id),
    refreshRuns: () => {
      runs.refresh();
      panels.poke();
    },
    out,
  };
  const approvals = new Approvals(client, (id) => panels.show(id), out);
  const panels = new RunPanels(context.extensionUri, client, {
    review: (id) => void reviewBranch(client, id),
    cancel: (id) => void cmd.cancelRun(deps, id),
    resume: (id) => void cmd.resumeRun(deps, id).then(() => panels.poke(id)),
    approvals: (id) => void approvals.review(id).then(() => panels.poke(id)),
    approval: (aid) => void approvals.offerById(aid).then(() => panels.poke()),
  }, out);
  const views = new Panels(context.extensionUri, { cmd: deps, approvals });

  // ---------------------------------------------------------------- status bar
  const bar = vscode.window.createStatusBarItem("cadre.usage", vscode.StatusBarAlignment.Left, 50);
  bar.name = "Cadre usage";
  bar.command = "cadre.runs.focus";
  let lastTightest: ReturnType<typeof tightestDailyUsage>;
  const paint = () => {
    const s = statusText(lastTightest, approvals.count);
    bar.text = s.text;
    bar.tooltip = `${s.tooltip}\nClick to open the Runs view.`;
    bar.backgroundColor = s.level === "hot"
      ? new vscode.ThemeColor("statusBarItem.errorBackground")
      : s.level === "warn" || approvals.count > 0
        ? new vscode.ThemeColor("statusBarItem.warningBackground")
        : undefined;
    bar.show();
  };
  const paintProblem = (text: string, tooltip: string) => {
    bar.text = text;
    bar.tooltip = tooltip;
    bar.backgroundColor = undefined;
    bar.show();
  };
  approvals.onDidChange(() => {
    paint();
    panels.poke();
    views.refresh("approvals", "memory"); // a memory proposal is an approval too
  });

  // ---------------------------------------------------------------- poller
  let online = false;
  let ticks = 0;
  let timer: NodeJS.Timeout | undefined;
  let busy = false;
  let lastProblem = "";

  const schedule = (ms: number) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void tick(), ms);
  };

  const tick = async (): Promise<void> => {
    if (busy) return;
    busy = true;
    try {
      if (!online && !(await client.health())) {
        paintProblem("$(circle-slash) Cadre",
          `Cadre: no server on 127.0.0.1:${port()}. Open the Runs view to start one (cadre serve).`);
        approvals.clear();
        return;
      }
      const wantQuota = !online || ticks % QUOTA_EVERY_TICKS === 0;
      const [pending, quota, rows] = await Promise.all([
        client.approvals(),
        wantQuota ? client.quota() : Promise.resolve(undefined),
        tree.visible ? client.runs(50) : Promise.resolve(undefined),
      ]);
      online = true;
      lastProblem = "";
      ticks++;
      if (quota) lastTightest = tightestDailyUsage(quota);
      approvals.update(pending);
      if (rows) runs.update(rows);
      paint();
    } catch (e) {
      online = false;
      let problem: string;
      if (e instanceof ServerUnreachable) {
        problem = "unreachable";
        paintProblem("$(circle-slash) Cadre", `Cadre: the server on 127.0.0.1:${port()} stopped answering.`);
        approvals.clear();
      } else if (e instanceof TokenMissing || (e instanceof ApiError && e.status === 401)) {
        problem = "token";
        paintProblem("$(warning) Cadre", `Cadre: ${e.message}`);
      } else {
        problem = (e as Error).message;
        paintProblem("$(warning) Cadre", `Cadre: ${problem}`);
      }
      if (problem !== lastProblem) out.appendLine(`[poll] ${(e as Error).message}`);
      lastProblem = problem;
    } finally {
      busy = false;
      schedule(online ? ONLINE_EVERY_MS : OFFLINE_EVERY_MS);
    }
  };

  // ---------------------------------------------------------------- commands
  const register = (id: string, fn: (...args: unknown[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  register("cadre.refreshRuns", () => {
    online = false;
    runs.refresh();
    void tick();
  });
  register("cadre.openRun", async (arg) => {
    const id = runIdOf(arg) ?? (await cmd.pickRun(deps, "Open which run?"));
    if (id) panels.show(id);
  });
  register("cadre.forecast", () => cmd.forecastCommand(deps));
  register("cadre.startRun", () => cmd.startRunCommand(deps));
  register("cadre.reviewBranch", async (arg) => {
    const id = runIdOf(arg);
    if (!id && !(await server.ensure())) return;
    await reviewBranch(client, id);
  });
  register("cadre.addProvider", () => cmd.addProviderCommand(deps));
  register("cadre.openDashboard", () => cmd.openDashboardCommand(deps));
  register("cadre.approvals", async (arg) => {
    if (!(await server.ensure())) return;
    await approvals.review(runIdOf(arg));
    panels.poke();
  });
  register("cadre.startServer", async () => {
    if (await server.start()) {
      online = false;
      runs.refresh();
      void tick();
    }
  });
  register("cadre.showUsage", () => views.show("usage"));
  register("cadre.showApprovals", () => views.show("approvals"));
  register("cadre.newRun", () => views.show("start"));
  register("cadre.showMemory", () => views.show("memory"));
  register("cadre.showOrgs", () => views.show("orgs"));
  register("cadre.cancelRun", async (arg) => {
    const id = runIdOf(arg) ?? (await cmd.pickRun(deps, "Cancel which run?"));
    if (id) await cmd.cancelRun(deps, id);
  });
  register("cadre.resumeRun", async (arg) => {
    const id = runIdOf(arg) ?? (await cmd.pickRun(deps, "Resume which run?"));
    if (id) {
      await cmd.resumeRun(deps, id);
      panels.poke(id);
    }
  });

  context.subscriptions.push(
    out,
    tree,
    runs,
    approvals,
    panels,
    views,
    bar,
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, new GitContent()),
    vscode.workspace.registerTextDocumentContentProvider(ORG_SCHEME, new OrgContent(client)),
    tree.onDidChangeVisibility((e) => {
      if (e.visible) void tick();
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("cadre.port")) {
        online = false;
        runs.refresh();
        void tick();
      }
    }),
    { dispose: () => timer && clearTimeout(timer) },
  );

  paintProblem("$(pulse) Cadre", "Cadre: checking for a local server…");
  void tick();
}

export function deactivate(): void {
  // Nothing to stop: a server this extension started is detached on purpose, so runs outlive the editor.
}
