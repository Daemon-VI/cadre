# Cadre — Software Requirements Specification

_v1.1 · 2026-09-17 (v1.0 of 2026-09-16 plus §4.8, the v1.0 programme) · SDLC phase 1 (requirements). Design lives in `ARCHITECTURE.md`, verification
in `TEST_PLAN.md`, sequencing in `ROADMAP.md`, status in `PROJECT_STATE.md`._

## 1. The idea, refined

The raw idea was: *an agentic AI that runs on free models — add any free model by pasting its API
key — and that scales to an organisation, where many agents work as a company: some build, some
review, some verify, some decide.*

Three things in that sentence decide the whole design, so they are stated up front:

1. **"Free" means rate limits, not money, are the binding constraint.** Every free tier caps
   requests per minute, tokens per minute and requests per day, and a team of agents multiplies
   calls. Groq's free `openai/gpt-oss-120b` allows 8,000 tokens a minute and 1,000 requests a
   day; OpenRouter's `:free` models allow 20 a minute and 50 a day. A multi-agent platform on
   free keys is therefore, first of all, a **quota-aware scheduler** that spreads work across
   every key the owner has added and waits, falls back, or stops honestly when they run dry.
2. **"Review" and "verification" only mean something if they are independent.** A reviewer on the
   same model as the builder shares its blind spots. So a reviewer is routed to a *different
   model family* whenever one is available, and when a check can be a program (tests, a schema,
   a linter) the program decides — an LLM's approval never overrides a failing check.
3. **"Decision making" must be auditable.** When several agents decide, their votes are
   collected as strict JSON and **counted by code**, not summarised by a model, and dissent is
   kept in the record.

Refined statement: **Cadre is a self-hosted platform for running an organisation of AI agents —
builders, reviewers, verifiers, deciders and managers — declared as an org chart in YAML, powered
by whichever free model APIs the owner adds, and scheduled so a team stays inside every free
tier.**

## 2. Stakeholders and users

| Who | Wants |
|---|---|
| **Solo builder** (the owner; a student on an 8 GB laptop) | A team of agents that can build and check a small project for ₹0/month |
| **Team / organisation admin** | To define departments and roles once, share provider keys safely, cap spend and runs |
| **Operator** watching a run | To see who did what, with which model, at what token cost, and to approve risky steps |
| **Decision owner** | A recommendation with the options, the votes, the dissent and the reasoning |

## 3. Scope

**In scope (v0.1):** provider/key management, quota-aware routing, org-as-YAML, five
collaboration patterns (pipeline, parallel, review loop, council vote, manager→workers), a
per-run file workspace, named verification checks, human approval gates, persistence and
resume, CLI, REST API with live event stream, a local web dashboard.

**Out of scope (v0.1), deferred with reasons in `ROADMAP.md`:** multi-user accounts and SSO,
container sandbox for checks, web browsing tools, vector memory across runs, a hosted SaaS
deployment, fine-tuning models.

## 4. Functional requirements

IDs are referenced from `TEST_PLAN.md`; each requirement has at least one test.

### FR-1 Providers and keys (BYOK)
- **FR-1.1** Add a provider from a preset (Groq, Gemini, OpenRouter, Mistral, Cohere, NVIDIA,
  Cloudflare, Z.ai, Hugging Face, Ollama, llama.cpp, LM Studio) or any OpenAI-compatible URL,
  with only a key.
- **FR-1.2** Keys are stored in the OS credential store (fallback: environment variables) and
  **never** written to the config file, database, event log, API responses or terminal output.
- **FR-1.3** Test a provider (reachability, key accepted) and list the models it serves today.
- **FR-1.4** Each model carries a tier (`strong` / `fast`), a family, a tool protocol
  (`native` / `json`) and rate limits (RPM, RPD, TPM, TPD), prefilled from the preset.
- **FR-1.5** Models without native function calling still act, through a JSON tool protocol.

### FR-2 Quota-aware routing
- **FR-2.1** Before every call, estimate its tokens and check the chosen model's per-minute and
  per-day windows; wait if it will fit soon, otherwise use another model.
