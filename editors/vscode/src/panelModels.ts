// View models for the panel webviews (extension 1.4.0). Pure functions from API shapes to the plain
// data a panel renders — no `vscode` import, so every one is unit-tested directly. The webview never
// sees an API response: only what these return.
import type { Approval, MemoryList, OrgSummary, ProviderView, QuotaRow, UsageRow } from "./api";
import { duration, hasUsableProvider, int } from "./format";
import type {
  ApprovalsView, LedgerRow, MemoryItem, MemoryView, MeterView, OrgsView, StartView, UsageModelRow, UsageView,
} from "./protocol";
import { level, percent } from "./usage";

// ------------------------------------------------------------------ usage
/**
 * One meter. The status bar's thresholds (usage.level: 80% warn, 95% hot) so the two never
 * disagree. Severity is carried by a word as well as the fill colour (never colour alone).
 */
export function meter(used: number, cap: number | null | undefined, unit: string): MeterView {
  const n = Math.max(0, Number(used) || 0);
  if (!cap || cap <= 0) {
    return { used: n, cap: null, share: null, level: "none", text: `${int(n)} ${unit} · no daily cap`, state: "" };
  }
  const share = n / cap;
  const lv = level(share);
  return {
    used: n,
    cap,
    share,
    level: lv,
    text: `${int(n)} / ${int(cap)} ${unit} · ${percent(share)}`,
    state: lv === "hot" ? "at the cap" : lv === "warn" ? "near the cap" : "",
  };
}

function byName<T extends { provider: string; model: string }>(a: T, b: T): number {
  return a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model);
}

export function usageModel(quota: readonly QuotaRow[], ledger: readonly UsageRow[], days: number): UsageView {
  // A stable order (usable first, then by name) so a panel that refreshes itself doesn't reshuffle.
  const models: UsageModelRow[] = quota.map((q) => ({
    provider: q.provider,
    model: q.model,
    usable: q.usable !== false,
    requests: meter(q.day_requests, q.limits?.rpd, "requests"),
    tokens: meter(q.day_tokens, q.limits?.tpd, "tokens"),
    resets: q.resets_in_s ? `resets in ${duration(q.resets_in_s)}` : "",
  })).sort((a, b) => Number(b.usable) - Number(a.usable) || byName(a, b));

  const rows: LedgerRow[] = ledger.map((r) => ({
    day: r.day,
    provider: r.provider,
    model: r.model,
    requests: Number(r.requests) || 0,
    rpd: r.rpd ?? null,
    tokens: Number(r.tokens) || 0,
    tpd: r.tpd ?? null,
    share: r.share ?? null,
    nextReset: r.next_reset ?? "",
  })).sort((a, b) => b.day.localeCompare(a.day) || byName(a, b));

  return {
    days,
    models,
    ledger: rows,
    totals: rows.reduce((t, r) => ({ requests: t.requests + r.requests, tokens: t.tokens + r.tokens }),
      { requests: 0, tokens: 0 }),
  };
}

// ------------------------------------------------------------------ approvals
export function approvalLabel(kind: string): string {
  switch (kind) {
    case "exec": return "Run code";
    case "gate": return "Gate";
    case "question": return "Question";
    case "memory": return "Remember this fact?";
    default: return kind || "Approval";
  }
}

export function approvalsModel(items: readonly Approval[]): ApprovalsView {
  return {
    items: [...items]
      .sort((a, b) => a.created - b.created) // oldest first: it has waited longest
      .map((a) => ({
        id: a.id,
        runId: a.run_id,
        kind: a.kind,
        label: approvalLabel(a.kind),
        agent: a.agent ?? "",
        prompt: a.prompt ?? "",
        created: a.created,
      })),
  };
}

