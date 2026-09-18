// Starting the engine detached (ADR-029, AC-19.1), without `vscode` so it is testable on its own.
//
// Detached, so a run outlives the editor. Output goes to a log file rather than a pipe: a pipe
// breaks when the editor closes and would take the server down with it. `cadre serve` never
// prints the token (it prints the dashboard address without it).
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import type { Invocation } from "./cli";

export interface Launched {
  pid: number | undefined;
  /** undefined while running; the exit code (or -1 when it could not start) afterwards. */
  exitCode(): number | null | undefined;
  error(): string | undefined;
}

export function launchDetached(inv: Invocation, logPath: string, env: NodeJS.ProcessEnv = process.env,
  maxLogBytes = 1_000_000): Launched {
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  try {
    if (fs.statSync(logPath).size > maxLogBytes) fs.truncateSync(logPath, 0);
  } catch {
    // no log yet
  }
  const fd = fs.openSync(logPath, "a");
  let code: number | null | undefined;
  let err: string | undefined;
  try {
    const child = spawn(inv.command, inv.args, {
      detached: true,
      stdio: ["ignore", fd, fd],
      windowsHide: true,
      env: { ...env, PYTHONIOENCODING: "utf-8" },
    });
    child.on("exit", (c) => { code = c; });
    child.on("error", (e) => {
      code = -1;
      err = e.message;
    });
    child.unref();
    return { pid: child.pid, exitCode: () => code, error: () => err };
  } finally {
    fs.closeSync(fd); // the child holds its own handle
  }
}

/** Poll `check` until it is true, `stop` says give up, or `limitMs` passes. */
export async function waitFor(check: () => Promise<boolean>,
  opts: { limitMs: number; everyMs?: number; stop?: () => boolean }): Promise<boolean> {
  const deadline = Date.now() + opts.limitMs;
  while (Date.now() < deadline) {
    if (await check()) return true;
    if (opts.stop?.()) return false;
    await new Promise((r) => setTimeout(r, opts.everyMs ?? 500));
  }
  return false;
}
