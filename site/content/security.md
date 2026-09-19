---
title: Security model
description: What Cadre runs as you, where keys live, how the local API is locked, and why no front end can approve model-written code.
---

# Security model

Cadre is a local tool. Everything it does runs with **your** permissions on **your** machine. This
page says what that means, and where the lines are. The decisions behind it are the ADRs in
[`docs/ARCHITECTURE.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/ARCHITECTURE.md).

## Checks run as you, unless you put them in a container

A *check* is a command declared in the org file, such as `python -m pytest -q`. Checks decide
whether work passes, and by default they **run code the agents wrote, as you, on your machine.
That is not a sandbox.**

What does hold (ADR-006):

- **A model never supplies a command.** `run_check` takes the *name* of a check; the command comes
  from the org file (or, in project mode, from `.cadre/checks.yaml` as committed at the base commit,
  which agents cannot edit). There is no "run this shell command" tool.
- Checks run in the run's workspace with a timeout, a 16 KB output cap, and an environment
  stripped of anything named like a key, token, secret or password (loaded key values are removed
  too).
- They need `--allow-exec`, or your explicit approval before the first one runs.

### Checks in a container (since M12)

A check can say `runner: docker` or `runner: podman` and name an image pinned by digest
(`name@sha256:…`). It then runs in a throwaway container:

- **Only the run's workspace is mounted**, at `/work`. Your home folder, Cadre's own state, your
  repository's `.git` and the Docker socket are not.
- **No network** (`--network none`), a **read-only** root filesystem with a small `/tmp`, **every
  capability dropped**, no privilege escalation, and a **non-root user**.
- **Capped** processes, memory (no swap) and CPU, set per check; the timeout kills the container.
- **Nothing from your environment** except the variables the check names in `env:`, passed by
  name (Cadre adds only `HOME=/tmp` and `PYTHONDONTWRITEBYTECODE=1`); names that look like keys
  are refused. The image's own variables are present.
- **Cadre never pulls an image.** A missing one fails the check with the exact `docker pull` to run.

Checked with real containers under Docker and Podman in CI: a check could not reach the network,
write outside `/work`, read a key planted in the host's environment, fork past its process limit
or allocate past its memory cap, and one that outlived its timeout was killed.

**What a container does not protect against** (ADR-031):

- It **shares the host's kernel**. A kernel or runtime exploit escapes it. With Docker on Linux
  that means root; Docker Desktop on Windows and macOS adds a VM in between.
- **The workspace is writable.** A check can change anything in it, including files you might
  later run yourself (a `Makefile`, a `conftest.py`, an editor task). Review the branch before you
  run anything from it. In project mode the worktree's `.git` **file** is in the workspace too; a
  check that rewrites it once made Cadre's own `git commit` (which runs on the host) execute a
  planted command. That is **fixed**: Cadre's git ignores the workspace's `.git`, restores it, and
  fails a check that changed it (ADR-031). Nothing here merges itself — you review the branch.
- Anything already in the workspace, such as a committed `.env`, is readable.
- The image is trusted: a digest pins it, but does not make it safe. Disk use is not capped.

By default a container check still asks for your approval, and the prompt says where each check
runs. `--allow-container-exec` lets container checks run without asking; checks that run as you
still ask. **Don't give an untrusted goal to an org whose checks run as you.**

## Files and your repository

- File tools are confined to the run's workspace. Absolute paths, `..`, symlinks, drive letters,
  device names and alternate data streams are refused.
- In project mode, agents work in a git worktree on a new branch `cadre/<run-id>` and cannot write
  under `.cadre/`. Your working tree, current branch and uncommitted changes are not touched.
- Cadre never merges, pushes, or deletes a branch it did not create.

## Model calls leave your machine

Your prompts and files go to the providers you added. Some free tiers train on prompts (Google AI
Studio's free tier says so). `cadre run … --private` limits a run to providers that say they do not,
records which models it excluded, and fails before its first call if none are left.

## Keys are never stored in files

Keys live in the OS credential store (Windows Credential Manager, macOS Keychain, Secret Service), or
in environment variables (ADR-011). They are never written to `config.yaml`, the database, events,
API responses or logs, and every loaded key is redacted from everything stored. A test plants a key,
echoes it through a mocked error and types it into a goal, then searches the whole database for it.

Never paste a key into an issue, a chat or a bug report. A key that was pasted anywhere public
must be rotated at the provider.

## The local API is a locked door

The dashboard and every front end talk to `cadre serve` (ADR-010):

- It binds to **`127.0.0.1`**.
- Every `/api/v1` call needs a random 256-bit **bearer token** from `~/.cadre/token`, compared in
  constant time. The token never goes in a URL query; the dashboard receives it in the URL
  *fragment*, which browsers never send to a server.
- It **rejects any `Host` header** that is not loopback (a defence against DNS rebinding), except
  the exact names you pass with `--allowed-host`.
- It sends **no CORS headers** and serves a strict Content-Security-Policy. The dashboard inserts
  model-written text with `textContent` only.

## Front ends are clients, not second engines

The MCP server, the GitHub Action and the VS Code extension call the same API (ADR-024). None of
them re-implements routing, quotas or approvals. Each reads the token from `CADRE_HOME/token` when
it needs it, keeps it in memory, and never prints, logs or forwards it: MCP tool results never
contain it, and the extension never passes it to a webview or stores it in settings (ADR-029).

## No approvals over MCP

`cadre mcp` can start and inspect runs, but **no approval of any kind can be granted over MCP**
(ADR-027): neither an *exec* approval (it lets model-written code run) nor a gate. The caller is
itself a model, and a gate that one model can open for another model's work is not a gate. Prompt
injection in the editor's context can call any tool, so the tools can spend your free quota
(bounded by budgets) but cannot approve, add providers or read keys. `cadre_start_run` sets
neither `allow_exec` nor `auto_approve`, so checks wait for a human. (In 1.0.0 and 1.0.1 the tool
forwarded an `allow_exec` argument that skipped the exec approval — a model could run checks
unapproved; fixed in 1.1.0.) A waiting run tells you to use `cadre approve <id>` or the dashboard.

## Who may trigger the GitHub Action

An issue body is untrusted text that becomes the goal (ADR-028), so:

- only `OWNER`, `MEMBER` and `COLLABORATOR` associations can start a run;
- the goal is passed through an environment variable, never interpolated into a shell line;
- the workflow has `contents`, `pull-requests` and `issues` write permissions, nothing else;
- the result is a pull request that a human merges, never a push to the default branch;
- fork pull requests get no secrets and cannot trigger it, and `pull_request_target` is not used.

On the runner, checks run without asking (`allow-exec` defaults to on) because the runner is a
throwaway machine; your keys arrive as repository secrets in environment variables.

## This site

Static pages, a strict Content-Security-Policy (`default-src 'self'`), no cookies, trackers,
external fonts or third-party scripts. The only script is the [replay player](replay.html), which
loads one JSON file from this site and inserts every string with `textContent`.

## Reporting a vulnerability

Please report privately; don't open a public issue. Use GitHub's **private vulnerability
reporting** (*Security → Report a vulnerability* on
[the repository](https://github.com/Daemon-VI/cadre)). Include the version (`cadre --version`),
your OS and the steps to reproduce, and **never include a real API key**, even a revoked one. You
should hear back within 7 days. Full policy:
[`SECURITY.md`](https://github.com/Daemon-VI/cadre/blob/main/SECURITY.md).
