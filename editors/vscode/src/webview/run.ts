// Run-detail webview. Everything shown here arrived by postMessage from the extension host and is
// inserted with textContent — never parsed as HTML (ADR-029; eslint forbids innerHTML & co.).
// It makes no network requests (CSP default-src 'none') and holds no token.
import type { EventView, HostMessage, PanelRun, WebviewMessage } from "../protocol";

declare function acquireVsCodeApi(): { postMessage(msg: WebviewMessage): void };

const vscode = acquireVsCodeApi();
const app = document.getElementById("app") as HTMLElement;

type Kid = Node | string | null | undefined | false;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, ...kids: Kid[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return node;
}

function text<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, value: string): HTMLElementTagNameMap[K] {
  const node = el(tag, cls);
  node.textContent = value;
  return node;
}

function button(label: string, msg: WebviewMessage, cls = ""): HTMLButtonElement {
  const b = text("button", cls, label);
  b.type = "button";
  b.addEventListener("click", () => vscode.postMessage(msg));
  return b;
}

const int = (n: number) => Math.round(n || 0).toLocaleString("en-US");
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const when = (ts: number) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function span(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

// ------------------------------------------------------------------ layout (built once)
const header = el("header");
const notice = el("div", "notice hidden");
const facts = el("dl", "facts");
const callouts = el("div");
const timeline = el("ol", "timeline");
const emptyTimeline = text("p", "muted", "No events yet.");
const result = el("section", "hidden");
const files = el("section", "hidden");

app.replaceChildren(
  header, notice, callouts, facts,
  el("section", "", text("h2", "", "Timeline"), emptyTimeline, timeline),
  result, files,
);

// ------------------------------------------------------------------ run summary
function renderRun(r: PanelRun | null): void {
  if (!r) {
    header.replaceChildren(text("p", "muted", "Loading…"));
    return;
  }
  const actions = el("div", "actions");
  if (r.pendingApprovals > 0) {
    actions.append(button(`Review ${r.pendingApprovals === 1 ? "approval" : `${r.pendingApprovals} approvals`}`,
      { type: "approvals" }, "primary"));
  }
  if (r.project) actions.append(button("Review branch", { type: "review" }));
  if (r.active) actions.append(button("Cancel run", { type: "cancel" }, "danger"));
  if (r.resumable) actions.append(button("Resume", { type: "resume" }));

  header.replaceChildren(
    el("div", "title",
      text("h1", "", r.org),
      text("span", `pill ${toneOf(r.status)}`, r.status),
      r.demo ? text("span", "pill", "demo") : null),
    text("p", "goal", r.goal),
    actions,
  );

  const tokens = `${int(r.totals.promptTokens)} in · ${int(r.totals.completionTokens)} out`;
  const rows: [string, string][] = [
    ["Run", r.id],
    ["Started", when(r.created)],
    ["Duration", span((r.finished ?? Date.now() / 1000) - r.created)],
    ["Model calls", int(r.totals.calls)],
    ["Tokens", tokens],
    ["Files", String(r.files.length)],
  ];
  if (r.project) rows.push(["Branch", `${r.project.branch} (from ${r.project.base.slice(0, 10)})`], ["Project", r.project.path]);
  facts.replaceChildren(...rows.flatMap(([k, v]) => [text("dt", "", k), text("dd", "", v)]));

  const notes: HTMLElement[] = [];
  if (r.status === "parked" && r.resumeAt) {
    notes.push(text("div", "callout info", `Parked on daily limits. It resumes by itself around ${when(r.resumeAt)} while the server runs.`));
  }
  if (r.status === "waiting") notes.push(text("div", "callout", "Waiting for you — see the approval prompt, or Review approval above."));
  if (r.error) notes.push(text("div", "callout bad", r.error));
  if (r.unapproved.length) notes.push(text("div", "callout", `Finished, but not everything was approved: ${r.unapproved.join(", ")}.`));
  callouts.replaceChildren(...notes);

  if (r.result) {
    result.replaceChildren(text("h2", "", "Result"), text("pre", "", r.result));
    result.classList.remove("hidden");
  } else {
    result.classList.add("hidden");
  }
  if (r.files.length) {
    files.replaceChildren(text("h2", "", "Files"),
      el("ul", "files", ...r.files.map((f) => el("li", "",
        text("span", "mono", f.path), text("span", "muted", `  v${f.versions}${f.agent ? ` · ${f.agent}` : ""}`)))));
    files.classList.remove("hidden");
  } else {
    files.classList.add("hidden");
  }
}

function toneOf(status: string): string {
  if (status === "succeeded") return "good";
  if (["failed", "rejected"].includes(status)) return "bad";
  if (["queued", "running"].includes(status)) return "live";
  if (["waiting", "parked", "unapproved", "stopped", "interrupted", "cancelling"].includes(status)) return "warn";
  return "";
}

// ------------------------------------------------------------------ timeline
function renderEvent(e: EventView): HTMLLIElement {
  const what = el("div", "what", text("span", "kind", e.kind), text("span", "text", e.text));
  if (e.approvalId) what.append(button("Decide…", { type: "approval", id: e.approvalId }, "small"));
  if (e.detail) {
    const details = el("details", "", text("summary", "", "details"), text("pre", "", e.detail));
    what.append(details);
  }
  return el("li", `ev ${e.tone}`,
    text("time", "t", clock(e.ts)),
    text("span", "who", e.agent),
    what);
}

function appendEvents(events: EventView[]): void {
  if (!events.length) return;
  const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
  timeline.append(...events.map(renderEvent));
  emptyTimeline.classList.add("hidden");
  if (nearBottom) window.scrollTo({ top: document.body.scrollHeight });
}

// ------------------------------------------------------------------ messages from the host
window.addEventListener("message", (ev: MessageEvent) => {
  const msg = ev.data as HostMessage;
  if (!msg || typeof msg !== "object") return;
  switch (msg.type) {
    case "reset":
      timeline.replaceChildren();
      emptyTimeline.classList.remove("hidden");
      renderRun(msg.run);
      appendEvents(msg.events);
      break;
    case "run":
      renderRun(msg.run);
      break;
    case "events":
      appendEvents(msg.events);
      break;
    case "notice":
      notice.textContent = msg.text;
      notice.classList.remove("hidden");
      break;
  }
});

vscode.postMessage({ type: "ready" });
