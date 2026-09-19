# Cadre — Architecture

_2026-09-16 · v0.1; ADR-016…022 and the v1.0 data model added 2026-09-17 · SDLC phase 2 (design). Requirements: `SRS.md`._

## Shape

```
            CLI (typer)          Dashboard (static JS)
                 \                    /
                  \        REST + event stream (FastAPI, loopback, bearer token)
                   \                /
                    RunManager ──── Store (SQLite: runs, events, step results, usage, quota, approvals)
                        │
                     Engine ─── workflow tree: agent · sequence · parallel · review_loop · council · manager · approval
                        │        └── Project (git worktree on cadre/<run-id>, per-step commits)  [v1.0]
                        │
                   AgentRunner ── Tools (list/read/write file, run_check, post_note, ask_human)
                        │               └── Workspace (per run, versioned, confined)
                     Router ──── QuotaBook (RPM/TPM/RPD/TPD windows + headers + provider day clocks)
                        │        └── privacy filter, independence, park on daily limits  [v1.0]
                        │
              OpenAICompatProvider ×N  (Groq, Gemini, OpenRouter, Mistral, … , Ollama)   ScriptedProvider (tests/demo)
                        │
                  SecretStore (OS keyring → env vars)   — keys never leave this box except to their own provider
```

```
src/cadre/
  types.py        messages, tool calls, usage, rate info — the vocabulary everything shares
  presets.py      free-tier presets, dated
  secrets.py      key lookup/storage and redaction
  providers.py    OpenAI-compatible adapter, JSON tool protocol, scripted + demo providers
  quota.py        per-model limiter
  router.py       model selection, fallback, independence
  config.py       CADRE_HOME, config.yaml (providers/models, no keys), API token
  org.py          org YAML schema + validation, templates
  workspace.py    confined, versioned run directory
  tools.py        tool registry and permission classes
  store.py        SQLite persistence
  agent.py        one agent's bounded tool loop; strict-JSON asks
  engine.py       workflow execution, resume cache, budgets
  runs.py         run lifecycle (start / resume / cancel / approvals / park)
  project.py      git worktree, per-step commits, repo checks (ADR-016)
  clocks.py       provider day clocks (ADR-018)
  forecast.py     usage ledger and forecast (ADR-019)
  repomap.py      capped repo map (ADR-022)
  api.py          FastAPI app + dashboard
  cli.py          command line
  web/            dashboard (no build step)
  templates/      example orgs
```

## Decisions

### ADR-001 — One adapter: the OpenAI-compatible `/chat/completions`
Every free provider that matters in September 2026 — Groq, Gemini (compat layer), OpenRouter,
Mistral, Cohere (compat), NVIDIA, Cloudflare Workers AI, Z.ai, Hugging Face router — and every
local server (Ollama, llama.cpp, LM Studio) speaks this shape. One adapter plus a preset table
is how "add a model by adding its key" stays true. A provider object is per *endpoint*, and the
model is chosen per call, because routing is between models, not between endpoints.
Rejected: SDK-per-vendor (LangChain/LiteLLM) — a large dependency tree to cover a shape we can
cover in ~250 lines, and harder to control retries, which is where free tiers actually fail.
Carried over from Tessera (`tessera/agent/providers.py`), which ran it against live Groq.

### ADR-002 — The scheduler, not the model, is the product
A free tier fails by **rate limit**, not by bill. The router owns every call: it estimates
tokens (chars/4 + schemas + reserved output), asks each candidate's limiter how long until the
call fits, and picks the soonest (then the configured priority, then most headroom). 429 cools a
model for `retry-after`; 401/403 disables the provider for the session; 5xx backs off. A request
larger than a model's whole TPM is never sent to it (Groq answers those with 413/429 forever).
Waiting is preferred to failing up to `max_wait` (90 s), because one minute is exactly how long
a TPM window takes to drain. Verified by `tests/test_quota.py`, `tests/test_router.py`.

### ADR-003 — Headers beat presets
Published limits change without notice (Groq's changed twice in 2026). The limiter uses the
preset numbers as a prior and overrides them with `x-ratelimit-remaining-*` / `reset-*` whenever
a provider sends them, which also accounts for other programs using the same key.

### ADR-004 — Independence is a routing constraint, and its absence is recorded
Reviewers and voters are routed away from the builder's model *family* (`gpt-oss`, `gemini`,
`llama`, …). With one key there is no alternative, so the call proceeds and the event says
`independent: false` — an honest degradation instead of a silent one.

