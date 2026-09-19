# Cadre — Project State

_Last updated: 2026-09-19 (1.1.0 released; M13 phase 1 done on `main` and unreleased: users, roles, hashed tokens, RBAC, audit log; phase 2 next: teams, budgets/allowances, approval routing, OIDC)_

## What this is
A self-hosted platform that runs an organisation of AI agents — builders, reviewers, verifiers,
deciders, managers — declared as one YAML file and powered by whichever free model APIs the
owner adds by key. Python 3.12 + uv, FastAPI, SQLite; built for the i3-1215U / 7.7 GB laptop,
which it runs on without any local model.

Started 2026-09-16 from Rithik's idea: "an agentic AI on free models where you add models by
their API keys, at organisation scale — agents as workers, some building, some reviewing,
some verifying, some deciding". The refined statement and the three design drivers are in
`SRS.md` §1.

## Status: 1.1.0 released; M13 phase 1 (accounts) done on `main`, unreleased
_One release for the engine (v1.0 programme, M5–M11) and distribution (D0–D3, D5; the VS Code
extension, D4, is built but not published); see "1.0.0 release" below. The v0.1.0 section that follows is the offline record of 2026-09-16 and is kept as history._

## M13 phase 1 — users, roles and API tokens — DONE on `main`, 2026-09-19

FR-23 (AC-23.1…23.6), ADR-032 (identity & RBAC) and ADR-033 (tokens hashed at rest + bootstrap).
Scope was set with Rithik: **phase it** (identity layer now, OIDC and networked multi-user later),
**per-user API tokens** (not passwords).

- `src/cadre/accounts.py`: three fixed roles (viewer ⊂ member ⊂ admin) → capabilities
  (`read`, `run`, `approve`, `providers`, `admin`); `Accounts` service (create/list/role/disable
  users, mint/list/revoke tokens, resolve, audit); the last enabled admin is protected.
- `store.py` schema **v3**: `users`, `tokens` (hash, not the secret), `audit` tables, and an
  `owner_user` column on `runs`. Migration adds them to a v2 database without touching its rows.
- API: `auth` resolves the bearer token to a user; a `requires(cap)` dependency returns `403`
  when the role lacks the capability. Reads need any token; provider writes need `admin`; run
  start/cancel/resume and org edits need `member`+; approvals need `member`+. New `GET /me`,
  admin `GET /users`, admin `GET /audit`. Run start records the owner and audits `run.started`;
  deciding an approval audits `approval.decided`.
- **Bootstrap:** `create_app` turns the existing `CADRE_HOME/token` into an admin `owner` the
  first time it sees a userless DB, so the dashboard, MCP, the extension and every existing test
  authenticate unchanged (they pass the owner token → admin).
- CLI: `cadre user add|list|role|disable|enable`, `cadre token new|list|revoke`, `cadre audit`.
  `cadre token new` prints the secret once; only its SHA-256 is stored.

**Verified:** 252 passed, 9 skipped; `ruff` clean. `tests/test_accounts.py` (13 tests): the role
lattice, tokens hashed (secret absent from a full `tokens` dump, hash present), revoke/disable stop
auth, bootstrap idempotent, the last-admin guard, the audit trail, the v2→v3 migration keeping
rows, and RBAC over the live API (viewer 403 on run/manage, member 403 on users/providers, admin
sees users+audit, no secret ever returned). `tests/test_cli.py` (+3): the user/token lifecycle
(secret not in the DB file) and `cadre audit`. The OpenAPI snapshot gained only `/me`, `/users`,
`/audit`.

**Not in this phase (M13 phase 2):** teams; per-team token/run budgets and model allowances;
approvals routed to a role/team (today any member/admin may decide any run's approval — the audit
log makes it accountable); OIDC SSO; row-level run ownership enforcement. The dashboard and the
VS Code extension don't surface users/roles yet.

## 1.1.0 release (2026-09-19, `PROMPT_M12.md`)

Tag `v1.1.0` on 4be50cd. Release run 35448589292 **green on the first attempt**, all 8 jobs:
wheel/sdist, three standalone builds, container image, TestPyPI, PyPI, GitHub Release. From a
clean uv cache and a new `CADRE_HOME`: `cadre 1.1.0`, and a `decision-board --demo` run succeeded
(needed `uvx --refresh` — PyPI's JSON API lagged the simple index by a few minutes, as with 1.0.1,
though the simple index had 1.1.0 within ~20 s). SHA-256 identical on PyPI, TestPyPI and the GitHub
Release: `077a3153…` wheel (154,302 bytes), `5320d3da…` sdist. GHCR `1.1.0`, `1.1` and `latest`
all return manifest 200 anonymously. This release closes, on PyPI, the MCP `allow_exec` bypass that 1.0.0 and 1.0.1 carried.

