// The panel webviews (extension 1.4.0): Usage, Approvals, New run, Memory and Organisations.
// One webview per kind, reused while it is open. The extension host makes every API call and posts
// plain view models (panelModels.ts); a webview can only post back what `isPanelMessage` admits
// (protocol.ts), and only the messages its own kind uses. The token never leaves the host (ADR-029).
//
// Approvals are never decided in a panel. Decide… calls the same Approvals.offerById that the run
// view's button uses, so letting code run still takes the modal's explicit "Allow execution".
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import type { Approvals } from "./approvals";
import { cadreHome } from "./cli";
import { type Deps, launchRun } from "./commands";
import { hasUsableProvider } from "./format";
import { makeNonce, panelHtml } from "./html";
import { orgUri } from "./orgDocs";
import { approvalsModel, memoryModel, orgsModel, startModel, usageModel } from "./panelModels";
import {
  isAllowedPanelMessage, type PanelHostMessage, type PanelKind, type PanelMessage, type PanelView, type Tone,
} from "./protocol";
import { inert } from "./text";

export const TITLES: Record<PanelKind, string> = {
  usage: "Cadre · Usage",
  approvals: "Cadre · Approvals",
  start: "Cadre · New run",
  memory: "Cadre · Memory",
  orgs: "Cadre · Organisations",
};

const USAGE_EVERY_MS = 15_000;

export interface PanelDeps {
  cmd: Deps;
  approvals: Approvals;
}

class HostPanel {
  private days = 7;
  private prefillOrg = "";
  private timer: NodeJS.Timeout | undefined;
  private disposed = false;
  private loading: Promise<void> | undefined;
  private again = false;

  constructor(
    readonly kind: PanelKind,
    readonly panel: vscode.WebviewPanel,
    private readonly deps: PanelDeps,
    private readonly open: (kind: PanelKind, org?: string) => void,
    onDispose: () => void,
  ) {
    panel.onDidDispose(() => {
      this.disposed = true;
      if (this.timer) clearTimeout(this.timer);
      onDispose();
    });
    panel.webview.onDidReceiveMessage((m: unknown) => void this.onMessage(m));
    // Coming back to a panel refreshes it — but never starts a server just because a tab was clicked.
    panel.onDidChangeViewState((e) => {
      if (e.webviewPanel.visible) void this.reloadIfUp();
    });
  }

  prefill(org: string | undefined): void {
    if (org) this.prefillOrg = org;
  }

  private post(msg: PanelHostMessage): void {
    if (!this.disposed) void this.panel.webview.postMessage(msg);
  }

  private notice(text: string, tone: Tone = ""): void {
    this.post({ type: "notice", text, tone });
  }