- **FR-2.2** Learn remaining quota from provider rate-limit headers.
- **FR-2.3** On 429, cool that model for the provider's `retry-after` and fall back; on 401/403,
  disable the provider for the session; on 5xx/network errors, back off and fall back.
- **FR-2.4** A request larger than a model's whole per-minute budget is never sent to it.
- **FR-2.5** Daily counters survive a restart.
- **FR-2.6** When nothing can serve a call within the wait limit, fail with a message that names
  each model and when it frees up.
- **FR-2.7** A call may require a model family different from a named agent's (independence).
  If none exists, proceed and **record that the review was not independent**.

### FR-3 Organisation model
- **FR-3.1** An org is one YAML file: agents (id, role, instructions, tier, tools, independence),
  named checks, budget, and a workflow tree.
- **FR-3.2** Validation rejects unknown agents, tools, checks, duplicate ids and bad templates
  before any model is called, with the path of the error.
- **FR-3.3** Ship templates: software team, decision board, startup company.

### FR-4 Collaboration patterns
- **FR-4.1 Agent step** — one agent, one task, tools, bounded turns.
- **FR-4.2 Sequence** — steps in order; each can reference the previous output and named outputs.
- **FR-4.3 Parallel** — steps concurrently (bounded), optionally joined by a synthesising agent.
- **FR-4.4 Review loop** — builder → checks → reviewers (strict JSON verdicts) → feedback, up to N
  rounds. Approved only if **every check passes** and the reviewer rule holds.
- **FR-4.5 Council** — independent proposals → optional critique rounds → consolidated options →
  strict-JSON votes → **deterministic tally** (majority / supermajority / unanimous / plurality)
  → chair memo with dissent. Ties and failed quorums are broken by the chair and marked so.
  Unparseable votes become recorded abstentions.
- **FR-4.6 Manager** — manager emits a task plan (JSON DAG); the engine validates it (known
  assignees, known dependencies, no cycles, size cap), runs ready tasks in parallel, optionally
  reviews each, skips dependents of failed tasks, and has the manager integrate a final report.
- **FR-4.7 Approval gate** — the run pauses until a human approves or rejects.

### FR-5 Tools and workspace
- **FR-5.1** Each run has its own workspace directory; file tools cannot escape it (absolute
  paths, `..`, symlinks, drive letters are refused).
- **FR-5.2** Every file write is versioned with the writing agent and a hash.
- **FR-5.3** `run_check` runs only checks **named in the org file**; the model picks a name,
  never a command. Checks run with secrets scrubbed from the environment, a timeout and an
  output cap, and require an `exec` approval unless the run allows execution.
- **FR-5.4** A shared team board: agents post notes; recent notes are shown to every agent.
- **FR-5.5** `ask_human` pauses the agent until the operator answers.
- **FR-5.6** An agent is offered only the tools its spec lists.

### FR-6 Runs
- **FR-6.1** Start a run with an org and a goal from CLI, API or dashboard.
- **FR-6.2** Budgets per run: model calls, tokens, wall-clock minutes, parallelism. The run
  stops with the name of the budget that stopped it.
- **FR-6.3** Every model call, tool call, verdict, vote, plan and approval is an event.
- **FR-6.4** Token usage is recorded per agent, per provider, per model.
- **FR-6.5** A run interrupted by a crash or restart can be resumed; finished steps are not
  re-run and not re-billed.
- **FR-6.6** Runs can be cancelled.

### FR-7 Interfaces
- **FR-7.1** CLI covering providers, orgs, runs, approvals, quota and the server.
- **FR-7.2** REST API with a streamed event feed.
- **FR-7.3** Web dashboard: providers and quota, orgs, new run, run timeline, files, approvals.
- **FR-7.4** An offline demo mode that exercises every pattern with no key.

### 4.8 v1.0 programme (added 2026-09-17)

