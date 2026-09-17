# Cadre — Project State

_Last updated: 2026-09-17 (v0.1.0 + v1.0 programme: M6–M10 done offline; M5 and M11 blocked)_

## What this is
A self-hosted platform that runs an organisation of AI agents — builders, reviewers, verifiers,
deciders, managers — declared as one YAML file and powered by whichever free model APIs the
owner adds by key. Python 3.12 + uv, FastAPI, SQLite; built for the i3-1215U / 7.7 GB laptop,
which it runs on without any local model.

Started 2026-09-16 from Rithik's idea: "an agentic AI on free models where you add models by
their API keys, at organisation scale — agents as workers, some building, some reviewing,
some verifying, some deciding". The refined statement and the three design drivers are in
`SRS.md` §1.

## Status: v0.1.0 — complete and verified offline; never yet run against a real model
_Everything below was observed with the scripted/demo provider and mocked HTTP. Whether free
models follow the verdict, vote and plan JSON shapes, how many repair turns they need, and what
a template really costs in tokens are **unverified** until M5 runs on a live key. No key was
available in this session (none in the environment; Tessera's stored Groq key was deliberately
not borrowed without asking)._

### SDLC record (2026-09-16)
| Phase | Artefact | State |
|---|---|---|
| Requirements | `SRS.md` — refined concept, 30 functional + 9 non-functional requirements | done |
| Design | `ARCHITECTURE.md` — 15 ADRs, data model, prompt shapes | done |
| Objectives | `OBJECTIVES.md` — O1–O10 with evidence | done |
| Implementation | `src/cadre/` — 16 modules + dashboard + 4 templates | done |
| Verification | `TEST_PLAN.md` — traceability FR→tests; 96 passed, 1 skipped, ~8 s | done offline |
| Deployment | `uv run cadre serve` (loopback, token) — smoke-tested over HTTP | done locally |
| Maintenance | `ROADMAP.md`, this file, `CLAUDE.md` | done |

### v0.1.0 — COMPLETE, verified offline 2026-09-16
- **Quota-aware router (flagship)** — every call is sized (chars/4 + schemas + reserved output),
  checked against each model's RPM/TPM/RPD/TPD sliding windows and the provider's own
  `x-ratelimit-*` headers, then sent to the soonest-available model; 429 cools the model for its
  `retry-after` and falls back, 401/403 disables the provider for the session, 413/context errors
  exclude only that model, "no tools" flips the model to the JSON tool protocol. A request larger
  than a model's whole TPM is never sent (Groq would reject it forever). Tests show a 1-RPM model
  waited 40.0 s rather than failing, and an exhausted daily limit failing with
  `a/m: daily limit of 1 requests reached (frees in …)`. Real Groq/Gemini headers: **unverified**. OK
- **Independent review** — reviewers and council members are routed away from the builder's
  model family; with one family available the call proceeds and the event says
  `independent: false`. In the demo the reviewer ran on `demo-b/beta-large` while the engineer ran
  on `demo-a`. Independence outranks tier (a fast other-family model beats a strong same-family
  one). OK
- **Checks gate, reviewers advise** — test: the reviewer approved round 1 but `check.py` exited 1,
  so the round was rejected (`gated_by_checks: true`), the builder got "check tests FAILED" in its
  round-2 prompt, fixed the file, and round 2 passed. In the software-team demo, `compileall` and
  `unittest` really ran: "Ran 1 test … OK" in 0.11 s. OK
- **Council with votes counted by code** — demo decision-board: 4 members, 1 critique round, 15
  calls, 7,232 + 682 tokens; the vote split 2–2, the majority rule failed, the chair broke the tie,
  and `DECISION.md` recorded the table, each member's choice and reason, and "Dissent: cfo, risk".
  Unparseable votes become listed abstentions (tested). OK
- **Manager → workers (company mode)** — the plan is JSON, validated (assignees, dependencies,
  cycles, size) with one repair turn; tasks run in topological waves bounded by `max_parallel`; a
  failed task skips its dependents; optional QA review loop per task; `REPORT.md` ends with a task
  ledger written by code. Demo startup-company: 11 calls, 5 files. Test: plan with a ghost
  assignee → repaired → t1 failed, t2 skipped, t3 done, run marked not approved. OK
- **Seven step types** — agent, sequence, parallel (+ join), review_loop, council, manager,
  approval; YAML shorthand infers the type from its keys. All four templates run end to end in
  demo mode (software-team 9 calls, decision-board 15, startup-company 11, research-desk 9). OK