On Rithik's separate yes, **`v1` was moved to 1.1.0** (4be50cd; `git tag -f`, pushed `--force` for `v1` only). Proof: labelled issue #8 on `cadre-action-demo` -> run 35450766290 downloaded `Daemon-VI/cadre@v1` at **4be50cd** -> PR #9, one-line title, body ends `Closes #8`, tests pass. The GitHub Marketplace shows **v1.1.0** as Latest. A GitHub security advisory for the MCP bypass (affected 1.0.0/1.0.1, fixed 1.1.0) is drafted for Rithik to publish.

## M12 — container runner for checks — released in 1.1.0, 2026-09-19 (`PROMPT_M12.md`)

FR-22 (AC-22.1…22.9), ADR-031 (with the threat model and what a container does not protect).
`src/cadre/containers.py` builds `docker run` / `podman run` as an argument list; `CheckSpec` gains
`runner`, `image`, `allow_unpinned`, `memory`, `cpus`, `pids_limit`, `env`; the engine's approval
prompt says where every check runs, and `allow_exec: container_only` (`--allow-container-exec`)
auto-approves only container checks. Subprocess stays the default.

**Containment, real containers** (CI run 35444898292 on branch `m12`, job `containment`, Ubuntu,
image `python:3.12-slim@sha256:2f17fc04…`; the same outcomes on the first run, 35444664883, and on
the final code, 35445270881, with Docker 28.0.4 and Podman 4.9.3, 7 of 7 each):

| Attack | Docker | Podman |
|---|---|---|
| Reach the network (TCP 1.1.1.1:443, DNS, HTTP; the host itself reached 1.1.1.1:443) | contained: `Network is unreachable`, DNS `Temporary failure in name resolution` | contained, same |
| Write outside `/work` (`/etc`, `/`, `/usr/lib`, `/home`, `/work/..`) | contained: all `OSError`; `/work` and `/tmp` writable; ran as uid 1001 | contained, same |
| Read a key planted in the host environment (as `*_API_KEY`, and under an innocent allowlisted name) | contained: not in the environment nor in `/proc/1/environ`; the allowlisted non-secret variable passed | contained, same |
| Fork bomb, `pids_limit: 64` | contained: stopped after 63 forks, `[Errno 11] Resource temporarily unavailable` | contained, same |
| Allocate 512 MiB, `memory: 128m` | contained: killed, exit 137 | contained: killed, exit 137 |
| Outlive a 5 s timeout | killed; `docker ps -a` no longer lists it | killed and removed |
| Normal check: the M11 unit converter's unittest | passed, `Ran 9 tests … OK` | passed, `Ran 9 tests … OK` |

The first CI run failed only the Podman normal check's *assertion*: the check passed, but Podman
reports image ids as bare hex, not `sha256:<hex>`. The test now accepts both (recorded in ADR-031).

**Not contained, by design (ADR-031):** the shared kernel, the read-write workspace (a check can
plant files a person later runs), anything already in the workspace, the image itself, disk use,
and prompt injection through the check's output.

