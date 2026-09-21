// Runs the bundled panel webview (dist/webview/panels.js) in a sandbox with a small fake DOM and
// drives it with the host's messages, as webview-dom.test.ts does for the run view. The fake DOM
// throws if anything assigns innerHTML/outerHTML, so passing also shows that model-written text
// (prompts, facts, org descriptions, model names) only ever becomes Text nodes.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import * as vm from "node:vm";
import type {
  ApprovalsView, MemoryView, OrgsView, PanelHostMessage, PanelMessage, StartView, UsageView,
} from "../../src/protocol";
import { isAllowedPanelMessage, isPanelMessage } from "../../src/protocol";

const BUNDLE = path.resolve(__dirname, "..", "..", "..", "dist", "webview", "panels.js");

class FakeNode {
  childNodes: FakeNode[] = [];
  append(...nodes: FakeNode[]): void {
    this.childNodes.push(...nodes);
  }
  replaceChildren(...nodes: FakeNode[]): void {
    this.childNodes = [...nodes];
  }
  get textContent(): string {
    return this.childNodes.map((n) => n.textContent).join("");
  }
  set textContent(v: string) {
    this.childNodes = [new FakeText(String(v))];
  }
}

class FakeText extends FakeNode {
  constructor(readonly data: string) {
    super();
  }
  get textContent(): string {
    return this.data;
  }
}

