// The panels' two trust boundaries, tested without VS Code: what a panel webview may post
// (protocol.ts isPanelMessage / isAllowedPanelMessage) and what the host turns API data into
// (panelModels.ts). The webview side runs in panels-dom.test.ts.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Approval, MemoryList, OrgSummary, ProviderView, QuotaRow, UsageRow } from "../../src/api";
import {
  approvalLabel, approvalsModel, memoryModel, meter, orgsModel, scopeOrder, startModel, usageModel,
} from "../../src/panelModels";
import { isAllowedPanelMessage, isPanelMessage, PANEL_ALLOWS, PANEL_KINDS } from "../../src/protocol";

// ------------------------------------------------------------------ what a webview may post
const VALID = [
  { type: "ready" },
  { type: "refresh" },
  { type: "days", days: 7 },
  { type: "decide", id: "a1b2c3" },
  { type: "openRun", id: "20260921-093000-abc123" },
  { type: "forecast", org: "decision-board", goal: "Pick one", folder: -1, private: false },
  { type: "start", org: "project-finisher", goal: "Make the tests pass", folder: 0, demo: false, private: true },
  { type: "memoryAdd", scope: "project:e863025677c9", text: "tests run with unittest", pinned: false, private: false },
  { type: "memoryAdd", scope: "team:eng", text: "x", pinned: true, private: true },
  { type: "memoryRemove", id: "m-3a038063" },
  { type: "openOrg", name: "software-team" },
  { type: "useOrg", name: "decision-board" },
];

test("every documented panel message is accepted", () => {
  for (const m of VALID) assert.ok(isPanelMessage(m), JSON.stringify(m));
});

test("anything else is dropped: unknown types, non-objects, bad fields", () => {
  const bad: unknown[] = [
    null, undefined, "ready", 42, [], [{ type: "ready" }],
    { type: "approve", id: "a1" },                        // there is no way to approve from a panel
    { type: "decideAndApprove", id: "a1" },
    { type: "days", days: 3 }, { type: "days", days: "7" },
    { type: "decide", id: "../approvals" }, { type: "decide", id: "" }, { type: "decide", id: "a".repeat(65) },
    { type: "openRun", id: "r1; rm -rf /" },
  ];
  for (const m of bad) assert.equal(isPanelMessage(m), false, JSON.stringify(m));
});

test("a run is started on a folder by index — a path from the webview is refused", () => {
  const base = { type: "start", org: "project-finisher", goal: "g", demo: false, private: false };
  for (const folder of ["C:\\\\Users\\\\someone\\\\repo", "/etc", -2, 0.5, 64, NaN, null]) {
    assert.equal(isPanelMessage({ ...base, folder }), false, `folder ${String(folder)}`);
  }
  assert.ok(isPanelMessage({ ...base, folder: -1 }));
  assert.ok(isPanelMessage({ ...base, folder: 63 }));
});

test("org names, goals and scopes are checked like the engine checks them", () => {
  const start = (o: object) => ({ type: "start", org: "decision-board", goal: "g", folder: -1, demo: true, private: false, ...o });
  for (const org of ["Decision-Board", "1team", "a b", "../orgs", "x".repeat(33), ""]) {
    assert.equal(isPanelMessage(start({ org })), false, `org ${org}`);
  }
  for (const goal of ["", "   ", "x".repeat(4001), 5]) assert.equal(isPanelMessage(start({ goal })), false);
  assert.ok(isPanelMessage(start({ goal: "x".repeat(4000) })));

  const add = (o: object) => ({ type: "memoryAdd", scope: "global", text: "a fact", pinned: false, private: false, ...o });
  for (const scope of ["../../token", "team:", "TEAM:eng", "project:xyz", "project:ABCDEF1", "global ", "local"]) {
    assert.equal(isPanelMessage(add({ scope })), false, `scope ${scope}`);
  }
  for (const text of ["", "  \n ", "x".repeat(2001)]) assert.equal(isPanelMessage(add({ text })), false);
  assert.equal(isPanelMessage(add({ pinned: "yes" })), false);
  for (const id of ["m-", "M-1", "x-1", "m-1/../2", "../m-1"]) {
    assert.equal(isPanelMessage({ type: "memoryRemove", id }), false, `id ${id}`);
  }
});

