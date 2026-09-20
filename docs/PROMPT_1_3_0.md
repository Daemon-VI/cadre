# Cadre — prompt for 1.3.0 (ship M14) and the v1 freeze

_Written 2026-09-20. At that point M14 (memory across runs) was on `main` at 65b71ba, unreleased,
with CI and the docs site green and 284 tests passing. The last release was 1.2.0, and `v1` points
at it._

**How to use it:** open a terminal in the repository root, run `claude --model opus`, and say:
_"Read docs/PROMPT_1_3_0.md and follow it."_

---

You are continuing Cadre as its tech lead. The rules in `docs/MASTER_PROMPT.md`,
`docs/PROMPT_RELEASE.md` and `docs/PROMPT_M14.md` still apply:
- claims are measured;
- no keys or tokens in the transcript;
- no AI trailers on commits;
- no local paths or user names in tracked files;
- never re-push or move a published `vX.Y.Z` tag;
- PyPI never takes the same version twice;
- moving `v1` needs its own yes.

**This prompt closes v1.** M15 (web research) and M16 (hosting) are deliberately not in it. If
Rithik asks for M15 later, it gets its own prompt file.

## 0. Orient, and one question

1. Run `git status`, `git log --oneline -5`, `git tag`, `uv run pytest -q` (expect 284 passing)
   and `uv run ruff check src tests`, and check CI on `main`. Read "Where to pick up" in
   `PROJECT_STATE.md`.
2. Ask Rithik with **one** AskUserQuestion call, multi-select, and do only what he ticks:
   - a. **Release 1.3.0** (ships M14, memory across runs).
   - b. **Move `v1` to 1.3.0**, only after one demo-repo run on the new tag passes.
   - c. **Create the security advisory as a private draft**; he reviews and publishes it.
   - d. **Freeze v1** (section 4): honest docs, a cold-start handoff and a demo script.

   If `gh secret list` shows both `VSCE_PAT` and `OVSX_PAT`, add a second question offering the
   **VS Code extension publish**. If it doesn't, don't offer it, and say so in one line.

## 1. Release 1.3.0 (if a)

1. **Prove the migration on a real install before tagging.** Point `CADRE_HOME` at a new folder in
   the scratchpad, then:
   - run `uvx --from cadre-ai==1.2.0 cadre run decision-board "…" --demo` twice, and create a team
     so there is M13 data in the database;
   - run the `main` build against that same home.

   Check that the schema version advances, that both old runs are still listed with their events,
   that the team survives, and that `cadre memory list` works on a store that has no memory files
   yet. Record what you observed.
2. Set the version to `1.3.0` in `pyproject.toml` and `src/cadre/__init__.py`, and add a dated
   `CHANGELOG.md` entry from `git log v1.2.0..HEAD`. In that entry, say plainly:
   - what memory is, and whether it is on or off by default;
   - that a model can only **propose** memory and a human approves it, and that memory can't be
     approved over MCP;
   - the measured result, in the honest form: on one fixture, n=1, memory changed no outcome and
     cost about 94 tokens per model call. Don't dress this up.
3. Commit, tag `v1.3.0`, push the tag, and follow the release with `gh run watch`. Then verify:
   - PyPI: `uvx --from cadre-ai@1.3.0 cadre --version` and one `--demo` run;
   - the SHA-256 of the PyPI files against the GitHub Release assets;
   - the GHCR image (the CI smoke test is enough if Docker isn't running);
   - the Windows build: download it and run `--version`.
4. **Move `v1` (if b).** One cheap labelled issue on `cadre-action-demo` using the `v1.3.0` tag
   directly. Only after its PR is correct (a one-line title, a body ending with `Closes #N`, the
   tests passing) move `v1` and push it with `--force` for `v1` only. Check the Marketplace listing
   shows 1.3.0. Close the demo issue and PR afterwards, and leave the fixture's `main` unchanged.
5. If a publish job fails, find the cause first. Rerun it if nothing was uploaded; if something was,
   release 1.3.1.

## 2. The security advisory (if c)

1. Create a **draft** from `docs/SECURITY_ADVISORY_DRAFT.md` with
   `gh api -X POST repos/Daemon-VI/cadre/security-advisories`. A draft is private. Don't publish it
   and don't request a CVE: both are his acts.
2. Give him the draft's URL and one line: *review it, then press Publish.*
3. After he publishes, add the GHSA link to `SECURITY.md` and to the 1.1.0 entry in `CHANGELOG.md`.

## 3. Extension publish (only if both tokens exist and he ticks it)

Follow section 2 of `docs/PROMPT_M12.md`, with the extension version matching the engine release
(1.3.0 if it was released, otherwise 1.2.0). The secrets are repository secrets, and the
`vscode-marketplace` environment is created by the first run that uses it. Check afterwards that it
exists and has no protection rules he didn't ask for.

## 4. Freeze v1 (if d)

The point of this section is that Cadre can sit untouched for months and still be picked up — by
Rithik after a cleared session, by a stranger reading the repo, or by Rithik in an interview.

1. **Say what Cadre is and isn't, in the README.** Check every claim against the code, and state
   the measured null result for memory in the feature list rather than hiding it. Keep the
   limitations section honest: containment is proven on Linux CI only; the Action's "left today"
   figure is wrong; reads are not scoped by team.
2. **Refresh the known-bugs list** in `PROJECT_STATE.md`. Remove what is fixed (the Reject re-ask),
   keep what isn't, and give each remaining bug one line saying where the fix would go.
3. **`ROADMAP.md`:** mark M14 done. Say in one line that v1 is feature-complete and the project is
   in maintenance; M13.1 (OIDC), M15 (web research) and M16 (hosting) are designed but not started,
   and M13.1 is still a prerequisite of M16.
4. **A cold-start handoff.** Rewrite "Where to pick up" in `PROJECT_STATE.md` for someone returning
   in six months: how to install from source, how to add a key, how to run the tests, where the
   published channels are, and what the next milestone would be. Assume nothing is remembered.
5. **Write `docs/DEMO.md`: a three-minute walkthrough.** This is for a live audience, so every
   command in it must be one you ran yourself, in order, from a clean scratch `CADRE_HOME`:
   - install with `uvx --from cadre-ai cadre`;
   - `cadre forecast` on a real goal, showing the cost estimate before anything runs;
   - one `--demo` run of `decision-board`, showing the build → review → vote timeline;
   - `cadre usage`, showing what each model spent and what quota is left;
   - one sentence each on the parts you can't demo offline: the container runner, teams, and the
     GitHub Action.

   Record the real output. If a step takes longer than the script implies, say the true timing.
6. Run `claim-auditor` over the README, `docs/DEMO.md`, the docs site and `PROJECT_STATE.md`, and
   fix what it flags.
7. Commit as `Cadre: freeze v1 (docs, handoff, demo script)`, push, and check the docs site builds.

## Report

Keep it under 12 lines:
- 1.3.0: the migration check, the channels and hashes, and the `v1` status;
- the advisory draft's URL;
- the extension, if it was published;
- the freeze: what the claim-auditor flagged, and what `DEMO.md` actually showed on screen;
- what still needs Rithik.
