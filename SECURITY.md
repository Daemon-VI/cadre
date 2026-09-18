# Security

## What Cadre runs as you

Cadre is a local tool, and everything it does runs with **your** permissions on **your** machine:

- **Checks run code the agents wrote.** A check is a command declared in the org file, such as
  `python -m pytest -q`. The model can only choose a check by name and never supplies a command.
  Checks run in the run's workspace with a timeout, capped output, and an environment stripped of
  anything that looks like a key, token or password. They still run as you and are **not
  sandboxed**, so they need `--allow-exec` or your explicit approval. A container runner is
  roadmap item M12.
- **File tools** are confined to the run's workspace. Absolute paths, `..`, symlinks, drive
  letters, device names and alternate data streams are refused. In project mode, agents work in
  a git worktree on a new `cadre/<run-id>` branch and cannot write under `.cadre/`. Cadre never
  merges, pushes, or deletes a branch it did not create.
- **Model calls** send your prompts and files to the providers you added. `--private` limits a
  run to providers that say they do not train on prompts.

## Keys and the API token

- Keys live in the OS credential store (or environment variables with `CADRE_NO_KEYRING=1`).
  They are never written to `config.yaml`, the database, events, API responses or logs, and
  every loaded key is redacted from everything stored.
- `cadre serve` binds to `127.0.0.1` and requires a random bearer token (`~/.cadre/token`) on
  every `/api/v1` call. It rejects `Host` headers other than loopback and the exact names given
  with `--allowed-host`, sends no CORS headers, and serves a strict Content-Security-Policy.
- Front ends (the MCP server, the VS Code extension, the GitHub Action) are clients of that API.
  None of them can grant an approval that lets model-written code run
  ([ADR-027](docs/ARCHITECTURE.md), [ADR-029](docs/ARCHITECTURE.md)).

## Reporting a vulnerability

Please report privately. Don't open a public issue.

- Use GitHub's **private vulnerability reporting**: *Security → Report a vulnerability* on
  <https://github.com/Daemon-VI/cadre>.
- Include the version (`cadre version`), your OS, and the steps to reproduce.
- **Never include a real API key**, even a revoked one. If you pasted a key anywhere public,
  rotate it at the provider first.

You should hear back within 7 days. Fixes are released as a patch version and credited in
`CHANGELOG.md` unless you ask not to be named.

## Supported versions

Only the latest minor release gets security fixes.
