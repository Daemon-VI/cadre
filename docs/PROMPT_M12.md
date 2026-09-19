# Cadre — prompt for 1.0.1, the extension publish, and M12 (container runner)

_Written 2026-09-19, after 1.0.0 shipped. The last commit was e3c5db7, and CI and the docs site
were green._

**How to use it:** open a terminal in the repository root, run `claude --model opus`, and say:
_"Read docs/PROMPT_M12.md and follow it."_

---

You are continuing Cadre as its tech lead. The rules in `docs/MASTER_PROMPT.md` and
`docs/PROMPT_RELEASE.md` still apply:
- claims are measured;
- no keys or tokens in the transcript;
- no AI trailers on commits;
- no local paths or user names in tracked files;
- never re-push or move a published `vX.Y.Z` tag;
- PyPI never takes the same version twice.

## 0. Orient, and one question

1. Run `git status`, `git log --oneline -5`, `git tag`, `uv run pytest -q` (expect 208 passing)
   and `uv run ruff check src tests`, and check CI on `main`. Read "Where to pick up" in
   `PROJECT_STATE.md`.
2. Ask Rithik once, multi-select, and do only what he ticks:
   - **a. Release 1.0.1 and move `v1` to it.** The PR-title fix, `Closes #N` and the dashboard
     fixes are on `main` but not in 1.0.0. Users of `@v1` and of the PyPI package don't have
     them yet.
   - **b. Publish the VS Code extension.** Only if he confirms `VSCE_PAT` and `OVSX_PAT` are set.
     Check with `gh secret list`, which shows names only.
   - **c. Start M12.**

## 1. Release 1.0.1 (if a)

1. Set the version to `1.0.1` in `pyproject.toml` and `src/cadre/__init__.py`, and add a dated
   `CHANGELOG.md` entry covering only what changed since `v1.0.0` (`git log v1.0.0..HEAD`).
2. Commit, tag `v1.0.1`, push the tag, and follow the release run with `gh run watch`.
3. Verify from a clean uv cache with `uvx --from cadre-ai@1.0.1 cadre --version` and one
   `--demo` run. Check the SHA-256 of the PyPI files against the GitHub Release assets.
4. Move `v1` to the `v1.0.1` commit (he approved this in (a)), and push it with
   `--force` **for `v1` only**.
5. Prove the move works with one cheap labelled issue on `cadre-action-demo`. The new PR's title
   must be a single line, and its body must end with `Closes #N`.
6. Check that the GitHub Marketplace listing shows 1.0.1. If it still shows 1.0.0, tell Rithik
   to tick "Publish this Action to the GitHub Marketplace" on the v1.0.1 release page, as he did
   for 1.0.0.

## 2. Publish the extension (if b)

1. The extension's publisher ID is `daemon-vi` (`editors/vscode/package.json`).
   - **Open VSX:** if the namespace doesn't exist yet, create it once with
     `npx ovsx create-namespace daemon-vi` using `OVSX_PAT`, from CI or a one-off workflow step.
     Never put the token on a command line in this transcript.
   - **Version:** set the extension to `1.0.1` so it matches the engine it drives, and record the
     choice in the CHANGELOG.
2. Push the `vscode-v1.0.1` tag and watch `vscode.yml`. Confirm the listing on both the VS Code
   Marketplace and open-vsx.org.
3. Install it **from the Marketplace** into a separate VS Code instance, using its own
   `--user-data-dir` and `--extensions-dir` in the scratchpad, so Rithik's own VS Code is never
   touched. Run Forecast and check that a pending approval shows as a notification and never
   takes focus. Screenshot it.
4. **Antigravity or Cursor:** only if Rithik says one is installed and tells you where. Install
   the extension from Open VSX there and record the result.
5. **Copilot agent mode:** only after he has signed in to Copilot in his own VS Code. Record one
   Cadre tool call made from agent mode.

## 3. M12 — container runner for checks

**Why this milestone exists.** Checks run model-written code as the owner (ADR-006). That is the
last real safety gap, and it blocks M13 (user accounts) and M16 (hosting). `ROADMAP.md` M12
describes the design; follow it:
- `runner: docker | podman` per check;
- the workspace mounted read-write;
- no network;
- CPU and memory caps;
- the image named in the org file;
- the subprocess runner stays the default.

