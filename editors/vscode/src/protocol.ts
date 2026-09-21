// Messages between the extension host and the run-detail webview. Shared by both bundles, so this
// file imports nothing. The webview only ever receives these shapes — never the API token, never
// a URL that carries one.

export type Tone = "" | "quiet" | "warn" | "bad" | "good";

export interface EventView {
  seq: number;
  ts: number;          // seconds since the epoch
  kind: string;
  agent: string;
  text: string;        // plain text, inserted with textContent
  tone: Tone;
  detail?: string;     // longer plain text shown in a <details><pre>
  approvalId?: string; // set on approval.requested
}

export interface PanelRun {
  id: string;
  org: string;
  goal: string;
  status: string;
  demo: boolean;
  created: number;
  finished: number | null;
  error: string | null;
  result: string | null;
  resumeAt: number | null;
  project: { path: string; base: string; branch: string } | null;
  totals: { calls: number; promptTokens: number; completionTokens: number };
  files: { path: string; versions: number; agent: string }[];
  unapproved: string[];
  pendingApprovals: number;
  active: boolean;
  resumable: boolean;
}

export type HostMessage =
  | { type: "reset"; run: PanelRun | null; events: EventView[] }
  | { type: "run"; run: PanelRun }
  | { type: "events"; events: EventView[] }
  | { type: "notice"; text: string };

export type WebviewMessage =
  | { type: "ready" }
  | { type: "review" }
  | { type: "cancel" }
  | { type: "resume" }
  | { type: "approvals" }                // this run's pending approvals
  | { type: "approval"; id: string };    // one approval, from its timeline entry

/** The host trusts nothing a webview posts: only these exact shapes are acted on. */
export function isWebviewMessage(x: unknown): x is WebviewMessage {
  if (!x || typeof x !== "object") return false;
  const m = x as { type?: unknown; id?: unknown };
  switch (m.type) {
    case "ready":
    case "review":
    case "cancel":
    case "resume":
    case "approvals":
      return true;
    case "approval":
      return typeof m.id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(m.id);
    default:
      return false;
  }
}

// ================================================================== panels (extension 1.4.0)
// Five more webviews share one bundle (dist/webview/panels.js): usage, approvals, a new-run form,
// memory and organisations. Same rules as the run view: the host fetches, the webview only renders
// what it is posted, with textContent, and holds no token. Everything a panel may post is below,
// and `isPanelMessage` is the only door — the host drops anything else.

export type PanelKind = "usage" | "approvals" | "start" | "memory" | "orgs";
export const PANEL_KINDS: readonly PanelKind[] = ["usage", "approvals", "start", "memory", "orgs"];

export const USAGE_DAYS: readonly number[] = [1, 7, 14, 30];
export const GOAL_MAX = 4000;
export const MEMORY_TEXT_MAX = 400;     // the engine's limit (memory.MAX_LEN), after whitespace folds
const MEMORY_RAW_MAX = 2000;            // generous: the engine folds whitespace, then enforces 400
const ID = /^[A-Za-z0-9_-]{1,64}$/;
const ORG_NAME = /^[a-z][a-z0-9_-]{0,31}$/;                 // org.SLUG
const MEMORY_ID = /^m-[a-z0-9]{1,40}$/;                      // memory._new_id / _mk_id
/** memory.MemoryStore.path_for: global, a team (accounts.TEAM_ID) or a project (its root commit). */
export const MEMORY_SCOPE = /^(global|team:[a-z0-9][a-z0-9_-]{0,31}|project:[0-9a-f]{7,40})$/;

export type Level = "ok" | "warn" | "hot" | "none";

/** One quota meter: how much of a daily cap is used. `share` is 0..1, null when there is no cap. */
export interface MeterView {
  used: number;
  cap: number | null;
  share: number | null;
  level: Level;
  text: string;      // e.g. "24 / 1,000 requests · 2%" — the value is always shown as text
  state: string;     // "" | "near the cap" | "at the cap" — severity is never colour alone
}

export interface UsageModelRow {
  provider: string;
  model: string;
  usable: boolean;
  requests: MeterView;
  tokens: MeterView;
  resets: string;    // "resets in 3h 20m" | ""
}

export interface LedgerRow {
  day: string;
  provider: string;
  model: string;
  requests: number;
  rpd: number | null;
  tokens: number;
  tpd: number | null;
  share: number | null;
  nextReset: string;
}

export interface UsageView {
  days: number;
  models: UsageModelRow[];
  ledger: LedgerRow[];
  totals: { requests: number; tokens: number };
}

export interface ApprovalItem {
  id: string;
  runId: string;
  kind: string;      // exec | gate | question | memory
  label: string;     // "Run code" | "Gate" | "Question" | "Remember this fact?"
  agent: string;
  prompt: string;
  created: number;
}

export interface ApprovalsView {
  items: ApprovalItem[];
}

export interface StartOrg {
  name: string;
  source: string;
  workflow: string;
  description: string;
  checks: string[];
}

