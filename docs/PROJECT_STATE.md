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

### M11 — capstone on free keys — both runs SUCCEEDED, 2026-09-17/18
Fixtures only, under `~/.cadre/capstone/` (none of Rithik's real repositories).

| Capstone | Template | Status | Wall / active | Calls | Tokens in + out | Waits / fallbacks / 429s | Parks | Reviews independent | Forecast → actual |
|---|---|---|---|---|---|---|---|---|---|
| 1. Build a unit-converter CLI (`…231009`) | software-team `--allow-exec` | **succeeded** after one interruption | interrupted when the session ended; resumed 18 Sep, 73 s | 35 | 110,073 + 8,443 | 11 / 5 / 0 | 0 | 0 of 9 | measured n=1: 27 calls, 94.0k → 35, 118.5k (1.26×) |
| 2. Finish `m11-inventory` (`…152845`) | project-finisher `--allow-exec --project` | **succeeded** | 179 s | 17 | 35,666 + 2,641 | 0 / 4 / 0 | 0 | 0 of 3 | measured n=1 (from the 300-token m5 fixture): 14, 16.8k → 17, 38.3k (**2.3×**) |

- **Capstone 1 is a real program.** Goal: *"A command-line unit converter (length, mass,
  temperature) in Python, standard library only, with unittest tests and a README."* Delivered
  `app.py` (149 lines), `test_app.py` (85), `README.md` (48), `SPEC.md`. Re-run by hand: `Ran 9 tests
  … OK`; `app.py 10 km mi` → `6.2137`, `app.py 100 c f` → `212.0000`, `app.py 5 kg lb` → `11.0231`
  (all correct); `--help` works. The engineer used `edit_file` 3× and `search` 2× with no tool
  errors. The run was **interrupted** when the Claude Code session ended mid-run on 17 Sep and
  `cadre resume` finished it on 18 Sep reusing every finished step — live evidence for resume.
