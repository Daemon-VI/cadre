// Cadre dashboard. No framework, no build step, no third-party code.
// Everything a model wrote is inserted with textContent — never as HTML.
"use strict";

// ---------------------------------------------------------------- token (ADR-010)
const TOKEN_KEY = "cadre-token";
(function takeTokenFromFragment() {
  const m = location.hash.match(/token=([A-Za-z0-9_\-]+)/);
  if (m) {
    try { sessionStorage.setItem(TOKEN_KEY, m[1]); } catch (_) { /* storage blocked */ }
    window.__cadreToken = m[1];
    history.replaceState(null, "", location.pathname + "#/runs");
  }
})();
function token() {
  if (window.__cadreToken) return window.__cadreToken;
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; }
}

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $main = () => document.getElementById("main");
function mount(...nodes) { const m = $main(); m.replaceChildren(...nodes); }
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 4500);
}
const fmt = (n) => (n || 0).toLocaleString();
function when(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function brief(s, n) { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function duration(a, b) {
  if (!a) return "–";
  const s = Math.max(0, Math.round((b || Date.now() / 1000) - a));
  return s < 90 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
const STATUS_CLASS = {
  succeeded: "good", running: "live", waiting: "warn", queued: "live", cancelling: "warn",
  unapproved: "warn", stopped: "warn", interrupted: "warn", parked: "warn",
  failed: "bad", rejected: "bad", cancelled: "",
};
const pill = (status) => h("span", { class: `pill ${STATUS_CLASS[status] ?? ""}`, text: status });
const ACTIVE = new Set(["queued", "running", "waiting", "cancelling"]);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Authorization": `Bearer ${token()}`, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("not signed in"); }
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : d && d.errors ? d.errors.join("\n") : JSON.stringify(d || data);
    throw new Error(msg);
  }
  return data;
}

// ---------------------------------------------------------------- router
let stopView = () => {};
function route() {
  stopView();
  stopView = () => {};
  const [, view, arg] = (location.hash || "#/runs").split("/");
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === (view === "run" ? "runs" : view)));
  if (!token()) return showLogin();
  const views = { runs: viewRuns, run: () => viewRun(arg), new: () => viewNew(arg), orgs: viewOrgs, providers: viewProviders, usage: viewUsage, approvals: viewApprovals };
  (views[view] || viewRuns)();
  $main().focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

function showLogin() {
  const input = h("input", { type: "password", id: "tok", autocomplete: "off" });
  mount(h("div", { class: "panel stack" },
    h("h1", { text: "Connect to your Cadre server" }),
    h("p", { class: "sub" }, "Open the dashboard with ", h("code", { text: "cadre ui" }),
      ", which passes the access token automatically, or paste the token from ", h("code", { text: "~/.cadre/token" }), "."),
    h("label", { for: "tok", text: "Access token" }), input,
    h("button", { class: "primary", onclick: () => {
      window.__cadreToken = input.value.trim();
      try { sessionStorage.setItem(TOKEN_KEY, window.__cadreToken); } catch (_) { /* ignore */ }
      location.hash = "#/runs"; route();
    } }, "Connect")));
}

// ---------------------------------------------------------------- approvals badge
async function refreshBadge() {
  if (!token()) return;
  try {
    const pending = await api("/api/v1/approvals?pending=true");
    const b = document.getElementById("approval-count");
    b.textContent = pending.length;
    b.classList.toggle("hidden", pending.length === 0);
  } catch (_) { /* offline */ }
}
setInterval(refreshBadge, 4000);

// ---------------------------------------------------------------- runs list
function viewRuns() {
  const body = h("tbody");
  const empty = h("p", { class: "muted hidden" }, "No runs yet. ", h("a", { href: "#/new", text: "Start one" }),
    " — the offline demo needs no API key.");
  mount(
    h("div", { class: "row spread" },
      h("div", {}, h("h1", { text: "Runs" }), h("p", { class: "sub", text: "Every run, its status and what it cost in tokens." })),
      h("a", { class: "btn", href: "#/new" }, "New run")),
    h("div", { class: "panel table-wrap" },
      h("table", {}, h("thead", {}, h("tr", {},
        h("th", { text: "Started" }), h("th", { text: "Organisation" }), h("th", { text: "Goal" }),
        h("th", { text: "Status" }), h("th", { class: "num", text: "Calls" }), h("th", { class: "num", text: "Tokens" }))), body),
      empty));
  const load = async () => {
    const runs = await api("/api/v1/runs");
    empty.classList.toggle("hidden", runs.length > 0);
    body.replaceChildren(...runs.map((r) => h("tr", { class: "click", onclick: () => (location.hash = `#/run/${r.id}`) },
      h("td", { class: "small", text: when(r.created) }),
      h("td", { text: r.org }),
      h("td", { text: brief(r.goal, 90) }),
      h("td", {}, pill(r.status)),
      h("td", { class: "num", text: fmt(r.calls) }),
      h("td", { class: "num", text: fmt(r.prompt_tokens + r.completion_tokens) }))));
  };
  load().catch((e) => toast(e.message));
  const t = setInterval(() => load().catch(() => {}), 3000);
  stopView = () => clearInterval(t);
}

