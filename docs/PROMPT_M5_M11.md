# Cadre — prompt for the live phase (M5 → M11 → v1.0)

_Written 2026-09-17, after M6–M10 were built and verified offline (150 tests)._

**Before starting:** in Claude Code, add `Bash(uv run cadre:*)` under `/permissions`. Then open a
terminal in the repository root, run `claude --model opus`, and say:
_"Read docs/PROMPT_M5_M11.md and follow it."_

---

You are continuing Cadre as its tech lead. `docs/MASTER_PROMPT.md` still sets the rules and the
definition of done for v1.0. This prompt covers only what remains: live verification (M5), the
capstone (M11) and the release. Everything else is built and tested offline. **Don't redesign
it. Measure it, and fix what real models break.**

## Start

1. Read `CLAUDE.md`, `docs/PROJECT_STATE.md` ("Where to pick up") and the Rules and Definition
   of done sections of `docs/MASTER_PROMPT.md`.
2. Run `uv run pytest -q`, `uv run ruff check src tests` and `git status`. Expect 150 passed and
   1 skipped. If the result differs, stop and find out why before anything else.
3. Run `uv run cadre provider list`. If Groq isn't configured, run `uv run cadre provider add groq`.
   The key is already in Windows Credential Manager, and the command uses it without prompting.
   **If any `uv run cadre` command is refused by a permission check, don't retry it, and don't
   work around it** (no other shell, no Python wrapper, no settings edit). Stop and give Rithik
   the exact command to run with a leading `!`.
4. Ask Rithik once whether he wants to add Gemini (`uv run cadre provider add gemini`, which
   prompts for the key). If he doesn't, continue with Groq only; its Qwen model provides the
   second family for independent review.

## Rules for this phase

- **Keys:** never print, echo, log or paste a key. If one appears anywhere in the transcript,
  stop and tell Rithik to rotate it.
- **Quota:** Groq's free tier is the whole budget, so spend it deliberately. Run
  `uv run cadre forecast …` before every run and `uv run cadre usage` after it. Run the cheapest
  templates first. If the day runs out, let the run park; that is live evidence for M10. Record
  the park and continue after the reset, and never start a second Groq account.
- **`--allow-exec`** is allowed only for `software-team` and for runs on fixtures in
  `~/.cadre/capstone/`. Never point `--project` at one of Rithik's real repositories.
- **Scheduler:** don't run `cadre scheduler install` without asking.
- **Failures are results.** If a real model breaks a reply format, misuses `edit_file`, or loops
  until a budget stops it, record exactly what happened first, then fix it:
  1. Add a regression test built from the real response, with secrets removed.
  2. Make the smallest fix to the prompt or the parser.
  3. Rerun the template.

  Keep both results, the failure and the fix.
- **Hardware:** run one heavy thing at a time (a live run, the server, or the test suite).

## M5 — live verification

1. **First test:** run
   `uv run cadre run decision-board "Should a two-person team build a budgeting app or a notes app first?" --yes`.
   Confirm that the votes parse, the tally is counted by code, and `DECISION.md` is written.
2. **Every template, once:** `decision-board`, `research-desk --yes`, `startup-company`,
   `software-team --allow-exec`, and `project-finisher --project <fixture repo>`. For the fixture,
   make a small git repo under `~/.cadre/capstone/m5-fixture/` with one failing test.
3. For each run, record the following in a table in `PROJECT_STATE.md`:
   - status and wall time;
   - model calls, prompt tokens and completion tokens;
   - median fixed prompt tokens per call, and repair turns;
   - waits, fallbacks and 429s;
   - which model served each role, and whether each review was independent;
   - forecast against actual (calls and tokens).
4. **Rate-limit headers:** confirm Groq's `x-ratelimit-*` headers appear in `GET /api/quota`. A
   burst of calls should produce a `route.wait` event instead of a 429.
5. **Editing:** check whether the real models used `edit_file`, `search` and line-range reads
   correctly in `software-team` and `project-finisher`. Count the misuses.
6. **Forecast:** if a forecast was off by more than 2× in either direction, correct that
   template's estimate from the measured numbers. Record the old value, the new value and n.
7. **Server memory and dashboard:** start `uv run cadre serve`, stream a run into the dashboard,
   and measure the server's memory with
   `tasklist /FI "IMAGENAME eq python.exe"` (target < 150 MB).
   - If the Chrome extension is connected, open the dashboard with `uv run cadre ui`. Take a
     screenshot of the Runs, Usage and Providers pages, and of a parked run if one exists. Fix
     anything that renders badly.
   - Otherwise, ask Rithik to look at those pages and tell you what he sees.
8. Update the docs and commit as `Cadre M5: live verification on Groq`.

## M11 — capstone

Use scratch fixtures under `~/.cadre/capstone/` only.

1. **Build a new project.** Run `software-team --allow-exec` with this goal: *"A command-line unit
   converter (length, mass, temperature) in Python, standard library only, with unittest tests
   and a README."* It succeeds only if the checks really pass. Record the check output.
2. **Finish a half-built project.** First write the fixture yourself: a small Python package with
   one finished feature and one half-built one, and 3–5 failing tests that specify the missing
   behaviour. Commit it. Then run `project-finisher` against it. It succeeds only if the tests
   pass on the `cadre/<run-id>` branch and the fixture's own branch and working tree are
   unchanged (check `git status --porcelain` and HEAD before and after).
3. For both runs, record the same table as in M5, plus days taken, parks and resumes. If either
   run fails, say so plainly, with the cause.
4. Commit as `Cadre M11: capstone on free keys`.

## Release

1. Run the `claim-auditor` agent over `README.md`, `docs/OBJECTIVES.md` and
   `docs/PROJECT_STATE.md`, and fix every claim it marks stale or unsupported.
2. Check every item in the master prompt's definition of done. Include a `git grep` for key
   prefixes (`gsk_`, `AIza`, `sk-or-`) and the planted-key test.
3. **If every item holds:**
   - set the version to 1.0.0 in `pyproject.toml` and `src/cadre/__init__.py`;
   - write `CHANGELOG.md` covering v0.1.0 and v1.0.0;
   - update the README quick start: add a key → forecast → run → review the branch;
   - commit, and create a local tag `v1.0.0`.

   **If any item fails:** don't tag. List what's missing in `PROJECT_STATE.md` and stop.
4. There is no remote. Report that the push was skipped. Ask Rithik whether he wants a
   **private** `Daemon-VI/cadre` repository created. Don't create it without his yes.
5. Update the handoff docs (`state-writer` agent) so "Where to pick up" names the next step:
   either the unmet items or ROADMAP M12.

## Report at the end

Keep the report under 15 lines:
- the live results table: template → status, calls, tokens, forecast error;
- what real models broke, and what was fixed;
- the capstone verdicts;
- whether v1.0 was tagged;
- what is still unverified;
- what needs Rithik.
