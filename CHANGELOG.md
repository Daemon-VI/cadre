# Changelog

All dates are 2026. Numbers come from `docs/PROJECT_STATE.md`, where each one is traced to a test,
observed output, or a dated source.

## Unreleased

### Added — users, roles and API tokens (M13 phase 1, FR-23)
- More than one person can share one Cadre server. Each has a bearer token and a role: **viewer**
  (read only), **member** (read, run, decide approvals) or **admin** (everything, plus managing
  users, tokens and providers, and the audit log). Every write endpoint is capability-checked; a
  role without the capability gets `403`.
- Tokens are stored **hashed** (SHA-256) and shown once when minted; a revoked token, or any token
  of a disabled user, stops working. The existing `CADRE_HOME/token` becomes the bootstrap admin,
  so single-user installs are unchanged.
- `cadre user add|list|role|disable|enable`, `cadre token new|list|revoke`, and `cadre audit`
  manage accounts locally. `GET /api/v1/me`, and admin-only `GET /users` and `GET /audit`.
- An append-only **audit log** records user/token/role changes, run starts (with the owning user)
  and approval decisions. The last enabled admin cannot be disabled or demoted.
- Shared provider keys stay server-side: only an admin manages providers; a member uses them.
### Added — teams, budgets, model allowances and action routing (M13 phase 2, FR-23)
- **Teams** group users. A run belongs to a team (named at start, or the user's single team, else
  personal). `cadre team add|list`, `cadre team member add|remove`.
- **Per-team budgets** checked at run start: runs per UTC day, tokens per UTC day, and concurrent
  runs (`cadre team budget`). A run already going is never interrupted.
- **Per-team model allowances**: limit a team to models matching `provider`, `provider/model` or
  `*` (`cadre team allow`); a run whose allowance matches nothing fails at the start.
- **Action routing**: cancelling/resuming a run and deciding its approvals is limited to an admin,
  the run's owner, or a member of its team (others get 403). `GET /me` lists your teams; admin
  `GET /teams`.
- Still deferred: OIDC SSO (local per-user tokens remain the only sign-in).

## 1.1.0 — 2026-09-19

### Added — a container runner for checks (M12, FR-22)
- A check may say `runner: docker` or `runner: podman` with an `image:` pinned by digest. It then
  runs in a throwaway container. The run's workspace is the only mount (at `/work`); there is no
  network, the root filesystem is read-only with a small `/tmp`, every capability is dropped,
  privilege escalation is off, the user is not root, and processes, memory and CPU are capped
  (`pids_limit`, `memory`, `cpus`; defaults 256, 512m and 1). Only the variables named in the
  check's `env:` pass through, by name, and credential-like names never do. The subprocess runner
  stays the default.
- Cadre never pulls an image: a missing one fails the check with the exact `docker pull` command.
  An unpinned image needs `allow_unpinned: true`, and the approval prompt says so.
- `--allow-container-exec` (API `"allow_exec": "container_only"`) runs container checks without
  asking and still asks before any check that runs as you.
- Where each check runs is shown: the exec approval lists the runtime, image and limits per
  check; the dashboard and the VS Code extension show the runtime and image and "no network"; the
  CLI shows the runtime; MCP's `cadre_run_status` carries the runner and image.
- Tested against real containers under Docker and Podman in Linux CI: a check could not reach
  the network, write outside `/work`, read a key planted in the host environment, fork past its
  pids limit or allocate past its memory cap, and one that outlived its timeout was killed and
  removed. What a container does not protect against is in ADR-031.

### Fixed — security
- MCP's `cadre_start_run` forwarded an `allow_exec` argument, and `allow_exec` skips the exec
  approval. So a model, or a prompt injection in the host's context, could start a run whose
  checks executed model-written code with nobody approving, although ADR-027 said checks would
  still wait for a human. The tool no longer takes the argument, and tests check that nothing sent
  over MCP skips the approval. Affects 1.0.0 and 1.0.1.
- In project mode Cadre runs `git add -A` and `git commit` in the worktree on the host after every
  step. The worktree's `.git` file lives in the workspace, which a check can write, so a check
  could repoint it or replace it with a directory carrying a hostile `core.fsmonitor`/hook and make
  that commit run a command outside any container, as the owner. Cadre's git now resolves the real
  git dir from the owner's repository, passes it with `--git-dir`/`--work-tree`, restores the
  `.git` pointer before its own git and after every check, and fails a check that changed it. Found
  by the claim-auditor while building M12; it never shipped.

## 1.0.1 — 2026-09-19

Fixes found by looking at the dashboard on screen for the first time and by the first run of the
Action through `uses: Daemon-VI/cadre@v1`. Nothing else changed since 1.0.0.

