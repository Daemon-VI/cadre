import assert from "node:assert/strict";
import { test } from "node:test";
import type { RunDetail, RunEvent } from "../../src/api";
import { brief, describeEvent, duration, hasUsableProvider, int, offerStyle, statusIcon, toPanelRun } from "../../src/format";

const ev = (kind: string, data: Record<string, unknown>, agent: string | null = "builder"): RunEvent =>
  ({ seq: 3, ts: 1_700_000_000, kind, agent, data });

test("agent.call reads like the dashboard", () => {
  const v = describeEvent(ev("agent.call", { model: "llama-3.3-70b", tokens_in: 1234, tokens_out: 56, tools: ["write_file"], independent: false, waited: 4 }));
  assert.equal(v.text, "llama-3.3-70b · 1,234→56 tokens · calls write_file · NOT independent (no other model family available) · waited 4s for quota");
  assert.equal(v.tone, "warn");
  assert.equal(v.agent, "builder");
});

test("run events are attributed to cadre when no agent is set", () => {
  const v = describeEvent(ev("run.started", { org: "decision-board", models: ["demo/a", "demo/b"] }, null));
  assert.equal(v.agent, "cadre");
  assert.equal(v.text, "started decision-board · models: demo/a, demo/b");
});

test("run.forecast reads as a headline, not raw JSON", () => {
  const v = describeEvent(ev("run.forecast", { verdict: "fits_now", headline: "fits now", requests_left: null, reasons: [] }, null));
  assert.equal(v.text, "forecast: fits now");
  assert.equal(v.tone, "quiet");
  assert.equal(describeEvent(ev("run.forecast", { verdict: "needs_days", headline: "needs 2 days", reasons: ["tpd"] })).tone, "warn");
  assert.equal(describeEvent(ev("run.forecast", { error: "no history" })).text, "forecast unavailable: no history");
});

test("run.finished tone follows the status", () => {
  assert.equal(describeEvent(ev("run.finished", { status: "succeeded" })).tone, "good");
  const failed = describeEvent(ev("run.finished", { status: "failed", error: "boom" }));
  assert.equal(failed.text, "finished: failed — boom");
  assert.equal(failed.tone, "warn");
});

test("approval.requested carries the approval id and the prompt as detail", () => {
  const v = describeEvent(ev("approval.requested", { id: "abc123", kind: "exec", prompt: "Checks:\n  tests: pytest -q" }));
  assert.equal(v.approvalId, "abc123");
  assert.equal(v.text, "waiting for permission to run code");
  assert.equal(v.detail, "Checks:\n  tests: pytest -q");
});

test("long text goes to detail; the line stays short", () => {
  const long = "x".repeat(500);
  const v = describeEvent(ev("agent.answer", { text: long }));
  assert.ok(v.text.length <= 140);
  assert.equal(v.detail, long);
});

test("check, review and vote events", () => {
  const check = describeEvent(ev("check.finished", { name: "tests", passed: false, seconds: 2.5, output_tail: "1 failed" }));
  assert.equal(check.text, "check tests failed in 2.5s");
  assert.equal(check.tone, "bad");
  assert.equal(check.detail, "1 failed");
  const review = describeEvent(ev("review.round", {
    round: 2, approved: false, checks: [{ name: "tests", passed: true }],
    verdicts: [{ reviewer: "r1", approve: false, issues: 2 }], gated_by_checks: false,
  }));
  assert.equal(review.text, "review round 2: changes requested · checks: tests ✓ · reviewers: r1 requests changes (2 issues)");
  const tally = describeEvent(ev("council.tally", { rule: "majority", counts: { yes: 2, no: 1 }, winner: "yes", decided_by: "count", abstained: ["c"] }));
  assert.equal(tally.text, "vote (majority): yes: 2, no: 1 → yes decided by count · abstained: c");
});

