"""Exercise `cadre mcp` over a real stdio transport, as an editor would (FR-17, D2).

Spawns `cadre mcp` with a throwaway CADRE_HOME and a spare port; the MCP server finds no API and
starts `cadre serve` detached; the script lists the tools and calls the read-only ones, then stops
that server. No key is involved.   uv run python tools/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

PORT = "8798"


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        env = {**os.environ, "CADRE_HOME": str(home), "CADRE_NO_KEYRING": "1"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "cadre.cli", "mcp", "--port", PORT],
                                       env=env, cwd=tmp)
        try:
            async with Client(params, raise_exceptions=True) as c:
                tools = sorted(t.name for t in (await c.list_tools()).tools)
                print("tools:", ", ".join(tools))
                orgs = await c.call_tool("cadre_list_orgs", {})
                names = [o["name"] for o in json.loads(orgs.content[0].text)] if not orgs.structured_content \
                    else [o["name"] for o in orgs.structured_content["result"]]
                print("orgs:", ", ".join(names))
                fc = await c.call_tool("cadre_forecast", {"org": "decision-board", "goal": "Open a second office?"})
                print("forecast:", (fc.structured_content or {}).get("verdict") or fc.content[0].text[:200])
                usage = await c.call_tool("cadre_usage", {"days": 1})
                print("usage: ok" if not usage.is_error else f"usage error: {usage.content[0].text}")
                token = (home / "token").read_text(encoding="utf-8").strip()
                leaked = any(token in json.dumps(r.model_dump(mode="json")) for r in (orgs, fc, usage))
                print("token in any result:", leaked)
        finally:
            pid_file = home / "logs" / "serve.pid"
            if pid_file.exists():
                pid = int(pid_file.read_text())
                print("stopping the auto-started server, pid", pid)
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                await asyncio.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
