# Cadre — Project State

_Last updated: 2026-09-16 (v0.1.0)_

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
- Single user. The token is one shared secret; no roles, no per-team budgets yet (M7).
- Resume gives the run a fresh budget window (documented choice, not an accident).
- Starlette warns that its TestClient's `httpx` backend is deprecated; harmless for now.

## Where to pick up
1. **M5 — live verification.** Rithik adds a free key (`uv run cadre provider add groq`, or the
   dashboard's *Models & keys* form), then run each template once and record here: calls, tokens,
   repairs, 429s/waits, whether reviews were independent. With only Groq, reviews will report
   `independent: false` — add Gemini too.
2. Look at the dashboard in a browser and fix what renders badly.
3. Then `ROADMAP.md` M6+.

## Environment
`cd C:\Users\Rishi\cadre`, `uv sync`, `uv run pytest -q`. State in `~/.cadre` (`CADRE_HOME`
overrides). Git: branch `main`, **no remote** (commit only).