- **Safety** — tools: list/read/write file, post_note, run_check, ask_human; an agent sees only
  its listed tools (a refused `write_file` left no file). `run_check` takes a *name* from the org
  file; checks run with `cwd` = workspace, secret-looking env vars and loaded key values removed
  (a check printing `SOME_TOKEN` saw `None`), a timeout and a 16 KB cap, and need an `exec`
  approval unless `--allow-exec`. **Not a sandbox**: approved checks run model-written code as the
  owner. The workspace refuses `..`, absolute paths, drive letters, `~`, NTFS streams, device
  names and `.git` (12 cases tested). OK
- **Keys** — OS credential store (`keyring`), else `CADRE_KEY_<ID>`, else the preset's variable.
  A planted key echoed back by a mocked 401 *and* typed into the goal was absent from
  `cadre.sqlite*` afterwards (found and fixed a real leak doing this — see TEST_PLAN defects). OK
- **Persistence and resume** — SQLite (WAL): runs, events, step results, usage, quota counters,
  approvals, file versions. Resuming a run that failed at step 2 re-ran only step 2 (provider call
  count +1) and step 2 still saw step 1's output. Cross-process cancel of a run waiting on approval
  → `cancelled`, pending approvals cancelled. Stale active runs (no heartbeat for 90 s) become
  `interrupted`. OK
- **CLI** — `init, presets, provider add|key|list|test|models|set-model|remove, quota, org
  list|show|validate|new, run, resume, runs, show, cancel, approvals, approve, serve, ui`.
  `cadre run decision-board "…" --demo` streamed the whole timeline in a Windows console (stdout
  forced to UTF-8). `provider add` against a live endpoint: **unverified**. OK
- **API + dashboard** — `cadre serve` on 127.0.0.1: `/api/health` 200, `/api/runs` without token
  401, foreign `Host` 421, CSP without `unsafe-inline`, no CORS; a demo run started over HTTP
  finished in under 3 s; the server process used ~62 MB (tasklist working set) after one run.
  Dashboard: runs, live timeline (fetch-streamed), files, usage, new run, org editor with
  validation, providers + live quota bars, approvals. `app.js` passes `node --check` and contains
  no `innerHTML`; **rendering in a browser is unverified** (Chrome extension not connected). OK

### Numbers
- Code: ~5,000 lines of Python in `src/`, ~740 lines of dashboard JS/CSS, ~1,170 lines of tests.
- Tests: 96 passed, 1 skipped (directory symlink needs Windows Developer Mode), 8.1 s.
- Lint: `ruff check src tests` clean.

### Known limitations
- No live-model evidence (M5). The demo provider is plumbing, not intelligence — it says `[demo]`.
- Gemini, Mistral, Z.ai limits are conservative guesses or third-party reports (`presets.py`
  marks each); the header learning corrects them only where a provider sends headers.
- Checks are not sandboxed (ADR-006). Use `--allow-exec` only for goals you trust.
- Single user. The token is one shared secret; no roles, no per-team budgets yet (M13).
- Resume gives the run a fresh budget window (documented choice, not an accident).
- Starlette warns that its TestClient's `httpx` backend is deprecated; harmless for now.

## v1.0 programme (started 2026-09-17)

**Headline (end of 2026-09-17):** requirements, design and plan written; M6 catalogue/clocks/privacy,
M7 ledger/forecast, M8 edit tools/repo map, M9 project mode, M10 multi-day runs all built and
tested offline — **150 passed, 1 skipped, 24.5 s**, ruff clean, commits `1547bd3`…`90a776d`, no
remote (push skipped). **Still no live model call has ever been made**, so everything that depends
on real model behaviour (JSON shapes, repair rates, `edit_file` accuracy, token estimates, real
429s and headers) is unverified. M5 and M11 are blocked on a session permission, not on a key.
`git grep` for key prefixes finds only the four fake fixtures in `tests/`.

`docs/MASTER_PROMPT.md` turns Rithik's restated idea — *build or finish a project on free keys
only, across every provider, managing each model's usage* — into milestones M5–M11.

### Phase 0 baseline (2026-09-17)
`uv sync` clean; `uv run pytest -q` → **96 passed, 1 skipped in 9.2 s**; `ruff check` clean;
tree clean at `d8975a2` apart from the new `docs/MASTER_PROMPT.md`. No provider configured.