Rithik restated the idea on 2026-09-17: *"combine all the free API keys available — a Google
account, OpenRouter, DeepSeek and the like — and orchestrate an agentic AI that does tasks, or
builds or completes a project, while managing the usage of each model and API."* The product
statement becomes: **a team of AI agents that builds a new project or finishes an existing one on
free keys only, spreads the work across every provider added, stays inside each one's limits,
waits for the reset when a day's quota runs out, and shows what every call cost and which model
made it.** Each requirement below lists acceptance criteria (AC) a test can check.

### FR-8 Project mode
A run can target an existing git repository and deliver its work as a branch for the owner to
review; the owner's tree and branches are never modified.
- **AC-8.1** `--project PATH` that is not a git repository is refused before any model call.
- **AC-8.2** A dirty working tree is refused unless `--allow-dirty`; with it, the base is HEAD and
  the run says uncommitted changes are not carried over.
- **AC-8.3** The workspace is a `git worktree` on a new branch `cadre/<run-id>` from the base.
- **AC-8.4** After each finished step that changed files, a commit `cadre(<step>): <summary>` is
  made on that branch with the repo's configured identity and no trailers.
- **AC-8.5** Checks come from the org file and from `.cadre/checks.yaml` read from the **base
  commit** at run creation; agents cannot write under `.cadre/` in project mode.
- **AC-8.6** The result carries `git diff --stat <base>...cadre/<id>` and the commands to review
  or discard the branch. Cadre never merges, pushes, force-pushes or deletes a branch it did not
  create.
- **AC-8.7** A resumed project run continues on the same branch and worktree.

### FR-9 Multi-day runs
- **AC-9.1** When every eligible model is blocked by a *daily* limit beyond `max_wait`, the run
  becomes `parked` with `resume_at` = earliest reset + jitter, and a `run.parked` event names each
  model and its reset time. Minute-window waits still wait; auth failures still fail.
- **AC-9.2** A parked run that is due is resumed by `cadre resume --due` or by `cadre serve`;
  finished steps are neither repeated nor re-billed.
- **AC-9.3** Budgets are cumulative across parks and resumes; `max_days` stops a run by name,
  and `max_tokens_per_day` parks it until the next UTC day (changed 2026-09-17 during M10: a
  per-day cap that stopped the run would defeat a multi-day run).

### FR-10 Provider catalogue
- **AC-10.1** Every free chat model a key can reach is its own quota bucket with its own
  RPM/TPM/RPD/TPD, each number carrying a source (`docs`, `reported`, `guess`) and the date checked.
- **AC-10.2** Each provider declares `day_reset` (`UTC`, an IANA zone, or `rolling`); daily
  counters and "frees in" follow that clock; existing UTC counters survive the change.
- **AC-10.3** `cadre provider refresh` lists models added and removed at the provider compared
  with `config.yaml` and never overwrites a limit the owner set.

### FR-11 Usage ledger and forecast
- **AC-11.1** `cadre usage --days N` and a dashboard page show requests, tokens and share of the
  daily cap per day × provider × model, with the next reset shown in IST.
- **AC-11.2** `cadre forecast <org> "<goal>"` answers *fits now*, *fits today after ~N min of
  waits*, *needs ~N days* or *cannot run* (with the reason), and `cadre run` prints it first.
- **AC-11.3** `reserve_pct` (default 10) keeps that share of every daily cap unused.

### FR-12 Data-policy routing
- **AC-12.1** Each preset records `trains_on_free_data` = `yes` / `no` / `unknown` with a source URL.
- **AC-12.2** A run with `privacy: private` never calls a model whose provider is `yes` or
  `unknown`; each exclusion is an event; if nothing is left the run fails before its first call
  and names the excluded providers.

### FR-13 Code-editing tools
- **AC-13.1** `edit_file(path, old, new)` replaces exactly one exact match; zero or several
  matches return an error with the count; edits are versioned like writes.
- **AC-13.2** `read_file` accepts `start_line` / `end_line`; `search(pattern, glob)` returns a
  capped list of `path:line: text` and cannot leave the workspace.
- **AC-13.3** Agents with file tools receive a repo map (paths, line counts, top-level Python
  `def`/`class` names) under a hard token cap.