### Fixed — the GitHub Action
- The pull request's title took the first 60 bytes of the goal, newline included. It is now the
  goal's first line (the issue's title), cut at a word.
- The pull request's body ends with `Closes #N` when the run came from an issue, so merging it
  closes the issue.

### Fixed — the dashboard
- A run page printed "nullnullnull" under the goal: absent optional parts are now dropped.
- Usage meters were shorter on rows with a "near cap" badge; the badge has its own slot.
- Run start times wrapped onto a second line, and at phone width goals were squeezed to one word a
  line.
- An artifact event showed raw JSON with the absolute path; it now names the saved file.
- The provider heading read "Google AI Studio (Gemini) (gemini)"; the id is shown as code.

## 1.0.0 — 2026-09-19

The first public release. Cadre can now **finish an existing project** as well as build a new
one, on free keys only, and it has been run against real free models. It installs from PyPI as
`cadre-ai`, and it also ships as a container, standalone downloads, an MCP server and a GitHub
Action. The work planned as 1.0.0 (the engine) and 1.1.0 (distribution) ships together, because
neither had been released.

### Added — the engine
- **Project mode** — `cadre run <org> "<goal>" --project PATH [--base B] [--allow-dirty]` works in a
  git worktree on `cadre/<run-id>`, commits each finished step with the repository's own identity,
  reads checks from `.cadre/checks.yaml` at the base commit, and never touches the owner's working
  tree, current branch or other branches. `cadre runs cleanup` removes finished worktrees and keeps
  their branches. New `project-finisher` template.
- **Multi-day runs** — a run blocked only by daily limits is `parked` until the earliest reset and
  resumed by `cadre serve`, `cadre resume --due`, or an optional Task Scheduler job
  (`cadre scheduler install`, asks first). Budgets are cumulative; `max_days`,
  `max_tokens_per_day`, `resume --add-calls/--add-tokens`.
- **Provider catalogue** — every free chat model a key reaches is its own quota bucket with a dated
  source; each provider has its own daily clock (Gemini: midnight Pacific; Cloudflare, OpenRouter:
  UTC; Groq: rolling 24 h); `cadre provider refresh [--apply]`; Groq's Qwen model gives a second
  family on one free key.
- **Data-policy routing** — `trains_on_free_data` per provider with its source; `--private` /
  `privacy: private` never uses a provider that trains (or may train) on prompts.
- **Usage ledger and forecast** — `cadre usage`, a dashboard *Usage* page, `cadre forecast` with
  four verdicts and a stated basis (measured history, or the org file's size — calibrated on live
  runs, sized by the project when there is one); every run records its forecast.
- **Code-editing tools** — `edit_file`, line-range `read_file`, `search`, and a capped repo map;
  kept on measured savings (a one-line change: 7,056 tokens by rewrite, 134 by edit).
- `reserve_pct` keeps 10% of every daily cap for other programs using the same key.
- `tools/run_metrics.py` for the live measurement tables.

### Added — distribution
- Apache-2.0 licence; the PyPI distribution is `cadre-ai` (the command stays `cadre`, and a
  `cadre-ai` alias makes `uvx cadre-ai …` work).
- `/api/v1`: every endpoint, pinned by an OpenAPI snapshot test. The unversioned `/api/*` stays as
  an alias until 2.0.
- `cadre serve --allowed-host NAME` for Tailscale names and containers (exact names only).
- `cadre scheduler install` on Linux (systemd user timer) and macOS (launchd agent).
- CI: tests on Windows, macOS and Ubuntu with Python 3.12 and 3.13; licence, history and gitleaks
  checks; a wheel smoke test that runs a demo from a clean venv.
- `SECURITY.md`, `CONTRIBUTING.md`, issue and pull-request templates.
- `cadre mcp`: an MCP server over stdio for AI editors, with five tools: forecast, start run, run
  status, usage, list orgs. It starts `cadre serve` when needed and can never grant an approval.
  Optional extra: `uvx --from "cadre-ai[mcp]" cadre mcp`.
- `GET /api/v1/runs/{id}/events?tail=N` returns the latest N events.
- `cadre --version`.
- A GitHub Action (`uses: Daemon-VI/cadre@v1`): label an issue `cadre` or comment `/cadre <goal>`,
  and Cadre works on the checkout and opens a pull request with its report and usage. Only the
  owner, members and collaborators can trigger it (`examples/github-action/cadre.yml`).
- `cadre provider add-from-env` registers every free provider whose key is in the environment,
  and `cadre run --result-json PATH` writes the outcome for scripts.
