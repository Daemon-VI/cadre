import assert from "node:assert/strict";
import { test } from "node:test";
import type { QuotaRow } from "../../src/api";
import { level, statusText, tightestDailyUsage } from "../../src/usage";

const row = (model: string, dayReq: number, rpd: number | null, dayTok: number, tpd: number | null, resets = 3600): QuotaRow =>
  ({ provider: "groq", model, limits: { rpm: 30, rpd, tpm: 6000, tpd }, day_requests: dayReq, day_tokens: dayTok, resets_in_s: resets });

test("the tightest of requests/rpd and tokens/tpd across every model", () => {
  const t = tightestDailyUsage([
    row("a", 100, 1000, 10_000, 500_000), // 10% / 2%
    row("b", 5, 14_400, 90_000, 100_000), // 0.03% / 90%
    row("c", 700, 1000, 0, null),         // 70% / no cap
  ]);
  assert.equal(t?.model, "b");
  assert.equal(t?.kind, "tokens");
  assert.equal(t?.used, 90_000);
  assert.equal(t?.limit, 100_000);
  assert.ok(Math.abs((t?.fraction ?? 0) - 0.9) < 1e-9);
});

test("models without daily caps do not count; no caps at all gives undefined", () => {
  assert.equal(tightestDailyUsage([]), undefined);
  assert.equal(tightestDailyUsage([row("x", 50, null, 5000, null), row("y", 1, 0, 1, 0)]), undefined);
});

test("over the cap reads as over 100%, not clamped", () => {
  const t = tightestDailyUsage([row("a", 1100, 1000, 0, null)]);
  assert.equal(statusText(t, 0).text, "$(pulse) Cadre 110%");
});

test("status text, tooltip and level", () => {
  const t = tightestDailyUsage([row("llama", 850, 1000, 0, null, 5 * 3600 + 3 * 60)]);
  const s = statusText(t, 2);
  assert.equal(s.text, "$(pulse) Cadre 85% · 2 approvals");
  assert.match(s.tooltip, /groq\/llama, 850 of 1,000 requests \(85%\)\. Resets in 5h 03m\./);
  assert.match(s.tooltip, /2 approvals are waiting/);
  assert.equal(s.level, "warn");
  assert.equal(statusText(undefined, 0).text, "$(pulse) Cadre");
  assert.equal(level(0.5), "ok");
  assert.equal(level(0.8), "warn");
  assert.equal(level(0.95), "hot");
});
