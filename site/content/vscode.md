---
title: VS Code extension
description: Runs, usage, approvals, memory and a new-run form inside VS Code — and in Antigravity, Cursor and Windsurf through Open VSX.
---

# VS Code extension

> **Published** as [`daemon-vi.cadre-ai`](https://marketplace.visualstudio.com/items?itemName=daemon-vi.cadre-ai)
> on the VS Code Marketplace and on [Open VSX](https://open-vsx.org/extension/daemon-vi/cadre-ai),
> so Antigravity, Cursor, Windsurf and VSCodium can install it too. Install it with
> `ext install daemon-vi.cadre-ai`, or search **Cadre AI** in the Extensions view. (It is
> `cadre-ai` rather than `cadre` because the Marketplace requires globally unique names and both
> `cadre` and `Cadre` were already taken.) Source:
> [`editors/vscode/`](https://github.com/Daemon-VI/cadre/tree/main/editors/vscode).

## What it is for

A **Runs** tree with status icons, a run view with live events, a status-bar item with today's
usage against the tightest daily limit, and commands to **Forecast**, **Start run on this folder**
(project mode on the workspace root), **Review branch** (diffs of the changed files) and **Add
provider**.

Since 1.4.0, five **panels** open as editor tabs:

- **New run…** — one form: organisation, goal, folder (or none), the offline demo model and
  *private*, with a **Forecast** before you spend any quota.
- **Usage** — each model's spend today against its daily caps, as meters that state their value
  and state in words, plus a ledger for the last 1, 7, 14 or 30 days.
- **Approvals** — everything waiting for you; **Decide…** opens the same dialog a notification
  would, so nothing is approved from the page.
- **Memory** — remembered facts by scope: add, delete, and decide a model's proposals.
- **Organisations** — agents, roles, tools, checks and budget; edit your own org's YAML, view a
  template's, or start a run with it.

## How it behaves

- **It finds or starts the server.** It asks `GET /api/v1/health` on the configured port (default
  8765). If nothing answers, it starts `cadre serve` (if `cadre` is on PATH) or `uvx cadre-ai serve`,
  detached, and waits until it is healthy. It is a client of the same engine as everything else.
- **The token never reaches a webview.** The extension reads the API token from
  `~/.cadre/token` (or `CADRE_HOME/token`) when it needs it and keeps it in the extension host.
  It is never shown, never written to VS Code settings (Settings Sync would upload it), and never
  passed to a webview. Webviews get data by message, run under a CSP with a nonce, and insert
  model-written text with `textContent` only.
- **A panel can only ask for a fixed list of things.** Every message a panel sends is checked in
  the extension against a short list of shapes, and each panel may only send the ones it uses. A
  run is started on a workspace folder by its position in the list, never by a path the page
  supplies.
- **Your key never passes through it.** **Add provider** opens the integrated terminal on
  `cadre provider add <id>`, so you type the key into the CLI's hidden prompt.
- **An exec approval shows the exact command, and only when you ask.** Before a check may run code
  the agents wrote, a notification says a run is waiting. It never takes keyboard focus, so a key
  you press in another window can't answer it. **Review…** opens a modal with the check's name and
  its exact command from the org file, and nothing runs until you click **Allow execution**. Gate
  approvals arrive as notifications. No panel and no form can pre-approve running code.