- **AC-13.4** Each tool is kept only if its measured saving exceeds its per-call schema cost
  (numbers recorded in ADR-021/022).

### 4.9 Distribution programme (added 2026-09-18)

Cadre works and has been measured on real free models (v1.0 programme). This programme puts it
where people can get it: an open-source repository, a package and standalone downloads, an MCP
server for AI editors, a GitHub Action, a VS Code extension (also on Open VSX) and a docs site.
Every front end is a thin client of the one Python engine and its local API (ADR-024). Decided
with Rithik on 2026-09-18: Apache-2.0, PyPI name `cadre-ai` (the command stays `cadre`), the
repository goes public after the D0 gate, and the session prompts and handoff docs stay public
with local paths generalised.

### FR-14 Open-source readiness
- **AC-14.1** `LICENSE` is Apache-2.0 and `pyproject.toml` declares it; CI lists dependency
  licences and fails on a copyleft one.
- **AC-14.2** CI runs pytest and ruff on Windows, macOS and Ubuntu with Python 3.12 and 3.13.
- **AC-14.3** CI runs gitleaks over the full history, and fails if the laptop account's user name
  appears in any tracked file.
- **AC-14.4** Every commit is authored and committed by
  `Rithik Krishna <317035893+Daemon-VI@users.noreply.github.com>`, checked in CI.
- **AC-14.5** `cadre scheduler install` works on Windows (Task Scheduler), Linux (systemd user
  timer) and macOS (launchd agent); the unit files are generated by tested code.
- **AC-14.6** With `CADRE_NO_KEYRING=1` (a headless machine with no keychain) keys come from
  `CADRE_KEY_<ID>` or the preset's variable, and nothing touches a keychain; tested.
- **AC-14.7** `cadre serve --allowed-host NAME` (repeatable) accepts that `Host` header, for
  Tailscale names and containers; loopback stays the default and any other host still gets 421.

### FR-15 Versioned API
- **AC-15.1** Every endpoint is served under `/api/v1`; the unversioned `/api/*` paths stay as an
  alias until 2.0, hidden from the schema.
- **AC-15.2** A snapshot of the `/api/v1` OpenAPI document is checked in; any change to it fails a
  test until the snapshot is regenerated on purpose.

### FR-16 Packages
- **AC-16.1** The wheel from `uv build`, installed in a clean venv, completes
  `cadre run decision-board "<goal>" --demo` (proves `web/` and `templates/` ship); in CI too.
- **AC-16.2** A `v*` tag publishes to TestPyPI and then PyPI by trusted publishing, with no token.
- **AC-16.3** PyInstaller one-folder builds for Windows, macOS and Linux are built in CI, pass
  `cadre --version` and one demo run, and are attached to the GitHub Release.
- **AC-16.4** A GHCR image runs as a non-root user with `CADRE_HOME=/data` on a volume and keys
  from environment variables.

### FR-17 MCP server
- **AC-17.1** `cadre mcp` serves MCP over stdio with exactly five tools: `cadre_forecast`,
  `cadre_start_run`, `cadre_run_status`, `cadre_usage`, `cadre_list_orgs`.
- **AC-17.2** It is a client of `cadre serve`, which it starts detached when none answers; runs
  outlive the editor.
- **AC-17.3** No approval of any kind can be granted over MCP; a waiting run's status names
  `cadre approve <id>` and the dashboard.
- **AC-17.4** `cadre_start_run` defaults the project to the host's first workspace root: MCP roots
  when the host declares them, else the folder the server was started in when it is a git
  repository; `project: ""` means a fresh workspace (amended 2026-09-18: roots are deprecated in
  the MCP 2026-07-28 spec).
- **AC-17.5** The API token never appears in a tool result.

### FR-18 GitHub Action
- **AC-18.1** A composite action posts the forecast on the issue, runs project mode on the
  checkout, pushes `cadre/<run-id>`, and opens a pull request whose body is `REPORT.md` plus usage.
