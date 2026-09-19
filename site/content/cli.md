---
title: Command line
description: Install Cadre, add free model keys, run a team, and manage runs, usage and forecasts from the terminal.
---

# Command line

The command is `cadre`. Everything else on this site (the MCP server, the GitHub Action, the VS Code
extension, the container) calls the same engine.

## Install

| How | Command | Status |
|---|---|---|
| Run without installing | `uvx cadre-ai <command>` | after the first PyPI publish |
| Install as a tool | `uv tool install cadre-ai` or `pip install cadre-ai` | after the first PyPI publish |
| From a clone (works today) | `git clone https://github.com/Daemon-VI/cadre && cd cadre && uv sync`, then `uv run cadre <command>` | works |
| Standalone build, no Python | see [Containers and downloads](containers.html) | 1.0.0 on the GitHub Release |

Cadre needs Python 3.12 or newer (uv installs one for you). `cadre --version` prints the version.
Its state lives in `~/.cadre/` (set `CADRE_HOME` to put it elsewhere): `config.yaml` (providers
and models, never keys), `cadre.sqlite`, the API `token`, your `orgs/`, and one folder per run.

## Add free models

```bash
cadre presets                       # every provider Cadre knows, with free-tier notes
cadre provider add groq             # prompts for the key with input hidden
cadre provider add gemini
cadre provider add openrouter       # discovers the current :free models
cadre provider list
cadre provider test groq
cadre provider key groq             # replace a stored key, e.g. after rotating it
```

The key goes to the OS credential store, never to a file. Environment variables are checked first:
`CADRE_KEY_GROQ`, then the provider's usual name (`GROQ_API_KEY`). On a server, in CI or in a
container with no keychain, set `CADRE_NO_KEYRING=1`, put the keys in environment variables, and
run `cadre provider add-from-env` to register every free provider whose key is present.

Use **one account per provider.** Capacity grows by adding *different* providers; several accounts
at one provider to multiply a free limit breaks their terms. `cadre provider refresh` shows what
each endpoint serves today, and `cadre provider set-model` changes any limit Cadre starts from.

## Run a team

```bash
cadre run software-team "a CSV to Markdown table converter" --allow-exec
cadre run decision-board "Should we open a second office?"
cadre run project-finisher "Make the failing tests pass" --project ./repo --allow-exec
```

| Option | Does |
|---|---|
| `--allow-exec` | Checks run the code the agents wrote without asking first. **Not a sandbox.** Without it, the first check asks you |
| `--project PATH` | Work on an existing git repository, in a worktree on a new branch `cadre/<run-id>` |
| `--base B` | The branch or commit to start from (default `HEAD`) |
| `--allow-dirty` | Start from `HEAD` even if the working tree has uncommitted changes (a dirty tree is refused otherwise) |
| `--private` | Use only providers that say they do not train on prompts. The run fails before its first call if none are left |
| `--yes` | Approve gates and questions automatically |
| `--approve-elsewhere` | Leave approvals to the dashboard or `cadre approve <id>` |
| `--demo` | An offline scripted model; no key needed |
| `--result-json PATH` | Also write the outcome as JSON, for scripts and CI |

Every run gets a workspace at `~/.cadre/runs/<run-id>/workspace/`, and every file version is kept.

## Watch and manage runs

```bash
cadre runs                          # recent runs
cadre show <run-id> --events        # the whole timeline
cadre resume <run-id>               # finished steps are reused, not billed again
cadre cancel <run-id>
cadre approvals                     # what is waiting for you
cadre approve <approval-id>         # or --reject
cadre runs cleanup                  # remove finished worktrees; branches are kept
```

## Will it fit? Usage and forecasts

```bash
cadre forecast software-team "a CSV to Markdown table converter"
cadre usage --days 7
cadre quota
```

The forecast says *fits now*, *fits today after ~N min of waits*, *needs ~N days* or *cannot run*,
and always states its basis: `measured, n = …` once an org has finished runs, or
`no history, estimated from template size` before that. `cadre run` prints it before starting.
Forecasts are rough: see [Measured numbers](numbers.html#limitations).

## Long jobs: parking and resuming

When every model a run can use has hit its *daily* limit, the run is **parked** with the time it
can continue, instead of failing. Nothing already finished is repeated or billed again.

```bash
cadre serve                         # resumes parked runs by itself while it runs
cadre resume --due                  # or resume every run whose reset has passed, once
cadre scheduler install             # or check every 30 minutes from the OS scheduler (asks first)
```

This is verified with a fake clock only; no run has parked live yet. Budgets count everything a run
used across days: `max_days` (default 7) stops a run that has gone on too long, and
`cadre resume <id> --add-calls 20` raises a stopped run's allowance.

## Dashboard

```bash
cadre serve                         # http://127.0.0.1:8765, loopback only
cadre ui                            # opens the browser with the access token in the URL fragment
```

To reach it through a Tailscale name or from a container, add `--allowed-host NAME` (repeatable,
exact names only); any other `Host` is still refused. The dashboard has not yet been checked in a
browser (see [Measured numbers](numbers.html#limitations)).

## Your own organisation

```bash
cadre org list
cadre org new my-team --from software-team
cadre org validate my-team
```

```yaml
name: tiny-team
agents:
  - id: builder
    role: Python engineer
    tools: [list_files, read_file, write_file, run_check]
  - id: reviewer
    role: Code reviewer
    diverse_from: builder          # prefer a different model family
    tools: [list_files, read_file]
checks:
  - name: tests
    command: ["{python}", "-m", "unittest", "discover", "-p", "test_*.py"]
budget: {max_calls: 40, max_tokens: 150000, max_minutes: 20, max_parallel: 2}
workflow:
  - builder: builder
    reviewers: [reviewer]
    checks: [tests]
    max_rounds: 3
    task: "Build {goal} in app.py with tests in test_app.py."
```

A model can only name a check (`run_check("tests")`); the command comes from this file. The full
reference (step types, tools, templates, free providers) is in the
[README](https://github.com/Daemon-VI/cadre#the-organisation-file).
