---
title: GitHub Action
description: Label an issue `cadre` or comment `/cadre <goal>`, and Cadre works on the repository and opens a pull request with its report and usage.
---

# GitHub Action

Label an issue `cadre`, or comment `/cadre <goal>` on one. Cadre posts a forecast on the issue, runs
`project-finisher` on the checkout, pushes a branch `cadre/<run-id>`, and opens a pull request whose
body is its report plus a usage table. A human merges it, or doesn't.

> **Status: written, not yet run for real.** The action (`action.yml`) and the example workflow
> exist, but they have not yet been tested on a real repository, and `Daemon-VI/cadre@v1` needs a
> `v1` tag that has not been pushed. Until then, treat this page as the design.

## Set it up

1. Copy [`examples/github-action/cadre.yml`](https://github.com/Daemon-VI/cadre/blob/main/examples/github-action/cadre.yml)
   to `.github/workflows/cadre.yml` in your repository.
2. Add at least one free key under **Settings → Secrets and variables → Actions**, for example
   `GROQ_API_KEY` ([console.groq.com](https://console.groq.com/keys)) and `GEMINI_API_KEY`
   ([aistudio.google.com](https://aistudio.google.com/apikey)). `OPENROUTER_API_KEY` works too.
3. Optionally commit `.cadre/checks.yaml`, so your own tests gate the work:

```yaml
checks:
  - name: tests
    command: ["{python}", "-m", "pytest", "-q"]
```

The heart of the example workflow:

```yaml
on:
  issues:
    types: [labeled]
  issue_comment:
    types: [created]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  cadre:
    if: >-
      (github.event_name == 'issues' && github.event.label.name == 'cadre' &&
       contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.issue.author_association)) ||
      (github.event_name == 'issue_comment' && !github.event.issue.pull_request &&
       startsWith(github.event.comment.body, '/cadre ') &&
       contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      # a step that turns the issue or the comment into the goal, through environment variables
      - uses: Daemon-VI/cadre@v1
        with:
          goal: ${{ steps.goal.outputs.goal }}
          org: project-finisher
          issue-number: ${{ github.event.issue.number }}
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

## Who can start a run

Only the repository's `OWNER`, `MEMBER`s and `COLLABORATOR`s. A stranger's issue or comment does
nothing, so nobody else can spend your keys or steer the agents. Fork pull requests never get
secrets (GitHub's design) and cannot trigger it. The workflow asks for `contents`, `pull-requests`
and `issues` write permissions and nothing else. More on why in the
[security model](security.html#who-may-trigger-the-github-action).

## What it does on the runner

1. Installs Cadre from the action's own source, so the action and the engine are the same version.
2. Registers every free provider whose key is in the environment (`CADRE_NO_KEYRING=1`; there is
   no keychain on a runner).
3. Posts the forecast on the issue.
4. Runs the org on the checkout in project mode. The goal travels in an environment variable and is
   never pasted into a shell line. Gates are approved automatically (`--yes`), and with
   `allow-exec: true` (the default) checks run without asking, because the runner is a throwaway
   machine. The pull request is the human gate.
5. If the run succeeded or finished unapproved, pushes `cadre/<run-id>` and opens a pull request
   against the default branch (or `base`). Commits are attributed to the person who asked.
6. Comments the outcome on the issue, including when the run did not start or failed. A parked run
   comments when it could continue and stops; the action does not resume across jobs yet, so
   re-run it after that time.

## Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `goal` | (required) | What the team should achieve |
| `org` | `project-finisher` | The organisation to run |
| `allow-exec` | `"true"` | Let the org's checks run model-written code |
| `issue-number` | empty | Issue to comment on (forecast, outcome) |
| `base` | the default branch | Branch the pull request targets |
| `github-token` | `github.token` | Token for the comment, the push and the pull request |
| `git-user-name` / `git-user-email` | the person who triggered the run | Commit author |

Outputs: `run-id`, `status` (`succeeded`, `parked`, `unapproved`, `failed`, …), `branch`, `pr-url`.
