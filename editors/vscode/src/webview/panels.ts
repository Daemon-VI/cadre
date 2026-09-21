// The panel webviews (extension 1.4.0): Usage, Approvals, New run, Memory, Organisations — one
// bundle, told which panel it is by the first view it is posted. Everything shown arrived by
// postMessage from the extension host and goes in with textContent, never parsed as HTML
// (ADR-029; eslint forbids innerHTML & co.). No network access (CSP default-src 'none'), no token.
// The only thing a panel can do is post one of the PanelMessage shapes, which the host validates.
import type {
  ApprovalItem, ApprovalsView, ForecastView, MemoryItem, MemoryView, MeterView, OrgCard, OrgsView, PanelHostMessage,
  PanelKind, PanelMessage, PanelView, StartOrg, StartView, Tone, UsageModelRow, UsageView,
} from "../protocol";

declare function acquireVsCodeApi(): { postMessage(msg: PanelMessage): void };

const vscode = acquireVsCodeApi();
const app = document.getElementById("app") as HTMLElement;
const post = (msg: PanelMessage) => vscode.postMessage(msg);

// ------------------------------------------------------------------ DOM helpers
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

/** A button that posts one fixed message. */
function button(label: string, msg: PanelMessage, cls = ""): HTMLButtonElement {
  return act(label, () => post(msg), cls);
}

/** A button with a local handler (which may post). */
function act(label: string, fn: () => void, cls = ""): HTMLButtonElement {
  const b = text("button", cls, label);
  b.type = "button";
  b.addEventListener("click", fn);
  return b;
}

/** replaceChildren, skipping absent optional parts (as el() does). */
function put(parent: HTMLElement, ...kids: Kid[]): void {
  parent.replaceChildren(...kids.filter((k): k is Node | string => k !== null && k !== undefined && k !== false));
}

function option(value: string, label: string): HTMLOptionElement {
  const o = text("option", "", label);
  o.value = value;
  return o;
}

function field(label: string, control: HTMLElement, ...extra: Kid[]): HTMLLabelElement {
  return el("label", "field", text("span", "field-label", label), control, ...extra);
}

function check(label: string, box: HTMLInputElement): HTMLLabelElement {
  box.type = "checkbox";
  return el("label", "check", box, text("span", "", label));
}

const int = (n: number) => Math.round(n || 0).toLocaleString("en-US");
const pct = (x: number) => `${Math.round(x * 100)}%`;
const when = (ts: number) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
/** As the engine stores a memory entry: whitespace folded to single spaces. */
const fold = (s: string) => s.split(/\s+/).filter(Boolean).join(" ");

// ------------------------------------------------------------------ frame (built on the first view)
const HEAD: Record<PanelKind, [string, string]> = {
  usage: ["Usage", "What each free model has spent today, against its daily cap. Caps reset on each provider's own clock."],
  approvals: ["Approvals", "Everything waiting for you. Decide… opens the same dialog a notification would — nothing is approved from this page."],
  start: ["New run", "Put an organisation of agents to work on a goal."],
  memory: ["Memory", "Facts remembered across runs, replayed to builders and managers as data — never as instructions. Reviewers and voters get none."],
  orgs: ["Organisations", "Your organisations and the built-in templates: who is on each team, its checks and its budget."],
};

let kind: PanelKind | undefined;
const LOAD_PROBLEM = /^(Could not load|No Cadre server answers)/;
const header = el("header");
const notice = el("div", "notice hidden");
const body = el("div", "panel-body");
app.replaceChildren(header, notice, body);

function frame(k: PanelKind): void {
  if (kind === k) return;
  kind = k;
  const [title, sub] = HEAD[k];
  header.replaceChildren(
    el("div", "title", text("h1", "", title), button("Refresh", { type: "refresh" }, "small")),
    text("p", "sub muted", sub),
  );
}

function showNotice(value: string, tone: Tone): void {
  notice.textContent = value;
  notice.className = `notice ${tone}`;
}

// ------------------------------------------------------------------ usage
function meter(label: string, m: MeterView): HTMLElement {
  const head = el("div", "meter-head",
    text("span", "meter-label", label),
    text("span", "meter-value", m.text),
    // severity is a word too, never colour alone
    m.state ? text("span", `pill ${m.level === "hot" ? "bad" : "warn"}`, m.state) : null);
  if (m.share === null) return el("div", "meter", head);
  const fill = el("div", `fill ${m.level}`);
  fill.style.width = `${Math.min(100, Math.max(0, Math.round(m.share * 100)))}%`;
  const track = el("div", `track ${m.level}`, fill);
  track.setAttribute("role", "meter");
  track.setAttribute("aria-label", `${label}: ${m.text}`);
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", String(Math.round(m.share * 100)));
  track.title = m.text;
  return el("div", "meter", head, track);
}

