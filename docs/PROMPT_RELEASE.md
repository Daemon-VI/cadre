# Cadre — prompt for the first public release (1.0.0)

_Written 2026-09-19._

**Where things stand:**
- The repo is public, CI is green, and the docs site is live.
- The GitHub Action is verified on `cadre-action-demo` (PRs #2 and #3).
- D6 was skipped.
- Nothing is tagged or published yet.

**How to use it:** open a terminal in the repository root, run `claude --model opus`, and say:
_"Read docs/PROMPT_RELEASE.md and follow it."_

---

You are finishing Cadre's first public release as its tech lead. The rules in
`docs/MASTER_PROMPT.md` and `docs/PROMPT_DISTRIBUTION.md` still apply:
- claims are measured;
- keys and tokens never enter the transcript;
- git commits carry no AI trailers;
- front ends stay thin clients of the engine.

Two more rules apply in this repository now that it is public:

- **No local paths or user names in tracked files.** A CI check already fails on the laptop
  account's user name. The same care applies to screenshots: crop or retake any that show a user
  folder.
- **PyPI never accepts the same version twice**, even after a failed or deleted upload, and
  TestPyPI doesn't either. If a publish job fails after uploading, fix the problem and release
  the next patch version. Never move or re-push a published `vX.Y.Z` tag.

## Decision already made: one release, 1.0.0

Nothing has been released under 1.0.0 or 1.1.0, and every distribution feature is already on
`main`. Ship **everything as 1.0.0 from current `main`**:
- Merge the two unreleased sections of `CHANGELOG.md` into `1.0.0`.
- Update the references to "v1.1.0" in `ROADMAP.md`, `PROJECT_STATE.md` and the docs site.
- Record the decision in `ROADMAP.md`.

## 0. Orient

1. Run `git status`, `git log --oneline -5`, `git tag`, `uv run pytest -q` and
   `uv run ruff check src tests`. Check that CI on `main` is green (`gh run list -L 5`).
2. Fix the stale parts of "Where to pick up" in `PROJECT_STATE.md`:
   - Item 1 says to re-add rotated keys with `provider add`. When a key is already stored, that
     command keeps the **old** key, so the right command is `provider key <id>`. Rithik has
     already rotated both keys.
   - Item 7 asks to make the repo public; that is already done.
3. **Fix the trap at its source.** When a key already exists, `provider add` should also say
   *"to replace it, run `cadre provider key <id>`"*. Add a test, and correct any doc that tells
   people to use `provider add` to replace a key.
4. Check whether the old key file is still in Rithik's Downloads folder by listing its name
   only; never open it. If it's there, remind him to delete it with Shift+Delete.

## 1. One question to Rithik (multi-select)

Ask once which of these he approves now. Do only what he ticks:

- **a. Tag and publish 1.0.0.** Pushing the tag publishes to TestPyPI and PyPI, builds the
  standalone downloads, pushes the first GHCR image and creates the GitHub Release. Offer this
  only if he confirms the pending publishers exist on **both** sites:

  | Site | Project | Owner / repo | Workflow | Environment |
  |---|---|---|---|---|
  | test.pypi.org | `cadre-ai` | `Daemon-VI` / `cadre` | `release.yml` | `testpypi` |
  | pypi.org | `cadre-ai` | `Daemon-VI` / `cadre` | `release.yml` | `pypi` |

- **b.** Push a `v1` tag so people can write `uses: Daemon-VI/cadre@v1`.
- **c.** List the action on the GitHub Marketplace.
- **d.** In `cadre-action-demo`, merge PR #3 and close PR #2 with a comment pointing to #3.
- **e.** Publish the VS Code extension. He must confirm `VSCE_PAT` and `OVSX_PAT` are already set,
  which `gh secret list` shows by name only.

## 2. Release 1.0.0 (if a)

1. Check that `cadre-ai` is still free: `curl -s -o /dev/null -w "%{http_code}"
   https://pypi.org/pypi/cadre-ai/json` should print 404.
2. Update the release files:
   - set the version to `1.0.0` in `pyproject.toml` and `src/cadre/__init__.py`;
   - date the `CHANGELOG.md` entry;
   - update the README quick start to add a key → forecast → run → review the branch, with
     install commands `uvx --from cadre-ai cadre` and `pipx install cadre-ai`.
3. Commit, then tag the commit `v1.0.0` and push the tag.
4. Watch every job with `gh run watch`. Then verify each channel from a clean environment, and
   record the outputs:
   - **PyPI:** in a fresh temporary venv, `uvx --from cadre-ai cadre --version` and one
     `cadre run decision-board "…" --demo`.
   - **GHCR:** `docker run` the image with `--demo`, or record why it can't run on this laptop
     and rely on the CI smoke test. Make sure the package is **public** on GitHub; a new GHCR
     package can start out private.
   - **GitHub Release:** download the Windows build and run `cadre --version` from it.
5. **If a job fails:** find the cause first. If nothing was uploaded, rerun the job. If something
   was, release 1.0.1.

## 3. The Action tag (if b)

- Point `v1` at the `v1.0.0` commit. When a later 1.x release needs `v1` moved, the move itself
  needs his yes.
- Change the Action docs to use `@v1`.
- **Optional:** one cheap run on the demo repo with `@v1`, to prove the tag resolves. Record the
  result.

## 4. See it on screen

Neither the dashboard nor the extension's UI has ever been observed.

1. **Dashboard:** start `uv run cadre serve`, open it with `uv run cadre ui`, and look at Runs,
   Usage, Models & keys and a run's timeline.
   - If the Chrome extension is connected, use it.
   - Otherwise take screenshots with PowerShell (`System.Windows.Forms` +
     `System.Drawing.Graphics.CopyFromScreen`) into the scratchpad, read the images, and fix
     what renders badly.
2. **VS Code extension:** launch VS Code with the installed `.vsix` on a fixture repo. Walk through
   Forecast → Start run → watch the run → Review branch, capturing screenshots the same way.
3. Screenshots used on the docs site must show no user folder or user name.

## 5. MCP in real hosts (D2 is still pending)

1. **Claude Code:** after 1.0.0 is on PyPI, run `claude mcp add cadre -- uvx --from cadre-ai cadre mcp`.
   Record the tools list and one `cadre_forecast` call.
2. **VS Code agent mode and Antigravity:** ask whether he has them. Test only what he has, and
   record each result.

## 6. Extension publish (if e)

- Bump the extension's version and push a `vscode-v<version>` tag.
- Confirm the listing appears on both the VS Code Marketplace and Open VSX.
- Install it from Open VSX in Antigravity if he has it, and record the result.

## 7. Close out

1. Run `claim-auditor` over the README, the docs site and `PROJECT_STATE.md`, and fix what it
   flags.
2. Update the docs:
   - `PROJECT_STATE.md` gets a "Published channels" table (channel, version, URL, and how it was
     verified).
   - `ROADMAP.md` shows the distribution track status.
   - "Where to pick up" says: ROADMAP M12, the container runner for checks.
3. Commit and push.

## Report

Keep it under 12 lines:
- each channel, whether it's live, and its URL;
- what was seen on screen and what was fixed;
- the MCP host results;
- anything that failed, and why;
- what still needs Rithik.
