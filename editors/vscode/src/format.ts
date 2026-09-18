// Turning API rows into text: run statuses, event lines, the webview's run summary. Pure (no
// `vscode`), mirrors `describe()` in src/cadre/web/app.js so the editor and the dashboard say the
// same thing about the same event.
import type { ProviderView, RunDetail, RunEvent } from "./api";
import type { EventView, PanelRun, Tone } from "./protocol";

/** Statuses from src/cadre/store.py and runs.py. */
export const ACTIVE = new Set(["queued", "running", "waiting", "cancelling"]);
export const RESUMABLE = new Set(["interrupted", "failed", "stopped", "cancelled", "unapproved", "parked"]);
export const STREAM_END = new Set(["succeeded", "unapproved", "failed", "stopped", "rejected", "cancelled",
  "interrupted", "parked"]);

/** Codicon id and theme colour id for a run status in the Runs tree. */
export function statusIcon(status: string): { icon: string; color?: string } {
  switch (status) {
    case "queued": return { icon: "clock" };
    case "running": return { icon: "sync~spin", color: "charts.blue" };
    case "waiting": return { icon: "bell-dot", color: "charts.yellow" };
    case "cancelling": return { icon: "loading~spin", color: "charts.yellow" };
    case "parked": return { icon: "debug-pause", color: "charts.yellow" };
    case "succeeded": return { icon: "pass", color: "testing.iconPassed" };
    case "unapproved": return { icon: "warning", color: "charts.yellow" };
    case "failed": return { icon: "error", color: "testing.iconFailed" };
    case "rejected": return { icon: "close", color: "testing.iconFailed" };
    case "stopped": return { icon: "debug-stop", color: "charts.yellow" };
    case "interrupted": return { icon: "debug-disconnect", color: "charts.yellow" };
    case "cancelled": return { icon: "circle-slash" };
    default: return { icon: "circle-outline" };
  }
}

export function brief(value: unknown, n: number): string {
  const s = str(value).replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, Math.max(0, n - 1)) + "…" : s;
}

export function int(value: unknown): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : Number(value) || 0;
  return Math.round(n).toLocaleString("en-US");
}

/** 45s · 12m · 3h 04m */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