function modelRow(r: UsageModelRow): HTMLElement {
  return el("section", `model${r.usable ? "" : " unusable"}`,
    el("div", "model-head",
      text("span", "mono", `${r.provider}/${r.model}`),
      r.usable ? null : text("span", "pill", "not usable now"),
      r.resets ? text("span", "muted", r.resets) : null),
    meter("Requests", r.requests),
    meter("Tokens", r.tokens));
}

function renderUsage(v: UsageView): void {
  const days = el("select");
  days.append(...[1, 7, 14, 30].map((d) => option(String(d), d === 1 ? "Today" : `Last ${d} days`)));
  days.value = String(v.days);
  days.addEventListener("change", () => post({ type: "days", days: Number(days.value) }));

  const cols = ["Day", "Provider", "Model", "Requests", "RPD", "Tokens", "TPD", "Share", "Next reset"];
  const numeric = new Set(["Requests", "RPD", "Tokens", "TPD", "Share"]);
  const cap = (n: number | null) => (n === null ? "–" : int(n));
  const ledger = v.ledger.length
    ? el("div", "table-wrap", el("table", "ledger",
      el("thead", "", el("tr", "", ...cols.map((c) => text("th", numeric.has(c) ? "num" : "", c)))),
      el("tbody", "", ...v.ledger.map((r) => el("tr", "",
        text("td", "", r.day), text("td", "", r.provider), text("td", "mono", r.model),
        text("td", "num", int(r.requests)), text("td", "num", cap(r.rpd)),
        text("td", "num", int(r.tokens)), text("td", "num", cap(r.tpd)),
        text("td", "num", r.share === null ? "–" : pct(r.share)), text("td", "muted", r.nextReset)))),
      el("tfoot", "", el("tr", "",
        text("td", "", "Total"), text("td", "", ""), text("td", "", ""),
        text("td", "num", int(v.totals.requests)), text("td", "", ""),
        text("td", "num", int(v.totals.tokens)), text("td", "", ""), text("td", "", ""), text("td", "", "")))))
    : text("p", "empty", "No usage recorded in this period.");

  body.replaceChildren(
    text("h2", "", "Today, per model"),
    text("p", "hint", "Caps here keep back each provider's reserve (10% unless you set reserve_pct), so a "
      + "1,000-request cap shows as 900. The ledger's share is of the full cap."),
    v.models.length
      ? el("div", "meters", ...v.models.map(modelRow))
      : text("p", "empty", "No models configured yet. Add a key with “Cadre: Add provider”."),
    el("div", "section-head", text("h2", "", "Ledger"), field("Period", days)),
    ledger,
  );
}

// ------------------------------------------------------------------ approvals
function approvalCard(a: ApprovalItem): HTMLElement {
  const exec = a.kind === "exec";
  return el("li", `card${exec ? " exec" : ""}`,
    el("div", "card-head",
      text("span", `pill ${exec ? "warn" : ""}`, a.label),
      text("span", "muted", `run ${a.runId}${a.agent ? ` · ${a.agent}` : ""} · ${when(a.created)}`)),
    text("pre", "prompt", a.prompt),
    exec ? text("p", "hint", "Decide… opens a dialog listing each exact command and where it runs. "
      + "Nothing runs until you choose Allow execution there.") : null,
    el("div", "actions",
      button("Decide…", { type: "decide", id: a.id }, "primary"),
      button("Open run", { type: "openRun", id: a.runId })));
}

function renderApprovals(v: ApprovalsView): void {
  body.replaceChildren(v.items.length
    ? el("ol", "cards", ...v.items.map(approvalCard))
    : text("p", "empty", "Nothing is waiting for you."));
}

// ------------------------------------------------------------------ new run
interface StartForm {
  org: HTMLSelectElement;
  about: HTMLElement;
  goal: HTMLTextAreaElement;
  folder: HTMLSelectElement;
  folderNote: HTMLElement;
  demo: HTMLInputElement;
  demoNote: HTMLElement;
  priv: HTMLInputElement;
  forecastBtn: HTMLButtonElement;
  startBtn: HTMLButtonElement;
  forecast: HTMLElement;
  orgs: StartOrg[];
  orgKey: string;
  folderKey: string;
  folderNames: string[];
}