### Requirements and design first
Write FR-22 onward in `SRS.md` with acceptance criteria, and an ADR in `ARCHITECTURE.md` that
includes a threat model.

**The ADR must say plainly what a container does NOT protect against.** It shares the host
kernel, and anything mounted can be changed. It must also settle two policy questions:

1. **Approval policy.** Recommended: keep the `exec` approval by default, and add
   `allow_exec: container_only`. That setting auto-approves only checks that run in a container
   with no network. Checks on the subprocess runner still ask.
2. **Image policy.**
   - Images are pinned by digest in the org file.
   - Cadre never pulls an image silently. A pull needs its own approval, because it uses the
     network on the host.
   - Missing images fail with the exact `docker pull` command to run.

### Container flags, all required and each asserted by a test on the built command line
- `--network none`
- `--read-only`, plus a `--tmpfs /tmp`
- `--cap-drop ALL` and `--security-opt no-new-privileges`
- `--pids-limit`, `--memory` and `--cpus` from the org file, with safe defaults
- `--user` with a non-root uid
- `--rm`, and a name derived from the run id so a timeout can `docker kill` it

**What goes in:**
- The **only** mount is the workspace, as `/work`. In project mode the worktree's `.git` is a
  file pointing outside the mount. Checks don't need git, so leave it that way. Never mount the
  owner's repository `.git`, the Docker socket, `CADRE_HOME` or the home folder.
- No environment variables pass through except a per-check allowlist. Keys and token-like names
  are always dropped, as in the subprocess runner.
- Build the command as an argument list, never through a shell. Windows paths go to Docker
  Desktop as they are. Watch for Git Bash path conversion in any manual test, and use
  `MSYS_NO_PATHCONV=1`.

**Timeout, output cap and exit code** behave exactly like the subprocess runner. The check's
result records the runner, the image digest and the limits used.

**Podman:** the same flags where Podman accepts them. Record any that differ, and test the
command line.

### Front ends
- The dashboard, the extension's approval view and the MCP `run_status` show the runner and the
  image, so a human approving a check knows where it will run.
- Org validation rejects a container check that has no image, or an image that isn't pinned,
  unless the check sets `allow_unpinned: true`, which is recorded.

### Tests, and the laptop
- **Unit tests** (on every OS, no Docker): building the command line, validation, the approval
  policy, and removal of environment variables.
- **Containment tests** (Linux CI only; GitHub's Ubuntu runners have Docker). A fixture check
  that tries each of these must fail to do it:
  1. reach the network (`python -c "urllib.request.urlopen(...)"`);
  2. write outside `/work`;
  3. read a key planted in the host environment;
  4. fork-bomb (stopped by the pids limit);
  5. allocate past the memory cap (killed by OOM).

  Record each outcome. A normal check (unittest on the unit-converter fixture) must pass inside
  the container.
- **This laptop:** Docker Desktop needs memory the laptop rarely has free (about 0.9 GB was free
  on 2026-09-19). Don't start it without asking Rithik. If he agrees, close other heavy programs
  first, run one real local check in a container, record its memory use, then stop Docker.
- **The GitHub Action** keeps the subprocess runner by default, because its machine is already
  throwaway. Document when to use the container runner there.

### Close out M12
1. Update `TEST_PLAN.md` traceability, `README.md` (the limitations section changes: checks can
   now be contained), the docs site's security page, `ROADMAP.md` (M12 done; next M13) and
   `PROJECT_STATE.md`.
2. Run `claim-auditor` over the security claims in particular; don't overstate containment.
3. Commit as `Cadre M12: container runner for checks`. A 1.1.0 release is a new feature, so it
   needs Rithik's yes: tag `v1.1.0`, and move `v1` only with a separate yes.

## Report

Keep it under 12 lines:
- 1.0.1: the channels checked and the demo PR link;
- the extension: the listings and the on-screen check;
- M12: each containment test's outcome, what isn't contained, and the test counts;
- what still needs Rithik.