- A VS Code extension (`editors/vscode`), built as a `.vsix` in CI and published separately with
  its own `vscode-v*` tags: a Runs tree, a live run view,
  today's usage in the status bar, Forecast, Start run on this folder, Review branch, and Add
  provider through the terminal's hidden prompt. `exec` approvals are a notification whose Review…
  opens a modal showing the exact
  command. The token never reaches a webview.
- A docs site (`site/`, GitHub Pages) with a replay of a real run's event log.
- `docs/MCP_HOSTS.md`: config for Claude Code, VS Code, Cursor, Windsurf and Antigravity, each
  checked against the host's docs on 2026-09-18.
- Standalone builds (PyInstaller) and a container image (`ghcr.io/daemon-vi/cadre`, non-root,
  `CADRE_HOME=/data`), both built and smoke-tested in `release.yml`. There is also a
  `compose.yaml`.

### Changed
- A daily limit parks a run instead of failing it (ADR-017).
- Reviews avoid every model family the builder used, and receive the files it wrote inline.
- One call may wait up to 15 min (`max_total_wait`) for per-minute windows; v0.1 gave up at 270 s.
- Gemini 2.5 models were removed from the preset (404 for new users); DeepSeek is labelled paid.
- Research and startup writers may produce up to 2,500–3,000 output tokens.
- `exec` approval prompts show each check's command as it will actually run (`{python}`
  resolved), and `GET /api/v1/runs` rows include the branch, project and resume time.
- In a standalone build, `{python}` in a check means the first Python on PATH (the build has
  no interpreter of its own), and the scheduler job calls `cadre scheduled-run`.
- README rewritten for people who have never seen Cadre: quick start, measured numbers,
  limitations.
- `edit_file` takes an optional `line`. When `old` occurs more than once, the error lists the
  lines where it occurs, and `line` picks one. When `old` matches nothing, the error names the line
  where its first line is.
- `provider add-from-env --test` skips a provider whose key is rejected. The Action uses it.
- `release.yml` fires only on full version tags (`v1.0.0`), never on the Action's `v1`.
- `cadre provider add` keeps a key it already finds, as before, and now says how to replace it:
  `cadre provider key <id>` for the credential store, or the environment variable it came from.

### Fixed — found by the first real GitHub Action runs (2026-09-19)
- Google AI Studio answers a bad key with `400 INVALID_ARGUMENT`, not 401. It was treated as one
  model refusing one request, so every call tried five Gemini models first. It now disables the
  provider for the session, and `provider test` reports the key as rejected.
- The Action tried to open a pull request for a run with no commits and failed. It now skips the
  pull request, and the issue comment carries the report and the usage table.
- The Action prints the run's timeline into the job log and keeps `REPORT.md` and `plan.json` as a
  workflow artifact, so a failed run can be diagnosed after the runner is gone.

### Fixed — found before installing the scheduler
- The scheduled job ran `python.exe` (a console window every 30 minutes); it now runs the
  environment's `pythonw.exe -m cadre.scheduled`, which logs to `~/.cadre/logs/scheduler.log`.
- A run heartbeat only when it emitted an event, so one model call or quota wait longer than
  90 s let another process's `resume --due` mark a live run interrupted; runs now also
  heartbeat every 20 s.

### Fixed — found by running real free models (M5)
- Gemini 3 tool loops failed without `thought_signature`; signatures are replayed to their issuer
  and foreign calls get Google's placeholder; a tool loop stays on its model when it can.
- Gemini 3 thinking truncated strict-JSON replies; Gemini gets `reasoning_effort: low`, and cut-off
  replies are recorded.
- Gemini 503 bursts are met with an escalating rest; a 404 removes a model for the session;
  daily-quota 429s wait for the model's own reset.
- Agents that answered in text instead of writing the named file are nudged, then their answer is
  saved; identical repeated reads are not re-run.
- `cadre provider add` no longer hangs on a hidden prompt without a terminal.
- Found offline: a key typed into a goal was stored before redaction (0.1.0); file writes walked the
  whole tree and the file cap counted a project's existing files (M8).

## 0.1.0 — 2026-09-16

First version, verified offline only.

- One OpenAI-compatible adapter with dated free-tier presets; keys in the OS credential store,
  redacted from everything stored.
- Quota-aware router: RPM/TPM/RPD/TPD windows, rate-limit headers, 429 cooldown, fallback,
  independence routing.
- Organisation as one YAML file; seven step types (agent, sequence, parallel, review loop, council,
  manager, approval); checks gate and reviewers advise; votes are counted by code; plans are
  validated before they run.
- Confined, versioned workspace; named checks with a scrubbed environment and exec approval.
- SQLite persistence with step-level resume and cross-process cancel.
- CLI, token-locked loopback REST API with an event stream, and a dashboard; offline demo mode.
- 96 tests.