### ADR-005 — Programs gate, models advise
In a review loop, checks run first and are authoritative: approval requires every check to pass
*and* the reviewer rule to hold. A model can reject working code; it cannot approve failing code.

### ADR-006 — The model names a check; the org file owns the command
`run_check(name)` looks the name up in the org YAML. There is no "run this shell command" tool.
Checks run with `cwd` = workspace, an environment scrubbed of anything named like a key, token,
secret or password, a timeout and a 16 KB output cap, and — unless the run was started with
`allow_exec` — only after an `exec` approval. **This is not a sandbox**: the code the builder
wrote runs as the owner. Documented in the README; a container runner is on the roadmap.

### ADR-007 — Votes are counted by code
Council members vote in strict JSON (`{"choice": "B", "confidence": 0.7, "reason": "…"}`). The
tally, quorum and tie handling are Python. The chair writes the memo from the tally; it cannot
change it. Unparseable votes (after one repair turn) are abstentions and are listed.

### ADR-008 — Plans are data and are validated before anything runs
The manager's plan is JSON: tasks with ids, assignees, dependencies. The engine checks assignee
membership, dependency existence, acyclicity (Kahn), and size; errors go back to the manager
once, then the step fails. Execution is a topological wave schedule bounded by `max_parallel`;
a failed task skips its dependents rather than letting them run on nothing.

### ADR-009 — Resume by step path
Every step has a deterministic path (`0/1/review/r2/build`). A finished step's output is
stored under `(run_id, path)`. Resuming re-walks the tree and returns stored outputs
immediately, so finished work is neither repeated nor re-billed. Partially finished agent turns
are re-run (their file writes are idempotent overwrites). This is simpler than checkpointing
coroutines and survives code edits to unfinished steps.

### ADR-010 — The API is a door, so it is locked
The dashboard needs HTTP, and Tessera's lesson is that a loopback port is a second door into
anything that runs code. So: bind `127.0.0.1` by default; a random 256-bit bearer token in
`CADRE_HOME/token` is required on every `/api` call (constant-time compare); the `Host` header
must be a loopback name (DNS-rebinding defence); no CORS headers are ever sent; the event feed is
read with `fetch` + headers, not `EventSource`, so the token never appears in a URL or access
log. The static dashboard carries no data and receives the token in the URL *fragment*, which
browsers never send to a server.

### ADR-011 — Keys live in the OS credential store
`keyring` (Windows Credential Manager, macOS Keychain, Secret Service) under service `cadre`,
falling back to environment variables (`CADRE_KEY_<ID>`, then the preset's conventional name
such as `GROQ_API_KEY`). `config.yaml` holds only a reference. Every loaded key value is
registered with a redactor that scrubs event payloads and error messages before they are
stored; a test searches the whole database for a planted key.

### ADR-012 — SQLite, one file, polled event feed
One writer process per run manager, WAL mode, a lock around writes. The event stream polls the
events table (0.5 s), so runs started from the CLI are visible in a dashboard served by a
different process, and a resumed run's history is continuous. At the volumes a free tier allows
(hundreds of events per run) polling costs nothing measurable.

### ADR-013 — Bounded everything
Per agent step: `max_turns` (default 8), 4 KB per tool observation, output token cap. Per run:
model calls, tokens, minutes, parallelism. Manager plans: `max_tasks`. Review loops:
`max_rounds`. A looping model stops with the name of the limit it hit.

### ADR-014 — Few tools, because tools cost tokens on every call
Six tools existed in v0.1 (eight since ADR-021) and an agent sees only those its spec lists (Tessera measured 100–230 tokens per
schema per call). Team notes and the workspace listing are injected into the prompt instead of
being tools, since nearly every agent needs them.

### ADR-015 — Demo provider is plumbing, not intelligence
`--demo` swaps in a deterministic provider that writes a file, approves, proposes, votes and
plans by reading the prompt's structure. It exists so every pattern can be exercised end to end
with no key and no network. It says so in every answer it gives.

