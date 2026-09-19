---
title: GitHub Action
description: Label an issue `cadre` or comment `/cadre <goal>`, and Cadre works on the repository and opens a pull request with its report and usage.
---

# GitHub Action

Label an issue `cadre`, or comment `/cadre <goal>` on one. Cadre posts a forecast on the issue, runs
`project-finisher` on the checkout, pushes a branch `cadre/<run-id>`, and opens a pull request whose
body is its report plus a usage table. A human merges it, or doesn't.

> **Status: tested on a real repository (2026-09-19).** On
> [`Daemon-VI/cadre-action-demo`](https://github.com/Daemon-VI/cadre-action-demo), a half-finished
> package with 5 of 7 tests failing, a `/cadre` comment led to
> [pull request #3](https://github.com/Daemon-VI/cadre-action-demo/pull/3): 18 calls and 42,936
> tokens on free Gemini and Groq keys, about 75 seconds, with the review done by a different
> model family from the engineer and all 7 tests passing on the branch. On Groq alone the same job
> took about 7 minutes ([#2](https://github.com/Daemon-VI/cadre-action-demo/pull/2)). The first
> two tries failed and exposed four bugs, all now fixed. Since 1.0.0 the demo uses `Daemon-VI/cadre@v1`,
> and its first run through that tag opened [#5](https://github.com/Daemon-VI/cadre-action-demo/pull/5).
> After `v1` moved to 1.0.1, issue #6 opened [#7](https://github.com/Daemon-VI/cadre-action-demo/pull/7),
> with a one-line title and `Closes #6` at the end of its body.

## Set it up

1. Copy [`examples/github-action/cadre.yml`](https://github.com/Daemon-VI/cadre/blob/main/examples/github-action/cadre.yml)
   to `.github/workflows/cadre.yml` in your repository.
2. Add at least one free key under **Settings → Secrets and variables → Actions**, for example
   `GROQ_API_KEY` ([console.groq.com](https://console.groq.com/keys)) and `GEMINI_API_KEY`
   ([aistudio.google.com](https://aistudio.google.com/apikey)). `OPENROUTER_API_KEY` works too.
3. Turn on **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create
   and approve pull requests**. Without it, the run still pushes its branch, but opening the pull
   request fails.
4. Optionally commit `.cadre/checks.yaml`, so your own tests gate the work:

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
   no keychain on a runner). Each key is checked first, and one the provider rejects is skipped
   with "skipped gemini: its key was rejected; check the secret" in the log.
3. Posts the forecast on the issue. Every job starts with an empty usage ledger, so "left today"
   is the providers' full free limits: it can't see what your laptop, or an earlier job, has already
   spent against the same key. A provider that runs out mid-run still parks it correctly.
4. Runs the org on the checkout in project mode. The goal travels in an environment variable and is
   never pasted into a shell line. Gates are approved automatically (`--yes`), and with
   `allow-exec: true` (the default) checks run without asking, because the runner is a throwaway
   machine. The pull request is the human gate. Checks run as the job's user by default, so a
   check can reach the network and, probably, use the git credentials that `actions/checkout`
   persists by default (the action's own `git push` relies on them; not tested from inside a
   check). If the checks come from a repository you don't fully trust, declare them with
   `runner: docker` in `.cadre/checks.yaml` (Ubuntu runners have Docker; pull the pinned image in
   an earlier step, since Cadre never pulls): then the check itself has neither.
5. If the run succeeded or finished unapproved *and made at least one commit*, pushes
   `cadre/<run-id>` and opens a pull request against the default branch (or `base`). Commits are
   attributed to the person who asked. Unapproved means a check or reviewer never passed the
   work: review it with extra care.
6. Prints the run's whole timeline into the job log (group "Cadre timeline"; GitHub masks secrets
   there) and keeps `REPORT.md` and `plan.json` as a workflow artifact for 14 days.
7. Comments the outcome on the issue, including when the run did not start or failed. Without a
   pull request, the comment carries the report and the usage table itself. A parked run
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
