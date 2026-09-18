# Changelog

All dates are 2026. Numbers come from `docs/PROJECT_STATE.md`, where each one is traced to a test,
observed output, or a dated source.

## 1.1.0 — unreleased (distribution)

### Added
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
- Standalone builds (PyInstaller) and a container image (`ghcr.io/daemon-vi/cadre`, non-root,
  `CADRE_HOME=/data`), both built and smoke-tested in `release.yml`. There is also a
  `compose.yaml`.

### Changed
- In a standalone build, `{python}` in a check means the first Python on PATH (the build has
  no interpreter of its own), and the scheduler job calls `cadre scheduled-run`.
- README rewritten for people who have never seen Cadre: quick start, measured numbers,
  limitations.

## 1.0.0 — unreleased

Cadre can now **finish an existing project** as well as build a new one, on free keys only, and it
has been run against real free models.

### Added
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

### Changed
- A daily limit parks a run instead of failing it (ADR-017).
- Reviews avoid every model family the builder used, and receive the files it wrote inline.
- One call may wait up to 15 min (`max_total_wait`) for per-minute windows; v0.1 gave up at 270 s.
- Gemini 2.5 models were removed from the preset (404 for new users); DeepSeek is labelled paid.
- Research and startup writers may produce up to 2,500–3,000 output tokens.

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
