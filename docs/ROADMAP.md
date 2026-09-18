# Cadre — Roadmap

_2026-09-17 · v0.1 → v1.0 programme (`MASTER_PROMPT.md`). Status with evidence is in
`PROJECT_STATE.md`; decisions are in `ARCHITECTURE.md` (ADR numbers below point there)._

## Order, and why this order

A team of agents on free keys fails by **rate limit** before it fails for any other reason, and on
2026-09-17 Rithik sharpened the goal: *build or finish a project on free keys only, across every
provider, waiting for resets when a day runs out.* The order follows from what each step needs:

- **Measurement comes first (M5).** The forecast (M7) and the keep-or-drop decisions on new tools
  (M8) both need real numbers from real models.
- **The catalogue comes before the forecast (M6 → M7).** A forecast is only as good as the limits
  and day clocks it divides by.
- **Editing tools come before project mode (M8 → M9).** Rewriting whole files would make work on
  an existing repository unaffordable at 8,000 tokens a minute.
- **Multi-day runs come last (M10).** Only large jobs exercise them, and they reuse everything above.

| # | Milestone | Closes | Status |
|---|---|---|---|
| **M0** | Requirements and design — `SRS.md`, `ARCHITECTURE.md` ADR-001…015 | — | done 2026-09-16 |
| **M1** | Providers, quota, router | — | done 2026-09-16 |
| **M2** | Org model, workspace, tools, store | — | done 2026-09-16 |
| **M3** | Collaboration patterns, budgets, resume | — | done 2026-09-16 |
| **M4** | CLI, REST API, dashboard, demo mode | — | done 2026-09-16 |
| **P1–P3** | v1.0 requirements (SRS §4.8, FR-8…13), design (ADR-016…022), this plan | — | done 2026-09-17 |
| **M5** | Live verification on real free keys | G1 | done 2026-09-17 (Groq + Gemini; 10 live-found defects fixed) |
| **M6** | Provider catalogue, daily clocks, data policy | G3, G5, G8 · FR-10, FR-12 | done 2026-09-17 (offline; live refresh pending M5) |
| **M7** | Usage ledger and forecast | G6 · FR-11 | done 2026-09-17 (offline; estimates to be checked against M5) |
| **M8** | Code-editing tools and repo map | G7 · FR-13 | done 2026-09-17 (offline) |
| **M9** | Project mode | G2 · FR-8 | done 2026-09-17 (offline, git fixtures) |
| **M10** | Multi-day runs | G4 · FR-9 | done 2026-09-17 (offline, fake clock; scheduler not installed) |
| **M11** | Capstone (build one project, finish another, free keys only) and v1.0 release | — | capstone done 2026-09-18 (both succeeded); release pending the definition-of-done items in PROJECT_STATE |
| M12 | Container runner for checks | — | after v1.0 |
| M13 | Multi-user organisations | — | after v1.0 |
| M14 | Memory across runs | — | after v1.0 |
| M15 | Web research tool | — | after v1.0 |
| M16 | Hosted deployment | — | after v1.0 |

**Deviation, recorded 2026-09-17.** M5 is blocked, not skipped. The Groq key was copied into
Cadre's credential entry, but the session's auto-mode safety classifier refuses every command that
reads it (`cadre provider add`, even writing the provider entry for it). M6, M9 and M10 do not
depend on M5's numbers, so they proceed. M7 and M8 ship their mechanisms now with their
estimate-based fallbacks clearly labelled, and receive measured numbers when M5 runs.

## M5 — live verification (blocked on a permission, not on a key)

To unblock, Rithik either adds an allow rule for `Bash(uv run cadre:*)` via `/permissions`, or
runs the commands himself with a leading `!`:

```
! cd cadre && uv run cadre provider add groq
! cd cadre && uv run cadre run decision-board "Should a two-person student team build a budgeting app or a note-taking app first?" --yes
```

Then, for each template (`software-team --allow-exec`, `decision-board`, `startup-company`,
`research-desk --yes`) record in `PROJECT_STATE.md`: model calls, prompt/completion tokens,
median fixed prompt tokens per call, repair turns, wall time, which model served each role,
every wait, fallback and 429, final status, and whether reviews were independent (Groq's free
key reaches two families: `gpt-oss` and `qwen`). Confirm header learning in `GET /api/quota`,
the JSON tool protocol on one OpenRouter `:free` model without function calling, and server RSS
while a run streams to the dashboard (< 150 MB). A JSON shape a real model keeps breaking gets a
prompt or parser fix plus a regression test built from the real reply, secrets removed.

## M6 — provider catalogue, daily clocks, data policy
Every free chat model a key reaches is its own bucket with sourced limits (Google AI Studio's pricing page
shows ten Gemini chat models plus Gemma 4 on its free tier, updated 2026-09-16); `day_reset` per provider (ADR-018) with
migration of today's UTC counters; `trains_on_free_data` per provider and `privacy: private`
routing (ADR-020); `cadre provider refresh`.

## M7 — usage ledger and forecast
`cadre usage [--days N]` and a dashboard page; `cadre forecast` with the four verdicts
(ADR-019), printed by `cadre run`; `reserve_pct` per provider (default 10).

## M8 — code-editing tools and repo map
`edit_file`, line-range `read_file`, `search`, and the capped repo map; each kept only if the
measured saving beats its schema cost (ADR-021, ADR-022).