// ---------------------------------------------------------------- one run
function describe(e) {
  const d = e.data || {};
  switch (e.kind) {
    case "run.started": return [`started ${d.org}${d.resumed ? " (resumed)" : ""} · models: ${(d.models || []).join(", ")}`, ""];
    case "run.finished": return [`finished: ${d.status}${d.error ? " — " + d.error : ""}`, d.status === "succeeded" ? "" : "warn"];
    case "run.parked": return [h("div", {}, `parked until ${d.resume_at_ist} — daily limits:`,
      h("ul", {}, ...(d.blocks || []).map((b) => h("li", { text: `${b.model}: ${b.why} (frees in ${b.frees_in})` })))), "warn"];
    case "project.worktree": return [`worktree ${d.how} on ${d.branch} from ${d.base}`, "quiet"];
    case "project.commit": return [`committed ${d.sha}: ${d.message}`, ""];
    case "project.commit_failed": return [`commit failed: ${d.error}`, "bad"];
    case "privacy.excluded": return [`private run — excluded ${(d.models || []).map((m) => m.model).join(", ")}`, "quiet"];
    case "run.crashed": return [h("details", {}, h("summary", { text: "internal error — trace" }), h("pre", { text: d.trace })), "bad"];
    case "step.started": return [`${d.type} step ${d.title ? "“" + d.title + "”" : ""} started`, "quiet"];
    case "step.finished": return [`${d.type} step finished${d.approved === false ? " — NOT approved" : d.approved ? " — approved" : ""}`, d.approved === false ? "warn" : "quiet"];
    case "step.reused": return [`${d.type} step reused from the earlier attempt`, "quiet"];
    case "agent.start": return [h("span", {}, "task: ", brief(d.task, 220)), "quiet"];
    case "agent.call": {
      const bits = [`${d.model} · ${fmt(d.tokens_in)}→${fmt(d.tokens_out)} tokens`];
      if (d.tools && d.tools.length) bits.push(`calls ${d.tools.join(", ")}`);
      if (d.independent === true) bits.push("independent of builder");
      if (d.independent === false) bits.push("NOT independent (no other model family available)");
      if (d.waited) bits.push(`waited ${d.waited}s for quota`);
      return [bits.join(" · "), d.independent === false ? "warn" : "quiet"];
    }
    case "agent.tool": return [h("span", {}, h("b", { text: d.tool }), " ", h("span", { class: "mono", text: brief(JSON.stringify(d.args), 160) }),
      h("details", {}, h("summary", { text: d.ok ? "result" : "error" }), h("pre", { text: d.result }))), d.ok ? "" : "warn"];
    case "agent.answer": return [h("details", {}, h("summary", { text: brief(d.text, 140) || "(empty answer)" }), h("pre", { text: d.text })), ""];
    case "agent.repair": return [`reply was not valid JSON (${d.error}) — asked once more`, "warn"];
    case "agent.invalid": return [`reply still unusable: ${d.error}`, "bad"];
    case "route.wait": return [`waiting ${d.seconds}s for ${d.model}: ${d.reason}`, "warn"];
    case "route.fallback": return [`fallback — ${d.note}`, "warn"];
    case "note": return [h("span", {}, "📌 ", d.text), ""];
    case "file.written": return [`wrote ${d.path}`, "quiet"];
    case "check.started": return [`running check ${d.name}: ${(d.command || []).join(" ")}`, "quiet"];
    case "check.finished": return [h("span", {}, h("span", { class: `pill ${d.passed ? "good" : "bad"}`, text: `${d.name} ${d.passed ? "passed" : "failed"}` }),
      ` ${d.seconds}s ${d.note || ""}`, d.output_tail ? h("details", {}, h("summary", { text: "output" }), h("pre", { text: d.output_tail })) : null), ""];
    case "review.round": return [h("span", {},
      `review round ${d.round}: `, h("span", { class: `pill ${d.approved ? "good" : "warn"}`, text: d.approved ? "approved" : "changes requested" }), " ",
      ...(d.checks || []).map((c) => h("span", { class: `pill ${c.passed ? "good" : "bad"}`, text: c.name })), " ",
      ...(d.verdicts || []).map((v) => h("span", { class: `pill ${v.approve ? "good" : "warn"}`, text: `${v.reviewer}${v.issues ? " · " + v.issues + " issues" : ""}` })),
      d.gated_by_checks ? h("div", { class: "small", text: "Reviewers approved, but a failing check blocked it." }) : null), ""];
    case "council.tally": return [h("span", {}, `vote (${d.rule}): `,
      ...Object.entries(d.counts || {}).map(([k, v]) => h("span", { class: `pill ${k === d.winner ? "good" : ""}`, text: `${k}: ${v}` })),
      ` → ${d.winner} decided by ${d.decided_by}`, d.abstained && d.abstained.length ? ` · abstained: ${d.abstained.join(", ")}` : ""), ""];
    case "plan.created": return [h("div", {}, "plan:", h("ul", {}, ...(d.tasks || []).map((t) =>
      h("li", { text: `${t.id} → ${t.assignee}: ${t.title}${t.depends_on && t.depends_on.length ? " (after " + t.depends_on.join(", ") + ")" : ""}` })))), ""];
    case "task.started": return [`task ${d.task} started: ${d.title}`, "quiet"];
    case "task.finished": return [`task ${d.task} finished${d.approved === false ? " — not approved" : ""}`, d.approved === false ? "warn" : ""];
    case "task.failed": return [`task ${d.task} failed: ${d.error}`, "bad"];
    case "task.skipped": return [`task ${d.task} skipped: ${d.reason}`, "warn"];
    case "approval.requested": return [approvalBox(d), "warn"];
    case "approval.decided": return [`${d.kind} ${d.approved ? "approved" : "rejected"}${d.answer ? " — " + d.answer : ""}`, ""];
    case "approval.auto": return [`${d.kind} auto-approved: ${brief(d.prompt, 160)}`, "quiet"];
    default: return [brief(JSON.stringify(d), 200), "quiet"];
  }
}

