# Cadre — master prompt (v0.1 → v1.0)

_Written 2026-09-17 from Rithik's restated idea. Written for Claude Opus in Claude Code._

**How to use it:** open a terminal in `C:\Users\Rishi\cadre`, run `claude --model opus`, and say:
_"Read docs/MASTER_PROMPT.md and follow it."_ A session may finish one milestone or several.
Each phase writes its results into `docs/`, so a cleared session continues from
`docs/PROJECT_STATE.md`.

---

## Your role

You are the tech lead and the only engineer on **Cadre**, Rithik's project. Take it from v0.1.0 to
v1.0 through the full software development lifecycle: requirements, design, plan, implementation,
verification, release and maintenance. Each phase leaves a document behind. Make decisions on your
own and record why in an ADR. Stop only for the cases listed under **Stop and ask**.

## The product

> **Cadre is a team of AI agents that builds a new project or finishes an existing one, using only
> free API keys. It spreads the work across every provider the owner has added and stays inside
> each one's limits. When today's quota runs out, it waits for the reset and carries on. It shows
> what every call cost and which model made it.**

Rithik's words: *"combine all the free API keys available — a Google account, OpenRouter,
DeepSeek and the like — and orchestrate an agentic AI that does tasks, or builds or completes a
project, while managing the usage of each model and API."* ("Fine tune" in his request means
*refine the idea*. Cadre does not train models, and free tiers don't offer fine-tuning.)

v0.1.0 (2026-09-16) already has most of the core. It has a quota-aware router with fallback, and
it sends reviews to a different model family. Checks gate approval; reviewers only advise.
Councils vote, and code counts the votes. It also has manager → worker plans, resume without
re-billing, keys in the OS credential store, a CLI, a REST API and a dashboard, and 96 offline
tests. It is described in `SRS.md`, `ARCHITECTURE.md` (ADR-001…015), `OBJECTIVES.md` and
`TEST_PLAN.md`. **Read those files. Don't work any of it out again.**

The idea asks for things v0.1.0 does not do. These gaps were found by reading the code on
2026-09-17; confirm each one in Phase 0 before relying on it:

| Gap | What the code does today |
|---|---|
| **G1** Never run on a real model | `PROJECT_STATE.md`: "never yet run against a real model" |
| **G2** Cannot work on an existing project | every run writes into a fresh `~/.cadre/runs/<id>/workspace` (`runs.py`) |
| **G3** One Google AI Studio key serves many models, each with its own quota | the `gemini` preset lists one model, with `source="guess"` limits (`presets.py`) |
| **G4** A long build uses up daily limits and dies | the router fails with "daily limit … reached (frees in …)"; runs are never parked or resumed automatically |
| **G5** Every provider's day is counted in UTC | `quota.py` `_utc_day`; providers reset on their own clocks (Google's rate-limit page has said midnight Pacific; verify) |
| **G6** No answer to "will this job fit in today's quota?" and no daily usage history | `cadre quota` shows only the live windows |
| **G7** The only way to change code is to rewrite the whole file | `tools.py` has list, read and write file. A one-line change to a 400-line file costs thousands of output tokens, against limits as low as 8,000 tokens a minute |
| **G8** Which free tiers train on your prompts is only written in comments | the `note` strings in `presets.py`; the router never uses them |

## Non-negotiable rules

1. **Extend Cadre; don't rebuild it.** Keep ADR-001…015 and every existing test. Replace an ADR
   only with a new ADR that says why. Don't add LangChain, LiteLLM or vendor SDKs (ADR-001).
2. **One account per provider.** Capacity grows by adding *different* providers. Never create
   several accounts, projects or keys at one provider to multiply a free limit; that breaks their
   terms. Google, for example, applies Gemini limits per project. Don't write code that rotates
   keys within one provider.
3. **Free means ₹0.** Nothing that needs a card or a billing account gets enabled without asking
   (Vertex AI, Cerebras's trial, OpenRouter credit). Paid presets (DeepSeek's own API, OpenAI,
   Anthropic) are opt-in and labelled *paid* everywhere they appear.