test("each kind of panel may only send the messages it uses", () => {
  const start = VALID.find((m) => m.type === "start");
  const remove = VALID.find((m) => m.type === "memoryRemove");
  const decide = VALID.find((m) => m.type === "decide");
  assert.equal(isAllowedPanelMessage("usage", start), false, "the usage panel cannot start a run");
  assert.equal(isAllowedPanelMessage("approvals", remove), false, "the approvals panel cannot delete memory");
  assert.equal(isAllowedPanelMessage("orgs", decide), false, "the organisations panel cannot decide anything");
  assert.equal(isAllowedPanelMessage("start", decide), false);
  assert.ok(isAllowedPanelMessage("approvals", decide));
  assert.ok(isAllowedPanelMessage("memory", decide), "a memory proposal is decided from the memory panel");
  for (const kind of PANEL_KINDS) {
    assert.ok(isAllowedPanelMessage(kind, { type: "ready" }) && isAllowedPanelMessage(kind, { type: "refresh" }));
    for (const t of PANEL_ALLOWS[kind]) assert.ok(VALID.some((m) => m.type === t), `${kind} allows a documented type ${t}`);
  }
});

// ------------------------------------------------------------------ usage
test("a meter says its value and its state in words, on the status bar's thresholds", () => {
  assert.deepEqual(meter(24, 1000, "requests"),
    { used: 24, cap: 1000, share: 0.024, level: "ok", text: "24 / 1,000 requests · 2%", state: "" });
  assert.equal(meter(800, 1000, "requests").state, "near the cap");
  assert.equal(meter(800, 1000, "requests").level, "warn");
  assert.equal(meter(950, 1000, "requests").state, "at the cap");
  assert.equal(meter(1200, 1000, "requests").level, "hot");
  const none = meter(46225, null, "tokens");
  assert.equal(none.share, null);
  assert.equal(none.level, "none");
  assert.equal(none.text, "46,225 tokens · no daily cap");
  assert.equal(meter(-5, 100, "x").used, 0);
});

test("the usage model keeps a stable order, totals the ledger and says when caps reset", () => {
  const quota: QuotaRow[] = [
    { provider: "groq", model: "zeta", usable: true, limits: { rpd: 1000, tpd: 200000 }, day_requests: 950, day_tokens: 10, resets_in_s: 12000 },
    { provider: "gemini", model: "flash", usable: false, limits: { rpd: 20 }, day_requests: 20, day_tokens: 0 },
    { provider: "groq", model: "alpha", usable: true, limits: {}, day_requests: 3, day_tokens: 900 },
  ];
  const ledger: UsageRow[] = [
    { day: "2026-09-19", provider: "groq", model: "alpha", requests: 9, tokens: 11192, rpd: 1000, tpd: 200000, share: 0.06 },
    { day: "2026-09-20", provider: "groq", model: "zeta", requests: 35, tokens: 47006, rpd: 1000, tpd: 200000, share: 0.24, next_reset: "07:30 IST" },
  ];
  const v = usageModel(quota, ledger, 7);
  assert.deepEqual(v.models.map((m) => `${m.provider}/${m.model}`), ["groq/alpha", "groq/zeta", "gemini/flash"]);
  assert.equal(v.models[1].requests.state, "at the cap");
  assert.equal(v.models[1].resets, "resets in 3h 20m");
  assert.equal(v.models[0].resets, "");
  assert.equal(v.models[2].usable, false);
  assert.deepEqual(v.ledger.map((r) => r.day), ["2026-09-20", "2026-09-19"]);
  assert.deepEqual(v.totals, { requests: 44, tokens: 58198 });
  assert.equal(v.ledger[0].nextReset, "07:30 IST");
  assert.equal(v.days, 7);
});

// ------------------------------------------------------------------ approvals
test("approvals are listed oldest first, each labelled by what it asks", () => {
  const a = (id: string, kind: string, created: number): Approval =>
    ({ id, run_id: "r1", kind, prompt: `p-${id}`, status: "pending", created, agent: kind === "question" ? "planner" : null });
  const v = approvalsModel([a("n", "memory", 30), a("x", "exec", 10), a("q", "question", 20)]);
  assert.deepEqual(v.items.map((i) => i.id), ["x", "q", "n"]);
  assert.deepEqual(v.items.map((i) => i.label), ["Run code", "Question", "Remember this fact?"]);
  assert.equal(v.items[1].agent, "planner");
  assert.equal(v.items[0].agent, "");
  assert.equal(approvalLabel("gate"), "Gate");
  assert.equal(approvalLabel("something-new"), "something-new");
});