### ADR-016 — Project mode works in a git worktree on its own branch (FR-8, NFR-10)
**Context.** Rithik wants Cadre to *finish* existing projects, and the owner's repositories are off
limits. **Decision.** A project run creates `git worktree add <CADRE_HOME>/runs/<id>/workspace
-b cadre/<run-id> <base>` and uses that directory as the workspace, so every existing confinement
rule (ADR-006, `.git` refused) still holds. After each cached step that changed files the engine
commits `cadre(<step path>): <first line of the step's output>` under an asyncio lock, with the
repo's own identity, hooks and signing — never `--no-verify`, never a trailer. A commit that fails
is recorded as `project.commit_failed` and the files stay in the worktree. Checks are the org's
plus those in `.cadre/checks.yaml` **read from the base commit** with `git show` at run creation
and stored in the run's options; agents cannot write under `.cadre/`, so a model cannot plant a
command there. A step may say `checks: [all]` to mean every declared check. The run never merges,
pushes or deletes an owner branch; `cadre runs cleanup` only removes worktrees of finished runs
and leaves `cadre/*` branches for review. **Rejected.** *Copying the tree* — loses history,
doubles disk, and the owner has to diff by hand. *Editing in place* — breaks rule 8 and NFR-10 by
design. *A separate clone* — the branch would live outside the owner's repo.
**Proof.** `tests/test_project.py`: HEAD, branch and `git status --porcelain` identical before and
after; dirty tree refused; resumed run continues on the same branch; `.cadre/` writes refused.

