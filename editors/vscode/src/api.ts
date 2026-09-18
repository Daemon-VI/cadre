// Client for the local Cadre API (`/api/v1`, pinned by tests/snapshots/openapi-v1.json in the repo).
// Pure Node (global fetch, no `vscode` import) so it is unit-tested directly.
//
// Token handling (ADR-010, ADR-029): the bearer token is read from CADRE_HOME/token by the
// `readToken` callback at the moment of each request, placed in the Authorization header, and
// dropped. It is never stored on the client, never put in a URL, and never put in an error
// message — errors carry the HTTP status and the server's `detail` text only.
import * as fs from "node:fs/promises";
import { SseParser } from "./sse";

// ------------------------------------------------------------------ shapes (see src/cadre/api.py)
export interface Health {
  ok: boolean;
  version: string;
}

export interface Totals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface RunRow extends Totals {
  id: string;
  org: string;
  goal: string;
  status: string;
  created: number;
  updated?: number | null;
  finished?: number | null;
  error?: string | null;
}

export interface Approval {
  id: string;
  run_id: string;
  kind: string; // exec | gate | question
  agent?: string | null;
  step?: string | null;
  prompt: string;
  status: string;
  answer?: string | null;
  created: number;
  decided?: number | null;
}

export interface RunFile {
  path: string;
  versions: number;
  bytes?: number;
  agent?: string | null;
}

export interface RunDetail {
  id: string;
  org: string;
  goal: string;
  status: string;
  options?: Record<string, unknown> | null;
  created: number;
  updated?: number | null;
  finished?: number | null;
  result?: string | null;
  summary?: { unapproved?: string[] } & Record<string, unknown> | null;
  error?: string | null;
  project_path?: string | null;
  base?: string | null;
  branch?: string | null;
  resume_at?: number | null;
  totals: Totals;
  files: RunFile[];
  approvals: Approval[];
  live?: boolean;
}

export interface RunEvent {
  seq: number;
  run_id?: string;
  ts: number;
  kind: string;
  agent?: string | null;
  step?: string | null;
  data: Record<string, unknown>;
}

export interface QuotaLimits {
  rpm?: number | null;
  rpd?: number | null;
  tpm?: number | null;
  tpd?: number | null;
}

export interface QuotaRow {
  provider: string;
  model: string;
  usable?: boolean;
  limits: QuotaLimits;
  day_requests: number;
  day_tokens: number;
  resets_in_s?: number | null;
}

export interface OrgSummary {
  name: string;
  source: string;
  valid: boolean;
  title?: string;
  description?: string;
  workflow?: string;
  checks?: string[];
  errors?: string[];
}

export interface Preset {
  id: string;
  label: string;
  free: boolean;
  local: boolean;
  signup?: string;
  note?: string;
}

export interface ProviderView {
  id: string;
  local: boolean;
  enabled?: boolean;
  key: string; // where the key is found, or "missing" — never the key itself
  models: unknown[];
}

export interface ForecastResult {
  verdict: string; // fits_now | fits_today | needs_days | cannot_run
  headline: string;
  lines: string[];
}

/**
 * What the extension may ask for when starting a run. `allow_exec` and `auto_approve` are left out
 * on purpose: checks always wait for the human's modal, and gates always ask.
 */
export interface StartRunBody {
  org: string;
  goal: string;
  project?: string;
  allow_dirty?: boolean;
  demo?: boolean;
}

export interface ForecastBody {
  org: string;
  goal: string;
  project?: string;
  demo?: boolean;
}

export interface StreamItem {
  event: string;
  data: unknown;
}

// ------------------------------------------------------------------ errors
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

/** Nothing answered on the port (connection refused, timeout). */
export class ServerUnreachable extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ServerUnreachable";
  }
}

export class TokenMissing extends Error {
  constructor(readonly path: string) {
    super(`No Cadre token at ${path}. The server writes it on first start (cadre serve); `
      + "if the server uses another CADRE_HOME, start VS Code with the same CADRE_HOME.");
    this.name = "TokenMissing";
  }
}

/** The server's error text, as the dashboard shows it (`app.js` api()). */
export function detailMessage(body: unknown, status: number): string {
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : body;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object" && Array.isArray((detail as { errors?: unknown }).errors)) {
    return (detail as { errors: unknown[] }).errors.map(String).join("\n");
  }
  if (Array.isArray(detail)) {
    // FastAPI request-validation errors: [{loc, msg, …}]
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join("\n");
  }
  if (status === 401) return "the server rejected the token (is it running with another CADRE_HOME?)";
  if (status === 421) return "the server refused the Host header";
  return `HTTP ${status}`;
}

export async function readTokenFile(file: string): Promise<string> {
  let text: string;
  try {
    text = await fs.readFile(file, "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") throw new TokenMissing(file);
    throw e;
  }
  const token = text.trim();
  if (!token) throw new TokenMissing(file);
  return token;
}

// ------------------------------------------------------------------ client
export interface ClientOptions {
  /** `http://127.0.0.1:<port>` — read on every call so a changed setting applies at once. */
  baseUrl: () => string;
  readToken: () => Promise<string>;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

const enc = encodeURIComponent;

export class CadreClient {
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;