// ------------------------------------------------------------------ new run
test("the new-run form offers valid orgs only, yours first, and never trusts a stale prefill", () => {
  const orgs: OrgSummary[] = [
    { name: "software-team", source: "template", valid: true, workflow: "review_loop", checks: ["tests"] },
    { name: "broken", source: "yours", valid: false },
    { name: "my-team", source: "yours", valid: true, description: "mine" },
  ];
  const keyed: ProviderView[] = [{ id: "groq", local: false, key: "keyring", models: ["m"] }];
  const v = startModel(orgs, keyed, [{ name: "repo" }, { name: "docs" }], true, "software-team");
  assert.deepEqual(v.orgs.map((o) => o.name), ["my-team", "software-team"]);
  assert.deepEqual(v.folders, [{ index: 0, name: "repo" }, { index: 1, name: "docs" }]);
  assert.equal(v.prefillOrg, "software-team");
  assert.equal(v.hasProvider, true);
  assert.equal(startModel(orgs, keyed, [], true, "broken").prefillOrg, "", "an invalid org is not prefilled");
  assert.equal(startModel(orgs, [{ id: "groq", local: false, key: "missing", models: ["m"] }], [], true).hasProvider, false);
  assert.equal(startModel(orgs, undefined, [], false).hasProvider, true, "no provider list is not proof of no key");
  assert.equal(startModel(orgs, keyed, [], false).trusted, false);
});

// ------------------------------------------------------------------ memory
test("memory is grouped by scope, global first, with proposals waiting at the top of each", () => {
  const list: MemoryList = {
    scopes: ["project:e863025677c9", "team:eng"],
    skipped: ["global entry 3: over 400 characters"],
    entries: [
      { id: "m-1", text: "old fact", scope: "global", tags: [], source: "human", date: "2026-09-18", by: "owner", pinned: false, private: false, approved: true },
      { id: "m-2", text: "pinned fact", scope: "global", tags: ["build"], source: "human", date: "2026-09-10", by: "owner", pinned: true, private: false, approved: true },
      { id: "m-3", text: "proposed", scope: "global", tags: [], source: "r1/retrospective", date: "2026-09-20", by: "", pinned: false, private: false, approved: false, approval: "ap9" },
      { id: "m-4", text: "project fact", scope: "project:e863025677c9", tags: [], source: "human", date: "2026-09-20", by: "owner", pinned: false, private: true, approved: true, approval: "stale" },
    ],
  };
  const v = memoryModel(list);
  assert.deepEqual(v.groups.map((g) => g.scope), ["global", "project:e863025677c9"]);
  assert.deepEqual(v.groups[0].items.map((i) => i.id), ["m-3", "m-2", "m-1"]);
  assert.equal(v.groups[0].items[0].approvalId, "ap9");
  assert.equal(v.groups[1].items[0].approvalId, "", "an approved entry has nothing to decide");
  assert.deepEqual(v.scopes, ["global", "team:eng", "project:e863025677c9"]);
  assert.deepEqual(v.skipped, ["global entry 3: over 400 characters"]);
  assert.deepEqual(memoryModel({ entries: [], skipped: [], scopes: [] }).scopes, ["global"]);
  // cross-checked against the pending approvals: ap9 still waits; a proposal whose approval is gone is stale
  const orphan = { ...list.entries[2], id: "m-5", approval: "closed-long-ago", date: "2026-09-21" };
  const live = memoryModel({ ...list, entries: [...list.entries, orphan] }, new Set(["ap9"]));
  const items = live.groups[0].items;
  assert.deepEqual(items.map((i) => i.id), ["m-3", "m-2", "m-1", "m-5"], "stale proposals sink to the bottom");
  assert.equal(items[0].stale, false);
  assert.equal(items[0].approvalId, "ap9");
  assert.equal(items[3].stale, true);
  assert.equal(items[3].approvalId, "", "a stale proposal offers no Decide…");
  assert.ok(!items.find((i) => i.approved)!.stale, "an approved fact is never stale");
  assert.equal(memoryModel(list).groups[0].items[0].stale, false, "without the pending list, nothing is called stale");
  assert.ok(scopeOrder("global", "team:a") < 0 && scopeOrder("team:z", "project:0") < 0);
});

// ------------------------------------------------------------------ organisations
test("organisations list yours first, with readable budgets and their errors", () => {
  const v = orgsModel([
    { name: "decision-board", source: "template", valid: true, workflow: "council",
      agents: [{ id: "ceo", role: "chair", tier: "strong", tools: [] }],
      budget: { max_calls: 80, max_tokens: 300000, max_days: 7, max_tokens_per_day: null } },
    { name: "mine", source: "yours", valid: false, errors: ["workflow: unknown agent ghost"] },
  ]);
  assert.deepEqual(v.orgs.map((o) => o.name), ["mine", "decision-board"]);
  assert.deepEqual(v.orgs[0].errors, ["workflow: unknown agent ghost"]);
  assert.equal(v.orgs[0].title, "mine");
  assert.deepEqual(v.orgs[1].budget, [["max calls", "80"], ["max tokens", "300,000"], ["max days", "7"]]);
  assert.deepEqual(v.orgs[1].agents, [{ id: "ceo", role: "chair", tier: "strong", tools: [] }]);
});