let form: StartForm | undefined;
let busy = false;

function buildStartForm(): StartForm {
  const f: StartForm = {
    org: el("select"),
    about: el("p", "muted about"),
    goal: el("textarea"),
    folder: el("select"),
    folderNote: el("p", "hint hidden"),
    demo: el("input"),
    demoNote: el("p", "hint hidden"),
    priv: el("input"),
    forecastBtn: act("Forecast", () => submit("forecast")),
    startBtn: act("Start run", () => submit("start"), "primary"),
    forecast: el("section", "forecast hidden"),
    orgs: [],
    orgKey: "",
    folderKey: "",
    folderNames: [],
  };
  f.goal.rows = 4;
  f.goal.maxLength = 4000;
  f.goal.placeholder = "e.g. Add a --json flag to the export command, with tests";
  f.org.addEventListener("change", describeOrg);
  f.goal.addEventListener("input", validate);
  body.replaceChildren(el("form", "run-form",
    field("Organisation", f.org, f.about),
    field("Goal", f.goal),
    field("Work on", f.folder, f.folderNote),
    check("Offline demo model — scripted, needs no key, spends no quota", f.demo), f.demoNote,
    check("Private — only providers that don't train on prompts", f.priv),
    text("p", "hint", "Checks never run without your approval: running code always asks in a dialog that "
      + "lists each command. This form has no switch to turn that off, on purpose."),
    el("div", "actions", f.forecastBtn, f.startBtn)),
  f.forecast);
  return f;
}

function describeOrg(): void {
  if (!form) return;
  const o = form.orgs.find((x) => x.name === form!.org.value);
  form.about.textContent = o
    ? [o.description, o.checks.length ? `Checks: ${o.checks.join(", ")}.` : "No checks."].filter(Boolean).join(" ")
    : "";
}

function validate(): void {
  if (!form) return;
  const ok = !!form.org.value && form.goal.value.trim().length > 0;
  form.forecastBtn.disabled = busy || !ok;
  form.startBtn.disabled = busy || !ok;
}

function submit(what: "forecast" | "start"): void {
  if (!form || busy) return;
  const goal = form.goal.value.trim();
  if (!form.org.value || !goal) return;
  const base = { org: form.org.value, goal, folder: Number(form.folder.value), private: form.priv.checked };
  post(what === "forecast" ? { type: "forecast", ...base } : { type: "start", ...base, demo: form.demo.checked });
}

function renderStart(v: StartView): void {
  if (!form) form = buildStartForm();
  const f = form;
  const orgKey = v.orgs.map((o) => o.name).join(",");
  if (orgKey !== f.orgKey) {
    const keep = f.org.value;
    f.org.replaceChildren(...v.orgs.map((o) =>
      option(o.name, `${o.name}${o.workflow ? ` — ${o.workflow}` : ""} (${o.source === "yours" ? "yours" : "template"})`)));
    f.orgs = v.orgs;
    f.orgKey = orgKey;
    f.org.value = v.orgs.some((o) => o.name === keep) ? keep : (v.orgs[0]?.name ?? "");
  }
  if (v.prefillOrg) f.org.value = v.prefillOrg; // "New run with this org", from the Organisations panel

  const folderKey = `${v.trusted}|${v.folders.map((x) => x.name).join(",")}`;
  if (folderKey !== f.folderKey) {
    // Follow the chosen folder by its name when the open folders change: its old index could now
    // point at a different folder (one removed, or the list reordered) while the form still showed
    // the old name. "No folder" stays "No folder".
    const hadList = f.folderKey !== "";
    const keptNone = hadList && f.folder.value === "-1";
    const keptName = hadList ? f.folderNames[Number(f.folder.value)] : undefined;
    const opts = v.folders.map((x) => {
      const o = option(String(x.index), `${x.name} — a project run on a new branch`);
      o.disabled = !v.trusted;
      return o;
    });
    opts.push(option("-1", "No folder — a fresh workspace"));
    f.folder.replaceChildren(...opts);
    f.folderKey = folderKey;
    f.folderNames = v.folders.map((x) => x.name);
    const found = keptName === undefined ? undefined : v.folders.find((x) => x.name === keptName);
    f.folder.value = keptNone || !v.trusted ? "-1"
      : found ? String(found.index)
      : v.folders.length ? "0" : "-1";
    f.folderNote.textContent = !v.trusted
      ? "This workspace is not trusted, so a run can't work on its folders. Trust it, or start with no folder."
      : !v.folders.length ? "No folder is open; the run gets a fresh workspace." : "";
    f.folderNote.classList[f.folderNote.textContent ? "remove" : "add"]("hidden");
  }

  f.demo.disabled = !v.hasProvider;
  if (!v.hasProvider) f.demo.checked = true;
  f.demoNote.textContent = v.hasProvider ? "" : "No model key yet, so only the offline demo can run. "
    + "Add one with “Cadre: Add provider”.";
  f.demoNote.classList[v.hasProvider ? "add" : "remove"]("hidden");
  describeOrg();
  validate();
}