function approvalBox(d) {
  const answer = h("input", { type: "text", placeholder: d.kind === "question" ? "Your answer" : "Optional note" });
  const decide = async (approve) => {
    try {
      await api(`/api/v1/approvals/${d.id}`, { method: "POST", body: { approve, answer: answer.value } });
      box.replaceChildren(`decided: ${approve ? "approved" : "rejected"}`);
      refreshBadge();
    } catch (err) { toast(err.message); }
  };
  const box = h("div", { class: "stack" },
    h("div", {}, h("b", { text: d.kind === "exec" ? "Allow code execution?" : d.kind === "question" ? "An agent asks:" : "Approval needed:" })),
    h("pre", { text: d.prompt }),
    answer,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => decide(true) }, d.kind === "question" ? "Answer" : "Approve"),
      h("button", { class: "danger", onclick: () => decide(false) }, "Reject")));
  return box;
}

async function readStream(rid, after, onEvent, signal) {
  const res = await fetch(`/api/v1/runs/${rid}/stream?after=${after}`, { headers: { Authorization: `Bearer ${token()}` }, signal });
  if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

function viewRun(rid) {
  const head = h("div");
  const tiles = h("div", { class: "tiles" });
  const timeline = h("ul", { class: "timeline" });
  const files = h("div");
  const usage = h("div", { class: "table-wrap" });
  const result = h("div");
  const panes = { Timeline: timeline, Result: result, Files: files, Usage: usage };
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const box = h("div", { class: "panel" });
  let current = "Timeline";
  const show = (name) => {
    current = name;
    [...tabs.children].forEach((b) => b.classList.toggle("on", b.textContent === name));
    box.replaceChildren(panes[name]);
  };
  Object.keys(panes).forEach((n) => tabs.append(h("button", { role: "tab", onclick: () => show(n) }, n)));
  mount(h("p", { class: "small" }, h("a", { href: "#/runs", text: "← all runs" })), head, tiles, tabs, box);
  show(current);

  let cursor = 0;
  const addEvent = (e) => {
    cursor = Math.max(cursor, e.seq);
    const [what, tone] = describe(e);
    timeline.append(h("li", { class: `ev ${tone}` },
      h("div", { class: "t", text: clock(e.ts) }),
      h("div", { class: "who", title: e.step || "", text: e.agent || (e.kind.startsWith("run") ? "cadre" : "") }),
      h("div", { class: "what" }, h("span", { class: "kind", text: e.kind }), what)));
  };

  const loadRun = async () => {
    const r = await api(`/api/v1/runs/${rid}`);
    const actions = h("div", { class: "row" });
    if (ACTIVE.has(r.status)) actions.append(h("button", { class: "danger", onclick: async () => {
      await api(`/api/v1/runs/${rid}/cancel`, { method: "POST" }); toast("Cancelling…"); } }, "Cancel run"));
    if (["interrupted", "failed", "stopped", "cancelled", "unapproved", "parked"].includes(r.status)) actions.append(h("button", { onclick: async () => {
      try { await api(`/api/v1/runs/${rid}/resume`, { method: "POST" }); toast("Resumed — finished steps are reused, not re-billed."); follow(); }
      catch (err) { toast(err.message); } } }, "Resume"));
    head.replaceChildren(h("div", { class: "row spread" },
      h("div", {},
        h("h1", {}, r.org, " ", pill(r.status), r.options && r.options.demo ? h("span", { class: "pill", text: "demo" }) : null),
        h("p", { class: "sub", text: r.goal })),
      actions),
      r.status === "parked" && r.resume_at
        ? h("div", { class: "callout info", text: `Parked on daily limits. It resumes by itself around ${when(r.resume_at)} while the server runs.` }) : null,
      r.error ? h("div", { class: "callout", text: r.error }) : null,
      r.summary && r.summary.unapproved && r.summary.unapproved.length
        ? h("div", { class: "callout", text: `Finished, but not everything was approved: ${r.summary.unapproved.join(", ")}. Open the Timeline to see the open issues.` }) : null);
    const t = r.totals;
    tiles.replaceChildren(
      ...[["Model calls", fmt(t.calls)], ["Tokens in", fmt(t.prompt_tokens)], ["Tokens out", fmt(t.completion_tokens)],
        ["Files", fmt(r.files.length)], ["Duration", duration(r.created, r.finished)]]
        .map(([k, v]) => h("div", { class: "tile" }, h("div", { class: "k", text: k }), h("div", { class: "v", text: v }))));
    usage.replaceChildren(h("table", {},
      h("thead", {}, h("tr", {}, ...["Agent", "Provider", "Model", "Calls", "Tokens in", "Tokens out"].map((x, i) => h("th", { class: i > 2 ? "num" : "", text: x })))),
      h("tbody", {}, ...r.usage.map((u) => h("tr", {},
        h("td", { text: u.agent }), h("td", { text: u.provider }), h("td", { class: "mono", text: u.model }),
        h("td", { class: "num", text: fmt(u.calls) }), h("td", { class: "num", text: fmt(u.prompt_tokens) }), h("td", { class: "num", text: fmt(u.completion_tokens) }))))),
      h("p", { class: "hint", text: "On free tiers these tokens cost nothing, but they count against each key's per-minute and per-day limits." }));
    result.replaceChildren(r.result ? h("pre", { text: r.result }) : h("p", { class: "muted", text: ACTIVE.has(r.status) ? "Still working…" : "No final output." }));
    const viewer = h("pre", { class: "hidden" });
    const list = h("div", { class: "filelist" }, ...r.files.map((f) => h("button", { onclick: async (ev) => {
      [...list.children].forEach((b) => b.classList.remove("on"));
      ev.currentTarget.classList.add("on");
      viewer.textContent = await api(`/api/v1/runs/${rid}/files/${f.path.split("/").map(encodeURIComponent).join("/")}`);
      viewer.classList.remove("hidden");
    } }, h("span", { class: "mono", text: f.path }), h("span", { class: "small muted", text: `  v${f.versions} · ${f.agent}` }))));
    files.replaceChildren(r.files.length ? h("div", { class: "split" }, list, viewer) : h("p", { class: "muted", text: "No files yet." }));
    return r;
  };

  let controller = null;
  const follow = async () => {
    if (controller) controller.abort();
    controller = new AbortController();
    try {
      await readStream(rid, cursor, (event, e) => {
        if (event === "end") return;
        addEvent(e);
        if (["run.finished", "file.written", "agent.call", "approval.decided"].includes(e.kind)) loadRun().catch(() => {});
      }, controller.signal);
    } catch (err) { if (err.name !== "AbortError") toast(`live feed: ${err.message}`); }
    loadRun().catch(() => {});
  };
  loadRun().then(follow).catch((e) => toast(e.message));
  stopView = () => { if (controller) controller.abort(); };
}

// ---------------------------------------------------------------- new run
async function viewNew(preselect) {
  const orgs = (await api("/api/v1/orgs").catch((e) => { toast(e.message); return []; })).filter((o) => o.valid);
  const providers = await api("/api/v1/providers").catch(() => []);
  const hasKey = providers.some((p) => p.models.length && (p.local || p.key !== "missing"));
  const select = h("select", { id: "org" }, ...orgs.map((o) => h("option", { value: o.name, text: `${o.name}${o.source === "yours" ? "" : "  (template)"}` })));
  if (preselect) select.value = preselect;
  const info = h("div", { class: "panel" });
  const goal = h("textarea", { id: "goal", placeholder: "What should the organisation achieve? e.g. “A command-line tool that converts CSV to Markdown tables”" });
  const exec = h("input", { type: "checkbox", id: "exec" });
  const auto = h("input", { type: "checkbox", id: "auto" });
  const demo = h("input", { type: "checkbox", id: "demo", checked: !hasKey });
  const describeOrg = () => {
    const o = orgs.find((x) => x.name === select.value);
    if (!o) return info.replaceChildren(h("p", { class: "muted", text: "No organisations found." }));
    info.replaceChildren(
      h("h3", { text: `${o.title} · ${o.workflow} workflow` }),
      h("p", { class: "muted small", text: o.description }),
      h("div", { class: "agents" }, ...o.agents.map((a) => h("span", { class: "agent" }, h("b", { text: a.id }), ` ${a.role} · ${a.tier}`))),
      o.checks.length ? h("p", { class: "small" }, "Checks: ", o.checks.join(", "), " — these run code the agents wrote.") : null,
      h("p", { class: "small muted", text: `Budget: ${o.budget.max_calls} calls, ${fmt(o.budget.max_tokens)} tokens, ${o.budget.max_minutes} min, ${o.budget.max_parallel} in parallel.` }));
  };
  select.addEventListener("change", describeOrg);
  describeOrg();
  const start = h("button", { class: "primary", onclick: async () => {
    start.disabled = true;
    try {
      const r = await api("/api/v1/runs", { method: "POST", body: { org: select.value, goal: goal.value, allow_exec: exec.checked, auto_approve: auto.checked, demo: demo.checked } });
      location.hash = `#/run/${r.id}`;
    } catch (err) { toast(err.message); start.disabled = false; }
  } }, "Start run");
  mount(
    h("h1", { text: "New run" }),
    h("p", { class: "sub", text: "Pick an organisation, give it a goal, and watch it work." }),
    hasKey ? null : h("div", { class: "callout info" }, "No model keys yet — demo mode is on, which runs the whole workflow offline with a scripted model. ",
      h("a", { href: "#/providers", text: "Add a free key" }), " for real work."),
    h("div", { class: "panel stack" },
      h("div", {}, h("label", { for: "org", text: "Organisation" }), select),
      info,
      h("div", {}, h("label", { for: "goal", text: "Goal" }), goal),
      h("label", { class: "check" }, exec, h("span", {}, "Allow checks to run without asking ",
        h("span", { class: "hint", text: "— checks execute code the agents wrote, as you, on this machine. Not a sandbox." }))),
      h("label", { class: "check" }, auto, h("span", {}, "Auto-approve approval gates and questions")),
      h("label", { class: "check" }, demo, h("span", {}, "Demo mode (offline scripted model, no key needed)")),
      h("div", { class: "row" }, start)));
}

// ---------------------------------------------------------------- orgs
async function viewOrgs() {
  const orgs = await api("/api/v1/orgs").catch((e) => { toast(e.message); return []; });
  const name = h("input", { type: "text", id: "orgname", placeholder: "my-team" });
  const yaml = h("textarea", { class: "code", id: "orgyaml", spellcheck: "false" });
  const out = h("div");
  const load = async (n) => {
    const o = await api(`/api/v1/orgs/${encodeURIComponent(n)}`);
    yaml.value = o.yaml;
    name.value = o.source === "yours" || String(o.source).endsWith(".yaml") ? n : `my-${n}`;
    out.replaceChildren();
    yaml.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const validate = async () => {
    const r = await api("/api/v1/orgs/validate", { method: "POST", body: { yaml: yaml.value } });
    out.replaceChildren(r.valid ? h("div", { class: "callout info", text: `Valid: ${r.agents.length} agents, ${r.workflow} workflow.` })
      : h("div", { class: "callout" }, h("b", { text: "Not valid:" }), h("ul", {}, ...r.errors.map((e) => h("li", { class: "mono", text: e })))));
    return r.valid;
  };
  mount(
    h("h1", { text: "Organisations" }),
    h("p", { class: "sub", text: "An organisation is one YAML file: agents, checks, a budget and a workflow. Start from a template." }),
    h("div", { class: "grid" }, ...orgs.map((o) => h("div", { class: "panel" },
      h("div", { class: "row spread" }, h("h3", { text: o.name }), h("span", { class: "pill", text: o.source === "yours" ? "yours" : "template" })),
      o.valid ? [
        h("p", { class: "small muted", text: o.description }),
        h("p", { class: "small" }, h("b", { text: o.workflow }), ` · ${o.agents.length} agents${o.checks.length ? " · checks: " + o.checks.join(", ") : ""}`),
        h("div", { class: "agents" }, ...o.agents.map((a) => h("span", { class: "agent", title: a.role }, h("b", { text: a.id })))),
      ] : h("div", { class: "callout", text: o.errors.join("; ") }),
      h("div", { class: "row" },
        h("button", { onclick: () => load(o.name) }, "Edit a copy"),
        o.valid ? h("a", { class: "btn", href: `#/new/${o.name}` }, "Run") : null)))),
    h("h2", { text: "Editor" }),
    h("div", { class: "panel stack" },
      h("div", {}, h("label", { for: "orgname", text: "Save as" }), name),
      h("div", {}, h("label", { for: "orgyaml", text: "YAML" }), yaml),
      out,
      h("div", { class: "row" },
        h("button", { onclick: () => validate().catch((e) => toast(e.message)) }, "Validate"),
        h("button", { class: "primary", onclick: async () => {
          try {
            await api(`/api/v1/orgs/${encodeURIComponent(name.value.trim())}`, { method: "PUT", body: { yaml: yaml.value } });
            toast("Saved."); viewOrgs();
          } catch (err) { toast(err.message); }
        } }, "Save"))));
}

// ---------------------------------------------------------------- providers + quota
async function viewProviders() {
  const [presetInfo, providers] = await Promise.all([api("/api/v1/presets"), api("/api/v1/providers")]).catch((e) => { toast(e.message); return [{ presets: [] }, []]; });
  const presets = presetInfo.presets;
  const quotaBody = h("tbody");
  const loadQuota = async () => {
    const rows = await api("/api/v1/quota");
    quotaBody.replaceChildren(...rows.map((q) => {
      const L = q.limits;
      const bar = (used, lim) => {
        if (!lim) return h("span", { class: "small muted", text: `${fmt(used)} / –` });
        const pct = Math.min(100, Math.round((100 * used) / lim));
        const inner = h("span");
        inner.style.width = `${pct}%`;
        return h("div", {}, h("div", { class: `bar ${pct > 80 ? "hot" : ""}` }, inner), h("span", { class: "small muted", text: `${fmt(used)} / ${fmt(lim)}` }));
      };
      return h("tr", {},
        h("td", {}, h("span", { class: "mono", text: `${q.provider}/${q.model}` }), q.usable ? null : h("span", { class: "pill bad", text: "unusable" })),
        h("td", { text: `${q.tier} · ${q.family}` }),
        h("td", {}, bar(q.minute_requests, L.rpm)),
        h("td", {}, bar(q.minute_tokens, L.tpm)),
        h("td", {}, bar(q.day_requests, L.rpd)),
        h("td", {}, bar(q.day_tokens, L.tpd)),
        h("td", { class: "num", text: q.cooldown_s ? `${q.cooldown_s}s` : "" }));
    }));
  };

  const presetSel = h("select", { id: "preset" }, ...presets.map((p) => h("option", { value: p.id, text: `${p.label}${p.free ? "" : " (paid)"}` })));
  const presetNote = h("div", { class: "hint" });
  const key = h("input", { type: "password", id: "key", autocomplete: "off", placeholder: "paste the key — it goes to your OS credential store" });
  const models = h("input", { type: "text", id: "models", placeholder: "optional: model ids, comma-separated" });
  const params = h("div");
  const syncPreset = () => {
    const p = presets.find((x) => x.id === presetSel.value);
    if (!p) return;
    presetNote.replaceChildren(
      p.note ? h("div", { text: p.note }) : null,
      h("div", {}, `Limits source: ${p.source} (checked ${presetInfo.checked}). `,
        p.signup ? h("a", { href: p.signup, target: "_blank", rel: "noopener noreferrer", text: "Get a key" }) : null),
      h("div", { text: p.models.length ? `Suggested models: ${p.models.join(", ")}` : "Models are discovered from the endpoint." }));
    key.disabled = p.local;
    params.replaceChildren(...p.params.map((name) => h("div", {}, h("label", { text: name }), h("input", { type: "text", "data-param": name }))));
  };
  presetSel.addEventListener("change", syncPreset);
  syncPreset();
  const add = h("button", { class: "primary", onclick: async () => {
    add.disabled = true;
    const body = { preset: presetSel.value, key: key.value || null, params: {} };
    params.querySelectorAll("input[data-param]").forEach((i) => (body.params[i.dataset.param] = i.value.trim()));
    const m = models.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (m.length) body.models = m;
    try {
      const r = await api("/api/v1/providers", { method: "POST", body });
      key.value = "";
      toast(r.warning ? `Added ${r.id} — ${r.warning}` : `Added ${r.id} with ${r.models.length} model(s).`);
      viewProviders();
    } catch (err) { toast(err.message); add.disabled = false; }
  } }, "Add provider");

  const list = providers.length ? providers.map((p) => {
    const status = h("span", { class: "small muted" });
    return h("div", { class: "panel" },
      h("div", { class: "row spread" },
        h("div", {}, h("h3", { text: `${p.label} (${p.id})` }),
          h("span", { class: `pill ${p.key === "missing" ? "bad" : "good"}`, text: p.key === "missing" ? "no key" : `key: ${p.key}` }),
          p.disabled_reason ? h("div", { class: "callout", text: p.disabled_reason }) : null),
        h("div", { class: "row" },
          h("button", { onclick: async () => { status.textContent = "testing…"; const r = await api(`/api/v1/providers/${p.id}/test`, { method: "POST" }); status.textContent = `${r.ok ? "OK" : "problem"}: ${r.detail}`; } }, "Test"),
          h("button", { class: "danger", onclick: async () => { await api(`/api/v1/providers/${p.id}`, { method: "DELETE" }); toast(`Removed ${p.id}.`); viewProviders(); } }, "Remove"))),
      status,
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["Model", "Tier", "Family", "RPM", "RPD", "TPM", "TPD", "Protocol"].map((x) => h("th", { text: x })))),
        h("tbody", {}, ...p.models.map((m) => h("tr", {},
          h("td", { class: "mono", text: m.name }), h("td", { text: m.tier }), h("td", { text: m.family }),
          ...["rpm", "rpd", "tpm", "tpd"].map((k) => h("td", { class: "num", text: m[k] ? fmt(m[k]) : "–" })),
          h("td", { text: m.protocol })))))));
  }) : [h("p", { class: "muted", text: "No providers yet. Add one below — Groq and Google AI Studio both give free keys without a card." })];

  mount(
    h("h1", { text: "Models & keys" }),
    h("p", { class: "sub", text: "Add any free model by adding its key. Cadre spreads work across every model you add and keeps each one inside its free limits." }),
    ...list,
    h("h2", { text: "Add a provider" }),
    h("div", { class: "panel stack" },
      h("div", {}, h("label", { for: "preset", text: "Provider" }), presetSel, presetNote),
      h("div", {}, h("label", { for: "key", text: "API key" }), key),
      params,
      h("div", {}, h("label", { for: "models", text: "Models" }), models),
      h("div", { class: "row" }, add)),
    h("h2", { text: "Live quota" }),
    h("div", { class: "panel table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Model", "Tier · family", "Requests/min", "Tokens/min", "Requests today", "Tokens today", "Cooling"].map((x) => h("th", { text: x })))),
      quotaBody)));
  loadQuota().catch((e) => toast(e.message));
  const t = setInterval(() => loadQuota().catch(() => {}), 3000);
  stopView = () => clearInterval(t);
}