  /** Load and post the view. Overlapping calls coalesce into one more load, never a pile-up. */
  load(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.loading) {
      this.again = true;
      return this.loading;
    }
    this.loading = (async () => {
      do {
        this.again = false;
        await this.loadOnce();
      } while (this.again && !this.disposed);
    })().finally(() => {
      this.loading = undefined;
      this.schedule();
    });
    return this.loading;
  }

  async reloadIfUp(): Promise<void> {
    if (await this.deps.cmd.server.isUp()) await this.load();
  }

  private async loadOnce(): Promise<void> {
    try {
      if (!(await this.deps.cmd.server.ensure())) {
        this.notice(`No Cadre server answers on 127.0.0.1:${this.deps.cmd.port()}. `
          + "Run “Cadre: Start server”, then refresh.", "bad");
        return;
      }
      this.post({ type: "view", data: await this.build() });
    } catch (e) {
      this.notice(`Could not load: ${(e as Error).message}`, "bad");
    }
  }

  /** Only Usage refreshes by itself, and only while it is on screen and a server is up. */
  private schedule(): void {
    if (this.kind !== "usage" || this.disposed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.tick(), USAGE_EVERY_MS);
  }

  /** One self-refresh tick. A load re-arms the timer itself; anything else (hidden, or no server
   *  answering) re-arms it here, so one missed tick never switches the self-refresh off. */
  private async tick(): Promise<void> {
    if (this.disposed) return;
    if (this.panel.visible && (await this.deps.cmd.server.isUp())) await this.load();
    else this.schedule();
  }

  private async build(): Promise<PanelView> {
    const c = this.deps.cmd.client;
    switch (this.kind) {
      case "usage": {
        const [quota, ledger] = await Promise.all([c.quota(), c.usage(this.days)]);
        return { kind: "usage", view: usageModel(quota, ledger, this.days) };
      }
      case "approvals":
        return { kind: "approvals", view: approvalsModel(await c.approvals()) };
      case "start": {
        const [orgs, providers] = await Promise.all([c.orgs(), c.providers().catch(() => undefined)]);
        const prefill = this.prefillOrg;
        this.prefillOrg = ""; // one-shot: a later refresh must not undo the person's own choice
        return {
          kind: "start",
          view: startModel(orgs, providers, vscode.workspace.workspaceFolders ?? [], vscode.workspace.isTrusted, prefill),
        };
      }
      case "memory": {
        const [list, pending] = await Promise.all([c.memory(), c.approvals()]);
        return { kind: "memory", view: memoryModel(list, new Set(pending.map((a) => a.id))) };
      }
      case "orgs":
        return { kind: "orgs", view: orgsModel(await c.orgs()) };
    }
  }

  private async onMessage(raw: unknown): Promise<void> {
    if (!isAllowedPanelMessage(this.kind, raw)) {
      this.deps.cmd.out.appendLine(`[panel ${this.kind}] dropped a message that is not allowed here`);
      return;
    }
    const m = raw;
    try {
      switch (m.type) {
        case "ready":
        case "refresh":
          await this.load();
          break;
        case "days":
          this.days = m.days;
          await this.load();
          break;
        case "decide":
          await this.deps.approvals.offerById(m.id);
          await this.load();
          break;
        case "openRun":
          this.deps.cmd.openRun(m.id);
          break;
        case "forecast":
          await this.forecast(m);
          break;
        case "start":
          await this.start(m);
          break;
        case "memoryAdd":
          await this.memoryAdd(m);
          break;
        case "memoryRemove":
          await this.memoryRemove(m.id);
          break;
        case "openOrg":
          await this.openOrg(m.name);
          break;
        case "useOrg":
          this.open("start", m.name);
          break;
      }
    } catch (e) {
      this.notice((e as Error).message, "bad");
    }
  }

  /** A workspace folder by index; the webview never sends a path. -1 is "no folder". */
  private folderPath(index: number): string | undefined {
    if (index < 0) return undefined;
    const folder = vscode.workspace.workspaceFolders?.[index];
    if (!folder) throw new Error("That folder is no longer open in this window. Refresh the form.");
    return folder.uri.fsPath;
  }

  private async busy<T>(fn: () => Promise<T>): Promise<T> {
    this.post({ type: "busy", busy: true });
    try {
      return await fn();
    } finally {
      this.post({ type: "busy", busy: false });
    }
  }

  private async forecast(m: Extract<PanelMessage, { type: "forecast" }>): Promise<void> {
    // As the palette's Forecast does: the folder is only read when the workspace is trusted.
    const project = vscode.workspace.isTrusted ? this.folderPath(m.folder) : undefined;
    const f = await this.busy(() => this.deps.cmd.client.forecast({
      org: m.org, goal: m.goal.trim(), project, private: m.private,
    }));
    this.post({ type: "forecast", forecast: { verdict: f.verdict, headline: f.headline, lines: f.lines ?? [] } });
  }

  private async start(m: Extract<PanelMessage, { type: "start" }>): Promise<void> {
    if (m.folder >= 0 && !vscode.workspace.isTrusted) {
      throw new Error("Starting a run on a folder needs a trusted workspace. Pick “No folder”, or trust this workspace.");
    }
    const project = this.folderPath(m.folder);
    if (!m.demo) {
      const providers = await this.deps.cmd.client.providers().catch(() => undefined);
      if (providers && !hasUsableProvider(providers)) {
        throw new Error("Cadre has no model key yet. Tick “Offline demo model”, or add a key with “Cadre: Add provider”.");
      }
    }
    // Never allow_exec or auto_approve: checks still wait for the modal, gates still ask.
    const id = await this.busy(() => launchRun(this.deps.cmd, {
      org: m.org, goal: m.goal.trim(), project, demo: m.demo, private: m.private,
    }));
    if (id) this.notice(`Started run ${id}. It is open in its own tab.`, "good");
  }

  private async memoryAdd(m: Extract<PanelMessage, { type: "memoryAdd" }>): Promise<void> {
    // The engine checks length, scope and key shapes; its refusal (which never echoes a key) is shown.
    const e = await this.busy(() => this.deps.cmd.client.addMemory({
      scope: m.scope, text: m.text, pinned: m.pinned, private: m.private,
    }));
    await this.load();
    this.notice(`Remembered ${e.id} in ${e.scope}.`, "good");
  }

  private async memoryRemove(id: string): Promise<void> {
    const entry = (await this.deps.cmd.client.memory()).entries.find((e) => e.id === id);
    if (!entry) {
      await this.load();
      throw new Error(`Memory entry ${id} is already gone.`);
    }
    const pick = await vscode.window.showWarningMessage(`Delete memory entry ${id} (${entry.scope})?`,
      { modal: true, detail: inert(entry.text) }, "Delete");
    if (pick !== "Delete") return;
    await this.deps.cmd.client.removeMemory(id);
    await this.load();
    this.notice(`Deleted ${id}.`, "good");
  }

  private async openOrg(name: string): Promise<void> {
    const org = (await this.deps.cmd.client.orgs()).find((o) => o.name === name);
    if (!org) throw new Error(`No organisation called ${name}.`);
    if (org.source === "yours") {
      // Your own org opens as the real file, so it can be edited; the name is a slug, so the path
      // cannot leave CADRE_HOME/orgs.
      const dir = path.join(cadreHome(), "orgs");
      for (const ext of [".yaml", ".yml"]) {
        const file = path.join(dir, `${name}${ext}`);
        if (path.dirname(file) === dir && fs.existsSync(file)) {
          await vscode.window.showTextDocument(vscode.Uri.file(file), { preview: false });
          return;
        }
      }
    }
    await vscode.window.showTextDocument(orgUri(name), { preview: true });
  }
}