## M9 — project mode
`--project PATH [--base B] [--allow-dirty]` on a git worktree and branch `cadre/<run-id>`,
per-step commits, repo checks from the base commit, `cadre runs cleanup`, and a
`project-finisher` template (ADR-016).

## M10 — multi-day runs
Park on daily limits, resume when due from `cadre serve` or `cadre resume --due`, optional
Windows Task Scheduler job (**installed only after asking**), cumulative budgets with `max_days`
and `max_tokens_per_day` (ADR-017).

## M11 — capstone and v1.0
In `~/.cadre/capstone/`: `software-team` builds a small real CLI with tests, and
`project-finisher` makes a half-built fixture repo's failing tests pass, both on free keys only.
Record forecast against actual (calls, tokens, wall time, days, parks), which model filled each
role, independence, check results, repairs and 429s. Then `claim-auditor`, version 1.0.0,
`CHANGELOG.md`, README quick start (add keys → forecast → run → review the branch), local tag
`v1.0.0`.

## After v1.0 (renumbered from v0.1's M6–M10)

- **M12 — container runner for checks.** Checks run model-written code as the owner (ADR-006).
  Optional `runner: docker|podman` per check: workspace mounted, no network, CPU/memory caps.
  Subprocess stays the default because Docker next to a Gradle build does not fit this laptop.
- **M13 — multi-user organisations.** Users and roles, per-team budgets and model allowances,
  approvals routed to a role, an audit log, OIDC SSO. Shared keys stay server-side.
- **M14 — memory across runs.** Per-org knowledge files injected under a hard cap; files before
  a vector database, because every token of memory is replayed on every call.
- **M15 — web research tool.** `fetch_url` / `search` with fetched text treated strictly as
  data, domain allow lists and size caps.
- **M16 — hosted deployment.** Dockerfile, optional Postgres, TLS guidance, M13's auth in front —
  never before M12 and M13.

## Distribution track D0–D7 (v1.0 → v1.1, added 2026-09-18)

`PROMPT_DISTRIBUTION.md`. Separate from the M numbers. Every front end is a thin client of the one
engine (ADR-024). Stops for Rithik: making the repo public, the first publish to each channel,
anything that costs money, installing software, widening his GitHub login, creating tokens, and
the D6 decision.

| # | Step | Closes | Status |
|---|---|---|---|
| **D0** | Open-source readiness: Apache-2.0, README for strangers, community files, CI matrix, cross-platform scheduler, `--allowed-host`, history/privacy scans, `/api/v1` | FR-14, FR-15 | built 2026-09-18; CI written but not yet run (needs the `workflow` scope); public switch waits on the D0 gate |
| **D1** | Package the engine: wheel smoke test, PyPI trusted publishing (`cadre-ai`), PyInstaller builds, GHCR image | FR-16 | built 2026-09-18; wheel smoke passes locally; `release.yml` not yet run (workflow scope); first publish needs Rithik |
| **D2** | MCP server (`cadre mcp`, five tools, no approvals over MCP) | FR-17 | built 2026-09-18; verified over real stdio; host checks pending |
| **D3** | GitHub Action (project mode on the checkout → PR; trusted triggers only) | FR-18 | built 2026-09-18 (`action.yml`, example workflow); the real test on a demo repo needs the workflow scope, the repo made public, Rithik's yes for `Daemon-VI/cadre-action-demo` and his secrets |
| **D4** | VS Code extension, published to the Marketplace and Open VSX | FR-19 | built 2026-09-18; 70 unit tests + 4 integration tests in the installed VS Code; `.vsix` installs; UI not observed on screen; not published |
| **D5** | Docs site on GitHub Pages | FR-20 | built 2026-09-18 (nine pages, links checked, real-run replay); Pages not enabled, needs the repo public |
| **D6** | Desktop app (Tauri 2 + PyInstaller sidecar) — only on Rithik's yes | FR-21 | **skipped** 2026-09-18 (Rithik): the dashboard, the extension and MCP cover it; unsigned installers would warn |
| **D7** | Hosted website — **not in this programme**: a public server would run strangers' model-written code and hold their keys, so it stays behind M12 (sandboxed checks) and M13 (user accounts) | — | deferred |

## Deferred, and why

- **LangChain / LiteLLM / vendor SDKs** — the OpenAI-compatible shape covers every free provider,
  and owning the retry path is the point (ADR-001, ADR-002).
- **Several keys, accounts or projects at one provider** — multiplying a free limit that way
  breaks the providers' terms (Google applies Gemini limits per project). Capacity grows by adding
  *different* providers.
- **Anything that needs a card** — Vertex AI, Cerebras's trial (card required since July 2026),
  OpenRouter credit, DeepSeek's own API (paid; no DeepSeek `:free` model on OpenRouter on
  2026-09-17). Paid presets stay opt-in and labelled.
- **Fine-tuning** — "fine tune" in the request meant *refine the idea*; free tiers do not offer it.
- **A vector database** — not before M14, and retrieved context costs the same tokens.
- **Local models as the engine** — this laptop runs a 1.5B q4 at ~3.4 tok/s.
- **Unified-diff editing** — exact-match replacement fails loudly instead of misapplying (ADR-021).