test("unknown kinds and odd data never throw", () => {
  const v = describeEvent({ seq: 1, ts: 0, kind: "future.kind", data: { a: [1, 2] } } as RunEvent);
  assert.equal(v.text, '{"a":[1,2]}');
  const w = describeEvent({ seq: 1, ts: 0, kind: "plan.created", data: { tasks: "not a list" } } as unknown as RunEvent);
  assert.equal(w.text, "plan: 0 tasks");
  const x = describeEvent({ seq: 1, ts: 0, kind: "agent.call", data: null } as unknown as RunEvent);
  assert.match(x.text, /0→0 tokens/);
});

test("status icons", () => {
  assert.deepEqual(statusIcon("succeeded"), { icon: "pass", color: "testing.iconPassed" });
  assert.equal(statusIcon("running").icon, "sync~spin");
  assert.equal(statusIcon("waiting").icon, "bell-dot");
  assert.equal(statusIcon("parked").icon, "debug-pause");
  assert.equal(statusIcon("failed").color, "testing.iconFailed");
  assert.equal(statusIcon("whatever").icon, "circle-outline");
});

test("toPanelRun: project runs expose their branch; flags follow the status", () => {
  const detail: RunDetail = {
    id: "r1", org: "software-team", goal: "g", status: "waiting", created: 10, finished: null,
    options: { demo: true }, project_path: "C:\\repo", base: "0123456789abcdef", branch: "cadre/r1",
    totals: { calls: 3, prompt_tokens: 100, completion_tokens: 20 },
    files: [{ path: "a.py", versions: 2, agent: "builder" }],
    approvals: [{ id: "a", run_id: "r1", kind: "gate", prompt: "p", status: "pending", created: 1 },
      { id: "b", run_id: "r1", kind: "gate", prompt: "p", status: "approved", created: 1 }],
    summary: { unapproved: ["t2"] },
  };
  const p = toPanelRun(detail);
  assert.deepEqual(p.project, { path: "C:\\repo", base: "0123456789abcdef", branch: "cadre/r1" });
  assert.equal(p.demo, true);
  assert.equal(p.active, true);
  assert.equal(p.resumable, false);
  assert.equal(p.pendingApprovals, 1);
  assert.deepEqual(p.unapproved, ["t2"]);
  assert.deepEqual(p.totals, { calls: 3, promptTokens: 100, completionTokens: 20 });
  const plain = toPanelRun({ ...detail, project_path: null, status: "parked" });
  assert.equal(plain.project, null);
  assert.equal(plain.resumable, true);
});

test("hasUsableProvider mirrors the dashboard's demo default", () => {
  assert.equal(hasUsableProvider([]), false);
  assert.equal(hasUsableProvider([{ id: "groq", local: false, key: "missing", models: [{}] }]), false);
  assert.equal(hasUsableProvider([{ id: "groq", local: false, key: "keyring", models: [] }]), false);
  assert.equal(hasUsableProvider([{ id: "groq", local: false, key: "keyring", models: [{}] }]), true);
  assert.equal(hasUsableProvider([{ id: "ollama", local: true, key: "missing", models: [{}] }]), true);
});

test("small formatters", () => {
  assert.equal(brief("a  b\n c", 10), "a b c");
  assert.equal(brief("abcdef", 4), "abc…");
  assert.equal(int(1234567.4), "1,234,567");
  assert.equal(int("x"), "0");
  assert.equal(duration(45), "45s");
  assert.equal(duration(600), "10m");
  assert.equal(duration(3 * 3600 + 4 * 60), "3h 04m");
});

test("an approval found by polling never opens a modal: a stray Enter must not allow execution", () => {
  // 2026-09-19: the exec modal popped up over another window with "Allow execution" as its default
  // button, and two demo runs were approved by keystrokes meant for something else
  assert.equal(offerStyle("exec", false), "notice");
  assert.equal(offerStyle("exec", true), "modal");  // the person clicked Review… or Decide…
  assert.equal(offerStyle("gate", false), "notice");
});

test("artifact.written names the file, not the absolute path under the user's home", () => {
  const v = describeEvent(ev("artifact.written", { path: "/home/someone/.cadre/runs/r/artifacts/plan.json", name: "plan.json" }, null));
  assert.equal(v.text, "saved plan.json");
});