class FakeElement extends FakeNode {
  className = "";
  type = "";
  id = "";
  title = "";
  placeholder = "";
  rows = 0;
  maxLength = -1;
  checked = false;
  disabled = false;
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  readonly tagName: string;
  private listeners: Record<string, (() => void)[]> = {};
  private _value = "";
  constructor(tag: string) {
    super();
    this.tagName = tag.toUpperCase();
  }
  get value(): string {
    if (this.tagName === "SELECT") {
      const opts = this.childNodes.filter((n): n is FakeElement => n instanceof FakeElement && n.tagName === "OPTION");
      return opts.some((o) => o.value === this._value) ? this._value : (opts[0]?.value ?? "");
    }
    return this._value;
  }
  set value(v: string) {
    this._value = String(v);
  }
  get classList() {
    const names = () => this.className.split(/\s+/).filter(Boolean);
    return {
      add: (c: string) => { if (!names().includes(c)) this.className = [...names(), c].join(" "); },
      remove: (c: string) => { this.className = names().filter((n) => n !== c).join(" "); },
      contains: (c: string) => names().includes(c),
    };
  }
  setAttribute(k: string, v: string): void {
    this.attrs[k] = String(v);
  }
  addEventListener(type: string, fn: () => void): void {
    (this.listeners[type] ??= []).push(fn);
  }
  fire(type: string): void {
    for (const fn of this.listeners[type] ?? []) fn();
  }
  click(): void {
    if (!this.disabled) this.fire("click");
  }
  set innerHTML(_v: string) {
    throw new Error("innerHTML was assigned");
  }
  set outerHTML(_v: string) {
    throw new Error("outerHTML was assigned");
  }
  get textContent(): string {
    return super.textContent;
  }
  set textContent(v: string) {
    super.textContent = v;
  }
  find(pred: (e: FakeElement) => boolean): FakeElement[] {
    const out: FakeElement[] = [];
    const walk = (n: FakeNode) => {
      for (const c of n.childNodes) {
        if (c instanceof FakeElement) {
          if (pred(c)) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }
}

function load() {
  const app = new FakeElement("main");
  const posted: PanelMessage[] = [];
  const handlers: ((ev: { data: PanelHostMessage }) => void)[] = [];
  const sandbox = {
    document: {
      createElement: (t: string) => new FakeElement(t),
      createTextNode: (t: string) => new FakeText(t),
      getElementById: (id: string) => (id === "app" ? app : null),
    },
    window: {
      addEventListener: (type: string, fn: (ev: { data: PanelHostMessage }) => void) => { if (type === "message") handlers.push(fn); },
    },
    acquireVsCodeApi: () => ({ postMessage: (m: unknown) => posted.push(JSON.parse(JSON.stringify(m))) }),
    Date,
    Math,
    JSON,
    Number,
    String,
  };
  vm.runInNewContext(fs.readFileSync(BUNDLE, "utf8"), sandbox);
  const send = (msg: PanelHostMessage) => handlers.forEach((h) => h({ data: msg }));
  const buttons = () => app.find((e) => e.tagName === "BUTTON");
  const byLabel = (label: string) => buttons().filter((b) => b.textContent === label);
  const all = (tag: string) => app.find((e) => e.tagName === tag);
  return { app, posted, send, buttons, byLabel, all };
}

const EVIL = '<img src=x onerror="alert(1)"><script>alert(2)</script><b>bold</b>';

function noMarkupFromText(app: FakeElement): void {
  assert.equal(app.find((e) => ["IMG", "SCRIPT", "B", "IFRAME"].includes(e.tagName)).length, 0,
    "model text never becomes elements");
}

/** Every message a panel posted is one the host would accept from that kind of panel. */
function allAdmitted(kind: Parameters<typeof isAllowedPanelMessage>[0], posted: PanelMessage[]): void {
  for (const m of posted) assert.ok(isAllowedPanelMessage(kind, m), `${kind} may post ${JSON.stringify(m)}`);
}

// ------------------------------------------------------------------ fixtures
const USAGE: UsageView = {
  days: 7,
  models: [
    { provider: "groq", model: EVIL, usable: true, resets: "resets in 3h 20m",
      requests: { used: 950, cap: 1000, share: 0.95, level: "hot", text: "950 / 1,000 requests · 95%", state: "at the cap" },
      tokens: { used: 900, cap: null, share: null, level: "none", text: "900 tokens · no daily cap", state: "" } },
    { provider: "gemini", model: "flash", usable: false, resets: "",
      requests: { used: 5, cap: 20, share: 0.25, level: "ok", text: "5 / 20 requests · 25%", state: "" },
      tokens: { used: 0, cap: 250000, share: 0, level: "ok", text: "0 / 250,000 tokens · 0%", state: "" } },
  ],
  ledger: [{ day: "2026-09-20", provider: "groq", model: "m", requests: 35, rpd: 1000, tokens: 47006, tpd: 200000, share: 0.24, nextReset: "" }],
  totals: { requests: 35, tokens: 47006 },
};

const APPROVALS: ApprovalsView = {
  items: [
    { id: "ap-exec", runId: "r1", kind: "exec", label: "Run code", agent: "", prompt: `tests: python -m pytest\n${EVIL}`, created: 1_758_000_000 },
    { id: "ap-mem", runId: "r2", kind: "memory", label: "Remember this fact?", agent: "retrospector", prompt: EVIL, created: 1_758_000_100 },
  ],
};

const START: StartView = {
  orgs: [
    { name: "my-team", source: "yours", workflow: "manager", description: EVIL, checks: [] },
    { name: "project-finisher", source: "template", workflow: "sequence", description: "Finishes a repo", checks: ["tests"] },
  ],
  folders: [{ index: 0, name: "repo" }],
  trusted: true,
  hasProvider: true,
  prefillOrg: "",
};

const MEMORY: MemoryView = {
  scopes: ["global", "project:e863025677c9"],
  skipped: [`global entry 4: ${EVIL}`],
  groups: [
    { scope: "global", items: [
      { id: "m-prop", scope: "global", text: EVIL, tags: [], source: "r1/retrospective", date: "2026-09-20", approved: false, pinned: false, private: false, approvalId: "ap9", stale: false },
      { id: "m-keep", scope: "global", text: "tests run with pytest -q", tags: ["tests"], source: "human", date: "2026-09-19", approved: true, pinned: true, private: true, approvalId: "", stale: false },
    ] },
  ],
};

const ORGS: OrgsView = {
  orgs: [
    { name: "mine", source: "yours", valid: false, title: "mine", description: EVIL, workflow: "", agents: [], checks: [], budget: [], errors: [EVIL] },
    { name: "decision-board", source: "template", valid: true, title: "decision-board", description: "A board votes", workflow: "council",
      agents: [{ id: "ceo", role: "chair", tier: "strong", tools: [] }], checks: [], budget: [["max calls", "80"]], errors: [] },
  ],
};

// ------------------------------------------------------------------ tests
test("the panel bundle exists (npm test builds it first)", () => {
  assert.ok(fs.existsSync(BUNDLE), BUNDLE);
});

test("says ready first, and posts nothing it isn't asked to", () => {
  const { posted } = load();
  assert.deepEqual(posted, [{ type: "ready" }]);
});

test("usage: meters carry width, an aria value and the state in words; the ledger totals", () => {
  const { app, posted, send, all } = load();
  send({ type: "view", data: { kind: "usage", view: USAGE } });
  const text = app.textContent;
  for (const s of ["Usage", "950 / 1,000 requests · 95%", "at the cap", "900 tokens · no daily cap", "resets in 3h 20m",
    "not usable now", "47,006", "Total"]) assert.ok(text.includes(s), `renders ${JSON.stringify(s)}`);
  const tracks = app.find((e) => e.className.startsWith("track"));
  assert.equal(tracks.length, 3, "the no-cap tokens meter has no bar (it would read as 0%)");
  assert.equal(tracks[0].attrs.role, "meter");
  assert.equal(tracks[0].attrs["aria-valuenow"], "95");
  assert.ok(tracks[0].className.includes("hot"));
  assert.equal(tracks[0].childNodes[0] instanceof FakeElement && (tracks[0].childNodes[0] as FakeElement).style.width, "95%");
  assert.equal(tracks[0].title, "950 / 1,000 requests · 95%");
  noMarkupFromText(app);
  // the period picker posts only an allowed day count
  const days = all("SELECT")[0];
  days.value = "30";
  days.fire("change");
  assert.deepEqual(posted.at(-1), { type: "days", days: 30 });
  allAdmitted("usage", posted);
});

test("approvals: Decide… posts only an id — nothing is approved from the page", () => {
  const { app, posted, send, byLabel } = load();
  send({ type: "view", data: { kind: "approvals", view: APPROVALS } });
  assert.ok(app.textContent.includes("Nothing runs until you choose Allow execution"), "an exec card says how it is decided");
  assert.ok(app.textContent.includes(EVIL), "the prompt is shown literally");
  noMarkupFromText(app);
  const decides = byLabel("Decide…");
  assert.equal(decides.length, 2);
  decides[0].click();
  byLabel("Open run")[1].click();
  assert.deepEqual(posted.slice(1), [{ type: "decide", id: "ap-exec" }, { type: "openRun", id: "r2" }]);
  assert.equal(byLabel("Approve").length + byLabel("Allow execution").length, 0, "no approve button on the page");
  allAdmitted("approvals", posted);
  send({ type: "view", data: { kind: "approvals", view: { items: [] } } });
  assert.ok(app.textContent.includes("Nothing is waiting for you."));
});

test("new run: posts a folder index, never a path; the goal survives a refresh", () => {
  const { app, posted, send, byLabel, all } = load();
  send({ type: "view", data: { kind: "start", view: START } });
  const [start] = byLabel("Start run");
  const [forecast] = byLabel("Forecast");
  assert.ok(start.disabled && forecast.disabled, "no goal, no run");
  assert.ok(app.textContent.includes("no switch to turn that off"), "says exec approval can't be skipped");
  const goal = all("TEXTAREA")[0];
  goal.value = "  Make the failing tests pass  ";
  goal.fire("input");
  assert.equal(start.disabled, false);
  const [org, folder] = all("SELECT");
  org.value = "project-finisher";
  org.fire("change");
  assert.ok(app.textContent.includes("Checks: tests."));
  assert.equal(folder.value, "0");
  forecast.click();
  start.click();
  assert.deepEqual(posted.slice(1), [
    { type: "forecast", org: "project-finisher", goal: "Make the failing tests pass", folder: 0, private: false },
    { type: "start", org: "project-finisher", goal: "Make the failing tests pass", folder: 0, private: false, demo: false },
  ]);
  allAdmitted("start", posted);
  // a refresh re-posts the view: the form is kept, not rebuilt, so the typed goal stays
  send({ type: "view", data: { kind: "start", view: START } });
  assert.equal(all("TEXTAREA")[0], goal);
  assert.equal(goal.value, "  Make the failing tests pass  ");
  assert.equal(org.value, "project-finisher");
  noMarkupFromText(app);
});

test("new run: without a key only the demo; an untrusted workspace can only run with no folder", () => {
  const { posted, send, byLabel, all } = load();
  send({ type: "view", data: { kind: "start", view: { ...START, hasProvider: false, trusted: false } } });
  const demo = all("INPUT").find((i) => i.type === "checkbox")!;
  assert.ok(demo.checked && demo.disabled);
  const folder = all("SELECT")[1];
  assert.equal(folder.value, "-1");
  assert.ok(folder.find((o) => o.tagName === "OPTION" && o.value === "0")[0].disabled);
  const goal = all("TEXTAREA")[0];
  goal.value = "Pick one";
  goal.fire("input");
  byLabel("Start run")[0].click();
  assert.deepEqual(posted.at(-1), { type: "start", org: "my-team", goal: "Pick one", folder: -1, private: false, demo: true });
});

test("new run: the chosen folder is followed by name when the open folders change", () => {
  const { send, all } = load();
  const view = (...names: string[]): PanelHostMessage =>
    ({ type: "view", data: { kind: "start", view: { ...START, folders: names.map((name, index) => ({ index, name })) } } });
  send(view("app", "docs"));
  const folder = all("SELECT")[1];
  folder.value = "1"; // docs
  // "app" is closed, so docs moves to index 0: the form must still mean docs, not whatever index 1 now is
  send(view("docs"));
  assert.equal(folder.value, "0");
  send(view("web", "docs"));
  assert.equal(folder.value, "1", "reordered: still docs");
  folder.value = "-1";
  send(view("docs"));
  assert.equal(folder.value, "-1", "\"No folder\" stays \"No folder\"");
  folder.value = "0"; // docs
  send(view("web", "api"));
  assert.equal(folder.value, "0", "the chosen folder closed: back to the first, which the list now shows");
  assert.deepEqual(folder.find((o) => o.tagName === "OPTION").map((o) => o.value), ["0", "1", "-1"]);
});

test("new run: a prefill selects the org, busy disables the buttons, a forecast is shown", () => {
  const { app, send, byLabel, all } = load();
  send({ type: "view", data: { kind: "start", view: { ...START, prefillOrg: "project-finisher" } } });
  assert.equal(all("SELECT")[0].value, "project-finisher");
  const goal = all("TEXTAREA")[0];
  goal.value = "g";
  goal.fire("input");
  send({ type: "busy", busy: true });
  assert.ok(byLabel("Start run")[0].disabled);
  send({ type: "busy", busy: false });
  assert.equal(byLabel("Start run")[0].disabled, false);
  send({ type: "forecast", forecast: { verdict: "fits_now", headline: "fits now",
    lines: ["Forecast: fits now", "calls: median 8", EVIL] } });
  assert.ok(app.textContent.includes("fits now") && app.textContent.includes("calls: median 8"));
  const pre = app.find((e) => e.tagName === "PRE").at(-1)!;
  assert.ok(!pre.textContent.includes("Forecast:"), "the headline is shown once, as the pill");
  noMarkupFromText(app);
});

test("memory: counts folded characters, refuses over 400, and posts the folded fact", () => {
  const { app, posted, send, byLabel, all } = load();
  send({ type: "view", data: { kind: "memory", view: MEMORY } });
  const [remember] = byLabel("Remember");
  const fact = all("TEXTAREA")[0];
  assert.ok(remember.disabled, "nothing typed yet");
  fact.value = "x".repeat(401);
  fact.fire("input");
  assert.ok(remember.disabled && app.textContent.includes("401 / 400"));
  fact.value = "  tests   run with\n unittest  ";
  fact.fire("input");
  assert.ok(app.textContent.includes("23 / 400"));
  const scope = all("INPUT").find((i) => i.attrs.list === "cadre-memory-scopes")!;
  scope.value = "project:e863025677c9";
  remember.click();
  assert.deepEqual(posted.at(-1),
    { type: "memoryAdd", scope: "project:e863025677c9", text: "tests run with unittest", pinned: false, private: false });
  allAdmitted("memory", posted);
  // once the new fact comes back in the list, the box clears
  const withIt: MemoryView = { ...MEMORY, groups: [{ scope: "project:e863025677c9", items: [
    { id: "m-new", scope: "project:e863025677c9", text: "tests run with unittest", tags: [], source: "human", date: "2026-09-21",
      approved: true, pinned: false, private: false, approvalId: "", stale: false }] }] };
  send({ type: "view", data: { kind: "memory", view: withIt } });
  assert.equal(fact.value, "");
});

test("memory: a proposal is decided through the host; an approved fact can be deleted", () => {
  const { app, posted, send, byLabel } = load();
  send({ type: "view", data: { kind: "memory", view: MEMORY } });
  const text = app.textContent;
  for (const s of ["proposed — waiting for you", "approved", "pinned", "private", "Skipped entries", "Tags: tests"]) {
    assert.ok(text.includes(s), `renders ${JSON.stringify(s)}`);
  }
  assert.ok(text.includes(EVIL), "a proposed fact is shown literally");
  noMarkupFromText(app);
  byLabel("Decide…")[0].click();
  byLabel("Delete")[0].click();
  assert.deepEqual(posted.slice(1), [{ type: "decide", id: "ap9" }, { type: "memoryRemove", id: "m-keep" }]);
  assert.equal(byLabel("Delete").length, 1, "only approved facts can be deleted from here");
  allAdmitted("memory", posted);
});

test("memory: a stale proposal says what happened and can only be deleted", () => {
  const { app, posted, send, byLabel } = load();
  const stale: MemoryView = { scopes: ["global"], skipped: [], groups: [{ scope: "global", items: [
    { id: "m-orphan", scope: "global", text: "an orphaned proposal", tags: [], source: "r1/retrospective", date: "2026-09-19",
      approved: false, pinned: false, private: false, approvalId: "", stale: true }] }] };
  send({ type: "view", data: { kind: "memory", view: stale } });
  assert.ok(app.textContent.includes("proposal — no longer waiting"));
  assert.ok(app.textContent.includes("no run will ever see it"));
  assert.equal(byLabel("Decide…").length, 0, "nothing to decide: its approval is gone");
  byLabel("Delete")[0].click();
  assert.deepEqual(posted.at(-1), { type: "memoryRemove", id: "m-orphan" });
});

test("organisations: your own opens for editing, templates for viewing; an invalid org can't start a run", () => {
  const { app, posted, send, byLabel } = load();
  send({ type: "view", data: { kind: "orgs", view: ORGS } });
  const text = app.textContent;
  for (const s of ["mine", "yours", "invalid", "decision-board", "template", "valid", "council", "ceo", "chair", "max calls", "80"]) {
    assert.ok(text.includes(s), `renders ${JSON.stringify(s)}`);
  }
  noMarkupFromText(app);
  byLabel("Edit YAML")[0].click();
  byLabel("View YAML")[0].click();
  assert.equal(byLabel("New run with this org").length, 1, "only the valid org offers a run");
  byLabel("New run with this org")[0].click();
  assert.deepEqual(posted.slice(1), [
    { type: "openOrg", name: "mine" }, { type: "openOrg", name: "decision-board" }, { type: "useOrg", name: "decision-board" },
  ]);
  allAdmitted("orgs", posted);
});

test("notices: a load failure clears once a view arrives; an action's error stays", () => {
  const { app, send } = load();
  const notice = () => app.find((e) => e.className.startsWith("notice"))[0];
  send({ type: "notice", text: "Could not load: server gone", tone: "bad" });
  assert.ok(notice().className.includes("bad"));
  send({ type: "view", data: { kind: "orgs", view: ORGS } });
  assert.ok(notice().className.includes("hidden"), "superseded by a fresh view");
  send({ type: "notice", text: "that looks like an API key; refusing to store it", tone: "bad" });
  send({ type: "view", data: { kind: "orgs", view: ORGS } });
  assert.ok(!notice().className.includes("hidden"), "an action's error is not wiped by a refresh");
  assert.ok(notice().textContent.includes("looks like an API key"));
});

test("everything the panels post is a valid panel message", () => {
  // belt and braces over the per-panel checks above: drive every panel and validate all output
  const { posted, send, buttons } = load();
  for (const data of [
    { kind: "usage", view: USAGE }, { kind: "approvals", view: APPROVALS }, { kind: "memory", view: MEMORY },
    { kind: "orgs", view: ORGS },
  ] as const) {
    send({ type: "view", data });
    for (const b of buttons()) b.click();
  }
  assert.ok(posted.length > 5);
  for (const m of posted) assert.ok(isPanelMessage(m), JSON.stringify(m));
});
