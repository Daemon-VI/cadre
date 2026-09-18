---
title: VS Code extension
description: Runs, usage and approvals inside VS Code, and in Antigravity, Cursor and Windsurf through Open VSX. Not yet published.
---

# VS Code extension

> **Not yet published.** The extension lives in
> [`editors/vscode/`](https://github.com/Daemon-VI/cadre/tree/main/editors/vscode) and will be
> published to the VS Code Marketplace and to Open VSX, so Antigravity, Cursor and Windsurf can
> install it too. Nothing on this page has been checked in an editor yet.

## What it is for

A **Runs** tree with status icons, a run view with live events, a status-bar item with today's
usage against the tightest daily limit, and commands to **Forecast**, **Start run on this folder**
(project mode on the workspace root), **Review branch** (diffs of the changed files) and **Add
provider**.

## How it behaves

- **It finds or starts the server.** It asks `GET /api/v1/health` on the configured port (default
  8765). If nothing answers, it starts `cadre serve` (if `cadre` is on PATH) or `uvx cadre-ai serve`,
  detached, and waits until it is healthy. It is a client of the same engine as everything else.
- **The token never reaches the webview.** The extension reads the API token from
  `~/.cadre/token` (or `CADRE_HOME/token`) when it needs it and keeps it in the extension host.
  It is never shown, never written to VS Code settings (Settings Sync would upload it), and never
  passed to a webview. Webviews get data by message, run under a CSP with a nonce, and insert
  model-written text with `textContent` only.
- **Your key never passes through it.** **Add provider** opens the integrated terminal on
  `cadre provider add <id>`, so you type the key into the CLI's hidden prompt.
- **An exec approval is a modal showing the exact command.** Before a check may run code the agents
  wrote, a modal shows the check's name and its exact command from the org file, and nothing runs
  until you click. Gate approvals arrive as notifications.