// ---------------------------------------------------------------- usage ledger
// A table, not a chart: the reader looks up one model on one day. Each row carries one
// single-hue meter for its share of the daily cap, with the number printed beside it.
function meter(share) {
  if (share === null || share === undefined) return h("span", { class: "small muted", text: "no daily cap" });
  const pct = Math.round(share * 100);
  const fill = h("span");
  fill.style.width = `${Math.min(100, pct)}%`;
  return h("div", { class: "meter-row", title: `${pct}% of the daily cap used` },
    h("div", { class: "bar" }, fill),
    h("span", { class: "small", text: `${pct}%` }),
    pct >= 90 ? h("span", { class: "pill warn", text: "near cap" }) : null);
}

function viewUsage() {
  const days = h("select", { id: "days" }, ...[1, 7, 14, 30].map((d) => h("option", { value: d, text: `${d} day${d > 1 ? "s" : ""}` })));
  days.value = "7";
  const body = h("tbody");
  const empty = h("p", { class: "muted hidden", text: "No usage recorded in this period." });
  const org = h("select", { id: "forecast-org" });
  const goal = h("input", { type: "text", id: "forecast-goal", placeholder: "Goal to forecast" });
  const out = h("pre", { class: "hidden" });
  const load = async () => {
    const rows = await api(`/api/v1/usage?days=${days.value}`);
    empty.classList.toggle("hidden", rows.length > 0);
    body.replaceChildren(...rows.map((r) => h("tr", {},
      h("td", { class: "small", text: r.day }),
      h("td", { text: r.provider }),
      h("td", { class: "mono", text: r.model }),
      h("td", { class: "num", text: fmt(r.requests) }),
      h("td", { class: "num", text: r.rpd ? fmt(r.rpd) : "–" }),
      h("td", { class: "num", text: fmt(r.tokens) }),
      h("td", {}, meter(r.share)),
      h("td", { class: "small", text: r.next_reset || "" }))));
  };
  days.addEventListener("change", () => load().catch((e) => toast(e.message)));
  api("/api/v1/orgs").then((orgs) => org.replaceChildren(...orgs.filter((o) => o.valid)
    .map((o) => h("option", { value: o.name, text: o.name })))).catch(() => {});
  const run = h("button", { onclick: async () => {
    try {
      const f = await api("/api/v1/forecast", { method: "POST", body: { org: org.value, goal: goal.value || "(goal)" } });
      out.textContent = f.lines.join("\n");
      out.classList.remove("hidden");
    } catch (err) { toast(err.message); }
  } }, "Forecast");
  mount(
    h("h1", { text: "Usage" }),
    h("p", { class: "sub", text: "What each free model has used, against its daily cap. Resets are shown in IST, on each provider's own clock." }),
    h("div", { class: "panel" },
      h("div", { class: "row spread" }, h("h3", { text: "Ledger" }), h("label", { class: "row" }, "Period ", days)),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["Day", "Provider", "Model", "Requests", "RPD", "Tokens", "Share of daily cap", "Next reset"]
          .map((x, i) => h("th", { class: i >= 3 && i <= 5 ? "num" : "", text: x })))),
        body)), empty),
    h("div", { class: "panel stack" },
      h("h3", { text: "Will it fit today?" }),
      h("div", { class: "row" }, org, goal, run),
      h("p", { class: "hint", text: "The forecast says whether it is measured from earlier runs or estimated from the org file." }),
      out));
  load().catch((e) => toast(e.message));
  const t = setInterval(() => load().catch(() => {}), 10000);
  stopView = () => clearInterval(t);
}

// ---------------------------------------------------------------- approvals
function viewApprovals() {
  const box = h("div");
  mount(h("h1", { text: "Approvals" }),
    h("p", { class: "sub", text: "Gates, questions from agents, and permission to run code. Runs wait here until you decide." }), box);
  let shown = null;
  const load = async () => {
    const items = await api("/api/v1/approvals?pending=true");
    const ids = items.map((a) => a.id).join(",");
    if (ids === shown) return; // don't wipe an answer being typed
    shown = ids;
    box.replaceChildren(...(items.length ? items.map((a) => h("div", { class: "panel" },
      h("p", { class: "small" }, h("a", { href: `#/run/${a.run_id}`, text: `run ${a.run_id}` }), a.agent ? ` · ${a.agent}` : "", ` · ${when(a.created)}`),
      approvalBox(a))) : [h("p", { class: "muted", text: "Nothing is waiting for you." })]));
  };
  load().catch((e) => toast(e.message));
  const t = setInterval(() => load().catch(() => {}), 4000);
  stopView = () => clearInterval(t);
}

route();
refreshBadge();
