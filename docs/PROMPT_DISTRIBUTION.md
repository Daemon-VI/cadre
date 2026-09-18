# Cadre — prompt for open source and distribution (v1.0 → v1.1)

_Written 2026-09-18, while the M11 capstone was still running. Use this prompt only once
`PROMPT_M5_M11.md` has finished, meaning v1.0.0 is tagged or its unmet items are listed._

**How to use it:** open a terminal in the repository root, run `claude --model opus`, and say:
_"Read docs/PROMPT_DISTRIBUTION.md and follow it."_

---

You are continuing Cadre as its tech lead. The engine works and has been measured on real free
models. This programme puts it where people can get it and use it. Its parts are:

- an open-source repository;
- an installable package and standalone downloads;
- an MCP server, so Cadre works inside AI editors (VS Code, Antigravity, Cursor, Windsurf,
  Claude Code);
- a GitHub Action ("finish this project" from any device);
- a VS Code extension, also published to Open VSX, which is where Antigravity, Cursor and
  Windsurf get extensions;
- a docs site;
- optionally, a desktop app.

`docs/MASTER_PROMPT.md` still sets the rules: claims are measured, keys never enter the
transcript, the owner's repositories are off limits, and git commits carry no AI trailers.

## Architecture rule for this programme

**Keep one engine and make every front end a thin client of it.** The Python engine and its
local API (`cadre serve`) stay the only implementation of routing, quota, runs and approvals. The
MCP server, the Action, the extension and the desktop app all call that API; none of them
re-implements engine logic in another language. If a front end needs something the API lacks,
add it to the API with tests, then use it.

## Stop and ask Rithik before

- creating a GitHub repository or making one public;
- the **first** publish to each channel: PyPI, the VS Code Marketplace, Open VSX, the GitHub
  Marketplace and GHCR;
- anything that costs money, such as code-signing certificates or Apple's developer programme;
- installing software on his machine (for example, VS Code extensions or Antigravity);
- widening his GitHub login's permissions. He runs `gh auth refresh -s workflow` himself; until
  he does, `.github/workflows/` files cannot be pushed;
- creating any publishing token. He creates tokens on the provider's site and stores them with
  `gh secret set <NAME>` in **his own terminal**. Never ask for a token in chat, and never write
  one to a file.

Also stop at the D6 gate, where he decides whether to build the desktop app. Decide everything
else, record the reason in an ADR, and continue.

**Hardware:** anything heavy is built in CI, not on this laptop. That covers Tauri, PyInstaller
matrices, Docker images and VS Code integration tests (Electron).

## Phase 0 — Orient

1. Read `CLAUDE.md`, `docs/PROJECT_STATE.md` and `docs/ROADMAP.md`. Run `git tag`,
   `git status`, `uv run pytest -q` and `uv run ruff check src tests`.
2. **If `v1.0.0` isn't tagged:** open `PROMPT_M5_M11.md` and read its Release section.
   - If items remain that don't need Rithik, finish them first.
   - If items remain that need him, list them in your first message and continue from D0. D0
     doesn't depend on the tag.
3. **Confirm four defaults with Rithik in one question.** Recommend each, and proceed with the
   recommendation if he just says go.

   | Decision | Recommended default | Reason |
   |---|---|---|
   | Licence | **Apache-2.0** | Permissive, with a patent grant; all dependencies are MIT, BSD or Apache |
   | PyPI name | **`cadre-ai`** | `cadre` is taken on PyPI; `cadre-ai` was free on 2026-09-17. The command stays `cadre` |
   | Visibility | **Public after the D0 gate** | Once history, docs and CI are clean |
   | Session prompts and handoff docs | **Keep them public** | They show how the project was engineered. Local paths in three docs can stay or be generalised; his choice (generalised, 2026-09-18) |

4. Add a **Distribution track D0–D7** to `ROADMAP.md`, leaving the existing M numbers alone.
   Add requirements FR-14 onward to `SRS.md`, with acceptance criteria.
5. Write these ADRs in `ARCHITECTURE.md`, starting at ADR-023. Each needs a short threat model:
   - front ends are thin clients of the API;
   - the API gets a version prefix (`/api/v1`), with a snapshot test of its OpenAPI spec;
   - how the engine is delivered: `uvx` versus a bundled binary;
   - MCP transport and what can be approved over MCP;
   - who is allowed to trigger the GitHub Action;
   - how the extension finds or starts the server, and how it handles the API token.

## D0 — Open-source readiness

1. **Licence:** add `LICENSE` and the `license` fields in `pyproject.toml`. Add a CI step that
   lists dependency licences and fails on anything copyleft.
