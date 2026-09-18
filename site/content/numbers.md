---
title: Measured numbers
description: Every template and two capstone projects run on free Groq and Google AI Studio keys, 2026-09-17/18, with run ids, tokens, waits, failures and honest limitations.
---

# Measured numbers

Every number on this page was observed on free **Groq** and **Google AI Studio (Gemini)** keys on
2026-09-17 and 2026-09-18, and comes from the M5 and M11 sections of
[`docs/PROJECT_STATE.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/PROJECT_STATE.md).
The two tables are inserted from that file each time this site is built, so they cannot drift from
it. Run ids are shown by their time suffix (`…230536` is `20260917-230536-…`). Anything not recorded
there is unverified, and the [limitations](#limitations) say what that covers.

## M11: two capstones on free keys (2026-09-17/18)

Fixtures only, not real projects: one new program built from a sentence, and one half-built package
finished in project mode.

<!-- project-state: M11 -->

- **Capstone 1 is a real program.** Goal: *"A command-line unit converter (length, mass,
  temperature) in Python, standard library only, with unittest tests and a README."* Delivered
  `app.py` (149 lines), `test_app.py` (85), `README.md` (48) and `SPEC.md`. Re-run by hand:
  `Ran 9 tests … OK`; `app.py 10 km mi` → `6.2137`, `app.py 100 c f` → `212.0000`,
  `app.py 5 kg lb` → `11.0231` (all correct). The run was interrupted when the session that started
  it ended, and `cadre resume` finished it the next day, reusing every finished step.
- **Capstone 2 finished a half-built package.** `inventory.store` was finished (3 passing tests);
  `inventory.report` was stubbed with `NotImplementedError` and 5 failing tests. Result: one commit
  on `cadre/20260918-152845-13a59c` (`inventory/report.py` +17/−3, tests untouched, no trailer).
  The branch re-run by hand: `Ran 8 tests … OK`. The owner's `main` HEAD, current branch and empty
  `git status` were identical before and after.
- **Honest gaps.** No review in either capstone was independent: the only model family neither
  builder had used (Groq's Qwen) was still inside its rolling 24-hour limit from the M5 runs. Cadre
  routed the reviews anyway and recorded `independent: false` for every one, so the verdicts rest on
  the checks. No run parked: daily limits were hit per model, but another model was always free.

## M5: every template on live keys (2026-09-17)

Keys: Groq (`gpt-oss-120b`, `gpt-oss-20b`, `qwen3.8-27b`) and Gemini. Measured with
`tools/run_metrics.py`, which reads only Cadre's own database. *Waits* are quota waits, *fallbacks*
are calls that moved to another model, *reviews independent* counts reviews on a different model
family from the work they judged (yes / no). The last row is the run on the
[replay page](replay.html).

<!-- project-state: M5 -->

Council members were spread across `gpt-oss-120b`, `gemini-3.6-flash`, `qwen3.8-27b` and
`gpt-oss-20b`; a fourth member cannot be independent with three families, and the run says so.
Reviewers and QA landed on `qwen3.8-27b`, the one family neither builder used, which made Qwen's
8,000 tokens per minute the pace-setter (QA waited 27 times in `startup-company`).

### What real models broke

Ten defects appeared only against real models. Each was fixed with a regression test built from the
live response or error.

1. **Gemini 3's thinking ate the output budget**: a strict-JSON options list stopped after 154
   visible tokens. Gemini now gets `reasoning_effort: "low"`, every call records `finish_reason`,
   and cut-off answers raise `agent.truncated`.
2. **Gemini "503 high demand", 7–12 times a run.** A busy model now rests 30 s, 60 s, 120 s … up
   to 10 minutes before it is tried again.
3. **Gemini 3 tool loops failed with 400 "Function call is missing a thought_signature"**, which
   failed the first `research-desk` run. Signatures are now kept and replayed to the provider that
   issued them; afterwards the checker ran 7 tool turns on Gemini with no 400.
4. **`gemini-2.5-flash`, `2.5-pro` and `2.5-flash-lite` answered 404 "no longer available to new
   users".** A 404 now takes the model out for the session, and the three left the preset.
5. **An analyst wrote its report as a text answer, and an editor listed files eight times without
   saving `BRIEF.md`.** Tasks that name a file are now checked by code (one nudge, then Cadre saves
   the answer), and identical repeated reads are answered with "you already did this".
6. **A review was marked independent when it wasn't.** Reviews now avoid every family the builder
   used.
7. **`startup-company`'s task t4 failed: "waited 299s for capacity and gave up".** The per-call wait
   cap is now 15 minutes, and reviewers get the written files inline so a review needs fewer turns.
8. **Gemini 429 "You exceeded your current quota" after 6–15 requests.** Google's quota details are
   parsed; a daily-quota 429 marks the model spent until its own reset and learns the real limit.
9. **Forecasts were off by up to 4.4×.** Recalibrated from these runs; every template's estimate is
   now within 2× (each n = 1).
10. **`cadre provider add` hung on the hidden prompt when no terminal could answer.** It now fails at
    once and names where it looked.

### Also observed live

- **Header learning works.** Groq's own `x-ratelimit-remaining-tokens` (4,689, reset 9.9 s) and
  remaining requests (971, reset 2,490 s) showed up in `GET /api/quota`. Groq sent at most one 429
  per run (2 in all, both on Qwen); the 4–8 per run in the later runs were Gemini daily-quota 429s.
- **`edit_file` in practice**: the `project-finisher` engineer made one edit, correct first time on a
  CRLF file; 0 misuses in the two runs that could edit.
- **`software-team` built something real**: an 87-line `app.py` and a 124-line `test_app.py`;
  `Ran 10 tests … OK` when re-run by hand, and `app.py demo.csv` printed a correct Markdown table.
- **`project-finisher` left the owner alone**: it changed `line_count` on
  `cadre/20260917-225900-fe3c25` (one commit, the owner's identity, no trailer) while `main`'s HEAD
  and an empty `git status` were unchanged.
- **Server memory**: 64.5 MB idle, 72.9 MB peak while a live run streamed 55 events (target under
  150 MB).
- Tests after M5: 170 passed, 1 skipped; `ruff` clean.

## Limitations

- **No run has parked live yet.** Parking on a daily limit and resuming after the reset is verified
  with a fake clock only. A real interrupted run (capstone 1) did resume the next day without
  re-billing finished steps.
- **Review quality is not measured.** Only the routing of reviewers to another model family is. In
  both capstones no review was independent at all.
- **Forecasts are rough.** They were off by up to 4.4× (`startup-company`) before recalibration, and
  2.3× low on capstone 2, because history is kept per org, not per project size.
- **Tested by hand on Windows only.** A CI matrix for Windows, macOS and Ubuntu is written but has
  not run yet.
- **The dashboard has never been checked in a browser.**
- **Interrupted time is lost from `active_seconds`**: capstone 1's first 485 s are missing, so
  `max_minutes` undercounts interrupted runs.
- **Free tiers change without notice.** Preset limits are dated starting values (checked
  2026-09-17), corrected at run time by each provider's rate-limit headers where it sends them.
