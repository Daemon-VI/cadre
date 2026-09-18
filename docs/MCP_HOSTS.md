# Cadre in AI editors (MCP)

_Each snippet was checked against the host's own documentation on 2026-09-18 (links below).
Hosts change their config formats; if one stops working, the linked page is the authority._

`cadre mcp` is an MCP server over stdio with five tools: `cadre_list_orgs`, `cadre_forecast`,
`cadre_start_run`, `cadre_run_status` and `cadre_usage`. It starts `cadre serve` in the background
when it isn't running. **On Windows**, hosts stop that auto-started server when the session
ends, and a run then shows as interrupted until `cadre resume <id>` continues it. Start
`cadre serve` yourself (or let the VS Code extension start it) so runs outlive the editor.

**It can't approve anything.** When a run waits for approval, you approve it yourself with
`cadre approve <id>` or in the dashboard (ADR-027).

It needs the `mcp` extra, so every host launches it with:

```
uvx --from "cadre-ai[mcp]" cadre mcp
```

**Which folder a run works on.** When the model doesn't pass a `project`, Cadre uses the first of
these that applies:

1. `CADRE_PROJECT`, if you set it in the host config (for example to `${workspaceFolder}`);
2. `CLAUDE_PROJECT_DIR`, which Claude Code sets;
3. the host's MCP roots (VS Code and Claude Code provide them; roots are deprecated in the MCP
   2026-07-28 spec but still work);
4. the folder the server was started in, if it is inside a git repository.

Project mode always works on a new branch `cadre/<run-id>` in a git worktree and never touches
your working tree.

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

`.vscode/mcp.json`. Note that the top-level key is `servers`, not `mcpServers`:

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
panel. Windsurf documents neither `cwd` nor a workspace variable, so name the project when you
ask for a run, or set `CADRE_PROJECT` to an absolute path:

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

## Verified

| Host | What was observed | When |
|---|---|---|
| A real stdio client (the MCP SDK's `Client` spawning `cadre mcp`, `tools/mcp_smoke.py`) | Five tools listed. `cadre serve` started detached on a spare port. Orgs, forecast (`cannot_run` with no key) and usage answered. The token appeared in no result, and the server was stopped afterwards | 2026-09-18 |
| Claude Code 2.1.276 (`claude mcp add --scope project` in a fixture repo, then `claude -p` with `--mcp-config .mcp.json`) | Called `cadre_list_orgs` (5 orgs), `cadre_forecast` (`cannot_run`, "no history, estimated from template size", ~18.2 calls / 17,746 tokens) and `cadre_usage` (0 rows). The auto-started `cadre serve` was **killed when the session ended**: Windows job object, see ADR-027. Registration removed afterwards | 2026-09-18 |
| VS Code, Cursor, Windsurf, Antigravity | Unverified in the host itself | — |