2. **README for strangers:**
   - what Cadre is, in two sentences;
   - quick start: `uvx cadre-ai` → `cadre provider add groq` → `cadre forecast` → `cadre run` →
     review the branch;
   - measured numbers, each linked to where it was measured;
   - an honest limitations section, including that checks aren't sandboxed.
3. **Community files:**
   - `SECURITY.md`: what runs as the owner, and how to report a problem privately;
   - `CONTRIBUTING.md`: uv, tests, ruff, and the rule that decisions get ADRs;
   - issue and pull request templates.
4. **CI** (`.github/workflows/ci.yml`): run pytest and ruff on Windows, macOS and Ubuntu, with
   Python 3.12 and 3.13. Fix whatever fails on macOS and Linux; Cadre has only ever run on
   Windows.
5. **Cross-platform gaps:**
   - `cadre scheduler install` works only on Windows. Add a systemd user timer (Linux) and a
     launchd agent (macOS), or tested cron instructions.
   - Headless machines with no OS keychain need the environment-variable fallback documented,
     with a test run under `CADRE_NO_KEYRING=1`.
   - Add a `cadre serve --allowed-host NAME` option, needed for Tailscale names and containers.
     It is covered by tests, and loopback remains the default.
6. **History and privacy scan:**
   - Run gitleaks over the full history in CI, plus a local `git log -p` search for `gsk_`,
     `AIza` and `sk-or-`.
   - Confirm every commit is by `Rithik Krishna <317035893+Daemon-VI@users.noreply.github.com>`.
   - The laptop account's user name appeared in no tracked file or commit on
     2026-09-18. Add a CI check that fails if it ever appears.
7. **Gate:** ask before creating `Daemon-VI/cadre`. Then push `main` and the tags, and confirm on
   GitHub that CI is green.

## D1 — Package the engine

1. **Wheel:** `uv build`. Install the wheel into a clean venv and run
   `cadre run decision-board "…" --demo` there. This proves `web/` and `templates/` ship inside
   the package. Add the same test to CI.
2. **PyPI through trusted publishing,** with no token:
   - add a `release.yml` workflow that runs on `v*` tags, publishing to TestPyPI first, then
     PyPI;
   - give Rithik the exact pending-publisher fields to enter on pypi.org: owner `Daemon-VI`,
     repository `cadre`, workflow `release.yml`, environment `pypi`;
   - check the name is still free before the first publish.
3. **Standalone downloads:** build PyInstaller one-folder builds for Windows, macOS and Linux in
   CI, and attach them to the GitHub Release. The CI smoke test runs `cadre --version` and one
   demo run. The README says unsigned builds trigger SmartScreen and Gatekeeper warnings.
4. **Container:** a Docker image published to GHCR and built in CI. It runs as a non-root user,
   with `CADRE_HOME=/data` on a volume and keys passed as environment variables. Also document a
   compose file.

## D2 — MCP server (every AI editor at once)

1. Add `cadre mcp`, an MCP server over stdio built on the official Python `mcp` SDK. Check the
   SDK's current API with context7 first.