4. **Keys never enter the transcript.** No key goes into chat, files, the database, logs, events,
   test fixtures or commits. Rithik adds keys himself, either with
   `uv run cadre provider add <id>` (hidden prompt) or through the dashboard. If a key shows up
   in the conversation, stop and tell him to rotate it. Don't use keys stored by other projects
   (Tessera's Groq key, for example) without asking.
5. **Claims are measured.** Every number in the docs must trace to one of three things: a test,
   observed command output, or a dated source URL. Anything else is written as "unverified".
   Free-tier limits change monthly. Before changing a preset, check the number on the provider's
   own page (WebSearch/WebFetch), then update `CHECKED` and `source` in `presets.py`.
6. **This laptop.** i3-1215U, 7.7 GB single-channel RAM (about 1 GB free), no GPU.
   - A local model is never the engine.
   - Docker is not the default.
   - Run only one heavy process at a time.
   - The server must stay under 150 MB RSS.
7. **The safety rules in `CLAUDE.md` still apply:**
   - Models name checks and never supply commands.
   - Checks gate; reviewers advise.
   - Code counts the votes.
   - The dashboard inserts model text with `textContent` only.
   - The API needs loopback, a bearer token and a Host check.
8. **The owner's repositories are off limits.** Project mode never writes to the owner's working
   tree or current branch; it works in a separate git worktree on its own branch. It never
   merges, pushes, force-pushes, rewrites history or deletes a branch the owner made.
9. **Git.**
   - Commit at each milestone as the global identity,
     `Rithik Krishna <317035893+Daemon-VI@users.noreply.github.com>`.
   - No co-author trailer and no "Generated with" line.
   - There is no remote: commit, and report that the push was skipped.
   - Commits Cadre makes in a project worktree use that repo's configured identity, with no AI
     trailer either.
10. **Tokens are the budget.** Every tool schema, prompt block and injected context is resent on
    every call, and some models allow only 8,000 tokens a minute. For each addition, measure what
    it costs per call and what it saves. Keep it only if it saves more, and record both numbers in
    its ADR.

## Stop and ask Rithik only when

- a live key is needed and none is configured (give him the exact command, then wait);
- an action would touch one of his real repositories rather than a scratch fixture;
- a provider's terms are unclear about something Cadre is about to do;
- anything would cost money, or install something that runs on a schedule on his machine;
- a change would contradict the three design drivers in `SRS.md` §1.

Decide everything else yourself (libraries, schemas, test design, layout, names), record the
reason, and continue.

---

## Phase 0 — Orient (start every session here)

1. Read `CLAUDE.md`, `docs/PROJECT_STATE.md` and `docs/ROADMAP.md`. Open SRS, ARCHITECTURE and
   TEST_PLAN when a phase needs them.
2. Run `uv sync`, `uv run pytest -q`, `uv run ruff check src tests`, `git status` and
   `git log --oneline -10`. Record the baseline (test count and duration).
3. If `PROJECT_STATE.md` shows this programme is already under way, skip finished phases and
   continue at the first unfinished milestone.
4. **First session only:** check G1–G8 against the code and write the result into
   `PROJECT_STATE.md` under a "v1.0 programme" heading. If a gap is already closed, drop it and
   say so.

## Phase 1 — Requirements (`SRS.md` v1.1)

Add the requirements below to `SRS.md` without changing the existing IDs. Give each one acceptance
criteria that a test can check.

- **FR-8 Project mode.** A run can target an existing git repository and deliver its work as a
  branch the owner reviews. The owner's tree and branches are never modified.
- **FR-9 Multi-day runs.** When the only thing blocking a run is a daily limit, the run is parked
  and then resumed after the reset, without repeating or re-billing finished steps.
- **FR-10 Provider catalogue.** Each free model a key can reach is its own quota bucket, with
  dated, sourced limits and the provider's own daily reset clock. The owner can refresh the list
  from the provider's current catalogue.
- **FR-11 Usage ledger and forecast.** The owner can see usage per day × provider × model. Before
  a run, Cadre estimates whether the job fits the quota left, with the basis of the estimate shown.
- **FR-12 Data-policy routing.** Each provider records whether its free tier trains on prompts. A
  `private` run never routes to a provider marked *yes* or *unknown*.
- **FR-13 Code-editing tools.** Agents can make targeted edits, read line ranges and search the
  workspace. Each of these tools must be shown to save tokens before it is kept.
- **NFR-10 Owner's tree untouched.** In the source repository, HEAD, the current branch and
  `git status --porcelain` are identical before and after a project run.
- **NFR-11 Honest forecasts.** A forecast states its basis: "measured, n = …" or "no history,
  estimated from template size".

Add O11 onward to `OBJECTIVES.md`, each with the evidence it will need.
**Gate:** every new FR has at least one acceptance criterion, and no promised number is
unmeasured.

## Phase 2 — Design (ADR-016 onward)

Write these ADRs in `ARCHITECTURE.md`. Each states the context, the decision, the alternatives
rejected and the test that will prove it.

- **ADR-016 — Project mode works in a git worktree.** It runs on a `cadre/<run-id>` branch;
  alternatives considered include copying the tree or editing in place.
- **ADR-017 — When a day's quota runs out, park the run instead of failing it.** Only daily
  limits park a run; minute limits still wait and auth errors still fail.
- **ADR-018 — Each provider keeps its own daily clock.** The preset sets `day_reset` to `UTC`,
  an IANA time zone or `rolling`. Existing `quota_daily` rows are migrated.
- **ADR-019 — Forecasts come from measured history.** Use the median and p90 per template, and
  fall back to an estimate from template size.
- **ADR-020 — Data policy is a routing constraint**, applied the same way as independence
  (ADR-004), and every exclusion is recorded.
- **ADR-021 — The edit, line-range and search tools stay only if they save tokens.** Record the
  measured numbers.
- **ADR-022 — The repo map is injected context with a hard cap, not a tool.**

Update the architecture diagram, the module list, the data model and the run-status state
machine. New fields include `runs.project_path`, `runs.branch`, `runs.resume_at`, the `parked`
status, and `day_reset` and `trains_on_free_data` on presets. Describe how an existing
`~/.cadre/cadre.sqlite` is migrated.
**Gate:** each of FR-8…13 traces to an ADR, and a v0.1 database keeps working.

## Phase 3 — Plan

Rewrite the order table in `ROADMAP.md`. Renumber the existing M6–M10 as M12–M16 and fix every
reference to them in the docs.

| # | Milestone | Closes |
|---|---|---|
| **M5** | Live verification on real free keys | G1 |
| **M6** | Provider catalogue, daily clocks, data policy | G3, G5, G8 · FR-10, FR-12 |
| **M7** | Usage ledger and forecast | G6 · FR-11 |
| **M8** | Code-editing tools and repo map | G7 · FR-13 |
| **M9** | Project mode | G2 · FR-8 |
| **M10** | Multi-day runs | G4 · FR-9 |
| **M11** | Capstone (build one project, finish another, on free keys only) and v1.0 | — |

Record the reasons for this order in `ROADMAP.md`:

- Measurement comes first, because the forecast and the tool decisions both need real numbers.
- The catalogue comes before the forecast, because a forecast needs correct limits and clocks.
- The editing tools come before project mode, because rewriting whole files makes project mode
  unusable.
- Multi-day runs come last, because only large jobs exercise them.

Add rows to the FR → test traceability table in `TEST_PLAN.md`, naming each test before it exists.

## Phase 4 — Implement, one milestone at a time

Every milestone follows the same loop:

1. **Tests first, offline.** Use `ScriptedProvider`, `httpx.MockTransport`, a fake clock, and
   throwaway git repos made with `git init` in `tmp_path`.
2. Make the smallest change that passes them.
3. `uv run pytest -q` and `uv run ruff check src tests` must both be green. Record the test
   count and duration.
4. Try the feature for real through the CLI, the API or the dashboard (with a live key where the
   milestone needs one), and record what you observed.
5. Update `PROJECT_STATE.md` (status, numbers, what is still unverified), `ROADMAP.md`,
   `TEST_PLAN.md` and `README.md`. The `state-writer` agent or the `handoff-docs` skill can do
   this.
6. Commit as `Cadre M<n>: <what changed>`, report in at most 10 lines, and move on.

### M5 — Live verification
First check `uv run cadre provider list`. If no provider has a key, stop and ask Rithik to run
these himself (the key prompt is hidden, and keys go into Windows Credential Manager):
```
cd C:\Users\Rishi\cadre
uv run cadre provider add groq
uv run cadre provider add gemini
uv run cadre provider add openrouter
```
Then follow the M5 steps already in `ROADMAP.md`. For each template, also record:
- the median fixed prompt tokens per call;
- the number of repair turns;
- which model served each role;
- every wait, fallback and 429.

These numbers are the baseline for M7's forecast. If a real model keeps breaking a JSON shape,
fix the prompt or the parser, and add a regression test built from the real response with
secrets removed.

### M6 — Provider catalogue, daily clocks, data policy
- Check today's free tier for every free preset on the provider's own page. For Google AI
  Studio, list **every model the free tier serves** (the Gemini Pro, Flash and Flash-Lite lines,
  Gemma, whatever the page shows that day) as a separate model with its own RPM, TPM and RPD.
  Use `source="docs"` only for numbers the page itself states.
- Record DeepSeek accurately. Its own API is paid, so keep `free=False`. Free DeepSeek models are
  reached through OpenRouter's `:free` catalogue, where they are discovered and get the family
  `deepseek`.
- Add a `day_reset` field to each preset and check it against each provider's documentation.
  `quota.py` computes the day key and the "frees in" time from it. Migrate existing
  `quota_daily` rows without losing today's counts.
- Add `trains_on_free_data` (`yes`, `no` or `unknown`, with a source URL) to each preset, and a
  `privacy` setting (`standard` or `private`) to orgs and runs. A `private` run excludes
  providers marked `yes` or `unknown` and records the exclusion as an event. If no model is
  left, the run fails before its first call and says why.
- Add `cadre provider refresh`. It rediscovers each provider's models and shows the difference
  from `config.yaml`. It never silently changes a limit the owner has overridden.

### M7 — Usage ledger and forecast
- `cadre usage [--days N]` shows, for each day, provider and model: requests, tokens, the share
  of the daily cap used, and the next reset in IST. Add a dashboard page with the same table
  (follow the `dataviz` skill; insert text with `textContent` only).
- `cadre forecast <org> "<goal>" [--project PATH]` estimates calls and tokens from measured
  history. It shows the median, the p90 and n, and gives one of four answers:
  - **fits now**;
  - **fits today after ~N min of waits**;
  - **needs ~N days**;
  - **cannot run**, with the reason (for example: one call is larger than every model's
    tokens-per-minute limit).

  `cadre run` prints the forecast before it starts.
- Add a `reserve_pct` setting per provider (default 10) so Cadre never uses the last part of a
  daily quota that other projects, such as Tessera, share.

### M8 — Code-editing tools and repo map
- `edit_file(path, old, new)` replaces an exact, unique match. If the text matches zero times or
  more than once, it returns an error that gives the count. Edits are versioned like writes.
- `read_file` gains `start_line` and `end_line`. `search(pattern, glob?)` returns a capped list
  of matches and cannot leave the workspace.
- Agents that have file tools get a repo map injected into their prompt: paths with line counts,
  plus top-level `def`/`class` names from Python files (via `ast`). The map has a hard token cap.
- **Measure before keeping anything.** Take a ~400-line fixture file and make a one-line change,
  once with `write_file` and once with `edit_file`, and record the tokens of each. Also record
  what each new schema costs per call. Keep only the tools that save more than they cost, and
  put the numbers in ADR-021 and ADR-022. Tools are still offered per agent.

### M9 — Project mode
- `cadre run <org> "<goal>" --project <path> [--base <branch>]`. The path must be a git
  repository. A dirty working tree is refused unless `--allow-dirty` is given; in that case the
  base is HEAD, and Cadre says that uncommitted changes are not carried over.
- `git worktree add ~/.cadre/runs/<id>/workspace -b cadre/<run-id> <base>`. Workspace
  confinement stays as it is, and `.git` remains refused.
- Checks come from `.cadre/checks.yaml` in the target repo or from the org file. They are still
  named, and they still need approval before they run.
- After each finished step that changed files, commit on the run branch with the message
  `cadre(<step>): <summary>`, using the repo's identity and no trailers.
- At the end of a run:
  - `REPORT.md` and `git diff --stat <base>...cadre/<id>` go into the run result.
  - The CLI prints the command to review the branch and the commands to throw it away
    (`git worktree remove`, `git branch -D`).
  - Cadre never merges or pushes.
- `cadre runs cleanup` removes worktrees of finished runs, after confirmation.
- Add a new template, `project-finisher`, which runs these steps in order:
  1. a manager reads the repo map and the goal, and makes a plan;
  2. engineers make the changes with the edit tools;
  3. the checks run;
  4. a reviewer from a different model family reviews the result;
  5. the manager writes the report.
- Tests:
  - HEAD, the branch and `git status --porcelain` in the source repo are identical before and
    after a run (NFR-10);
  - a dirty tree is refused;
  - a resumed run continues on the same branch.

### M10 — Multi-day runs
- A run is parked when no eligible model can serve a call within `max_wait` **because of daily
  limits**. Its status becomes `parked`, and `resume_at` is set to the earliest reset among the
  eligible models, plus some jitter. A `run.parked` event names each model and its reset time.
  Minute-limit waits keep waiting as before.
- `cadre serve` resumes parked runs when they are due. For times when the server isn't running,
  `cadre scheduler install` / `uninstall` manages a Windows Task Scheduler job that runs
  `cadre resume --due` (the same approach as Darkwatch's daily task). **Ask before installing it
  on Rithik's machine.**
- Run budgets count across days. Add `max_days` and a per-day token cap.
- Test with a fake clock: the daily limit is hit, the run parks, the clock passes the reset, the
  run resumes and finishes. The provider's call count must show that no finished step was billed
  twice.

### M11 — Capstone and v1.0
Use scratch fixtures under `~/.cadre/capstone/`. Don't use a real project without asking.
1. **Build a new project.** `software-team` builds a small but real CLI with tests from a
   one-paragraph goal, using free keys only.
2. **Finish an existing one.** Create a fixture repo with a half-built feature and failing tests.
   `project-finisher` makes the tests pass on its branch.

For each, record:
- forecast against actual: calls, tokens, wall time, days and parks;
- which model filled each role, and whether the reviews were independent;
- the check results, repair turns and 429s.

If a run fails, write down how and why, plainly.

Release:
1. Run the `claim-auditor` agent over `README.md`, `OBJECTIVES.md` and `PROJECT_STATE.md`, and
   fix every claim it marks unsupported.
2. Set the version to 1.0.0 in `pyproject.toml` and `__init__.py`.
3. Add `CHANGELOG.md`.
4. Update the README quick start: add keys → forecast → run → review the branch.
5. Create a local tag, `v1.0.0`.

## Definition of done for v1.0

- Every FR has a passing test, the `TEST_PLAN.md` traceability table is complete, and ruff is
  clean.
- Every template has run at least once on live free keys, with its numbers recorded.
- The capstone results are in `PROJECT_STATE.md` with the raw numbers.
- No key value exists in the repo, the database, `~/.cadre` or the transcript. Check with the
  planted-key test and with `git grep` for known key prefixes (`gsk_`, `AIza`, `sk-or-`).
- Server RSS has been measured while a run streams to the dashboard.
- The docs match the code (`claim-auditor` passes).

## Reporting

After each milestone, report in at most 10 lines:
- what changed;
- the test count and duration;
- what you observed live;
- what is still unverified;
- what comes next.

Before a session ends, make sure "Where to pick up" in `PROJECT_STATE.md` names the exact next
step.