function renderForecast(fc: ForecastView): void {
  if (!form) return;
  const tone = fc.verdict === "fits_now" ? "good" : fc.verdict === "cannot_run" ? "bad"
    : fc.verdict === "needs_days" ? "warn" : "";
  // the engine's first line repeats the headline that the pill already shows
  const lines = fc.lines.filter((l, i) => !(i === 0 && /^Forecast:/.test(l)));
  put(form.forecast,
    text("h2", "", "Forecast"),
    el("p", "", text("span", `pill ${tone}`, fc.headline)),
    lines.length ? text("pre", "", lines.join("\n")) : null);
  form.forecast.classList.remove("hidden");
}

// ------------------------------------------------------------------ memory
interface MemoryForm {
  scope: HTMLInputElement;
  scopes: HTMLDataListElement;
  fact: HTMLTextAreaElement;
  count: HTMLElement;
  pinned: HTMLInputElement;
  priv: HTMLInputElement;
  add: HTMLButtonElement;
  list: HTMLElement;
}

let memoryForm: MemoryForm | undefined;
let pendingFact = "";

function countFact(): void {
  if (!memoryForm) return;
  const n = fold(memoryForm.fact.value).length;
  memoryForm.count.textContent = `${n} / 400`;
  memoryForm.count.className = n > 400 ? "count over" : "count muted";
  memoryForm.add.disabled = busy || n === 0 || n > 400;
}

function buildMemoryForm(): MemoryForm {
  const f: MemoryForm = {
    scope: el("input"),
    scopes: el("datalist"),
    fact: el("textarea"),
    count: text("span", "count muted", "0 / 400"),
    pinned: el("input"),
    priv: el("input"),
    add: act("Remember", () => {
      const fact = fold(f.fact.value);
      const scope = f.scope.value.trim() || "global";
      if (!fact || fact.length > 400) return;
      pendingFact = fact;
      post({ type: "memoryAdd", scope, text: fact, pinned: f.pinned.checked, private: f.priv.checked });
    }, "primary"),
    list: el("div", "memory-list"),
  };
  f.scopes.id = "cadre-memory-scopes";
  f.scope.setAttribute("list", "cadre-memory-scopes");
  f.scope.value = "global";
  f.scope.placeholder = "global, team:<id> or project:<root commit>";
  f.fact.rows = 2;
  f.fact.placeholder = "One durable fact, e.g. The tests run with python -m unittest discover -s tests";
  f.fact.addEventListener("input", countFact);
  body.replaceChildren(
    el("form", "memory-form",
      text("h2", "", "Remember a fact"),
      field("Scope", f.scope, f.scopes),
      field("Fact", f.fact, f.count),
      check("Pinned — always offered first", f.pinned),
      check("Private — only sent in private runs", f.priv),
      text("p", "hint", "A fact that looks like an API key is refused. A model can only propose a fact; "
        + "proposals wait below until you decide."),
      el("div", "actions", f.add)),
    f.list);
  return f;
}

function memoryCard(m: MemoryItem): HTMLElement {
  const proposed = !m.approved && !m.stale;
  const status = m.stale ? "proposal — no longer waiting" : proposed ? "proposed — waiting for you" : "approved";
  return el("li", `card${proposed ? " proposed" : m.stale ? " stale" : ""}`,
    el("div", "card-head",
      text("span", `pill ${proposed ? "warn" : m.approved ? "good" : ""}`, status),
      m.pinned ? text("span", "pill", "pinned") : null,
      m.private ? text("span", "pill", "private") : null,
      text("span", "muted mono", m.id),
      text("span", "muted", [m.source, m.date].filter(Boolean).join(" · "))),
    text("p", "fact", m.text),
    m.tags.length ? text("p", "muted", `Tags: ${m.tags.join(", ")}`) : null,
    m.stale ? text("p", "hint", "Its approval was closed without a decision (or the database was reset), so no run "
      + "will ever see it. Delete it — and add the fact yourself if you still want it.") : null,
    el("div", "actions",
      proposed && m.approvalId ? button("Decide…", { type: "decide", id: m.approvalId }, "primary") : null,
      m.approved || m.stale ? button("Delete", { type: "memoryRemove", id: m.id }, "danger") : null));
}