- **AC-18.2** The example workflow runs only for `OWNER`, `MEMBER` or `COLLABORATOR`, with only
  `contents`, `pull-requests` and `issues` write permissions.
- **AC-18.3** A parked run comments its resume time and stops (v1).

### FR-19 VS Code extension (Marketplace and Open VSX)
- **AC-19.1** It uses a running server, else starts `cadre serve` or `uvx cadre-ai serve`; it
  reads the token from `CADRE_HOME/token` and never displays it or passes it to a webview.
- **AC-19.2** A Runs tree with status icons; a run-detail webview with live events (CSP with a
  nonce, text inserted with `textContent` only); a status-bar item with today's usage against the
  tightest daily limit.
- **AC-19.3** Commands: Forecast, Start run on this folder (project mode on the workspace root),
  Review branch (diffs of changed files), Add provider (the key is typed into the terminal's
  hidden prompt and never passes through the extension).
- **AC-19.4** Gate approvals are notifications; an `exec` approval is a notification that never
  takes keyboard focus, whose **Review…** opens a modal showing the check's name and its exact
  command from the org file, and needs an explicit click (amended 2026-09-19: a modal opened by
  polling took focus, and Enter typed into another window approved two demo runs).
- **AC-19.5** One tag workflow publishes to the VS Code Marketplace and to Open VSX.

### FR-20 Docs site
- **AC-20.1** A static site from `site/` on GitHub Pages: quick start, one setup page per front
  end, the security model, the measured numbers with dates and links, and a recording of a real
  run.

### FR-21 Desktop app (only if Rithik says yes at the D6 gate)
- **AC-21.1** A Tauri 2 shell around the dashboard with the PyInstaller engine as its sidecar;
  unsigned installers built in CI only.

## 5. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| **NFR-1 Cost** | Runs entirely on free tiers | ₹0/month; token cost per run measured and shown |
| **NFR-2 Footprint** | Fits this laptop (7.7 GB, ~1 GB free) | Server < 150 MB RSS; no local model needed |
| **NFR-3 Security** | API bound to loopback, bearer token on every API call, Host header checked, no CORS; keys only in the OS store | Tests assert 401 without token and that no key value appears in events |
| **NFR-4 Safety** | Model output never becomes a shell command; checks are declared, approved, time-limited | Tests for path escape, unknown check, exec approval |
| **NFR-5 Reliability** | Crash → resume without repeating finished work | Resume test counts provider calls |
| **NFR-6 Observability** | Every decision explainable from the event log | Events for routing choice, fallback, wait, verdict, tally |
| **NFR-7 Portability** | Windows first, Linux/macOS compatible; Python 3.12+, `uv` | CI matrix (not yet run — no workflow added) |
| **NFR-8 Honesty** | Model catalogues and free limits change; presets carry the date they were checked and are overridable | Preset table dated 2026-09-16 |
| **NFR-9 Testability** | All behaviour testable with no network | Scripted provider; HTTP mocked with `httpx.MockTransport` |
| **NFR-10 Owner's tree untouched** | A project run leaves the source repo's HEAD, current branch and `git status --porcelain` identical | Test compares all three before and after a run on a throwaway repo |
| **NFR-11 Honest forecasts** | Every forecast states its basis | Output contains "measured, n = …" or "no history, estimated from template size" |

## 6. Constraints and assumptions

- Hardware: i3-1215U, 7.7 GB single-channel RAM, no GPU. Local models run at ~3.4 tok/s (1.5B),
  so they are a fallback, never the default.
- Free-tier terms: Cadre respects each key's published limits and never creates accounts or
  rotates several keys of one provider to evade a limit. Owners must follow each provider's
  terms (some free tiers — Mistral's, for one — train on your data).
- Free catalogues are volatile: Cerebras moved to a card-required trial and GitHub Models was
  retired in July 2026. Presets are a starting point; `cadre provider models <id>` asks the
  endpoint what it serves today.

## 7. Objectives

The measurable objectives are in `OBJECTIVES.md`, each with how it is met and the evidence.