  constructor(private readonly opts: ClientOptions) {
    this.fetchImpl = opts.fetch ?? fetch;
    this.timeoutMs = opts.timeoutMs ?? 15_000;
  }

  /** `GET /api/v1/health` — the one call without a token. Undefined when no Cadre answers. */
  async health(timeoutMs = 2_000): Promise<Health | undefined> {
    try {
      const res = await this.fetchImpl(`${this.opts.baseUrl()}/api/v1/health`, { signal: AbortSignal.timeout(timeoutMs) });
      if (!res.ok) return undefined;
      const body = (await res.json()) as Partial<Health>;
      return body && body.ok === true ? { ok: true, version: String(body.version ?? "") } : undefined;
    } catch {
      return undefined;
    }
  }

  orgs(): Promise<OrgSummary[]> { return this.call("GET", "/orgs"); }
  presets(): Promise<{ presets: Preset[] }> { return this.call("GET", "/presets"); }
  providers(): Promise<ProviderView[]> { return this.call("GET", "/providers"); }
  quota(): Promise<QuotaRow[]> { return this.call("GET", "/quota"); }
  runs(limit = 50): Promise<RunRow[]> { return this.call("GET", `/runs?limit=${Math.trunc(limit)}`); }
  run(id: string): Promise<RunDetail> { return this.call("GET", `/runs/${enc(id)}`); }
  events(id: string, after = 0, limit = 2000): Promise<RunEvent[]> {
    return this.call("GET", `/runs/${enc(id)}/events?after=${Math.trunc(after)}&limit=${Math.trunc(limit)}`);
  }
  approvals(): Promise<Approval[]> { return this.call("GET", "/approvals?pending=true"); }
  forecast(body: ForecastBody): Promise<ForecastResult> { return this.call("POST", "/forecast", body); }
  cancel(id: string): Promise<{ cancelled: boolean }> { return this.call("POST", `/runs/${enc(id)}/cancel`); }
  resume(id: string): Promise<{ id: string; resumed: boolean }> { return this.call("POST", `/runs/${enc(id)}/resume`); }

  startRun(body: StartRunBody): Promise<{ id: string }> {
    // Only the fields StartRunBody allows go on the wire, whatever the caller's object carries.
    const wire: StartRunBody = { org: body.org, goal: body.goal };
    if (body.project !== undefined) wire.project = body.project;
    if (body.allow_dirty) wire.allow_dirty = true;
    if (body.demo) wire.demo = true;
    return this.call("POST", "/runs", wire);
  }

  decide(approvalId: string, approve: boolean, answer = ""): Promise<{ id: string; approved: boolean }> {
    return this.call("POST", `/approvals/${enc(approvalId)}`, { approve, answer });
  }

  /**
   * Follow `GET /api/v1/runs/{id}/stream?after=N`. Yields each event's parsed JSON; the final
   * `end` item carries `{status}`. Ends when the server closes the stream or `signal` aborts.
   */
  async *stream(id: string, after: number, signal?: AbortSignal): AsyncGenerator<StreamItem> {
    const res = await this.send("GET", `/runs/${enc(id)}/stream?after=${Math.trunc(after)}`, undefined, signal, false);
    if (!res.body) throw new ApiError("the event stream has no body", res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        for (const msg of parser.push(decoder.decode(value, { stream: true }))) {
          let data: unknown;
          try {
            data = JSON.parse(msg.data);
          } catch {
            continue; // a malformed frame is skipped, not fatal
          }
          yield { event: msg.event, data };
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  // ---------------------------------------------------------------- plumbing
  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await this.send(method, path, body);
    const type = res.headers.get("content-type") ?? "";
    return (type.includes("json") ? await res.json() : await res.text()) as T;
  }

  private async send(method: string, path: string, body: unknown, signal?: AbortSignal,
    timeout = true): Promise<Response> {
    const token = await this.opts.readToken();
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const signals = [signal, timeout ? AbortSignal.timeout(this.timeoutMs) : undefined]
      .filter((s): s is AbortSignal => s !== undefined);
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.opts.baseUrl()}/api/v1${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: signals.length ? AbortSignal.any(signals) : undefined,
      });
    } catch (e) {
      if (signal?.aborted) throw e; // the caller cancelled; let AbortError through
      throw new ServerUnreachable(`no Cadre server answered at ${this.opts.baseUrl()} (${errorName(e)})`);
    }
    if (!res.ok) {
      let data: unknown;
      try {
        const type = res.headers.get("content-type") ?? "";
        data = type.includes("json") ? await res.json() : await res.text();
      } catch {
        data = undefined;
      }
      throw new ApiError(detailMessage(data, res.status), res.status);
    }
    return res;
  }
}

function errorName(e: unknown): string {
  const cause = e && typeof e === "object" ? (e as { cause?: { code?: unknown } }).cause : undefined;
  if (cause && typeof cause.code === "string") return cause.code;
  return e instanceof Error ? e.name : "error";
}