- **Capstone 2 finished a half-built package.** Fixture: `inventory.store` finished (3 passing
  tests), `inventory.report` stubbed with `NotImplementedError` and 5 failing tests. Result: one
  commit on `cadre/20260918-152845-13a59c` (`inventory/report.py` +17/−3, tests untouched, author
  Rithik's identity, no trailer); `git archive` of the branch re-run by hand: `Ran 8 tests … OK`.
  The owner's side: `main` HEAD `87c6b40`, branch `main`, empty porcelain — identical before and
  after (NFR-10 live).
- **Honest gaps.** No review in either capstone was independent: Groq's Qwen — the only family
  neither builder used — was still inside its rolling 24-hour limit from the M5 runs, and both
  builders had used both gpt-oss and Gemini. Cadre routed the reviews anyway and recorded
  `independent: false` for every one (ADR-004 working as designed; the verdicts rest on the
  checks). **No run parked live**: daily limits were hit per model (Qwen, Gemini Flash) but
  another model was always free, so M10's park/resume is still verified only with a fake clock.
  Capstone 2's forecast was 2.3× low because the only measured history came from a 300-token
  fixture; history is per org, not per project size — recorded as a known limitation.

### M5 — live verification on Groq + Google AI Studio — COMPLETE, 2026-09-17
Keys: Groq (gpt-oss-120b, gpt-oss-20b, qwen3.8-27b) and Gemini, added by Rithik at the hidden
prompt. `provider test`: Groq "13 models listed", Gemini "58 models listed"; `provider refresh`:
all 3 + 10 preset models exist live. Measured with `tools/run_metrics.py` (reads only the store).

| Run | Template | Status | Wall | Calls | Tokens in + out | Median 1st prompt | Repairs | Waits | Fallbacks | 429s | Reviews independent | Forecast (p90) → actual |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `…220210` | decision-board | succeeded | 125 s | 15 | 14,285 + 7,188 | 526 | 1 | 0 | 13 | 0 | 5 yes / 4 no | 18.5 calls, 12.4k → 15, 21.5k (tokens 1.7× over) |
| `…220717` | research-desk (first try) | failed → resumed → succeeded | 171 + 231 s | 40 | 54,554 + 10,535 | — | 0 | 3 | 24 + 6 | 1 | — | — |
| `…222128` | research-desk | succeeded | 277 s | 23 | 71,366 + 9,065 | 509 | 0 | 3 | 6 | 1 | 5 / 1 | measured n=1: 40, 65.1k → 23, 80.4k (1.2×) |
| `…222632` | startup-company | **unapproved** (task t4 failed) | 1,249 s | 57 | 186,786 + 23,431 | 953 | 0 | 31 (QA 1,411 s) | 27 | 8 | 27 / 1 | 35.5, 47.7k → 57, 210.2k (**4.4×**) |
| `…224959` | software-team `--allow-exec` | succeeded | 466 s | 27 | 86,234 + 7,760 | 751 | 0 | 6 | 11 | 8 (Gemini daily) | 7 / 2 | 23.8, 36.9k → 27, 94.0k (**2.5×**) |
| `…225900` | project-finisher on `m5-fixture` | succeeded | 123 s | 14 | 15,364 + 1,478 | 823 | 0 | 5 | 7 | 4 (Gemini daily) | 2 / 1 | 60.5, 281k → 14, 16.8k (median 71k: **4.2× over**) |
| `…230536` | decision-board via API stream | succeeded | 146 s | 14 | 13,186 + 7,920 | — | 0 | 4 | 2 | 1 | 3 / 6 | measured n=1: 21.5k → 21.1k |

Which model served which role (examples): council members were spread across gpt-oss-120b,
gemini-3.6-flash, qwen3.8-27b and gpt-oss-20b; the fourth member cannot be independent with three
families, and the run says so. Engineers and editors mixed gpt-oss-120b with Gemini Flash models
(Gemini's 503s pushed them around); reviewers and QA landed on qwen3.8-27b, the one family neither
builder used — which made Qwen's 8,000 TPM the pace-setter (QA waited 27 times in startup-company).

**What real models broke, and the fix for each** (each fix has a regression test built from the
live response or error):
1. **Gemini 3 thinking ate the output budget** — a strict-JSON options list stopped after 154
   visible tokens. Gemini now gets `reasoning_effort: "low"`; every call records `finish_reason`;
   cut-off answers raise `agent.truncated`.
2. **Gemini "503 high demand", 7–12 times a run** — a busy model now rests 30 s, 60 s, 120 s … up
   to 10 min until it answers again (v0.1 rested 5–15 s and retried it at once).
3. **Gemini 3 tool loops failed with 400 "Function call is missing a thought_signature"** — this
   failed the first research-desk run. `tool_calls[].extra_content` is now kept and replayed to the
   provider that issued it; calls made by another model get Google's documented placeholder
   (`skip_thought_signature_validator`); a tool loop prefers to stay on its model. After the fix
   the checker ran 7 tool turns on Gemini with no 400.
4. **gemini-2.5-flash / 2.5-pro / 2.5-flash-lite answered 404 "no longer available to new users"**
   — a 404 now takes the model out for the session; the three are gone from the preset and were
   disabled in this config.
5. **An analyst wrote its report as a text answer; an editor called `list_files` eight times in a
   row and never saved `BRIEF.md`** — tasks that name a file ("Write market.md", "… into BRIEF.md")
   are checked by code: one nudge, then Cadre saves the answer to the file (`agent.deliverable_saved`);
   identical repeated reads are answered with "you already did this" instead of being run. The
   fresh research-desk run then wrote all four files and was approved in round 1 (1 repeat caught).
6. **A review was marked independent when it wasn't** — the editor worked on gpt-oss and answered
   on Gemini; the checker avoided only Gemini. Reviews now avoid every family the builder used.
7. **startup-company's t4 failed: "waited 299s for capacity and gave up"** — four QA reviews queued
   on the one independent model. The per-call wait cap is now 15 min (`max_total_wait`), and
   reviewers get the written files inline (capped) so a review needs fewer turns.
8. **Gemini 429 "You exceeded your current quota" after 6–15 requests** — Google's quota details
   are now parsed; a daily-quota 429 marks the model spent until its own reset and learns the
   reported limit; the error text kept in notes is 600 characters, not 300.
9. **Forecasts were off by up to 4.4×** — recalibrated from these runs (writers send ~2× their base
   prompt, readers ~3–5k, outputs 200–700 by role; readers' context is sized from the project when
   there is one). Template estimate (median tokens), old → new, each n = 1:
   decision-board 12,231 → 17,470 (actual 21,473); research-desk 22,399 → 71,262 (80,431);
   startup-company 32,168 → 121,678 (210,217); software-team 25,513 → 86,992 (93,994);
   project-finisher on the fixture 71,120 → 18,688 (16,842). All now within 2×.
10. **`cadre provider add` hung on the hidden prompt when no terminal could answer** — it now
    fails at once and names where it looked (found while the key was missing).

Also observed live: **header learning works** — `GET /api/quota` showed Groq's
`x-ratelimit-remaining-tokens` 4,689 (reset 9.9 s) and remaining requests 971 (reset 2,490 s);
Groq sent 1–8 429s per run anyway, which cooled the model for its `retry-after` and fell back.
**Editing tools**: the project-finisher engineer used `search`-free line reads and one
`edit_file`, correct first time on a CRLF file; software-team needed no edits; **0 misuses** in
the two runs. **software-team's deliverable is real**: 87-line `app.py`, 124-line `test_app.py`,
`Ran 10 tests … OK` when re-run by hand, and `app.py demo.csv` printed a correct Markdown table.
**project-finisher** changed `line_count` on `cadre/20260917-225900-fe3c25` (one commit, the
owner's identity, no trailer) while `main`'s HEAD `663e2f1` and an empty porcelain were unchanged.
**Server memory**: 64.5 MB idle, **72.9 MB peak** while a run streamed 55 events (target < 150 MB).
**Dashboard in a browser: still unverified** — the Chrome extension was not connected.

Tests after M5: **170 passed, 1 skipped, 24 s**; ruff clean.

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
