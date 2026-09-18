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
