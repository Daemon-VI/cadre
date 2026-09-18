// The run-detail webview (AC-19.2). The extension host follows the run's event stream and posts
// plain-text lines to the webview; the webview has no network access and never sees the token.
// Its CSP allows one nonce'd script from dist/webview and one stylesheet from media/.
import * as vscode from "vscode";
import type { CadreClient, RunEvent } from "./api";
import { ACTIVE, describeEvent, STREAM_END, toPanelRun } from "./format";
import { makeNonce, runPanelHtml } from "./html";
import { type EventView, type HostMessage, isWebviewMessage, type PanelRun } from "./protocol";

const MAX_EVENTS = 3000;

export interface PanelActions {
  review(runId: string): void;
  cancel(runId: string): void;
  resume(runId: string): void;
  approvals(runId: string): void;
  approval(approvalId: string): void;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(t); resolve(); }, { once: true });
  });
}

class RunPanel {
  private events: EventView[] = [];
  private cursor = 0;
  private run: PanelRun | null = null;
  private readonly stop = new AbortController();
  private followAgain = () => {};

  constructor(
    readonly runId: string,
    readonly panel: vscode.WebviewPanel,
    private readonly client: CadreClient,
    private readonly actions: PanelActions,
    private readonly out: vscode.OutputChannel,
    onDispose: () => void,
  ) {
    panel.onDidDispose(() => {
      this.stop.abort();
      onDispose();
    });
    panel.webview.onDidReceiveMessage((m: unknown) => this.onMessage(m));
    void this.loop();
  }

  /** Something changed from outside (an approval decided, a resume): reload and re-follow. */
  poke(): void {
    void this.loadRun();
    this.followAgain();
  }

  private post(msg: HostMessage): void {
    void this.panel.webview.postMessage(msg);
  }

  private onMessage(m: unknown): void {
    if (!isWebviewMessage(m)) return;
    switch (m.type) {
      case "ready":
        this.post({ type: "reset", run: this.run, events: this.events });
        break;
      case "review":
        this.actions.review(this.runId);
        break;
      case "cancel":
        this.actions.cancel(this.runId);
        break;
      case "resume":
        this.actions.resume(this.runId);
        break;
      case "approvals":
        this.actions.approvals(this.runId);
        break;
      case "approval":
        this.actions.approval(m.id);
        break;
    }
  }

  private async loadRun(): Promise<PanelRun | null> {
    try {
      const detail = await this.client.run(this.runId);
      this.run = toPanelRun(detail);
      this.panel.title = `Cadre · ${detail.org} · ${detail.status}`;
      this.post({ type: "run", run: this.run });
    } catch (e) {
      if (!this.stop.signal.aborted) this.post({ type: "notice", text: `Could not load the run: ${(e as Error).message}` });
    }
    return this.run;
  }

  private add(batch: RunEvent[]): void {
    if (!batch.length) return;
    const views = batch.filter((e) => e.seq > this.cursor).map(describeEvent);
    if (!views.length) return;
    this.cursor = Math.max(this.cursor, ...views.map((v) => v.seq));
    this.events.push(...views);
    if (this.events.length > MAX_EVENTS) this.events.splice(0, this.events.length - MAX_EVENTS);
    this.post({ type: "events", events: views });
  }

  /**
   * Follow the stream while the run can still produce events; when it ends (finished or parked),
   * check the status every 10 s so a resumed or un-parked run is followed again.
   */
  private async loop(): Promise<void> {
    const signal = this.stop.signal;
    await this.loadRun();
    let backoff = 1000;
    while (!signal.aborted) {
      const status = this.run?.status;
      if (status && !ACTIVE.has(status) && STREAM_END.has(status)) {
        // Catch up on everything not yet shown (a finished run opened fresh has all of it to
        // load), page by page, then wait for a change.
        try {
          for (let page = 0; page < 50 && !signal.aborted; page++) {
            const batch = await this.client.events(this.runId, this.cursor, 2000);
            this.add(batch);
            if (batch.length < 2000) break;
          }
        } catch {
          // offline; try again on the next round
        }
        await new Promise<void>((resolve) => {
          const t = setTimeout(resolve, 10_000);
          this.followAgain = () => { clearTimeout(t); resolve(); };
          signal.addEventListener("abort", () => { clearTimeout(t); resolve(); }, { once: true });
        });
        this.followAgain = () => {};
        await this.loadRun();
        continue;
      }
      try {
        for await (const item of this.client.stream(this.runId, this.cursor, signal)) {
          if (item.event === "end") break;
          const e = item.data as RunEvent;
          if (typeof e?.seq !== "number") continue;
          this.add([e]);
          if (["run.finished", "run.parked", "file.written", "agent.call", "approval.requested", "approval.decided"]
            .includes(e.kind)) void this.loadRun();
        }
        backoff = 1000;
      } catch (e) {
        if (signal.aborted) return;
        this.out.appendLine(`[run ${this.runId}] live feed: ${(e as Error).message}`);
        await sleep(backoff, signal);
        backoff = Math.min(backoff * 2, 15_000);
      }
      await this.loadRun();
    }
  }
}

export class RunPanels implements vscode.Disposable {
  private readonly open = new Map<string, RunPanel>();

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly client: CadreClient,
    private readonly actions: PanelActions,
    private readonly out: vscode.OutputChannel,
  ) {}

  show(runId: string): void {
    const existing = this.open.get(runId);
    if (existing) {
      existing.panel.reveal();
      return;
    }
    const dist = vscode.Uri.joinPath(this.extensionUri, "dist", "webview");
    const media = vscode.Uri.joinPath(this.extensionUri, "media");
    const panel = vscode.window.createWebviewPanel("cadre.run", `Cadre · ${runId}`, vscode.ViewColumn.Active, {
      enableScripts: true,
      enableCommandUris: false,
      enableFindWidget: true,
      localResourceRoots: [dist, media],
    });
    panel.iconPath = vscode.Uri.joinPath(media, "cadre.svg");
    const webview = panel.webview;
    webview.html = runPanelHtml({
      cspSource: webview.cspSource,
      nonce: makeNonce(),
      scriptUri: webview.asWebviewUri(vscode.Uri.joinPath(dist, "run.js")).toString(),
      styleUri: webview.asWebviewUri(vscode.Uri.joinPath(media, "run.css")).toString(),
    });
    this.open.set(runId, new RunPanel(runId, panel, this.client, this.actions, this.out, () => this.open.delete(runId)));
  }

  poke(runId?: string): void {
    for (const [id, p] of this.open) if (!runId || id === runId) p.poke();
  }

  dispose(): void {
    for (const p of [...this.open.values()]) p.panel.dispose();
    this.open.clear();
  }
}