2. **Offer few tools,** because every schema costs the host's tokens too:
   - `cadre_forecast`;
   - `cadre_start_run` (org, goal, and project path, defaulting to the host's workspace root);
   - `cadre_run_status` (status, the latest events, files changed, and the branch);
   - `cadre_usage`;
   - `cadre_list_orgs`.
3. **Runs outlive the editor.** The MCP server is a client of the local `cadre serve`, which it
   starts detached if it isn't running.
4. **Approvals stay in Cadre.** An `exec` approval, which lets model-written code run, can never
   be granted over MCP. The tool result tells the user to approve with `cadre approve <id>` or in
   the dashboard. Say in an ADR whether gate approvals may be granted over MCP.
5. **Verify in real hosts, and record what you see:**
   - Claude Code, with `claude mcp add cadre -- uvx cadre-ai mcp`;
   - VS Code agent mode, if Copilot is available to him;
   - Antigravity, if he has it; ask first.

   Write a config snippet for each host (VS Code `mcp.json`, Antigravity, Cursor, Windsurf, Claude
   Code), checked against that host's current docs and dated.

## D3 — GitHub Action ("finish this project" from anywhere)

1. **A composite action** (`action.yml`):
   1. set up uv;
   2. `cadre forecast`, posted as a comment on the issue;
   3. a project-mode run on the checkout;
   4. push the `cadre/<run-id>` branch;
   5. open a pull request whose body is `REPORT.md` plus the usage table.

   Keys come from repository secrets as environment variables. `allow-exec` is on by default
   here, because the runner is a throwaway machine.
2. **Who can trigger it.** Offer an example workflow triggered by the `cadre` label or a
   `/cadre <goal>` comment. It must check `author_association` so that only OWNER, MEMBER or
   COLLABORATOR can trigger it; strangers must not be able to spend his keys or inject
   instructions through an issue. Grant only these permissions: `contents: write`,
   `pull-requests: write`, `issues: write`.
3. **Parked runs:** in v1 the action comments the park time and stops. Resuming across jobs
   (`CADRE_HOME` in `actions/cache` plus a scheduled `cadre resume --due`) is a stretch goal;
   record it either way.
4. **Test it for real** on a demo repository (ask before creating
   `Daemon-VI/cadre-action-demo`). The Groq key goes into that repo's secrets, set by Rithik.
   Record the PR it opens. Ask before listing the action on the GitHub Marketplace.

## D4 — VS Code extension (also for Antigravity, Cursor and Windsurf)

1. **Where and how:** `editors/vscode/`, in TypeScript, bundled with esbuild, with as few runtime
   dependencies as possible. It reads the API token from `~/.cadre/token` and never displays it.
2. **Server lifecycle:** use an existing server if one is running. Otherwise run `cadre serve`
   if `cadre` is on PATH, else `uvx cadre-ai serve`. If neither works, point the user to
   installing uv.
3. **Features:**
   - a **Runs** tree with status icons;
   - a run-detail webview with live events (strict CSP with a nonce; text inserted with
     `textContent` only);
   - a status-bar item showing today's usage against the tightest daily limit;
   - **Cadre: Forecast** and **Cadre: Start run on this folder** (project mode on the workspace
     root; the engine's dirty-tree rule still applies);
   - **Review branch**, which opens diffs for the changed files;
   - **Add provider**, which opens the integrated terminal on `cadre provider add <id>`, so the
     key is typed into a hidden prompt and never passes through the extension.
4. **Approvals:** gate approvals show as notifications. An `exec` approval shows the check's
   name **and its exact command from the org file** in a modal dialog, and needs an explicit
   click.
5. **Tests:** unit tests for the API client, run locally. Electron integration tests
   (`@vscode/test-electron`) run in CI only.
6. **Publishing:** package with `vsce`. A tag workflow publishes to the VS Code Marketplace
   (`VSCE_PAT`) **and** Open VSX (`OVSX_PAT`). Rithik sets both tokens as secrets himself. Ask
   before the first publish.
7. **Verify:** install the `.vsix` locally in VS Code, and in Antigravity if he has it. Walk
   through forecast → start run → watch → review the branch on a fixture repo, and record what
   you see.

## D5 — Docs site

1. **Pages:** a static site on GitHub Pages, served from `site/` in the repo, covering:
   - the quick start;
   - one setup page per front end;
   - the security model;
   - the measured numbers, linked and dated from `PROJECT_STATE.md`;
   - a demo recording of a **real** run (GIF or asciinema).
2. **Build:** no framework unless it pays for itself. A single `uvx`-run static generator is
   acceptable.
3. **Portfolio:** Rithik's portfolio (`~/portfolio`, daemon-vi.github.io) is a
   separate project. Tell him to link Cadre from it; don't edit it from here.

## D6 — Desktop app (only if Rithik says yes at this gate)

- A Tauri 2 shell (`desktop/`) around the dashboard, with the PyInstaller engine from D1 as its
  sidecar.
- Installers for Windows, macOS and Linux built in CI only. They are unsigned, so document the
  security warnings; buying signing costs money and needs his decision.
- If he says no, record that and skip it.

## D7 — Hosted website

This is not part of this programme. It stays behind ROADMAP M12 (sandboxed checks) and M13 (user
accounts). A public server would run strangers' model-written code and hold their keys. Record
that in the ROADMAP.

## Every step

1. Write the tests first.
2. Get `uv run pytest -q` and ruff green, plus the front end's own checks (`tsc`, `eslint`,
   `vsce package`).
3. Exercise the step for real and record what you observed.
4. Update `PROJECT_STATE.md`, `ROADMAP.md`, `TEST_PLAN.md`, `README.md` and `CHANGELOG.md`.
5. Commit as `Cadre D<n>: <what changed>` and report in at most 10 lines.

## Release: v1.1.0

- **Contents:** everything from D1–D4 that is done. Also D5 if it's done.
- **Before tagging:**
  - run `claim-auditor` over the README, the docs site and `PROJECT_STATE.md`;
  - install each front end once from its **public** channel in a clean environment (a fresh
    venv or a CI runner) and record the result.
- **Then:** tag `v1.1.0`. The release workflow publishes, and only to channels Rithik has
  already approved.
- **Last:** update "Where to pick up" with the next step, which is ROADMAP M12 (sandboxed
  checks) unless he says otherwise.

## Report at the end

Keep the report under 15 lines:
- each channel, whether it is live, and its URL;
- what was verified on each host;
- what failed, and why;
- what still needs Rithik (tokens, approvals, the D6 decision);
- what comes next.
