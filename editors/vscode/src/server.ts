// Finding or starting the local server (ADR-029, AC-19.1): health on the configured port; if
// nothing answers, `cadre serve` (PATH) or `uvx cadre-ai serve`, detached (launch.ts), then poll
// health until it is up. Nothing is started at activation — only when a view or command needs it.
import * as path from "node:path";
import * as vscode from "vscode";
import type { CadreClient } from "./api";
import { describeInvocation, findOnPath, serverInvocation } from "./cli";
import { launchDetached, waitFor } from "./launch";

const UV_DOCS = "https://docs.astral.sh/uv/";

export class Server {
  private pending: Promise<boolean> | undefined;

  constructor(
    private readonly client: CadreClient,
    private readonly port: () => number,
    private readonly storageDir: string,
    private readonly out: vscode.OutputChannel,
  ) {}

  get logPath(): string {
    return path.join(this.storageDir, "server.log");
  }

  async isUp(): Promise<boolean> {
    return (await this.client.health()) !== undefined;
  }

  /** Make sure a server answers, starting one if the settings allow. Concurrent callers share one attempt. */
  ensure(): Promise<boolean> {
    return this.once(async () => {
      if (await this.isUp()) return true;
      const auto = vscode.workspace.getConfiguration("cadre").get<boolean>("autoStart", true);
      if (!auto) {
        const pick = await vscode.window.showInformationMessage(
          `No Cadre server answers on 127.0.0.1:${this.port()}.`, "Start server");
        if (pick !== "Start server") return false;
      }
      return this.spawnAndWait();
    });
  }

  /** Explicit start (the "Start server" command): no autoStart question. */
  start(): Promise<boolean> {
    return this.once(async () => ((await this.isUp()) ? true : this.spawnAndWait()));
  }

  private once(fn: () => Promise<boolean>): Promise<boolean> {
    if (!this.pending) {
      this.pending = fn().finally(() => {
        this.pending = undefined;
      });
    }
    return this.pending;
  }

  private async spawnAndWait(): Promise<boolean> {
    const port = this.port();
    const inv = serverInvocation(port, (name) => findOnPath(name));
    if (!inv) {
      const pick = await vscode.window.showErrorMessage(
        "Cadre could not find `cadre` or `uvx` on PATH, so it cannot start its server. Install uv "
        + "(then `uvx cadre-ai` works with nothing else installed) and reload the window.",
        "Install uv");
      if (pick) await vscode.env.openExternal(vscode.Uri.parse(UV_DOCS));
      return false;
    }
    const shown = describeInvocation(inv);
    this.out.appendLine(`[server] nothing answered on 127.0.0.1:${port}; starting: ${shown}`);

    let exitCode: () => number | null | undefined = () => -1;
    try {
      const launched = launchDetached(inv, this.logPath);
      exitCode = () => launched.exitCode();
    } catch (e) {
      this.out.appendLine(`[server] could not start ${shown}: ${(e as Error).message}`);
    }

    // uvx may need to download Python and the package on first use, so it gets longer.
    const limitMs = inv.via === "uvx" ? 120_000 : 30_000;
    const ok = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Cadre: starting the server (${shown})…` },
      () => waitFor(() => this.isUp(), { limitMs, stop: () => exitCode() !== undefined }));

    if (ok) {
      this.out.appendLine(`[server] up on 127.0.0.1:${port}`);
      return true;
    }
    const code = exitCode();
    const why = code !== undefined ? `it exited (code ${code})` : `it did not answer within ${limitMs / 1000} s`;
    this.out.appendLine(`[server] ${shown}: ${why}`);
    const pick = await vscode.window.showErrorMessage(
      `Cadre: the server did not start — ${why}. If port ${port} is taken, change the setting cadre.port.`,
      "Open server log", "Install uv");
    if (pick === "Open server log") {
      await vscode.window.showTextDocument(vscode.Uri.file(this.logPath), { preview: true });
    } else if (pick === "Install uv") {
      await vscode.env.openExternal(vscode.Uri.parse(UV_DOCS));
    }
    return false;
  }
}
