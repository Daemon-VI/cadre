// Today's usage against the tightest daily limit (AC-19.2): the largest of day_requests/rpd and
// day_tokens/tpd over every model the server reports. The engine keeps the quota book; this only
// reads `GET /api/v1/quota` (ADR-024 — no second copy of the arithmetic that decides routing).
import type { QuotaRow } from "./api";
import { duration, int } from "./format";

export interface Tightest {
  fraction: number;
  provider: string;
  model: string;
  kind: "requests" | "tokens";
  used: number;
  limit: number;
  resetsInS: number | null;
}

export function tightestDailyUsage(rows: readonly QuotaRow[]): Tightest | undefined {
  let best: Tightest | undefined;
  for (const r of rows) {
    const limits = r.limits ?? {};
    const pairs: [Tightest["kind"], number, number | null | undefined][] = [
      ["requests", r.day_requests, limits.rpd],
      ["tokens", r.day_tokens, limits.tpd],
    ];
    for (const [kind, usedRaw, limit] of pairs) {
      if (typeof limit !== "number" || !(limit > 0)) continue; // no daily cap on this axis
      const used = typeof usedRaw === "number" && usedRaw > 0 ? usedRaw : 0;
      const fraction = used / limit;
      if (!best || fraction > best.fraction) {
        best = { fraction, provider: r.provider, model: r.model, kind, used, limit, resetsInS: r.resets_in_s ?? null };
      }
    }
  }
  return best;
}

export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

export type Level = "ok" | "warn" | "hot";

export function level(fraction: number): Level {
  return fraction >= 0.95 ? "hot" : fraction >= 0.8 ? "warn" : "ok";
}

export interface StatusText {
  text: string;
  tooltip: string;
  level: Level;
}

/** What the status-bar item says. */
export function statusText(t: Tightest | undefined, pendingApprovals: number): StatusText {
  const waiting = pendingApprovals > 0
    ? ` · ${pendingApprovals} approval${pendingApprovals === 1 ? "" : "s"}`
    : "";
  const waitingTip = pendingApprovals > 0
    ? `\n${pendingApprovals} approval${pendingApprovals === 1 ? " is" : "s are"} waiting for you (Cadre: Review pending approvals).`
    : "";
  if (!t) {
    return {
      text: `$(pulse) Cadre${waiting}`,
      tooltip: `Cadre: no model with a daily limit is configured yet.${waitingTip}`,
      level: "ok",
    };
  }
  const reset = t.resetsInS === null ? "" : ` Resets in ${duration(t.resetsInS)}.`;
  return {
    text: `$(pulse) Cadre ${percent(t.fraction)}${waiting}`,
    tooltip: `Cadre: today's usage against the tightest daily limit — ${t.provider}/${t.model}, `
      + `${int(t.used)} of ${int(t.limit)} ${t.kind} (${percent(t.fraction)}).${reset}${waitingTip}`,
    level: level(t.fraction),
  };
}