**On this laptop** (Rithik's yes, once; he was not asked to close programs, and ~1.0 GB was free):
Docker Desktop 29.6.2 started in 28 s; `vmmemWSL` 1,216 MB after start, and free memory fell from
1,077 MB to 280 MB. The first check failed as designed ("image … is not on this machine, and Cadre
never pulls one … Run: docker pull …"). After that pull, the unit converter check ran through
`run_check_process` with `runner: docker`: **passed in 2.3 s, `Ran 9 tests … OK`, user
10001:10001, network none**. During it `vmmemWSL` peaked at 991 MB and free memory never fell
below 403 MB. `docker desktop stop` afterwards; no WSL distribution left running. The hostile
probes were not run on Windows.

**Two holes found and fixed while building M12** (both in 1.0.0/1.0.1 or introduced by M12):
1. *MCP approval bypass (in released 1.0.0/1.0.1).* `cadre_start_run` forwarded `allow_exec`, which
   skips the exec approval, so a model over MCP could run checks unapproved, contrary to ADR-027.
   The argument is gone; two tests fail on the old code.
2. *Cadre's own git could run a check's planted command (caught by the claim-auditor).* In project
   mode Cadre commits on the host after each step. The worktree's `.git` file is in the writable
   workspace; a check could repoint it or make it a directory with `core.fsmonitor`/a hook, and the
   next commit would run that outside any container, as the owner. Fixed: Cadre resolves the git
   dir from the owner's repo, uses `--git-dir`/`--work-tree`, restores the pointer around every git
   command and after each check, and fails a check that changed it. `tests/test_project.py` proves
   it with a control showing plain git *does* run the planted command.

Tests: **236 passed, 9 skipped** locally on Windows (the 7 containment tests skip without
`CADRE_CONTAINMENT`, and one planted-`.git` case skips where this git honoured neither
`core.fsmonitor` nor the hook), 7 of 7 containment tests under each runtime in CI; the extension's
72 unit tests pass; `ruff` clean.

## 1.0.1 release (2026-09-19)

Tag `v1.0.1` on 18e4aec (`git log v1.0.0..` = the Action's PR title and `Closes #N`, the dashboard
fixes, and doc commits). Release run 35443644003 green on the first attempt: TestPyPI, PyPI
(upload 12:43:44Z), GHCR, three standalone builds, GitHub Release. PyPI's project JSON lagged
behind its per-version JSON for a few minutes, so the first `uvx --from cadre-ai@1.0.1` said "no
version"; the simple index listed it at 12:47:50Z. Then, from a clean uv cache and a new
`CADRE_HOME`: `cadre 1.0.1`, and a `decision-board --demo` run succeeded. SHA-256 on PyPI =
TestPyPI = GitHub Release: `7d3fc653…` wheel (147,054 bytes), `32d0de29…` sdist. GHCR `1.0.1`,
`1.0` and `latest` all resolve anonymously to `sha256:d1eb4207…`. `v1` moved from c0073ba to
18e4aec (pushed with `--force` for `v1` only). Proof: labelled issue #6 on `cadre-action-demo` →
run 35443948799 downloaded `Daemon-VI/cadre@v1` at 18e4aec → PR #7, title `Cadre: Add __version__
= "0.1.0" to textstats/__init__.py` (one line; PR #5's title had carried the issue body's newline),
body ending `Closes #6`, all 7 tests passing. The GitHub Marketplace listing showed v1.0.1 as
Latest without any action from Rithik.

## 1.0.0 release (2026-09-19, `PROMPT_RELEASE.md`)

Tag `v1.0.0` on c0073ba (CI green on that commit: 9 jobs, plus the docs-site build and deploy). Release run 35436705812, attempt 1:
wheel/sdist, three standalone builds, container image and GitHub Release **succeeded**; TestPyPI
failed with `invalid-publisher` (the OIDC claims were exactly `Daemon-VI/cadre`, `release.yml`,
environment `testpypi`), so PyPI was skipped. Nothing had been uploaded, so the failed jobs were
re-run on the same tag after Rithik fixed each site's pending publisher: TestPyPI failed on attempts
1–3 and passed on attempt 4 (11:21Z); PyPI failed on attempt 4 and passed on attempt 5 (upload
11:34:36Z). The run is now green end to end; no version was burned.

### Published channels

| Channel | Version | Where | How it was verified |
|---|---|---|---|
| GitHub Release | 1.0.1 | github.com/Daemon-VI/cadre/releases/tag/v1.0.1 | 1.0.1: 5 assets, the wheel and sdist hashes match PyPI (see "1.0.1 release"); the builds were not re-run by hand. 1.0.0: 5 assets (wheel, sdist, Windows/macOS-arm64/Linux builds). The Windows zip, downloaded with `gh release download` into a clean folder: `cadre 1.0.0`, and `run decision-board … --demo` succeeded |
| GHCR | 1.0.1, 1.0, latest (and 1.0.0) | ghcr.io/daemon-vi/cadre | 1.0.1: anonymous manifest 200 for `1.0.1`, `1.0` and `latest`, all `sha256:d1eb4207…`. 1.0.0: anonymous pull token → manifest 200 for `1.0.0` and `latest` (so the package is public). Not run on this laptop: Docker Desktop was stopped and 0.9 GB RAM was free. The release job's smoke test ran it as uid 10001, `--version`, and a demo run that succeeded |
| MCP from PyPI | 1.0.0 | `uvx --from "cadre-ai[mcp]" cadre mcp` | **Claude Code 2.1.278**: `claude mcp add --scope project` in a scratch fixture, then `claude -p --mcp-config .mcp.json`: 5 tools; `cadre_list_orgs` → 5 templates; `cadre_forecast` → `cannot_run` (scratch home, no key), "no history, estimated from template size". **VS Code 1.138** (isolated instance, `.vscode/mcp.json`, *MCP: List Servers → Start Server*): uv installed 50 packages, log "Discovered 5 tools"; no agent-mode call (needs a Copilot sign-in). **Antigravity**: not found on this laptop |
| GitHub Action | `v1` → 18e4aec (1.0.1) | `uses: Daemon-VI/cadre@v1` | since 1.0.1: issue #6 → PR #7, one-line title, body ends `Closes #6`. Before: `cadre-action-demo` switched to `@v1`; issue #4 (labelled) → run `20260919-101549-2299e0` succeeded → PR #5 (+1 line, a docstring; 19 calls, 33,835 + 1,554 tokens; reviewer used Qwen and gpt-oss) |
| TestPyPI | 1.0.1 | test.pypi.org/project/cadre-ai | 1.0.1: first attempt, same SHA-256 as PyPI. 1.0.0: fourth attempt at the job, after Rithik corrected the pending publisher (the first three: `invalid-publisher`). Both files' SHA-256 match the GitHub Release (`6c7f593f…` wheel, `fcbb4de9…` sdist) |
| **PyPI** | **1.0.1** | pypi.org/project/cadre-ai | 1.0.1: first attempt; clean-cache `uvx --from cadre-ai@1.0.1` → `cadre 1.0.1`, demo run succeeded. 1.0.0: second attempt, after Rithik corrected the pypi.org publisher (the first: `invalid-publisher` for environment `pypi`; nothing uploaded). Same SHA-256 as above. From a clean uv cache and a new `CADRE_HOME`: `uvx --from cadre-ai cadre --version` → `cadre 1.0.0`, a `decision-board --demo` run succeeded, and `uvx cadre-ai --version` works too. `pipx` is not installed here, so `pipx install` was not tried |
| VS Code Marketplace / Open VSX | — | — | not published: `VSCE_PAT` and `OVSX_PAT` are not set (`gh secret list` empty on 2026-09-19, and no `vscode-marketplace` environment) |
| GitHub Marketplace | v1.0.1 | github.com/marketplace/actions/cadre-finish-this-project | listed by Rithik on the 1.0.0 release page (2026-09-19); on 2026-09-19 the page showed "v1.0.1 Latest" with no further action |

### Seen on screen for the first time (2026-09-19)

**Dashboard**, driven by headless Edge over the DevTools protocol (the Chrome extension was not
connected) at 1400 px, 390 px and in dark mode, against the real `~/.cadre`. Zero console errors.
Fixed (75bbbd3): a run page printed `nullnullnull` under the goal (DOM `replaceChildren` turns a
null child into text; every call now goes through `put()`, pinned by a static test); usage meters
shrank on rows with "near cap"; start times wrapped "pm"; phone width squeezed goals to a word per
line; `artifact.written` printed raw JSON with the absolute path; the provider heading read
"Google AI Studio (Gemini) (gemini)".

**VS Code extension**, in a separate VS Code 1.138 instance (its own user-data and extensions
folders in the scratchpad; Rithik's own VS Code untouched), on a fixture copy of the demo repo,
against a scratch `CADRE_HOME` with no keys so runs used the offline demo. Forecast (an honest
"cannot run: no usable model"), Start run on this folder → "Run demo", the live run view, and
Review branch (3 files, diff opens base ↔ branch) all work. **Found a real hazard:** the exec
approval modal, opened by the 5 s poll, took keyboard focus with "Allow execution" as its default
button; two demo runs were approved about 4 s after asking by keys pressed in another window
(the extension's log: `[approval] exec … approved`), and a third, left alone, stayed pending.
Fixed (b2c0bae): a poll shows a notification (Review… / Open run) that never takes focus; the
modal opens only from Review…, Decide… or Review pending approvals. Re-checked on screen: the
notification appeared, the status bar read "Cadre · 2 approvals", and both stayed pending.
Also noted: after a Reject, the engine asks again on the agent's next `run_check`.

**Action** (PR #5): the title took the first 60 bytes of the goal, newline included. Fixed
(75bbbd3): the goal's first line, cut at a word; the PR body ends with `Closes #N`. These reach
`@v1` users only with a 1.0.1 and a `v1` move (Rithik's call).

### v0.1.0 (2026-09-16) — verified offline

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
- **Keys** — `CADRE_KEY_<ID>`, else the preset's variable, else the OS credential store (`keyring`).
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
- v0.1.0 (2026-09-16): ~5,000 lines of Python in `src/`, ~740 lines of dashboard JS/CSS, ~1,170
  lines of tests; 96 passed, 1 skipped, 8.1 s.
- 2026-09-18: 6,985 lines of Python in `src/cadre`, 842 lines in `web/`, 2,522 lines of tests;
  170 passed, 1 skipped (directory symlink needs Windows Developer Mode); `ruff` clean.

### Known limitations (current)
- The demo provider is plumbing, not intelligence — it says `[demo]`.
- **No run has parked live**; park/resume is verified with a fake clock only (M10, M11).
- Review *quality* is not assessed — only routing and independence are measured.
- Active time lost on interruption: a killed process never adds its segment to `active_seconds`
  (capstone 1's first 485 s are missing), so `max_minutes` undercounts interrupted runs.
- Forecast history is per org, not per project size (capstone 2 was 2.3× over a measured n = 1
  taken on a 300-token fixture).
- The dashboard was first seen on screen on 2026-09-19, in headless Edge (see "1.0.0 release"); a
  person has not yet used it in a normal browser session.
- Gemini, Mistral, Z.ai limits are conservative guesses or third-party reports (`presets.py`
  marks each); the header learning corrects them only where a provider sends headers.
- Checks run as the owner unless the org gives them `runner: docker|podman` (M12, ADR-031); a
  container still shares the kernel and can change the workspace. Use `--allow-exec` only for
  goals you trust.
- Multi-user identity landed in M13 phase 1: users, roles (viewer/member/admin), hashed per-user
  tokens, RBAC and an audit log. Still single-tenant in effect until phase 2 adds teams, per-team
  budgets and model allowances, approval routing, and OIDC SSO.
- Budgets are cumulative across resumes and parks (M10); `resume --add-calls` raises them.
- Starlette warns that its TestClient's `httpx` backend is deprecated; harmless for now.

## Distribution programme D0–D7 (started 2026-09-18, `PROMPT_DISTRIBUTION.md`)

Decided with Rithik on 2026-09-18:
- **Licence and name:** Apache-2.0, PyPI name `cadre-ai` (the command stays `cadre`).
- **Visibility:** the repo goes public after the D0 gate.
- **Session prompts and handoff docs:** kept public, with local paths generalised.
- **Desktop app (D6):** skipped for now, recorded in the ROADMAP.
- **Real editors:** he allowed a Claude Code MCP registration in a fixture folder and a `.vsix` install for verification.
- **Action demo:** a public `Daemon-VI/cadre-action-demo` for the Action test once Cadre is public.

**CI first ran on 2026-09-19**, once Rithik added the `workflow` scope to his `gh` login. Two fixes
were needed before it went green. `astral-sh/setup-uv` no longer publishes a floating `v10` tag,
so every workflow now pins `v10.1.0`. The full-history gitleaks scan flagged the made-up
`"sk-secret-…"` value in `test_errors_never_contain_the_key`, a test that checks provider errors
never echo a key. That fingerprint is now allowlisted, with the reason, in `.gitleaksignore`.

| Workflow (run id) | Observed 2026-09-19 |
|---|---|
| CI (35419971387, manual run, full history) | 9/9 jobs green: **198 passed** on ubuntu, windows and macOS × py3.12 and 3.13; ruff clean; `systemd-analyze verify` accepts the timer and service; `plutil -lint` says the plist is OK; licence check passes; history check passes; gitleaks: "28 commits scanned, no leaks found"; wheel demo run succeeded on ubuntu and windows |
| Release (35419848195, manual run, publishes nothing) | wheel and sdist plus the wheel smoke test; PyInstaller builds with a smoke test on windows, macOS and ubuntu; container image: uid 10001, `--version`, demo run succeeded. The testpypi, pypi and github-release jobs and the GHCR push were skipped, because they only run on a `v*` tag |
| vscode (35419737533) | typecheck, lint, unit tests and `.vsix` on ubuntu and windows; the Electron integration test under xvfb on ubuntu |
| Docs site (35419846409) | build and link check green; deploy skipped while the repo is private |

**Public since 2026-09-19** (Rithik's yes). Pages is live at https://daemon-vi.github.io/cadre/:
a crawl fetched 10 pages and 14 internal URLs, none broken. The deploy is skipped while the repo is
private. Private vulnerability reporting is on. **The site has not been looked at in a browser**:
the Chrome extension was not connected.

### D3 on a real repository (2026-09-19)

`Daemon-VI/cadre-action-demo` is public and was created on Rithik's yes. It holds `textstats`, a
package where `word_count` works and `top_words` and `reading_time` are stubs, so 5 of 7 unittest
tests fail. `.cadre/checks.yaml` runs the tests. A reference implementation passed all 7 before
publishing. The repository has the `cadre` label and "Allow GitHub Actions to create pull
requests" on. Rithik set both secrets himself with `gh secret set`. The workflow pinned a Cadre
commit until 1.0.0; since then it uses `Daemon-VI/cadre@v1`.

| Run | Trigger | Outcome | What it exposed |
|---|---|---|---|
| 1 (`20260919-051254-c887fb`) | issue #1 labelled `cadre` | forecast comment posted; `unapproved` with 0 commits; the pull-request step then **failed** with "No commits between main and cadre/…" | the Action opened a PR for an empty branch, and its comment said "see the pull request". The reason for the failure was lost with the runner |
| — | `/cadre …` comment sent from Git Bash | **skipped**, correctly | MSYS path conversion rewrote the body to `C:/Program Files/Git/cadre …`, so the trigger guard rejected it (`MSYS_NO_PATHCONV=1`) |
| 2 (`20260919-052024-b28d53`) | `/cadre …` comment | `unapproved`, 0 commits, no PR, report and usage on the issue: 23 calls, 50,112 + 3,433 tokens, ~12 min | (a) the `GEMINI_API_KEY` secret was invalid, and Google's 400 bad-key reply was retried on five models on every call, leaving everything on Groq's 8,000 TPM (each engineer call waited ~58 s); (b) `edit_file` failed on two identical `raise NotImplementedError` lines ("matches 2 times" / "0 times") until the engineer ran out of turns; (c) **the engineer said "Implemented…" but the `tests` check failed, and the gate held** |
| 3 (`20260919-061803-9643b6`) | `/cadre …` comment | **succeeded → [PR #2](https://github.com/Daemon-VI/cadre-action-demo/pull/2)**, 1 commit, +19/−2 in `textstats/core.py`: 18 calls, 31,067 + 3,155 tokens, ~7 min | with the fixes in place, one edit missed and the error pointed at the right line, and the next two edits landed. The engineer's own `run_check` and the engine's gate both passed. **All 7 tests passed when re-run by hand on the PR branch.** The commit is attributed to Daemon-VI. The Gemini key was skipped before the run ("key rejected") |
| 4 (`20260919-064319-530a99`) | `/cadre …` comment, after the Gemini secret was fixed | **succeeded → [PR #3](https://github.com/Daemon-VI/cadre-action-demo/pull/3)**, +13/−2: 18 calls, 40,354 + 2,582 tokens, **~75 s** | both keys passed the pre-run check. The engineer used gemini-3.6/3.5-flash, the lead used gemini-3.8-flash and gpt-oss-120b, and the **reviewer used gpt-oss-120b, a different family from the engineer, so the review was independent**. The first check failed, one more edit fixed it, and the check passed. **All 7 tests passed when re-run by hand on the PR branch.** Spreading the load over two providers took the run from ~7 min to ~75 s |

Fixes (b1641fd, a6226ff, f9aace4; 205 tests):
- No PR for an empty branch; the comment then carries the report.
- The timeline goes to the log, and REPORT/plan to an artifact.
- Google's 400 bad-key reply is `AuthFailed`, in chat and in `health()`.
- `add-from-env --test` skips rejected keys, and the Action uses it.
- `edit_file` lists the lines of repeated matches and takes `line`.
- The release trigger narrowed to full versions.

The Gemini secret was malformed at first: 63 characters with whitespace, as found by a
metadata-only shape check on a throwaway branch, since deleted. That made run 3's review only
partly independent. Rithik saved the key to a file, and it was piped into `gh secret set` without
being displayed. It is Google's newer format (`AQ.` + 50 characters, HTTP 200 on `/models`), which
`tools/check_history.py` now recognises as well (cd19220).

**Still open:**
- ~~Both PRs open~~: on Rithik's yes, #3 was merged and #2 closed pointing to it (2026-09-19).
- The lead's report says "`pytest`… all tests pass"; the check actually ran `unittest`. This is
  model wording, and the gate result itself is accurate.
- A runner starts with an empty usage ledger, so "left today" is always the full free limit.
- ~~Not on the Marketplace~~: listed since 1.0.0.

History scan before going public: the only non-generic personal string left in old commits is
the author's local Windows home path (his first name) in four commits made before the paths were generalised (1c4e843 removed them).
No other account's name, no email address, and no key appears anywhere.

| Step | State | Evidence observed 2026-09-18 |
|---|---|---|
| **D0** open source | built | `LICENSE` (canonical Apache-2.0, sha256 cfc7749b…d30); README rewritten for strangers; `SECURITY.md`, `CONTRIBUTING.md`, templates. `/api/v1` pinned by an OpenAPI snapshot (24 paths, then 24 + `tail`); `serve --allowed-host`; scheduler on Linux (systemd) and macOS (launchd), generated by tested code. `tools/check_licences.py`: 31 runtime deps pass, and 52 with `[mcp]`; certifi is MPL-2.0, allowed by name (ADR-030). `tools/check_history.py`: every commit is the owner identity, no private user name, no key shapes. **CI green on 2026-09-19** (table above) |
| **D1** packages | **published 1.0.0** (2026-09-19; see "Published channels") | Published wheel `cadre_ai-1.0.0-py3-none-any.whl`, 146,808 bytes (earlier, on 2026-09-18, `tools/wheel_smoke.py` checked the 0.1.0 wheel in a clean venv: `cadre` and `cadre-ai` both work, and the demo run succeeded). `release.yml`: TestPyPI → PyPI by trusted publishing, three-OS PyInstaller builds with a smoke test, and a GHCR image smoke-tested for uid 10001 and a demo run |
| **D2** MCP | **verified from PyPI** in Claude Code 2.1.278 and VS Code 1.138's MCP client (2026-09-19; see "Published channels"). Before that: | Six MCP tests. Real stdio (`tools/mcp_smoke.py`): five tools, auto-started server, token in no result. **Claude Code 2.1.276** called `cadre_list_orgs`, `cadre_forecast` and `cadre_usage` from a fixture repo. **Found:** on Windows, the SDK client and Claude Code put stdio servers in a kill-on-close job object, so an auto-started `cadre serve` dies with the session. Breakaway is refused, and escaping via WMI was rejected as evasion-like. The start-run result now says so and points to `cadre resume` (ADR-027) |
| **D3** Action | **verified 2026-09-19** | See "D3 on a real repository" below. Built: `action.yml` (composite; engine from the action's own source), `examples/github-action/cadre.yml` (OWNER, MEMBER or COLLABORATOR only; contents, pull-requests and issues write), `provider add-from-env`, `run --result-json`; test of the PR body, parked comment and trigger rules. Real runs: PRs #2, #3 and (through `@v1`) #5 on `cadre-action-demo` |
| **D4** VS Code extension | **seen on screen 2026-09-19**, not published | `editors/vscode`: no runtime dependencies. `tsc` and `eslint` clean (eslint bans innerHTML and similar), 72 of 72 unit tests (70 on 2026-09-18), `.vsix` 28.54 KB. **The integration suite passed 4 of 4 inside the installed VS Code** (isolated profile, via `CADRE_VSCODE_EXE`). The `.vsix` installed into his VS Code as `daemon-vi.cadre@0.1.0` and was uninstalled again. The agent's live API smoke test: demo run streamed, dirty tree refused, 7 exec approvals rejected, review-branch diff listed 3 files. On screen on 2026-09-19 (see "1.0.0 release"): Forecast, Start run, the live run view, the approval notification and Review branch's diff. The Runs tree itself was not looked at. Not published |
| **D5** docs site | **live** | `site/`: nine pages; `build.py` generates them with markdown-it and no framework, pulling the M5/M11 tables from this file at build time. 186 internal links resolve; all pages returned 200 locally; 137,850 bytes. The replay is run `20260917-230536-aa0587` (54 events, 14 calls, 145.76 s) and the scrub check is clean. **Live since 2026-09-19** at daemon-vi.github.io/cadre (every page returns 200) |
| **D6** desktop | skipped | Rithik's decision, 2026-09-18 |
| **D7** hosted | deferred | Stays behind M12 and M13 (ROADMAP) |

Fixes made while building the distribution:
- `exec` approvals now show the command as it will run (`{python}` resolved).
- The run list carries the branch.
- A standalone build resolves `{python}` from PATH, and the scheduler job calls `scheduled-run`.
- `events?tail=N`.

## v1.0 programme (started 2026-09-17)

**Headline (2026-09-18):** requirements, design and plan (P1–P3), M6–M10 built offline, M5 live
verification on Groq + Google AI Studio (ten live-found defects fixed), M11 capstone (both runs
succeeded). **170 passed, 1 skipped**, ruff clean, pushed to the private `Daemon-VI/cadre` (2026-09-18). `git grep` for key
prefixes finds only five fake fixtures in `tests/` and the prefix names in two prompt docs.

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
| 1. Build a unit-converter CLI (`…231009`) | software-team `--allow-exec` | **succeeded** after one interruption | interrupted when the session ended (485 s, 11 calls); resumed 18 Sep, 72 s active — the first segment is not in `active_seconds` | 35 | 110,073 + 8,443 | 11 / 5 / 0 | 0 | 0 of 9 | measured n=1: 27 calls, 94.0k → 35, 118.5k (1.26×) |
| 2. Finish `m11-inventory` (`…152845`) | project-finisher `--allow-exec --project` | **succeeded** | 179 s | 17 | 35,666 + 2,641 | 0 / 4 / 0 | 0 | 0 of 3 | measured n=1 (from the 300-token m5 fixture): 14, 16.8k → 17, 38.3k (**2.3×**) |

- **Capstone 1 is a real program.** Goal: *"A command-line unit converter (length, mass,
  temperature) in Python, standard library only, with unittest tests and a README."* Delivered
  `app.py` (149 lines), `test_app.py` (85), `README.md` (48), `SPEC.md`. Re-run by hand: `Ran 9 tests
  … OK`; `app.py 10 km mi` → `6.2137`, `app.py 100 c f` → `212.0000`, `app.py 5 kg lb` → `11.0231`
  (all correct); `--help` works. The engineer used `edit_file` 3× and the reviewer `search` 2×, with no tool
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
| `…220210` | decision-board | succeeded | 125 s | 15 | 14,285 + 7,188 | 526 | 1 | 0 | 13 | 0 | 5 yes / 4 no | CLI output, not recorded as an event: 18.5 calls, ~12.2k → 15, 21.5k (tokens 1.7× over) |
| `…220717` | research-desk (first try) | failed → resumed → succeeded | 171 + 231 s | 40 | 54,554 + 10,535 | — | 0 | 3 + 4 | 24 + 6 | 1 (Groq) | — | — |
| `…222128` | research-desk | succeeded | 277 s | 23 | 71,366 + 9,065 | 509 | 0 | 3 | 6 | 1 (Groq) | 5 / 1 | measured n=1: 40, 65.1k → 23, 80.4k (1.2×) |
| `…222632` | startup-company | **unapproved** (task t4 failed) | 1,249 s | 57 | 186,786 + 23,431 | 953 | 0 | 31 (QA 1,411 s) | 27 | 8 (Gemini daily) | 27 / 1 | 35.5, 47.7k → 57, 210.2k (**4.4×**) |
| `…224959` | software-team `--allow-exec` | succeeded | 466 s | 27 | 86,234 + 7,760 | 751 | 0 | 6 | 11 | 8 (Gemini daily) | 7 / 2 | 23.8, 36.9k → 27, 94.0k (**2.5×**) |
| `…225900` | project-finisher on `m5-fixture` | succeeded | 123 s | 14 | 15,364 + 1,478 | 823 | 0 | 5 | 7 | 4 (Gemini daily) | 2 / 1 | 60.5, 281k → 14, 16.8k (median 71k: **4.2× over**) |
| `…230536` | decision-board via API stream | succeeded | 146 s | 14 | 13,186 + 7,920 | — | 0 | 4 | 2 | 1 (Gemini) | 3 / 6 | measured n=1: 21.5k → 21.1k |

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
   (as measured on 2026-09-17; later M5 changes moved them 1–3.5% higher, still within 2×)
   decision-board 12,231 → 17,470 (actual 21,473); research-desk 22,399 → 71,262 (80,431);
   startup-company 32,168 → 121,678 (210,217); software-team 25,513 → 86,992 (93,994);
   project-finisher on the fixture 71,120 → 18,688 (16,842). All now within 2×.
10. **`cadre provider add` hung on the hidden prompt when no terminal could answer** — it now
    fails at once and names where it looked (found while the key was missing).

Also observed live: **header learning works** — `GET /api/quota` showed Groq's
`x-ratelimit-remaining-tokens` 4,689 (reset 9.9 s) and remaining requests 971 (reset 2,490 s);
Groq itself sent at most one 429 per run (2 in all, both on Qwen), which cooled the model for its
`retry-after`; the 4–8 per run in the later runs were Gemini daily-quota 429s.
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

### M5 — blocked on a permission (2026-09-17) — superseded
Kept as history: the first attempt stalled on the session's safety classifier; Rithik then allowed
`Bash(uv run cadre:*)` and added fresh keys at the hidden prompt, and M5 ran (above).

## Definition of done for v1.0 (checked 2026-09-18)
| Item | State | Evidence |
|---|---|---|
| Every FR has a passing test; traceability complete; ruff clean | met | TEST_PLAN v0.1 + v1.0 tables; 170 passed, 1 skipped on 2026-09-18 (208 at release); `ruff check` clean |
| Every template ran at least once on live free keys, numbers recorded | met | M5 table (five templates) |
| Capstone results with raw numbers | met | M11 table (both succeeded) |
| No key value in the repo, the database, `~/.cadre` | met | `git grep` finds five fake fixtures and two prefix-name docs; a content scan of `~/.cadre` (incl. `cadre.sqlite*`) for full-length Groq/Google key shapes found none; planted-key tests pass |
| **No key value in the transcript** | met 2026-09-19 | On 2026-09-17 Rithik pasted his Groq and Gemini keys into the chat. On 2026-09-19 he rotated both at the providers, so the pasted values are dead. A new key replaces a stored one with `uv run cadre provider key groq` / `gemini`; `provider add` keeps a key it finds, and now says so |
| Server RSS measured while a run streams | met | 64.5 MB idle, 72.9 MB peak while a live run streamed 55 events through the dashboard's stream endpoint (curl as the client; the dashboard itself has not been seen in a browser) |
| Docs match the code (`claim-auditor`) | met | audit 2026-09-18: every live number matched the store; its stale/unsupported items were corrected in the same commit |

Every item is met, and 1.0.0 was tagged and released on 2026-09-19 (see "1.0.0 release").

## Where to pick up
1. **Next: `ROADMAP.md` M13 phase 2** — teams, per-team token/run budgets and model allowances,
   approvals routed to a role/team, and OIDC SSO. Build on `accounts.py` (phase 1). A 1.2.0 release
   carrying M13 phase 1 (the accounts layer) needs Rithik's yes; it is a new feature, not a fix.
2. Waiting on Rithik, each his call:
   - **Release 1.2.0** to ship the accounts layer (M13 phase 1), or hold it until phase 2 is done.
   - **Publish the security advisory** for the MCP `allow_exec` bypass. Draft text is in
     `docs/SECURITY_ADVISORY_DRAFT.md`: affected 1.0.0/1.0.1, fixed 1.1.0. Create it under the
     repo's Security → Advisories (GHSA); publishing is his act.
   - **VS Code extension publish**: `gh secret list` shows no secrets, and the `vscode-marketplace`
     environment the publish job uses does not exist yet. He creates the publisher `daemon-vi` on
     the VS Code Marketplace, both tokens, and runs `gh secret set VSCE_PAT` / `gh secret set
     OVSX_PAT` himself. Then set the extension to 1.1.0, and a `vscode-v1.1.0` tag publishes both.
     The Open VSX namespace `daemon-vi` may need creating once (`npx ovsx create-namespace` from a
     workflow step, never with the token on a command line).
   - **Agent-mode MCP call in VS Code** (needs his Copilot sign-in), and **Antigravity**, which he
     said he has but which was not found on this laptop.
   - Demo PRs #5 and #7 on `cadre-action-demo` are open; merging or closing them is his call.
   - Consider a short GitHub security advisory for 1.0.0/1.0.1 (the MCP `allow_exec` bypass), now
     that 1.1.0 fixes it.
3. Known and not yet fixed: after a Reject, the engine asks for exec approval again on the agent's
   next `run_check`. A runner's usage ledger starts empty, so the Action's "left today" is always
   the full free limit. Containment is proven on Linux only (see "M12").
4. Done, for the record: keys rotated (a new key goes in with `provider key <id>`, not `provider
   add`); the scheduler job is installed (every 30 min; remove with `cadre scheduler uninstall`);
   the repo is public with Pages and private vulnerability reporting; the D3 real test; the 1.0.0,
   1.0.1 and 1.1.0 releases; M12; `v1` moved to 1.1.0 (proven on the demo repo, PR #9); M13 phase 1
   (the accounts layer) on `main`.

## Environment
`cd cadre`, `uv sync`, `uv run pytest -q`. State in `~/.cadre` (`CADRE_HOME`
overrides). Git: branch `main`, remote `origin` = public `Daemon-VI/cadre` (commit, then push).