export class Panels implements vscode.Disposable {
  private readonly open = new Map<PanelKind, HostPanel>();
  private readonly subs: vscode.Disposable[];

  constructor(private readonly extensionUri: vscode.Uri, private readonly deps: PanelDeps) {
    // The new-run form lists the open folders by position: when they change (or the workspace
    // becomes trusted), re-post the list at once so the form never offers a stale one.
    this.subs = [
      vscode.workspace.onDidChangeWorkspaceFolders(() => this.refresh("start")),
      vscode.workspace.onDidGrantWorkspaceTrust(() => this.refresh("start")),
    ];
  }

  show(kind: PanelKind, org?: string): void {
    const existing = this.open.get(kind);
    if (existing) {
      existing.prefill(org);
      existing.panel.reveal();
      void existing.load();
      return;
    }
    const dist = vscode.Uri.joinPath(this.extensionUri, "dist", "webview");
    const media = vscode.Uri.joinPath(this.extensionUri, "media");
    const panel = vscode.window.createWebviewPanel(`cadre.${kind}`, TITLES[kind], vscode.ViewColumn.Active, {
      enableScripts: true,
      enableCommandUris: false,
      enableFindWidget: true,
      retainContextWhenHidden: kind === "start", // keep a half-typed goal when the tab is switched away
      localResourceRoots: [dist, media],
    });
    panel.iconPath = vscode.Uri.joinPath(media, "cadre.svg");
    const webview = panel.webview;
    webview.html = panelHtml({
      cspSource: webview.cspSource,
      nonce: makeNonce(),
      scriptUri: webview.asWebviewUri(vscode.Uri.joinPath(dist, "panels.js")).toString(),
      styleUris: ["run.css", "panels.css"].map((f) => webview.asWebviewUri(vscode.Uri.joinPath(media, f)).toString()),
      title: TITLES[kind],
    });
    const host = new HostPanel(kind, panel, this.deps, (k, o) => this.show(k, o), () => this.open.delete(kind));
    host.prefill(org);
    this.open.set(kind, host);
  }

  /** Something changed outside the panel (the approvals poll): reload the panels that show it. */
  refresh(...kinds: PanelKind[]): void {
    for (const [kind, p] of this.open) if (!kinds.length || kinds.includes(kind)) void p.reloadIfUp();
  }

  dispose(): void {
    for (const s of this.subs) s.dispose();
    for (const p of [...this.open.values()]) p.panel.dispose();
    this.open.clear();
  }
}
