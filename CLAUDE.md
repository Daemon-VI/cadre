# Cadre — instructions for Claude

A self-hosted platform that runs an organisation of AI agents (builders, reviewers, verifiers,
deciders, managers), declared in YAML, on whichever free model APIs the owner has added.

**Read `docs/PROJECT_STATE.md` before doing anything.** Sessions get cleared; that file, not the
conversation, holds where the project stands. `docs/SRS.md` has the requirements,
`docs/ARCHITECTURE.md` the decisions (ADR-001…), `docs/ROADMAP.md` what is next.

This file is loaded into every turn, so it only holds what a fresh session would otherwise get
wrong.

## Environment

`uv`, Python 3.12+.

```bash
uv sync
uv run cadre --help
uv run pytest -q                 # no network, no key needed
uv run ruff check src tests
```

Ruff: line length 110, target py312.

## Where the state is

The repo is code. A running install's state is **`~/.cadre/`** (override with `CADRE_HOME`):
`config.yaml` (providers and models, never keys), `cadre.sqlite`, `token`, `orgs/`, `runs/<id>/`.
Tests set `CADRE_HOME` to a temp dir and `CADRE_NO_KEYRING=1` so they never touch the real
credential store. `cadre serve` does not hot-reload code.

## Rules that are not up for re-derivation

- **A model never supplies a shell command.** `run_check` takes the *name* of a check declared in
  the org file (ADR-006). Do not add a "run command" tool.
- **Checks gate, reviewers advise** (ADR-005): approval needs every check to pass.
- **Votes are counted by code** (ADR-007); the chair cannot change the tally.
- **Keys never go into files, the database, events, API responses, logs or this transcript.**
  They live in the OS credential store or the environment and are redacted before storage.
  Never print or echo one; a key pasted into chat must be rotated at the provider.
- **The dashboard inserts model-written text with `textContent` only** — no `innerHTML`, no
  inline scripts or styles (the CSP forbids them), no third-party code.
- **Every `/api` call needs the bearer token, and the Host header must be loopback** (ADR-010).
  Do not add CORS or put the token in a URL query.
- **A new tool must earn its tokens.** Schemas are re-sent on every call and free tiers cap
  tokens per minute (Groq: 8,000). Prefer injecting context or extending an existing tool.
- **Presets are dated priors** (`presets.py`, `CHECKED`). Change a number only with a source,
  and update the date and `source` field.
- **Claims are measured.** Live evidence is the M5 and M11 tables in `PROJECT_STATE.md`;
  anything not recorded there is "unverified", not guessed.

## Git

Commits are authored and committed as
`Rithik Krishna <317035893+Daemon-VI@users.noreply.github.com>` (the global identity — don't
override it). Never add a Claude co-author trailer or "Generated with" line. Branch `main`.
Remote `origin` = `https://github.com/Daemon-VI/cadre`, **private** (created 2026-09-18). Commit,
then push `main`. Making it public, and pushing tags, are Rithik's calls — ask first.
A plain `git push` hangs here (Git Credential Manager opens a sign-in window); push with gh's login:
`git -c credential.helper= -c 'credential.helper=!"/c/Program Files/GitHub CLI/gh.exe" auth git-credential' push origin main`.
