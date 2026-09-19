---
title: Cadre: a team of AI agents on free model keys
description: Cadre runs a team of AI agents that builds a new project or finishes an existing one, using only free model API keys.
---

# Cadre

<p class="lede">Cadre runs a team of AI agents that builds a new project or finishes an existing one,
using only free model API keys. It spreads the work across every provider you add, stays inside
each one's rate limits, and parks a run when a day's quota runs out so it can continue after the
reset.</p>

You describe the team in one YAML file: who the agents are, which tools they may use, which checks
decide "done", and how they work together. Programs such as tests decide whether work passes, not
model opinions. Reviewers are routed to a *different* model family from the builder, and votes are
counted by code.

Every number this site quotes was measured on free Groq and Google AI Studio keys and is dated on
[Measured numbers](numbers.html). Parking on a daily limit has so far been verified only with a
fake clock; no live run has parked yet.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and a free key, for example
from [console.groq.com](https://console.groq.com/keys).

```bash
uvx cadre-ai provider add groq
uvx cadre-ai forecast project-finisher "Make the failing tests pass"
uvx cadre-ai run project-finisher "Make the failing tests pass" --project ./repo --allow-exec
git -C repo log --stat cadre/<run-id>
```

1. **Add a key.** You type it at a hidden prompt, and it goes to your OS credential store (Windows
   Credential Manager, macOS Keychain, Secret Service), never to a file. Add more providers
   (`gemini`, `openrouter`, …) for more capacity and for reviews that are genuinely independent.
2. **Forecast.** Cadre answers *fits now*, *fits today after ~N min of waits*, *needs ~N days* or
   *cannot run*, and says what the estimate is based on: measured history, or the template's size
   when there is none.
3. **Run.** `project-finisher` works in a git worktree on a new branch `cadre/<run-id>`. Your
   working tree, current branch and uncommitted changes are left alone, and a dirty tree is
   refused. `--allow-exec` lets the repository's checks (its tests, say) run the code the agents
   wrote **without asking you first. Checks are not sandboxed**: see the
   [security model](security.html).
4. **Review the branch.** Cadre never merges or pushes. The run ends by printing the branch's
   commits, `git diff --stat`, and the commands to review or discard it. Merge it yourself, or
   delete it.

Installed rather than run through `uvx`: `pipx install cadre-ai` or `uv tool install cadre-ai`,
then `cadre …`. Version 1.0.0 is on [PyPI](https://pypi.org/project/cadre-ai/).

No key yet? `uvx cadre-ai run decision-board "Should we open a second office?" --demo` runs an
offline, scripted team. It is plumbing, not intelligence: every answer it writes is marked
`[demo]`.

## Ways to use it

Cadre is one engine (`cadre serve`, a local API at `/api/v1`). Every front end is a thin client of
it, so routing, quotas and approvals exist in one place only.

| Front end | Use it when | Status |
|---|---|---|
| [Command line](cli.html) | You work in a terminal | 1.0.0 on PyPI (`cadre-ai`) |
| [AI editors (MCP)](mcp.html) | Claude Code, VS Code, Cursor, Windsurf or Antigravity should start runs | from PyPI: tools called in Claude Code; found by VS Code's MCP client |
| [GitHub Action](github-action.html) | An issue labelled `cadre` should become a pull request | `uses: Daemon-VI/cadre@v1`; tested on a real repository |
| [VS Code extension](vscode.html) | You want runs, usage and approvals inside the editor | not yet published |
| [Containers and downloads](containers.html) | A server, or a machine without Python | 1.0.0 on GHCR and the GitHub Release |

## What makes it different

- **The scheduler is the product.** Every call is sized and checked against each model's per-minute
  and per-day limits and the provider's own rate-limit headers, then sent to the model that is free
  soonest. A busy model rests and the work moves elsewhere.
- **Checks gate, reviewers advise.** A reviewer can reject working code. It cannot approve failing
  code.
- **Votes are counted by code.** In a council, the chair writes the memo from the tally and cannot
  change it. [Watch one happen](replay.html), replayed from a real run's event log.
- **Nothing is claimed that was not measured.** The [numbers page](numbers.html) links every figure
  to its run, and names what is still unverified.

## Templates

| Template | What it does |
|---|---|
| `software-team` | A PM writes a spec, an engineer builds and tests, an independent reviewer checks it, `compile` and `tests` checks gate it, a writer documents it |
| `decision-board` | CFO, CTO, CMO and a risk officer propose, critique and vote; the CEO chairs, breaks ties and writes the memo |
| `startup-company` | A founder plans a task graph for a researcher, engineer, designer and marketer; a QA lead reviews each deliverable |
| `project-finisher` | Finishes work in an existing repository: the lead plans, the engineer edits, the repository's own checks gate, a reviewer on another family advises, a branch is delivered |
| `research-desk` | Three analysts in parallel, an editor merges, a fact-checker reviews, a human approves |

Copy one to edit it: `cadre org new my-team --from software-team`, then `cadre org validate my-team`.
