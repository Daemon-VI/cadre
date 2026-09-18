// The Runs tree (AC-19.2): the server's most recent runs with a status icon each. Opening the
// view makes sure a server answers (starting one if `cadre.autoStart` allows); after that the
// poller feeds it, and it only redraws when an id, status or call count changed.
import * as vscode from "vscode";
import type { CadreClient, RunRow } from "./api";
import { ACTIVE, brief, int, RESUMABLE, statusIcon } from "./format";
import type { Server } from "./server";

export class RunNode {
  constructor(readonly run: RunRow) {}
}

function signature(rows: readonly RunRow[]): string {
  return rows.map((r) => `${r.id}:${r.status}:${r.calls}`).join("|");
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export class RunsProvider implements vscode.TreeDataProvider<RunNode>, vscode.Disposable {
  private rows: RunRow[] = [];
  private sig = "";
  private readonly changed = new vscode.EventEmitter<RunNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(
    private readonly client: CadreClient,
    private readonly server: Server,
    private readonly out: vscode.OutputChannel,
  ) {}

  dispose(): void {
    this.changed.dispose();
  }

  get current(): readonly RunRow[] {
    return this.rows;
  }

  /** From the poller: redraw only if something visible changed. */
  update(rows: RunRow[]): void {
    const s = signature(rows);
    if (s === this.sig) return;
    this.rows = rows;
    this.sig = s;
    this.changed.fire(undefined);
  }

  refresh(): void {
    this.sig = "";
    this.changed.fire(undefined);
  }

  async getChildren(element?: RunNode): Promise<RunNode[]> {
    if (element) return [];
    try {
      if (!(await this.server.ensure())) {
        this.rows = [];
        this.sig = "";
        return [];
      }
      this.rows = await this.client.runs(50);
      this.sig = signature(this.rows);
    } catch (e) {
      this.out.appendLine(`[runs] ${(e as Error).message}`);
      void vscode.window.showErrorMessage(`Cadre: could not list runs — ${(e as Error).message}`);
      return [];
    }
    return this.rows.map((r) => new RunNode(r));
  }

  getTreeItem(node: RunNode): vscode.TreeItem {
    const r = node.run;
    const item = new vscode.TreeItem(brief(r.goal, 70) || r.id, vscode.TreeItemCollapsibleState.None);
    item.id = r.id;
    item.description = `${r.org} · ${r.status}`;
    const { icon, color } = statusIcon(r.status);
    item.iconPath = new vscode.ThemeIcon(icon, color ? new vscode.ThemeColor(color) : undefined);
    const tokens = (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0);
    item.tooltip = `${r.goal}\n\n${r.org} · ${r.status}\n${r.id}\nstarted ${when(r.created)}\n`
      + `${int(r.calls)} model calls · ${int(tokens)} tokens${r.error ? `\n\n${r.error}` : ""}`;
    const flags = [
      ACTIVE.has(r.status) ? ":active" : "",
      RESUMABLE.has(r.status) ? ":resumable" : "",
      r.status === "waiting" ? ":waiting" : "",
    ].join("");
    item.contextValue = `cadreRun${flags}`;
    item.command = { command: "cadre.openRun", title: "Open run", arguments: [r.id] };
    return item;
  }
}