function renderMemory(v: MemoryView): void {
  if (!memoryForm) memoryForm = buildMemoryForm();
  const f = memoryForm;
  f.scopes.replaceChildren(...v.scopes.map((s) => option(s, s)));
  // The fact just remembered is now in the list: clear the box (unless it was edited since).
  if (pendingFact && v.groups.some((g) => g.items.some((i) => i.text === pendingFact))) {
    if (fold(f.fact.value) === pendingFact) f.fact.value = "";
    pendingFact = "";
  }
  put(f.list,
    ...(v.groups.length
      ? v.groups.flatMap((g) => [
        el("div", "section-head", text("h2", "mono", g.scope), text("span", "muted", `${g.items.length}`)),
        el("ol", "cards", ...g.items.map(memoryCard)),
      ])
      : [text("p", "empty", "No memory yet.")]),
    v.skipped.length
      ? el("section", "skipped",
        text("h2", "", "Skipped entries"),
        text("p", "muted", "These lines in the memory files could not be loaded, so no run sees them."),
        el("ul", "", ...v.skipped.map((s) => text("li", "", s))))
      : null);
  countFact();
}

// ------------------------------------------------------------------ organisations
function orgCard(o: OrgCard): HTMLElement {
  const mine = o.source === "yours";
  return el("li", "card org",
    el("div", "card-head",
      text("h2", "mono", o.name),
      text("span", "pill", mine ? "yours" : "template"),
      o.valid ? text("span", "pill good", "valid") : text("span", "pill bad", "invalid"),
      o.workflow ? text("span", "pill", o.workflow) : null),
    o.description ? text("p", "", o.description) : null,
    o.errors.length ? el("ul", "errors", ...o.errors.map((e) => text("li", "", e))) : null,
    o.agents.length ? el("div", "table-wrap", el("table", "agents",
      el("thead", "", el("tr", "", ...["Agent", "Role", "Tier", "Tools"].map((c) => text("th", "", c)))),
      el("tbody", "", ...o.agents.map((a) => el("tr", "",
        text("td", "mono", a.id), text("td", "", a.role), text("td", "", a.tier),
        text("td", "muted", a.tools.join(", ") || "none")))))) : null,
    text("p", "muted", o.checks.length ? `Checks: ${o.checks.join(", ")}` : "No checks."),
    o.budget.length ? el("dl", "facts", ...o.budget.flatMap(([k, val]) => [text("dt", "", k), text("dd", "", val)])) : null,
    el("div", "actions",
      button(mine ? "Edit YAML" : "View YAML", { type: "openOrg", name: o.name }),
      o.valid ? button("New run with this org", { type: "useOrg", name: o.name }, "primary") : null));
}

function renderOrgs(v: OrgsView): void {
  body.replaceChildren(v.orgs.length
    ? el("ol", "cards", ...v.orgs.map(orgCard))
    : text("p", "empty", "No organisations found."));
}

// ------------------------------------------------------------------ messages from the host
function render(d: PanelView): void {
  frame(d.kind);
  switch (d.kind) {
    case "usage": renderUsage(d.view); break;
    case "approvals": renderApprovals(d.view); break;
    case "start": renderStart(d.view); break;
    case "memory": renderMemory(d.view); break;
    case "orgs": renderOrgs(d.view); break;
  }
}

function setBusy(on: boolean): void {
  busy = on;
  validate();
  countFact();
}

window.addEventListener("message", (ev: MessageEvent) => {
  const msg = ev.data as PanelHostMessage;
  if (!msg || typeof msg !== "object") return;
  switch (msg.type) {
    case "view":
      // A fresh view supersedes an earlier failure to load — but not an action's error (a refused
      // fact, a run that could not start), which stays until the next action replaces it.
      if (LOAD_PROBLEM.test(notice.textContent ?? "")) notice.className = "notice hidden";
      render(msg.data);
      break;
    case "notice":
      showNotice(msg.text, msg.tone);
      break;
    case "forecast":
      renderForecast(msg.forecast);
      break;
    case "busy":
      setBusy(msg.busy);
      break;
  }
});

post({ type: "ready" });