### Gaps G1–G8, checked against the code on 2026-09-17 — all confirmed open
| Gap | Confirmed by |
|---|---|
| G1 never run on a real model | no provider in `~/.cadre/config.yaml`; no live run in the store |
| G2 cannot work on an existing project | `runs.py` builds every workspace as `runs_dir/<id>` via `RunContext` → `Workspace(run_dir)` |
| G3 one Gemini model, guessed limits | `presets.py`: `gemini` lists only `gemini-2.5-flash`, `source="guess"` |
| G4 daily limits end runs | `router.py` raises `NoModelAvailable` when every wait exceeds `max_wait`; `runs.py` marks it `failed` |
| G5 every day is UTC | `quota.py` `_utc_day` / `_until_midnight` |
| G6 no history, no forecast | `cadre quota` prints only today's counters; no per-day ledger or forecast command |
| G7 whole-file rewrites only | `tools.py`: `list_files`, `read_file`, `write_file` |
| G8 data policy only in comments | `presets.py` `note` strings; nothing in `router.py` reads them |

### Phases 1–3 — done 2026-09-17
SRS v1.1 §4.8 (FR-8…13 with 22 acceptance criteria, NFR-10/11), OBJECTIVES O11–O16,
ARCHITECTURE ADR-016…022 with the v1.0 state machine and migration plan, ROADMAP renumbered
(old M6–M10 → M12–M16), TEST_PLAN v1.0 traceability with every test named before it exists.
Free-tier facts behind them were read from each provider's own pages on 2026-09-17 (Google:
RPD resets "at midnight Pacific time", limits "per project", free tier "Used to improve our
products: Yes"; Cloudflare: "All limits reset daily at 00:00 UTC", no training; Groq: no training,
`qwen/qwen3.8-27b` on the free plan at 30 RPM / 1K RPD / 8K TPM / 200K TPD; DeepSeek's own API is
paid and OpenRouter lists no DeepSeek `:free` model).

### M10 — multi-day runs — COMPLETE offline, 2026-09-17
- **Park instead of fail (ADR-017)** — when every eligible model is blocked by a daily limit
  beyond `max_wait`, the run becomes `parked` with `resume_at` = the earliest reset + 30–120 s
  jitter and a `run.parked` event naming each model, its reason and the reset (in IST). Fake-clock
  test: 22:00 UTC, RPD 2, three steps → two ran, the run parked for 2 h + jitter, the clock passed
  midnight, `resume_due` finished it, and the provider saw **3 calls in total** (no step billed
  twice). Minute limits still wait (two `route.wait` events, no park). OK
- **Resuming** — `cadre resume --due` (one pass over due runs), `cadre serve` (checks every 60 s;
  tested with a due parked run that the server finished by itself), `cadre scheduler
  install|uninstall|status` (Windows Task Scheduler every 30 min; prints the exact `schtasks`
  command and asks first). The scheduler was **not installed** — that needs Rithik's yes.
  `cadre scheduler status` on this machine: "The scheduled task is not installed." OK
- **Cumulative budgets** — calls and tokens start from what the run already used; active minutes
  accumulate; `resume --add-calls/--add-tokens` raises a stopped run's allowance (test: stopped at
  2/2 calls, +1 call, finished). `max_days` (default 7) stops a run by name; `max_tokens_per_day`
  parks it until the next UTC day (test: 150/100 → parked after one call). OK
- Dashboard: `parked` status, a "resumes around …" callout, a Resume button, and timeline entries
  for parks, worktree, commits and privacy exclusions. (Not yet seen in a browser.)
- One v0.1 test changed on purpose: an exhausted daily limit now parks instead of raising
  `NoModelAvailable` (`test_router::test_exhausted_quota_parks_with_a_reason_that_names_the_model`).
- Tests: **150 passed, 1 skipped, 24.5 s**; ruff clean.