function str(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : typeof v === "object" ? JSON.stringify(v) : String(v);
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

type Line = { text: string; tone: Tone; detail?: string };

function line(text: string, tone: Tone = "", detail?: string): Line {
  return detail ? { text, tone, detail } : { text, tone };
}

/** One event → one line of plain text, a tone, and optional detail text. */
export function describeEvent(e: RunEvent): EventView {
  const d = obj(e.data);
  const l = describeData(e.kind, d);
  const view: EventView = {
    seq: e.seq,
    ts: e.ts,
    kind: e.kind,
    agent: e.agent ?? (e.kind.startsWith("run") ? "cadre" : ""),
    text: l.text,
    tone: l.tone,
  };
  if (l.detail) view.detail = l.detail;
  if (e.kind === "approval.requested" && typeof d.id === "string") view.approvalId = d.id;
  return view;
}

function describeData(kind: string, d: Record<string, unknown>): Line {
  switch (kind) {
    case "run.started":
      return line(`started ${str(d.org)}${d.resumed ? " (resumed)" : ""} · models: ${list(d.models).map(str).join(", ")}`);
    case "run.finished":
      return line(`finished: ${str(d.status)}${d.error ? " — " + str(d.error) : ""}`, d.status === "succeeded" ? "good" : "warn");
    case "run.parked": {
      const blocks = list(d.blocks).map((b) => {
        const o = obj(b);
        return `${str(o.model)}: ${str(o.why)} (frees in ${str(o.frees_in)})`;
      });
      return line(`parked until ${str(d.resume_at_ist)} — daily limits`, "warn", blocks.join("\n") || undefined);
    }
    case "run.forecast":
      return d.error
        ? line(`forecast unavailable: ${str(d.error)}`, "quiet")
        : line(`forecast: ${str(d.headline)}`, d.verdict === "cannot_run" || d.verdict === "needs_days" ? "warn" : "quiet",
          list(d.reasons).map(str).join("\n") || undefined);
    case "run.crashed":
      return line("internal error", "bad", str(d.trace) || undefined);
    case "project.worktree":
      return line(`worktree ${str(d.how)} on ${str(d.branch)} from ${str(d.base)}`, "quiet");
    case "project.commit":
      return line(`committed ${str(d.sha)}: ${str(d.message)}`);
    case "project.commit_failed":
      return line(`commit failed: ${str(d.error)}`, "bad");
    case "project.cleaned":
      return line(`worktree removed; branch ${str(d.branch_kept)} kept`, "quiet");
    case "privacy.excluded":
      return line(`private run — excluded ${list(d.models).map((m) => str(obj(m).model)).join(", ")}`, "quiet");
    case "step.started":
      return line(`${str(d.type)} step${d.title ? ` “${str(d.title)}”` : ""} started`, "quiet");
    case "step.finished":
      return line(`${str(d.type)} step finished${d.approved === false ? " — NOT approved" : d.approved ? " — approved" : ""}`,
        d.approved === false ? "warn" : "quiet");
    case "step.reused":
      return line(`${str(d.type)} step reused from the earlier attempt`, "quiet");
    case "agent.start":
      return line(`task: ${brief(d.task, 220)}`, "quiet", str(d.task).length > 220 ? str(d.task) : undefined);
    case "agent.call": {
      const bits = [`${str(d.model)} · ${int(d.tokens_in)}→${int(d.tokens_out)} tokens`];
      const tools = list(d.tools).map(str);
      if (tools.length) bits.push(`calls ${tools.join(", ")}`);
      if (d.independent === true) bits.push("independent of builder");
      if (d.independent === false) bits.push("NOT independent (no other model family available)");
      if (d.waited) bits.push(`waited ${str(d.waited)}s for quota`);
      return line(bits.join(" · "), d.independent === false ? "warn" : "quiet");
    }
    case "agent.tool":
      return line(`${str(d.tool)} ${brief(d.args, 160)}`, d.ok ? "" : "warn", str(d.result) || undefined);
    case "agent.answer":
      return line(brief(d.text, 140) || "(empty answer)", "", str(d.text) || undefined);
    case "agent.repair":
      return line(`reply was not valid JSON (${str(d.error)}) — asked once more`, "warn");
    case "agent.invalid":
      return line(`reply still unusable: ${str(d.error)}`, "bad");
    case "route.wait":
      return line(`waiting ${str(d.seconds)}s for ${str(d.model)}: ${str(d.reason)}`, "warn");
    case "route.fallback":
      return line(`fallback — ${str(d.note)}`, "warn");
    case "note":
      return line(`note: ${str(d.text)}`);
    case "file.written":
      return line(`wrote ${str(d.path)}`, "quiet");
    case "check.started":
      return line(`running check ${str(d.name)}: ${list(d.command).map(str).join(" ")}`, "quiet");
    case "check.finished":
      return line(`check ${str(d.name)} ${d.passed ? "passed" : "failed"} in ${str(d.seconds)}s${d.note ? " — " + str(d.note) : ""}`,
        d.passed ? "good" : "bad", str(d.output_tail) || undefined);
    case "review.round": {
      const checks = list(d.checks).map((c) => `${str(obj(c).name)} ${obj(c).passed ? "✓" : "✗"}`);
      const verdicts = list(d.verdicts).map((v) => {
        const o = obj(v);
        return `${str(o.reviewer)} ${o.approve ? "approves" : "requests changes"}${o.issues ? ` (${str(o.issues)} issues)` : ""}`;
      });
      const parts = [`review round ${str(d.round)}: ${d.approved ? "approved" : "changes requested"}`];
      if (checks.length) parts.push(`checks: ${checks.join(", ")}`);
      if (verdicts.length) parts.push(`reviewers: ${verdicts.join(", ")}`);
      if (d.gated_by_checks) parts.push("reviewers approved, but a failing check blocked it");
      return line(parts.join(" · "), d.approved ? "good" : "warn");
    }
    case "council.tally": {
      const counts = Object.entries(obj(d.counts)).map(([k, v]) => `${k}: ${str(v)}`).join(", ");
      const abstained = list(d.abstained).map(str);
      return line(`vote (${str(d.rule)}): ${counts} → ${str(d.winner)} decided by ${str(d.decided_by)}`
        + (abstained.length ? ` · abstained: ${abstained.join(", ")}` : ""));
    }
    case "plan.created": {
      const tasks = list(d.tasks).map((t) => {
        const o = obj(t);
        const after = list(o.depends_on).map(str);
        return `${str(o.id)} → ${str(o.assignee)}: ${str(o.title)}${after.length ? ` (after ${after.join(", ")})` : ""}`;
      });
      return line(`plan: ${tasks.length} task${tasks.length === 1 ? "" : "s"}`, "", tasks.join("\n") || undefined);
    }
    case "task.started":
      return line(`task ${str(d.task)} started: ${str(d.title)}`, "quiet");
    case "task.finished":
      return line(`task ${str(d.task)} finished${d.approved === false ? " — not approved" : ""}`, d.approved === false ? "warn" : "");
    case "task.failed":
      return line(`task ${str(d.task)} failed: ${str(d.error)}`, "bad");
    case "task.skipped":
      return line(`task ${str(d.task)} skipped: ${str(d.reason)}`, "warn");
    case "approval.requested": {
      const what = d.kind === "exec" ? "permission to run code" : d.kind === "question" ? "an answer" : "approval";
      return line(`waiting for ${what}`, "warn", str(d.prompt) || undefined);
    }
    case "approval.decided":
      return line(`${str(d.kind)} ${d.approved ? "approved" : "rejected"}${d.answer ? " — " + str(d.answer) : ""}`);
    case "approval.auto":
      return line(`${str(d.kind)} auto-approved: ${brief(d.prompt, 160)}`, "quiet");
    default:
      return line(brief(d, 200), "quiet");
  }
}

/** At least one provider that can answer: a local one, or one whose key was found (as app.js decides). */
export function hasUsableProvider(providers: readonly ProviderView[]): boolean {
  return providers.some((p) => Array.isArray(p.models) && p.models.length > 0 && (p.local || p.key !== "missing"));
}

/** The webview's view of a run: only what it draws, in plain values. */
export function toPanelRun(r: RunDetail, pendingApprovals?: number): PanelRun {
  const options = obj(r.options);
  const t = r.totals ?? { calls: 0, prompt_tokens: 0, completion_tokens: 0 };
  const pending = pendingApprovals ?? (r.approvals ?? []).filter((a) => a.status === "pending").length;
  return {
    id: r.id,
    org: r.org,
    goal: r.goal,
    status: r.status,
    demo: options.demo === true,
    created: r.created,
    finished: r.finished ?? null,
    error: r.error ?? null,
    result: r.result ?? null,
    resumeAt: r.resume_at ?? null,
    project: r.project_path && r.branch && r.base
      ? { path: r.project_path, base: r.base, branch: r.branch }
      : null,
    totals: { calls: t.calls ?? 0, promptTokens: t.prompt_tokens ?? 0, completionTokens: t.completion_tokens ?? 0 },
    files: (r.files ?? []).map((f) => ({ path: f.path, versions: f.versions, agent: f.agent ?? "" })),
    unapproved: list(obj(r.summary).unapproved).map(str),
    pendingApprovals: pending,
    active: ACTIVE.has(r.status),
    resumable: RESUMABLE.has(r.status),
  };
}
