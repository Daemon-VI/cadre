---
title: AI editors (MCP)
description: Let Claude Code, VS Code, Cursor, Windsurf or Google Antigravity start and watch Cadre runs over MCP. They cannot approve anything.
---

# AI editors (MCP)

`cadre mcp` is an MCP server over stdio with five tools: `cadre_list_orgs`, `cadre_forecast`,
`cadre_start_run`, `cadre_run_status` and `cadre_usage`. It starts `cadre serve` in the background
when it isn't running, so runs outlive the editor.

> **It can't approve anything.** When a run waits for an approval, you approve it yourself with
> `cadre approve <id>` or in the dashboard. The caller is itself a model, and a gate that one model
> can open for another model's work is not a gate ([why](security.html#no-approvals-over-mcp)).

The server needs the `mcp` extra, so every host launches it with:

```
uvx --from "cadre-ai[mcp]" cadre mcp
```

Each snippet below was checked against the host's own documentation on 2026-09-18. Hosts change
their config formats; if one stops working, the linked page is the authority.

> **Not on PyPI yet**, so `uvx --from "cadre-ai[mcp]"` cannot resolve today. From a clone, the
> command would be `uv run --project /path/to/cadre --extra mcp cadre mcp`. That form has not been
> tried in any host.

## Which folder a run works on

When the model doesn't pass a `project`, Cadre uses the first of these that applies:

1. `CADRE_PROJECT`, if you set it in the host config (for example to `${workspaceFolder}`);
2. `CLAUDE_PROJECT_DIR`, which Claude Code sets;
3. the host's MCP roots (VS Code and Claude Code provide them; roots are deprecated in the MCP
   2026-07-28 spec but still work);
4. the folder the server was started in, if it is inside a git repository.

`project: ""` forces a fresh workspace instead. Project mode always works on a new branch
`cadre/<run-id>` in a git worktree and never touches your working tree.

## Claude Code

```bash
claude mcp add --transport stdio --scope project cadre -- uvx --from "cadre-ai[mcp]" cadre mcp
```

`--scope project` writes `.mcp.json` at the repository root (Claude Code asks before first use),
`local` (the default) keeps it private to you, and `user` makes it available in every project. The
file form:

```json
{"mcpServers": {"cadre": {"type": "stdio", "command": "uvx", "args": ["--from", "cadre-ai[mcp]", "cadre", "mcp"]}}}
```

Folder: `CLAUDE_PROJECT_DIR` and roots. Source: <https://code.claude.com/docs/en/mcp>.

## VS Code (Copilot agent mode)

`.vscode/mcp.json`. The top-level key is `servers`, not `mcpServers`:

```json
{
  "servers": {
    "cadre": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "cadre-ai[mcp]", "cadre", "mcp"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

`cwd` defaults to the workspace folder anyway. For every workspace, use **MCP: Open User
Configuration**. Sources: <https://code.visualstudio.com/docs/copilot/customization/mcp-servers>,
<https://code.visualstudio.com/docs/agents/reference/mcp-configuration>.

## Cursor

`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for all projects. Cursor documents no
`cwd`, so pass the folder in `env`:

```json
{
  "mcpServers": {
    "cadre": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "cadre-ai[mcp]", "cadre", "mcp"],
      "env": {"CADRE_PROJECT": "${workspaceFolder}"}
    }
  }
}
```

Source: <https://cursor.com/docs/context/mcp>.

## Windsurf

Global only: `~/.codeium/windsurf/mcp_config.json`. Open it from the MCPs icon in the Cascade
panel. Windsurf documents neither `cwd` nor a workspace variable, so name the project when you ask
for a run, or set `CADRE_PROJECT` to an absolute path:

```json
{"mcpServers": {"cadre": {"command": "uvx", "args": ["--from", "cadre-ai[mcp]", "cadre", "mcp"]}}}
```

Source: <https://docs.windsurf.com/windsurf/cascade/mcp> (redirects to docs.devin.ai).

## Google Antigravity

Global `~/.gemini/config/mcp_config.json`, or `.agents/mcp_config.json` in a workspace (CLI and
IDE). In the IDE: **… → MCP Servers → Manage MCP Servers → View raw config**. `cwd` is supported
but has no documented default or variables, so use an absolute path:

```json
{"mcpServers": {"cadre": {"command": "uvx", "args": ["--from", "cadre-ai[mcp]", "cadre", "mcp"], "cwd": "C:\\path\\to\\project"}}}
```

Source: <https://antigravity.google/docs/mcp>.

## What the tools can and cannot do

| Tool | Does |
|---|---|
| `cadre_list_orgs` | Lists the organisations Cadre can run: the built-in templates and your own |
| `cadre_forecast` | Whether a run fits in today's free quota (calls, tokens, waits or days) and the basis of the estimate |
| `cadre_start_run` | Starts a run, in project mode on the folder above by default. It may ask for `allow_exec`, but declared checks still run only once a human approves, and it can never auto-approve |
| `cadre_run_status` | A run's status, its latest events, the files it changed, its branch, and anything it is waiting for |
| `cadre_usage` | Requests and tokens per provider and model, each daily cap's share, and the models cooling down or out of quota |

The tools cannot approve, add providers or read keys, and the API token appears in no result.

## Verified

| Host | What was observed | When |
|---|---|---|
| A real stdio client (the MCP SDK's `Client` spawning `cadre mcp`, `tools/mcp_smoke.py`) | Five tools listed. `cadre serve` started detached on a spare port. Orgs, forecast (`cannot_run` with no key) and usage answered. The token appeared in no result, and the server was stopped afterwards | 2026-09-18 |
| Claude Code, VS Code, Cursor, Windsurf, Antigravity | **Unverified in the host itself.** The package isn't on PyPI yet | — |
