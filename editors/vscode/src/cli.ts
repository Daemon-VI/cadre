// Where the engine lives on this machine (ADR-026, ADR-029). Pure Node: no `vscode` import, so the
// unit tests can load it.
//
// The engine is `cadre` when it is on PATH, else `uvx cadre-ai`. Nothing is ever run through a
// shell: callers get an executable path and an argument vector for spawn/execFile/createTerminal.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export interface Invocation {
  command: string;  // absolute path of the executable
  args: string[];
  via: "cadre" | "uvx";
}

export type Which = (name: string) => string | undefined;

type Env = Record<string, string | undefined>;

/** Environment lookup that is case-insensitive on Windows, as the OS is. */
export function envGet(env: Env, key: string, platform: NodeJS.Platform = process.platform): string | undefined {
  if (platform !== "win32") return env[key];
  const hit = Object.keys(env).find((k) => k.toUpperCase() === key.toUpperCase());
  return hit === undefined ? undefined : env[hit];
}

function isExecutableFile(p: string): boolean {
  try {
    if (!fs.statSync(p).isFile()) return false;
    if (process.platform !== "win32") fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * First `name` on PATH. On Windows only `.exe` / `.com` count: `.cmd` / `.bat` would need a shell
 * to run (Node refuses to spawn them directly), and a shell is exactly what we avoid.
 */
export function findOnPath(
  name: string,
  env: Env = process.env,
  platform: NodeJS.Platform = process.platform,
  exists: (p: string) => boolean = isExecutableFile,
): string | undefined {
  const win = platform === "win32";
  const raw = envGet(env, "PATH", platform) ?? "";
  const join = win ? path.win32.join : path.posix.join;
  const exts = win ? [".exe", ".com"] : [""];
  for (const entry of raw.split(win ? ";" : ":")) {
    const dir = entry.trim().replace(/^"(.*)"$/, "$1");
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = join(dir, name + ext);
      if (exists(candidate)) return candidate;
    }
  }
  return undefined;
}

/** `cadre <sub…>` if `cadre` is on PATH, else `uvx cadre-ai <sub…>`, else undefined. */
export function cliInvocation(sub: string[], which: Which): Invocation | undefined {
  const cadre = which("cadre");
  if (cadre) return { command: cadre, args: [...sub], via: "cadre" };
  const uvx = which("uvx");
  if (uvx) return { command: uvx, args: ["cadre-ai", ...sub], via: "uvx" };
  return undefined;
}

export function serverInvocation(port: number, which: Which): Invocation | undefined {
  return cliInvocation(["serve", "--port", String(port)], which);
}

/** How an invocation reads to a person: `cadre serve --port 8765`, `uvx cadre-ai serve …`. */
export function describeInvocation(inv: Invocation): string {
  return [inv.via, ...inv.args].join(" ");
}

export function validPort(value: unknown): number | undefined {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 65535 ? n : undefined;
}

/** CADRE_HOME, as the engine resolves it (`config.Home`): the variable if set and non-empty, else ~/.cadre. */
export function cadreHome(env: Env = process.env, home: string = os.homedir()): string {
  const set = envGet(env, "CADRE_HOME");
  return set ? set : path.join(home, ".cadre");
}

export function tokenPath(env: Env = process.env, home: string = os.homedir()): string {
  return path.join(cadreHome(env, home), "token");
}

/** Preset ids are slugs (`api.SLUG`); anything else never reaches a command line. */
export function isPresetId(id: string): boolean {
  return /^[a-z][a-z0-9_-]{0,31}$/.test(id);
}
