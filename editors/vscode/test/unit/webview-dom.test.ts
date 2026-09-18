// Runs the bundled webview script (dist/webview/run.js) in a sandbox with a minimal fake DOM, and
// drives it with the host's messages. The fake DOM throws if anything assigns innerHTML/outerHTML,
// so a passing test also shows model text only ever becomes Text nodes.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import * as vm from "node:vm";
import type { EventView, HostMessage, PanelRun } from "../../src/protocol";

const BUNDLE = path.resolve(__dirname, "..", "..", "..", "dist", "webview", "run.js");

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
  readonly tagName: string;
  private listeners: Record<string, (() => void)[]> = {};
  constructor(tag: string) {
    super();
    this.tagName = tag.toUpperCase();
  }
  get classList() {
    const names = () => this.className.split(/\s+/).filter(Boolean);
    return {
      add: (c: string) => { if (!names().includes(c)) this.className = [...names(), c].join(" "); },
      remove: (c: string) => { this.className = names().filter((n) => n !== c).join(" "); },
      contains: (c: string) => names().includes(c),
    };
  }
  addEventListener(type: string, fn: () => void): void {
    (this.listeners[type] ??= []).push(fn);
  }
  click(): void {
    for (const fn of this.listeners.click ?? []) fn();
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
  const posted: unknown[] = [];
  const handlers: ((ev: { data: HostMessage }) => void)[] = [];
  const sandbox = {
    document: {
      createElement: (t: string) => new FakeElement(t),
      createTextNode: (t: string) => new FakeText(t),
      getElementById: (id: string) => (id === "app" ? app : null),
      body: { scrollHeight: 0 },
    },
    window: {
      addEventListener: (type: string, fn: (ev: { data: HostMessage }) => void) => { if (type === "message") handlers.push(fn); },
      innerHeight: 800,
      scrollY: 0,
      scrollTo: () => undefined,
    },
    // Objects made inside the sandbox have its own Object prototype; copy them into this realm.
    acquireVsCodeApi: () => ({ postMessage: (m: unknown) => posted.push(JSON.parse(JSON.stringify(m))) }),
    Date,
    Math,
    JSON,
  };
  vm.runInNewContext(fs.readFileSync(BUNDLE, "utf8"), sandbox);
  const send = (msg: HostMessage) => handlers.forEach((h) => h({ data: msg }));
  return { app, posted, send };
}

const RUN: PanelRun = {
  id: "20260918-120000-abc123", org: "software-team", goal: "Add a --json flag", status: "waiting", demo: true,
  created: 1_758_000_000, finished: null, error: null, result: "done <b>bold</b>", resumeAt: null,
  project: { path: "C:\\repo", base: "0123456789abcdef", branch: "cadre/20260918-120000-abc123" },
  totals: { calls: 3, promptTokens: 1200, completionTokens: 80 },
  files: [{ path: "app.py", versions: 2, agent: "builder" }],
  unapproved: [], pendingApprovals: 1, active: true, resumable: false,
};

const EVIL = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
const EVENTS: EventView[] = [
  { seq: 1, ts: 1_758_000_001, kind: "agent.answer", agent: "builder", text: EVIL, tone: "", detail: EVIL },
  { seq: 2, ts: 1_758_000_002, kind: "approval.requested", agent: "verifier", text: "waiting for permission to run code", tone: "warn", approvalId: "ap1" },
];

test("the bundle exists (npm test builds it first)", () => {
  assert.ok(fs.existsSync(BUNDLE), BUNDLE);
});

test("says ready, then renders a reset: header, facts, timeline, result, files", () => {
  const { app, posted, send } = load();
  assert.deepEqual(posted, [{ type: "ready" }]);
  send({ type: "reset", run: RUN, events: EVENTS });
  const text = app.textContent;
  for (const s of ["software-team", "waiting", "demo", "Add a --json flag", "cadre/20260918-120000-abc123 (from 0123456789)",
    "1,200 in · 80 out", "waiting for permission to run code", "app.py", "done <b>bold</b>"]) {
    assert.ok(text.includes(s), `renders ${JSON.stringify(s)}`);
  }
  assert.equal(app.find((e) => e.tagName === "LI" && e.className.startsWith("ev")).length, 2);
});

test("model text stays text: no element is created from it", () => {
  const { app, send } = load();
  send({ type: "reset", run: RUN, events: EVENTS });
  assert.ok(app.textContent.includes(EVIL), "shown literally");
  assert.equal(app.find((e) => e.tagName === "IMG" || e.tagName === "SCRIPT" || e.tagName === "B").length, 0);
});

test("buttons post only the fixed messages", () => {
  const { app, posted, send } = load();
  send({ type: "reset", run: RUN, events: EVENTS });
  const buttons = app.find((e) => e.tagName === "BUTTON");
  const byLabel = (l: string) => buttons.find((b) => b.textContent === l);
  byLabel("Review branch")?.click();
  byLabel("Cancel run")?.click();
  byLabel("Review approval")?.click();
  byLabel("Decide…")?.click();
  assert.deepEqual(posted.slice(1), [{ type: "review" }, { type: "cancel" }, { type: "approvals" }, { type: "approval", id: "ap1" }]);
  assert.equal(byLabel("Resume"), undefined, "an active run offers no Resume");
});

test("appends streamed events and updates the run in place", () => {
  const { app, send } = load();
  send({ type: "reset", run: RUN, events: [] });
  assert.ok(app.textContent.includes("No events yet."));
  send({ type: "events", events: [EVENTS[0]] });
  send({ type: "events", events: [EVENTS[1]] });
  assert.equal(app.find((e) => e.tagName === "LI" && e.className.startsWith("ev")).length, 2);
  send({ type: "run", run: { ...RUN, status: "succeeded", active: false, resumable: false, pendingApprovals: 0 } });
  assert.ok(app.textContent.includes("succeeded"));
  assert.equal(app.find((e) => e.tagName === "BUTTON" && e.textContent === "Cancel run").length, 0);
});
