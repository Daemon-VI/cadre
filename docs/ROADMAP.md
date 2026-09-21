# Cadre — Roadmap

_2026-09-17 · v0.1 → v1.0 programme (`MASTER_PROMPT.md`). Status with evidence is in
`PROJECT_STATE.md`; decisions are in `ARCHITECTURE.md` (ADR numbers below point there)._

> **v1 is feature-complete and the project is in maintenance (2026-09-20, 1.3.0).** M5–M14 are done
> and released, and the distribution track **D0–D5 is now fully released** — D4, the VS Code
> extension, went to the Marketplace and Open VSX as `daemon-vi.cadre-ai` on 2026-09-20. D6 was
> skipped by his call and D7 deferred. **M13.1 (OIDC SSO)**, **M15 (web research tool)**
> and **M16 (hosted deployment)** are designed but **not started**, and M13.1 stays a prerequisite
> of M16 — single sign-on only matters once Cadre is hosted. Nothing below is scheduled; each would
> get its own prompt file if Rithik picks it up.

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
| **M12** | Container runner for checks | FR-22 · ADR-031 | done 2026-09-19; **released in 1.1.0** (containment tested in Linux CI under Docker and Podman; one check run in Docker Desktop on the laptop) |
| **M13** | Multi-user organisations | FR-23 · ADR-032/033/034 | **done 2026-09-19** except OIDC: users, roles, hashed tokens, RBAC, audit (phase 1); teams, per-team budgets and model allowances, action routing (phase 2). OIDC SSO deferred to M13.1, a prerequisite of M16 |
| **M14** | Memory across runs | FR-24 · ADR-036 | **done 2026-09-19; released in 1.3.0 on 2026-09-20** (files, deterministic selection under a cap, `memory` approval, retrospective, privacy, ledger + forecast). Live n=1 showed no benefit on a task the model already got right — recorded plainly |
| M15 | Web research tool | — | designed, **not started** |
| M16 | Hosted deployment | — | designed, **not started** (needs M13.1 OIDC first) |

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

- **M12 — container runner for checks. Done 2026-09-19** (`PROMPT_M12.md`, FR-22, ADR-031).
  Optional `runner: docker|podman` per check: workspace mounted, no network, CPU/memory caps.
  Subprocess stays the default because Docker next to a Gradle build does not fit this laptop.
  Released in 1.1.0 on 2026-09-19; `v1` moved to 1.1.0 the same day (proven on the demo repo, PR #9).
- **M13 — multi-user organisations. Done 2026-09-19** except OIDC (FR-23, ADR-032/033/034).
  Users and roles, hashed API tokens, RBAC and an audit log (phase 1); teams, per-team budgets
  and model allowances, and action routing (phase 2). Shared keys stay server-side. Unreleased —
  a 1.2.0 carrying it needs Rithik's yes.
- **M13.1 — OIDC SSO.** Deferred: it needs a real identity provider and can't be tested offline.
  Now a prerequisite of **M16 (hosting)** — single sign-on only matters once Cadre is hosted, so
  it is built when M16 is.
- **M14 — memory across runs. Done 2026-09-19** (FR-24, ADR-036). Markdown knowledge files under
  `CADRE_HOME/memory/` at global/team/project scope (project keyed by root commit), injected into
  builders and managers under a hard per-call token cap; deterministic selection (no embeddings);
  human + model-proposed entries behind a `memory` approval that cannot be granted over MCP; every
  entry passes the `check_history` key scan; memory tokens shown in the ledger and forecast. Files
  before a vector database, because every token of memory is replayed on every call. Released in
  1.3.0 on 2026-09-20; `v1` moved to it.
- **M15 — web research tool.** `fetch_url` / `search` with fetched text treated strictly as
  data, domain allow lists and size caps.
- **M16 — hosted deployment.** Dockerfile, optional Postgres, TLS guidance, M13's auth in front
  (including M13.1 OIDC SSO) — never before M12 and M13.

## Distribution track D0–D7 (shipped in 1.0.0; added 2026-09-18)

`PROMPT_DISTRIBUTION.md`. Separate from the M numbers. Every front end is a thin client of the one
engine (ADR-024). Stops for Rithik: making the repo public, the first publish to each channel,
anything that costs money, installing software, widening his GitHub login, creating tokens, and
the D6 decision.

**Decision 2026-09-19: one release, 1.0.0.** Neither 1.0.0 (the engine) nor 1.1.0 (distribution)
had been released, and every distribution feature was already on `main`. So both ship together as
1.0.0 from `main`, and the two unreleased CHANGELOG sections were merged into one
(`docs/PROMPT_RELEASE.md`).

| # | Step | Closes | Status |
|---|---|---|---|
| **D0** | Open-source readiness: Apache-2.0, README for strangers, community files, CI matrix, cross-platform scheduler, `--allowed-host`, history/privacy scans, `/api/v1` | FR-14, FR-15 | built 2026-09-18; CI green on three OSs × two Pythons and the full-history gitleaks scan is clean (2026-09-19); **public since 2026-09-19** |
| **D1** | Package the engine: wheel smoke test, PyPI trusted publishing (`cadre-ai`), PyInstaller builds, GHCR image | FR-16 | **published 1.0.0 on 2026-09-19**: PyPI and TestPyPI (`cadre-ai`, trusted publishing), GHCR (`ghcr.io/daemon-vi/cadre`, public), standalone builds on the GitHub Release; each checked from a clean environment (PROJECT_STATE "Published channels") |
| **D2** | MCP server (`cadre mcp`, six tools since M14, no approvals over MCP) | FR-17 | built 2026-09-18; **from PyPI, 2026-09-19:** tools called in Claude Code 2.1.278, and 5 tools discovered by VS Code 1.138's MCP client (that was before M14 added the sixth, `cadre_memory_list`) (an agent-mode call needs Rithik's Copilot sign-in); Cursor, Windsurf and Antigravity unverified |
| **D3** | GitHub Action (project mode on the checkout → PR; trusted triggers only) | FR-18 | **verified 2026-09-19** on `Daemon-VI/cadre-action-demo`: PR #2 (Groq only, ~7 min) and PR #3 (Gemini + Groq, independent review, ~75 s), all 7 tests pass on both branches; the first two runs found four bugs, all fixed; `v1` tag and Marketplace listing (github.com/marketplace/actions/cadre-finish-this-project) since 1.0.0 |
| **D4** | VS Code extension, published to the Marketplace and Open VSX | FR-19 | built 2026-09-18; 72 unit tests + 4 integration tests; **seen on screen 2026-09-19** (Forecast → Start run → live view → Review branch), which found the exec-approval focus hazard, fixed in b2c0bae; **PUBLISHED 2026-09-20** to both registries as `daemon-vi.cadre-ai` v1.3.0; **1.4.0 (2026-09-21)** adds five panels — New run, Usage, Approvals, Memory, Organisations |
| **D5** | Docs site on GitHub Pages | FR-20 | built 2026-09-18 (nine pages, links checked, real-run replay); **live 2026-09-19** at daemon-vi.github.io/cadre |
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