### M9 — project mode — COMPLETE offline, 2026-09-17
- **Worktree on its own branch (ADR-016)** — `cadre run <org> "<goal>" --project PATH [--base B]
  [--allow-dirty]` (and the API's `project/base/allow_dirty`). A non-repo, a repo with no commits,
  an unknown base and a dirty tree (without `--allow-dirty`) are refused before any model call.
  The run works in `git worktree add <run>/workspace -b cadre/<run-id> <base>`. OK
- **Owner untouched (NFR-10)** — test: HEAD, current branch (`feature/owner-work`) and
  `git status --porcelain` identical before and after, and the owner's `calc.py` unchanged while
  the branch carries the fix. From the CLI on a throwaway repo: `main` still checked out, porcelain
  empty, branch delivered. OK
- **Per-step commits** — after each finished step that changed files:
  `cadre(<step path>): <first line of the output>`, the repository's own identity, hooks and
  signing, no trailers (test checks author, subject, and the absence of `Co-authored`). OK
- **Repo checks from the base commit** — `.cadre/checks.yaml` is read with `git show <base>:…`
  at creation; editing it in the working tree afterwards changed nothing (test). Agents cannot
  write or edit anything under `.cadre/` in project mode. `checks: [all]` means every declared
  check; the exec approval now lists every check command and where it will run. OK
- **Records stay out of the repo** — `plan.json`, `REPORT.md`, `DECISION.md` go to
  `<run>/artifacts/` (API `GET /api/runs/{id}/artifacts/{name}`), not onto the branch. OK
- **Resume** — a project run that failed at step 2 resumed on the same branch and worktree (event
  `project.worktree: reused`); the branch ended with exactly two commits. OK
- **Result and cleanup** — the summary carries the branch, its commits, `git diff --stat
  base...branch`, and the review/discard commands the CLI prints. `cadre runs cleanup [--yes]`
  removes finished runs' worktrees without `--force` and keeps their branches. OK
- **`project-finisher` template** — lead plans (manager step), engineer edits with the M8 tools,
  repo checks gate each task, a reviewer on another family advises, lead reports. Demo run on a
  fixture with a failing test: status `unapproved` because the unit check failed — checks gate.
- Tests: **142 passed, 1 skipped, 22.4 s** (git fixtures add ~6 s); ruff clean. A real model
  finishing a real repository is **unverified until M11**.

### M8 — code-editing tools and repo map — COMPLETE offline, 2026-09-17
- **`edit_file`** (exactly one exact match, else an error that gives the count; CRLF files accept
  plain-newline edits; versioned like writes), **`read_file` line ranges**, **`search`** (regex or
  literal, ≤ 40 hits, workspace only). Measured with Cadre's estimator on a 400-line module: a
  one-line change costs 7,056 tokens by rewrite vs 134 by edit (saving 6,922); the schemas cost
  218 tokens per call → kept (ADR-021). A whole 400-line file needs 4 reads, because observations
  are cut at 4 KB — the range read is necessary, not a luxury. OK
- **Repo map** (ADR-022) — injected for agents holding file tools: path, line count, public
  classes then public functions (`ast`), capped at 1,200 tokens. Cadre's own 21 modules: 683
  tokens vs 101 for the plain listing. OK
- **Found and fixed while measuring:** every new file write walked the whole tree to enforce the
  300-file cap (quadratic; a 290-file test took 12.6 s) and the cap counted files a project
  already had, which would have blocked project mode on any real repository. The cap now counts
  files written during the run, and listing prunes `.git`, `node_modules`, `.venv`, `__pycache__`
  instead of walking into them (same test: 0.9 s).
- software-team: engineer gains `search` and `edit_file` and is told to edit rather than
  rewrite; reviewer gains `search`.
- Tests: **135 passed, 1 skipped, 15.8 s**; ruff clean. Real-model use of `edit_file` (do free
  models copy `old` exactly?) is **unverified until M5**.

### M7 — usage ledger and forecast — COMPLETE offline, 2026-09-17
- **Ledger** — `cadre usage [--days N]`, `GET /api/usage`, and a dashboard *Usage* page (table
  with one single-hue meter per row and the percentage printed beside it; text via
  `textContent`). Rolling hourly counters fold into their date; the next reset is shown in IST
  on the provider's own clock (test: Gemini "18 Sep 12:30 IST"). OK
- **Forecast** — `cadre forecast <org> "<goal>"`, `POST /api/forecast`, and a preamble printed by
  `cadre run`. With history: median and p90 (nearest rank) of finished, non-demo runs, printed as
  `measured, n = k` (test: calls 10/12/14/16/40 → median 14, p90 40). Without: the workflow tree
  is walked with each agent's real system prompt and tool schemas, printed as `no history,
  estimated from template size`. Offline estimates today (p90 calls / tokens): decision-board
  18.5 / 12.2k, research-desk 22.5 / 25.7k, software-team 23.8 / 31.6k, startup-company 35.5 /
  44.6k. For comparison, the one measured demo decision-board run used 15 calls / 7.9k tokens —
  but demo replies are tiny, so **the estimates are unverified until M5**. Verdicts tested:
  fits now, fits today after ~5 min, needs ~3 days, cannot run (call larger than every TPM; no
  model; private with nothing eligible). OK
- **Reserve** — `reserve_pct` (default 10) shrinks RPD/TPD as the router sees them (tested: 25 →
  1000 RPD becomes 750). **Active time** is accumulated per run in `runs.active_seconds`.
- Tests: **125 passed, 1 skipped, 12.3 s**; ruff clean; dashboard `app.js` passes `node --check`
  (the new page is not yet seen in a browser).

### M6 — provider catalogue, day clocks, data policy — COMPLETE offline, 2026-09-17
- **Catalogue** — Google AI Studio is now ten separate chat-model buckets (from its pricing page,
  updated 2026-09-16); Groq has three (two families); every preset model carries `source`
  (`docs` / `reported` / `guess`) and `CHECKED = 2026-09-17`. DeepSeek is labelled paid with its
  current ids (`deepseek-flash`, `deepseek-v4-pro`). Test: a 19-call burst against the Gemini
  preset spread across ≥ 4 buckets with none above its 5 RPM. OK
- **Day clocks (ADR-018)** — `clocks.py`; Gemini `America/Los_Angeles`, Cloudflare and OpenRouter
  `UTC`, Groq `rolling` (hourly buckets). Observed in `cadre quota`: Gemini's next reset shown as
  "18 Sep 12:30 IST" (midnight PDT). Today's UTC counters carry over when a model's clock changes
  (tested). `tzdata` added because Windows has no zone database. OK
- **Data policy (ADR-020)** — `trains_on_free_data` + source URL per preset; `--private` /
  `privacy: private` filters the router; `privacy.excluded` lists what was dropped; a private run
  with nothing left failed before any call (provider call count 0). A private demo run of
  decision-board succeeded from the CLI. OK
- **Refresh** — `cadre provider refresh [id] [--apply]` and `POST /api/providers/{id}/refresh`:
  Gemini's `models/` prefix stripped; a listed catalogue only adds free chat models it knows;
  OpenRouter adds only `:free`; an owner-set limit survived `--apply` (tested). **Live refresh
  unverified** — it needs a key the session could not read.
- **Migration** — `PRAGMA user_version` 0 → 2 adds `project_path, base, branch, resume_at,
  active_seconds, privacy` to `runs`; a hand-built v0.1 database kept its run, usage and quota
  rows (tested). `reserve_pct` (default 10) already shrinks daily caps for the router.
- Tests: **116 passed, 1 skipped, 11.1 s**; ruff clean.

### M5 — blocked on a permission (2026-09-17)
Rithik authorised using Tessera's Groq key and copied it into Cadre's Credential Manager entry
himself (his command printed `copied`). The session's auto-mode safety classifier then refused,
as credential access, `cadre provider add groq`, writing the provider entry, and adding an allow
rule — so no live call has been made. The Groq preset now includes `qwen/qwen3.8-27b`, giving a
second model family on the one free key (reviews can be independent).

## Where to pick up
0. **Next session prompt:** `docs/PROMPT_M5_M11.md` (live verification → capstone → v1.0),
   written 2026-09-17. Allow `Bash(uv run cadre:*)` first.
1. **Unblock M5 (Rithik).** The Groq key is already in Windows Credential Manager (copied from
   Tessera on 2026-09-17, never printed). This session's auto-mode classifier refused every
   command that reads it. Either add an allow rule with `/permissions` → `Bash(uv run cadre:*)`,
   or run the commands yourself with a leading `!`:
   ```
   ! cd /c/Users/Rishi/cadre && uv run cadre provider add groq
   ! cd /c/Users/Rishi/cadre && uv run cadre forecast decision-board "Should a two-person team build a budgeting app or a notes app first?"
   ! cd /c/Users/Rishi/cadre && uv run cadre run decision-board "Should a two-person team build a budgeting app or a notes app first?" --yes
   ```
   Adding Gemini (`uv run cadre provider add gemini`) as well gives a third family and ten more
   buckets.
2. **M5 measurements** — run the five templates (`software-team --allow-exec`, `decision-board`,
   `startup-company`, `research-desk --yes`, `project-finisher` on a scratch fixture repo) and
   record calls, tokens, median fixed prompt, repairs, waits/fallbacks/429s, which model served
   each role, and independence. Then compare with the M7 template estimates, check whether free
   models use `edit_file` correctly (M8), and measure server RSS while a run streams.
3. **M11 capstone** in `~/.cadre/capstone/` (a new CLI via `software-team`; a half-built fixture
   repo via `project-finisher`), then `claim-auditor`, version 1.0.0, `CHANGELOG.md`, tag `v1.0.0`.
4. Look at the dashboard in a browser (Usage page and parked runs are new and unseen).
5. Decide whether to install the resume scheduler (`uv run cadre scheduler install` asks first).

## Environment
`cd C:\Users\Rishi\cadre`, `uv sync`, `uv run pytest -q`. State in `~/.cadre` (`CADRE_HOME`
overrides). Git: branch `main`, **no remote** (commit only).