export interface StartView {
  orgs: StartOrg[];
  folders: { index: number; name: string }[];
  trusted: boolean;       // folder runs need a trusted workspace
  hasProvider: boolean;   // without a usable key only the offline demo can run
  prefillOrg: string;
}

export interface ForecastView {
  verdict: string;
  headline: string;
  lines: string[];
}

export interface MemoryItem {
  id: string;
  scope: string;
  text: string;
  tags: string[];
  source: string;
  date: string;
  approved: boolean;
  pinned: boolean;
  private: boolean;
  approvalId: string;     // set on a model's proposal that is still waiting
  /** A proposal whose approval is no longer pending (closed without a decision, or the database
   *  was reset while the memory file survived). No run will ever see it; it can only be deleted. */
  stale: boolean;
}

export interface MemoryView {
  groups: { scope: string; items: MemoryItem[] }[];
  skipped: string[];
  scopes: string[];
}

export interface OrgCard {
  name: string;
  source: string;         // "yours" | "template"
  valid: boolean;
  title: string;
  description: string;
  workflow: string;
  agents: { id: string; role: string; tier: string; tools: string[] }[];
  checks: string[];
  budget: [string, string][];
  errors: string[];
}

export interface OrgsView {
  orgs: OrgCard[];
}

export type PanelView =
  | { kind: "usage"; view: UsageView }
  | { kind: "approvals"; view: ApprovalsView }
  | { kind: "start"; view: StartView }
  | { kind: "memory"; view: MemoryView }
  | { kind: "orgs"; view: OrgsView };

export type PanelHostMessage =
  | { type: "view"; data: PanelView }
  | { type: "forecast"; forecast: ForecastView }
  | { type: "notice"; text: string; tone: Tone }
  | { type: "busy"; busy: boolean };

export type PanelMessage =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "days"; days: number }
  | { type: "decide"; id: string }            // the existing, reviewed offer flow decides — never the panel
  | { type: "openRun"; id: string }
  | { type: "forecast"; org: string; goal: string; folder: number; private: boolean }
  | { type: "start"; org: string; goal: string; folder: number; demo: boolean; private: boolean }
  | { type: "memoryAdd"; scope: string; text: string; pinned: boolean; private: boolean }
  | { type: "memoryRemove"; id: string }
  | { type: "openOrg"; name: string }
  | { type: "useOrg"; name: string };

const isStr = (v: unknown): v is string => typeof v === "string";
const isBool = (v: unknown): v is boolean => typeof v === "boolean";
/** A workspace folder by index (-1 = no folder). The webview never sends a filesystem path. */
const isFolder = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= -1 && (v as number) < 64;
const isGoal = (v: unknown): v is string => isStr(v) && v.trim().length > 0 && v.length <= GOAL_MAX;

/** What each kind of panel may ask for. A valid message outside its kind's list is dropped too. */
export const PANEL_ALLOWS: Readonly<Record<PanelKind, readonly PanelMessage["type"][]>> = {
  usage: ["ready", "refresh", "days"],
  approvals: ["ready", "refresh", "decide", "openRun"],
  start: ["ready", "refresh", "forecast", "start"],
  memory: ["ready", "refresh", "decide", "memoryAdd", "memoryRemove"],
  orgs: ["ready", "refresh", "openOrg", "useOrg"],
};

/** A message this kind of panel may send: the right shape, and one its kind uses. */
export function isAllowedPanelMessage(kind: PanelKind, x: unknown): x is PanelMessage {
  return isPanelMessage(x) && PANEL_ALLOWS[kind].includes(x.type);
}

/** The only door from a panel webview into the extension host. Anything else is dropped. */
export function isPanelMessage(x: unknown): x is PanelMessage {
  if (!x || typeof x !== "object" || Array.isArray(x)) return false;
  const m = x as Record<string, unknown>;
  switch (m.type) {
    case "ready":
    case "refresh":
      return true;
    case "days":
      return typeof m.days === "number" && USAGE_DAYS.includes(m.days);
    case "decide":
    case "openRun":
      return isStr(m.id) && ID.test(m.id);
    case "forecast":
      return isStr(m.org) && ORG_NAME.test(m.org) && isGoal(m.goal) && isFolder(m.folder) && isBool(m.private);
    case "start":
      return isStr(m.org) && ORG_NAME.test(m.org) && isGoal(m.goal) && isFolder(m.folder)
        && isBool(m.demo) && isBool(m.private);
    case "memoryAdd":
      return isStr(m.scope) && MEMORY_SCOPE.test(m.scope) && isStr(m.text) && m.text.trim().length > 0
        && m.text.length <= MEMORY_RAW_MAX && isBool(m.pinned) && isBool(m.private);
    case "memoryRemove":
      return isStr(m.id) && MEMORY_ID.test(m.id);
    case "openOrg":
    case "useOrg":
      return isStr(m.name) && ORG_NAME.test(m.name);
    default:
      return false;
  }
}