// ------------------------------------------------------------------ new-run form
export function startModel(orgs: readonly OrgSummary[], providers: readonly ProviderView[] | undefined,
  folders: readonly { name: string }[], trusted: boolean, prefillOrg = ""): StartView {
  const valid = orgs.filter((o) => o.valid)
    .sort((a, b) => Number(b.source === "yours") - Number(a.source === "yours") || a.name.localeCompare(b.name))
    .map((o) => ({
      name: o.name,
      source: o.source,
      workflow: o.workflow ?? "",
      description: o.description ?? "",
      checks: o.checks ?? [],
    }));
  return {
    orgs: valid,
    folders: folders.map((f, index) => ({ index, name: f.name })),
    trusted,
    // No provider list (an old server) is not "no provider": don't force the demo on a guess.
    hasProvider: providers === undefined ? true : hasUsableProvider(providers),
    prefillOrg: valid.some((o) => o.name === prefillOrg) ? prefillOrg : "",
  };
}

// ------------------------------------------------------------------ memory
/** global first, then teams, then projects; alphabetical inside each. */
export function scopeOrder(a: string, b: string): number {
  const rank = (s: string) => (s === "global" ? 0 : s.startsWith("team:") ? 1 : 2);
  return rank(a) - rank(b) || a.localeCompare(b);
}

/**
 * `pending` is the ids of approvals still waiting. A proposal whose approval is not among them is
 * stale: Decide… would only say "already decided", so it is offered for deletion instead. Without
 * `pending` (unknown), no proposal is called stale.
 */
export function memoryModel(list: MemoryList, pending?: ReadonlySet<string>): MemoryView {
  const groups = new Map<string, MemoryItem[]>();
  for (const e of list.entries ?? []) {
    const approval = e.approved ? "" : (e.approval ?? "");
    const stale = !e.approved && pending !== undefined && !pending.has(approval);
    const item: MemoryItem = {
      id: e.id,
      scope: e.scope,
      text: e.text,
      tags: e.tags ?? [],
      source: e.source ?? "",
      date: e.date ?? "",
      approved: !!e.approved,
      pinned: !!e.pinned,
      private: !!e.private,
      approvalId: stale ? "" : approval,
      stale,
    };
    const g = groups.get(e.scope);
    if (g) g.push(item);
    else groups.set(e.scope, [item]);
  }
  // Inside a scope: proposals waiting for you, then remembered facts (pinned first), then stale
  // proposals; newest first within each.
  const rank = (i: MemoryItem) => (i.stale ? 2 : i.approved ? 1 : 0);
  const within = (a: MemoryItem, b: MemoryItem) =>
    rank(a) - rank(b) || Number(b.pinned) - Number(a.pinned) || b.date.localeCompare(a.date);
  const scopes = [...new Set(["global", ...(list.scopes ?? []), ...groups.keys()])].sort(scopeOrder);
  return {
    groups: [...groups.entries()].sort(([a], [b]) => scopeOrder(a, b))
      .map(([scope, items]) => ({ scope, items: items.sort(within) })),
    skipped: list.skipped ?? [],
    scopes,
  };
}

// ------------------------------------------------------------------ organisations
function budgetRows(budget: Record<string, unknown> | undefined): [string, string][] {
  if (!budget) return [];
  return Object.entries(budget)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => [k.replace(/_/g, " "), typeof v === "number" ? int(v) : String(v)]);
}

export function orgsModel(orgs: readonly OrgSummary[]): OrgsView {
  return {
    orgs: [...orgs]
      .sort((a, b) => Number(b.source === "yours") - Number(a.source === "yours") || a.name.localeCompare(b.name))
      .map((o) => ({
        name: o.name,
        source: o.source,
        valid: !!o.valid,
        title: o.title ?? o.name,
        description: o.description ?? "",
        workflow: o.workflow ?? "",
        agents: (o.agents ?? []).map((a) => ({ id: a.id, role: a.role, tier: a.tier, tools: a.tools ?? [] })),
        checks: o.checks ?? [],
        budget: budgetRows(o.budget),
        errors: o.errors ?? [],
      })),
  };
}