### ADR-017 — Park on daily limits instead of failing (FR-9)
**Context.** A long build exhausts daily limits (Gemini's 3.x Flash models are reported at 20
requests a day). **Decision.** `ModelQuota.wait_time` also returns a machine-readable kind
(`minute`, `cooldown`, `daily`, `never`). When the router cannot place a call within `max_wait`
and the soonest block among eligible models is `daily`, it raises
`DailyQuotaExhausted(resume_in, blocks)`. The run manager sets status `parked`, `resume_at` =
now + resume_in + 30–120 s of jitter, and emits `run.parked` naming each model and its reset.
Minute-window waits keep waiting; auth failures keep failing; oversize requests stay `never`.
`cadre serve` checks every 60 s for due parked runs; `cadre resume --due` does the same once, and
`cadre scheduler install` registers that with Windows Task Scheduler — **only after asking the
owner**. Budgets become cumulative: calls and tokens start from the run's recorded usage, active
minutes accumulate in `runs.active_seconds`, and `max_days` / `max_tokens_per_day` join the
budget. This supersedes v0.1's "resume gives a fresh budget window"; `cadre resume --add-calls /
--add-tokens` raises a stopped run's allowance explicitly. **Rejected.** *Sleeping in-process
until the reset* — the laptop sleeps, the process dies, and the run holds memory for hours.
*Failing and asking the owner to resume* — that is v0.1, and it is gap G4.
**Proof.** `tests/test_multiday.py` with a fake clock: limit hit → parked → clock passes the reset
→ resumed → finished, and the provider's call count shows no step billed twice.
**As built (2026-09-17).** `QuotaParked` is deliberately *not* a `ProviderError`, so a manager
task cannot swallow it as a task failure. A park happens when the soonest block among eligible
models is `daily`; a long minute block still fails (`max_wait` below 60 s). `max_tokens_per_day`
parks until the next UTC day rather than stopping. Groq's request headers describe its daily
window, so a spent request window longer than five minutes counts as `daily`. `cadre serve`
checks for due parked runs every 60 s; `cadre scheduler install` (Windows Task Scheduler, every
30 min) shows the exact `schtasks` command and asks before creating it — it has **not** been
installed on Rithik's machine.

### ADR-018 — Each provider keeps its own daily clock (FR-10)
**Context.** v0.1 counted every day in UTC. Google: RPD quotas "reset at midnight Pacific time";
Cloudflare: "All limits reset daily at 00:00 UTC"; OpenRouter counts a "UTC day"; Groq states no
clock and reports resets in headers (all checked 2026-09-17). **Decision.** Presets carry
`day_reset`: `UTC`, an IANA zone, or `rolling` (a 24-hour sliding window kept as hourly
buckets). The limiter derives the day key and "frees in" from it. Day keys stay `YYYY-MM-DD` for
UTC (so v0.1 rows keep working), `YYYY-MM-DD@<zone>` for a zone, and `YYYY-MM-DDTHH@rolling` per
hour. When no row exists under a model's new key, today's UTC row is carried over (conservative:
it can only over-count). `tzdata` becomes a dependency because Windows has no zone database.
Groq is `rolling` until its reset time is documented; its `x-ratelimit-reset-requests` header
still overrides the estimate (ADR-003). **Rejected.** *UTC everywhere* — Gemini's day would end
12.5 hours early for an IST owner and the router would plan with quota that is not there.
**Proof.** `tests/test_clocks.py`.

### ADR-019 — Forecasts come from measured history (FR-11, NFR-11)
**Decision.** For an org, history is its finished runs' calls, tokens and active time; the
forecast uses the median and p90 (nearest rank) and prints `measured, n = k`. With no history it
walks the workflow tree, assembles each agent's real system prompt and tool schemas with the same
estimator the router uses, assumes a stated number of calls per step type, and prints `no
history, estimated from template size`. The verdict compares p90 with the capacity left today
(daily caps × (1 − `reserve_pct`) minus today's counters) and with per-minute pace: *fits now*
(≤ 2 min of pacing), *fits today after ~N min*, *needs ~N days*, or *cannot run* (no eligible
model, or one call larger than every model's TPM). **Rejected.** *Asking a model to estimate* —
unmeasurable, and it spends the quota it is estimating. **Proof.** `tests/test_forecast.py`.

### ADR-020 — Data policy is a routing constraint (FR-12)
**Decision.** Presets carry `trains_on_free_data` (`yes` / `no` / `unknown`) and a source URL.
Checked 2026-09-17 on each provider's own pages: Groq *no*, Cloudflare *no*, local servers *no*,
Gemini free tier *yes*, Mistral free mode *yes* (opt-out exists), NVIDIA trial *yes*, Cohere
trial *yes* unless opted out, OpenRouter `:free` *unknown* (depends on the upstream model), Z.ai
*unknown*, Hugging Face *unknown* (depends on the upstream provider). Orgs and runs carry
`privacy: standard | private`. A private call filters the pool like independence does (ADR-004)
but as a hard constraint; the run emits `privacy.excluded` once, listing every excluded model,
and fails before its first call if nothing is left. **Rejected.** *A warning only* — a private
prompt sent once cannot be recalled. **Proof.** `tests/test_privacy.py`.

### ADR-021 — Edit, line-range and search tools stay only if they save tokens (FR-13)
**Decision.** `edit_file(path, old, new)` (exactly one match, else an error with the count;
versioned like writes), `read_file(path, start_line, end_line)`, and `search(pattern, glob)`
(≤ 40 matches, workspace only). Each is kept only if the measured saving beats its per-call schema
cost. **Measured 2026-09-17** with Cadre's own 4-characters-a-token estimator
(`tests/test_edit_tools.py::measure_edit_saving`), changing one docstring line of a 400-line
module (3,414 tokens):

| Route | Tokens in | Tokens out | Total |
|---|---|---|---|
| read the whole file (4 reads — observations are cut at 4 KB) and `write_file` it back | 3,414 | 3,642 | 7,056 |
| `search` → `read_file` 5 lines → `edit_file` | 69 | 65 | 134 |

Saving: **6,922 tokens per one-line edit**. Cost: the three schemas add **218 tokens to every
call** (`edit_file` 99, `search` 74, `read_file`'s range parameters 45). Break-even is one targeted
edit every ~31 calls; the test enforces a 10× margin. **Kept.** The software-team engineer and
reviewer now hold them; tools stay opt-in per agent. Provider tokenizers differ from 4 characters
a token, so the absolute numbers are estimates; the ratio is what the decision rests on. **Rejected.** *Unified diffs from the model* — free models produce malformed
hunks often enough that a failed patch costs more than it saves; an exact-match replace either
applies or says why not.

### ADR-023 — What live providers taught (M5, 2026-09-17)
Provider-specific data that must round-trip (Gemini 3's `thought_signature` in
`tool_calls[].extra_content`) is carried on `ToolCall.extra` with the issuing provider, replayed only
to that provider, and replaced by Google's documented placeholder for foreign calls; a tool loop
prefers its model (`STICKY_WAIT` 5 s). A 404 removes a model for the session (`ModelGone`). A 503
rests a model on an escalating schedule. A daily-quota 429 (Google's `quotaId` contains `PerDay`)
marks the model spent until its own reset. Per-provider request fields (`request_params`, Gemini:
`reasoning_effort: low`) exist because thinking models spend the visible output budget. Tasks that
name a file are checked by code (nudge, then save), and identical repeated reads are not re-run —
both are cheaper and more reliable than prompting harder. Reviews avoid every family the builder
used, and receive the written files inline. Alternatives rejected: a per-vendor SDK (ADR-001 still
holds — each fix was a few lines in the one adapter), and "just retry" (it turned one broken
signature into six failed attempts).

### ADR-022 — The repo map is injected context with a hard cap, not a tool (FR-13)
**Decision.** Agents holding any file tool get a `REPO MAP` block instead of the plain listing:
each text file's path and line count and, for Python files, the top-level `def`/`class` names from
`ast`, cut at a token cap (default 1,200) with "… N more files". Orientation is needed on nearly
every task, so a tool would cost a round trip almost every time; injected context costs its
tokens once per call and saves the `list_files` turn. Public classes come before public functions
and private names are left out, because a first version cut off `class Engine` in Cadre's own
`engine.py`. **Measured 2026-09-17:** Cadre's own 21 modules map to 683 tokens against 101 for the
plain listing, so the map adds ~580 tokens per call; the software-team engineer's fixed prompt is
836 tokens, so one saved exploratory turn pays for about 1.5 calls of map. That is roughly even on
a tiny greenfield task and clearly positive on an existing repository (project mode, M9), which is
why it stays; M11's live runs will say whether a per-agent switch is needed.

## Data model (SQLite)

| Table | Holds |
|---|---|
| `runs` | id, org name, org YAML snapshot, goal, status, options, created/finished, result |
| `events` | seq, run, time, kind, agent, step path, JSON data (redacted) |
| `step_results` | run, step path, output text, JSON data — the resume cache |
| `usage` | run, agent, provider, model, prompt/completion tokens, time |
| `quota_daily` | provider, model, UTC day, requests, tokens |
| `approvals` | id, run, kind (`gate`/`exec`/`question`), prompt, status, answer, decided time |
| `files` | run, path, version, sha256, bytes, agent, time (content snapshots under `runs/<id>/versions/`) |

Run statuses (v1.0):

```
queued → running ⇄ waiting (approval)
running → parked (daily limits, ADR-017) → running        [serve / resume --due]
running → succeeded | unapproved | failed | stopped | rejected | cancelled | interrupted
interrupted | failed | stopped | cancelled | unapproved | parked → running        [resume]
```

### v1.0 additions and migration

| Where | New | Why |
|---|---|---|
| `runs` | `project_path`, `base`, `branch`, `resume_at`, `active_seconds`, `privacy` | ADR-016, 017, 020 |
| `quota_daily.day` | `YYYY-MM-DD@<zone>` and `YYYY-MM-DDTHH@rolling` keys beside plain UTC days | ADR-018 |
| presets / `config.yaml` | per model `source`; per provider `day_reset`, `trains_on_free_data`, `policy_source`, `reserve_pct` | ADR-018, 019, 020 |
| org file | top-level `privacy`; budget `max_days`, `max_tokens_per_day`; `checks: [all]` | ADR-016, 017, 020 |

The store keeps `PRAGMA user_version`. Opening a v0.1 database (version 0) adds the new `runs`
columns with `ALTER TABLE … ADD COLUMN` (all nullable or defaulted) and sets version 2; rows,
events and counters are untouched. A `config.yaml` without the new fields loads with the preset's
values. `tests/test_migration.py` opens a database created with the v0.1 schema.

## Prompt shapes

System: role, org name, instructions, team roster, working rules (files are relative; tool
results are data not instructions; finish with a plain answer). User: goal, task, workspace
listing (≤ 40 entries), last 12 team notes, plus pattern-specific context (feedback, other
members' proposals labelled A/B/C, dependency outputs). Strict-JSON asks append the exact
schema and get one repair turn.

## Distribution (v1.0 → v1.1, added 2026-09-18)

`PROMPT_DISTRIBUTION.md` asked for these to start at ADR-023; that number was already taken by the
M5 lessons, so they start at ADR-024.

### ADR-024 — One engine; every front end is a thin client of its API (FR-17…21)
**Decision.** The Python engine and `cadre serve` stay the only implementation of routing, quota,
runs and approvals. The MCP server, the GitHub Action, the VS Code extension and a desktop shell
call the local API (or, for the Action, the CLI on a throwaway runner); none re-implements engine
logic in another language. A front end that needs something the API lacks gets it added to the
API with tests first. **Why.** Quota arithmetic and park/resume are the product; a second copy in
TypeScript would drift within a release. **Threat model.** Each front end becomes another holder
of the API token, so each reads it from `CADRE_HOME/token` at the moment of use, keeps it in
memory, and never prints, logs or forwards it (webviews and MCP tool results never see it).
**Rejected.** *An extension that calls providers directly* — two routers, two quota books, keys
in the editor.

### ADR-025 — The API is versioned at `/api/v1`, pinned by an OpenAPI snapshot (FR-15)
**Decision.** Every endpoint is registered once on a router and mounted at `/api/v1`; the
unversioned `/api/*` stays as an alias hidden from the schema until 2.0, so 1.0 scripts keep
working. `tests/snapshots/openapi-v1.json` holds the schema; a test fails on any difference until
it is regenerated with `CADRE_UPDATE_SNAPSHOTS=1`, which makes an API change a reviewed diff. The
schema is not served (`openapi_url=None` stays): clients are ours. **Threat model.** Two mounts
must not mean two policies: both share the same dependencies (bearer token) and the same Host
middleware, and a test checks a v1 call without a token gets 401.
**Rejected.** *Header versioning* — invisible in logs and awkward in `curl`.

### ADR-026 — How the engine is delivered: `uvx` first, a bundled binary second (FR-16)
**Decision.** The primary channel is PyPI `cadre-ai`, run with `uvx cadre-ai …` (one tool, cached,
isolated, no global install) or `pip install cadre-ai`. Front ends call `cadre` if it is on PATH,
else `uvx cadre-ai`. For people without Python, PyInstaller one-folder builds are attached to each
GitHub Release, and a GHCR image serves containers. Everything heavy (PyInstaller matrix, Docker,
Electron tests) is built in CI, never on the 7.7 GB laptop. Publishing uses PyPI trusted
publishing (OIDC), so no PyPI token exists anywhere. **Threat model.** A published package runs as
the user who installs it: releases come only from a tag on `main` through `release.yml` in a
protected `pypi` environment; unsigned binaries trigger SmartScreen/Gatekeeper and the README says
so. **Rejected.** *Signing certificates now* — they cost money; Rithik's decision.

### ADR-027 — MCP over stdio, and no approvals over MCP (FR-17)
**Decision.** `cadre mcp` speaks MCP over stdio using the official Python SDK, with five tools,
because every schema is replayed in the host's context too. It is a client of `cadre serve`
(started detached when none answers), so a run survives the editor closing. **No approval of any
kind is granted over MCP** — neither `exec` approvals (they let model-written code run) nor gate
approvals: the caller is itself a model, and a gate that a model can open for another model's work
is not a gate. A run waiting on approval reports `cadre approve <id>` and the dashboard URL.
`cadre_start_run` may ask for `allow_exec` (checks still wait for a human's approval) but never
`auto_approve`. **Threat model.** Prompt injection in the host's context can call any tool, so
the tools can start and inspect runs (spending the owner's free quota — bounded by budgets) but
cannot approve, cancel another tool's run silently, add providers or read keys; the token never
appears in a result. **Rejected.** *HTTP/SSE transport* — a second listening port.

**Amended 2026-09-18, found while building D2.**
- **The SDK is an optional extra, `cadre-ai[mcp]`.** The official SDK (`mcp` 2.2.0, MIT) brings 52
  packages including `cryptography`, OpenTelemetry and `pywin32`. That is too much for every
  install. `uvx --from "cadre-ai[mcp]" cadre mcp` is the host command, and `cadre mcp` without the
  extra says exactly that on stderr. All 52 licences pass `tools/check_licences.py`.
- **Roots.** The MCP 2026-07-28 spec deprecates roots (SEP-2577), and a server-initiated
  `roots/list` has no back-channel there (`NoBackChannelError`, observed). So the project default
  comes from a `Resolve(ListRoots)` multi-round-trip, asked only when the host declares roots and
  the call names no project. Otherwise it is the folder the server was started in, when that folder
  is inside a git repository. Claude Code and the editors start stdio servers in the workspace
  folder, and the host config snippets set it where a host allows. `project: ""` forces a fresh
  workspace. The fallback is safe because project mode never writes to the owner's tree.
- **The auto-started server** logs to `CADRE_HOME/logs/serve.log` and writes its PID to
  `serve.pid`, because stdout belongs to the protocol.
- **Windows job objects (observed 2026-09-18).** The MCP SDK's stdio client and Claude Code 2.1.276
  both start stdio servers inside a job object with `KILL_ON_JOB_CLOSE` and no breakaway
  permission. So a `cadre serve` that `cadre mcp` started detached is killed when the host session
  ends: seen with the SDK client and with `claude -p`. `CREATE_BREAKAWAY_FROM_JOB` is tried and
  refused. Creating the process through WMI would escape the job, but that is a technique security
  products flag as evasion, so Cadre does not use it. Instead a run started that way says it stops
  with the session and `cadre resume <id>` continues it (finished steps are not repeated). Runs
  truly outlive the editor when `cadre serve` was started outside it: by you, by `cadre ui`, or by
  the VS Code extension. AC-17.2 holds on that path, and on Linux and macOS
  (`start_new_session`); the latter is unverified.

### ADR-028 — Who may trigger the GitHub Action (FR-18)
**Decision.** A composite action runs Cadre's CLI on the runner in project mode on the checkout,
pushes `cadre/<run-id>` and opens a pull request. The example workflow triggers on the `cadre`
label or a `/cadre <goal>` comment, and its first step exits unless `author_association` is
`OWNER`, `MEMBER` or `COLLABORATOR`. Permissions: `contents: write`, `pull-requests: write`,
`issues: write`, nothing else. `allow-exec` defaults to on because the runner is a throwaway VM.
Keys arrive as repository secrets in environment variables (`CADRE_NO_KEYRING=1`). **Threat
model.** An issue body is untrusted text that becomes the goal: only trusted associations can
start a run, the goal is passed through an environment variable (never interpolated into a shell
line), the PR is a proposal a human merges, and the branch never targets the default branch
directly. Fork pull requests get no secrets by GitHub's design and cannot trigger it.
**Rejected.** *`pull_request_target`* — runs with secrets on untrusted code.

### ADR-029 — How the extension finds the server and handles the token (FR-19)
**Decision.** The VS Code extension calls `GET /api/v1/health` on the configured port (default
8765); if nothing answers it starts `cadre serve` (PATH) or `uvx cadre-ai serve`, detached, and
polls until healthy. It reads the token from `CADRE_HOME/token` (default `~/.cadre/token`) when
it needs it and keeps it in the extension host only; webviews get data by `postMessage`, run
with a CSP nonce, and insert model text with `textContent`. Adding a provider opens the integrated
terminal on `cadre provider add <id>`, so the key goes into the CLI's hidden prompt. An `exec`
approval is a notification (never focus-taking) whose Review… opens a modal that shows the check's
exact command from the org file. It publishes to both
the VS Code Marketplace and Open VSX (Antigravity, Cursor and Windsurf install from Open VSX).
**Threat model.** Other extensions share the extension host; the token is never stored in VS Code
settings or `SecretStorage` copies, and never shown in UI. Webviews are the XSS surface: no
`innerHTML`, no remote resources. **Rejected.** *Token in settings* — synced to the cloud by
Settings Sync.

### ADR-030 — Apache-2.0, and one reviewed weak-copyleft dependency (FR-14.1)
**Decision.** Cadre is Apache-2.0 (permissive, with a patent grant; chosen with Rithik on
2026-09-18). `tools/check_licences.py` runs in CI in an environment holding only the package and
its runtime dependencies, fails on any copyleft licence, and fails on any licence it does not
recognise as permissive. **Measured 2026-09-18:** 31 runtime dependencies. 30 are MIT, BSD, ISC,
Apache-2.0 or PSF. `certifi` is **MPL-2.0**, so the distribution prompt's "all dependencies are
MIT, BSD or Apache" was not quite right. MPL-2.0 is file-level copyleft: it binds only changes to
certifi's own files, and Cadre uses it unmodified through httpx, so it is allowed **by name** and
no other MPL package is. PyInstaller (D1) is GPL with a bootloader exception that lets frozen
applications carry any licence; it is a build tool and is not shipped in the wheel.
**Rejected.** *A blanket MPL allowance* — the next MPL dependency should be a reviewed decision.
