"""`cadre mcp`: Cadre inside AI editors, as an MCP server over stdio (FR-17, ADR-027).

A thin client of the local API (ADR-024): it finds `cadre serve` on 127.0.0.1, starts it detached
when nothing answers (so a run outlives the editor), and calls `/api/v1`. Five tools, because every
schema is replayed in the host's context on every turn. **No approval of any kind can be granted
from here**: the caller is itself a model, and a gate a model can open is not a gate. A waiting run
says to use `cadre approve <id>` or the dashboard. The API token is read from `CADRE_HOME/token` when
a request is made and never appears in a tool result. Nothing may print to stdout: it carries the
protocol.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlparse

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context, ListRoots, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ListRootsResult

from .config import Home

STATUS_EVENTS = 12
RESULT_CHARS = 2000
START_TIMEOUT = 30.0
CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # Windows process-creation flag
DATA_KEYS = ("text", "verdict", "status", "error", "reason", "name", "passed", "runner", "image", "model",
             "tally", "resume_at_ist", "detail", "summary", "branch")


def format_event(e: dict[str, Any]) -> str:
    """One short line per event: a status summary, not a transcript."""
    data = e.get("data") or {}
    bits = [f"{k}={data[k]}" for k in DATA_KEYS if data.get(k) not in (None, "", [], {})]
    who = "/".join(x for x in (e.get("step"), e.get("agent")) if x)
    line = " ".join(x for x in (e.get("kind", "?"), f"[{who}]" if who else "", "; ".join(bits)) if x)
    return line if len(line) <= 200 else line[:197] + "…"


def path_from_uri(uri: str) -> str | None:
    u = urlparse(str(uri))
    if u.scheme != "file":
        return None
    path = unquote(u.path)
    if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # file:///C:/x → C:/x
    return str(Path(path))


class Api:
    """The local Cadre API, started on demand."""

    def __init__(self, home: Home, port: int | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, autostart: bool = True):
        self.home = home
        self.port = port or home.load_config().settings.port
        self.base = f"http://127.0.0.1:{self.port}/api/v1"
        self.transport = transport
        self.autostart = autostart
        self.started_here = False  # this MCP session started `cadre serve` itself

    def _client(self) -> httpx.AsyncClient:
        # the token is read at the moment of use and lives only in this request's headers
        headers = {"Authorization": f"Bearer {self.home.token()}"}
        return httpx.AsyncClient(base_url=self.base, headers=headers, timeout=30.0,
                                 transport=self.transport)

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self.base, timeout=2.0, transport=self.transport) as c:
                return (await c.get("/health")).status_code == 200
        except httpx.HTTPError:
            return False

    def serve_argv(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "serve", "--port", str(self.port)]
        return [sys.executable, "-m", "cadre.cli", "serve", "--port", str(self.port)]

    def start_server(self) -> None:
        """Detached, with its own log: stdout here belongs to the MCP protocol."""
        log_dir = self.home.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "serve.log", "a", encoding="utf-8")  # noqa: SIM115 — handed to the child
        kw: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
                              "env": {**os.environ, "CADRE_HOME": str(self.home.root)}}
        if os.name == "nt":
            # Hosts may run MCP servers inside a Windows job object that kills every descendant when
            # the session ends (observed with Claude Code 2.1.276): break away so runs outlive it.
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                child = subprocess.Popen(self.serve_argv(), creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                                         **kw)
            except OSError:  # the job forbids breakaway: still start, and say it may not outlive
                child = subprocess.Popen(self.serve_argv(), creationflags=flags, **kw)
        else:
            kw["start_new_session"] = True
            child = subprocess.Popen(self.serve_argv(), **kw)
        log.close()
        (log_dir / "serve.pid").write_text(str(child.pid), encoding="utf-8")

    async def ensure(self) -> None:
        if await self.healthy():
            return
        if not self.autostart:
            raise ToolError(f"Cadre is not running on port {self.port}; start it with `cadre serve`.")
        self.start_server()
        self.started_here = True
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if await self.healthy():
                return
        raise ToolError(f"started `cadre serve` but it did not answer on port {self.port} within "
                        f"{START_TIMEOUT:.0f} s; see {self.home.root / 'logs' / 'serve.log'}")

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        await self.ensure()
        async with self._client() as c:
            r = await c.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ToolError(f"Cadre said {r.status_code}: {detail}")
        return r.json()


def workspace_roots(ctx: Context, project: str | None = None) -> ListRoots | ListRootsResult:
    """Ask the host for its workspace folders only when it is needed and the host can answer.

    Roots arrive by the 2026-07-28 multi-round-trip (`ListRoots` marker); the server-initiated
    `roots/list` request has no back-channel there. A host that declares no roots capability, or a
    call that names its project, gets an empty answer instead of an error (-32021)."""
    caps = ctx.client_capabilities
    named = project is not None or any(os.environ.get(v) for v in PROJECT_VARS)
    if not named and caps is not None and caps.roots is not None:
        return ListRoots()
    return ListRootsResult(roots=[])


PROJECT_VARS = ("CADRE_PROJECT", "CLAUDE_PROJECT_DIR")


def default_project(roots: ListRootsResult | None, cwd: Path | None = None) -> str | None:
    """Where a run works when the call names no project, first match wins (checked against each
    host's docs on 2026-09-18):
      1. `CADRE_PROJECT` — set it in the host config, e.g. `${workspaceFolder}` in VS Code / Cursor;
      2. `CLAUDE_PROJECT_DIR` — Claude Code sets it for the servers it starts;
      3. the host's first MCP root (VS Code and Claude Code answer; deprecated since 2026-07-28);
      4. the folder this server was started in, when it is inside a git repository."""
    for var in PROJECT_VARS:
        if os.environ.get(var):
            return os.environ[var]
    for root in (roots.roots if roots else []):
        path = path_from_uri(str(root.uri))
        if path:
            return path
    here = (cwd or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / ".git").exists():
            return str(folder)
    return None


def build_server(home: Home, port: int | None = None, api: Api | None = None) -> MCPServer:
    api = api or Api(home, port)
    dashboard = f"http://127.0.0.1:{api.port}/ (open it with `cadre ui`)"
    mcp = MCPServer(
        "cadre",
        log_level="WARNING",  # stderr only; per-request INFO lines are noise in a host's log
        instructions=(
            "Cadre runs a team of AI agents on the user's free model keys. Forecast first, then start "
            "a run and poll its status. You cannot approve anything: when a run waits for approval, "
            "tell the user to run `cadre approve <id>` or use the dashboard."),
    )

    @mcp.tool()
    async def cadre_list_orgs() -> list[dict[str, Any]]:
        """List the organisations (teams) Cadre can run: built-in templates and the user's own."""
        rows = await api.call("GET", "/orgs")
        return [{k: o.get(k) for k in ("name", "source", "valid", "description", "workflow", "checks") if k in o}
                for o in rows]

    @mcp.tool()
    async def cadre_forecast(org: str, goal: str, project: str | None = None) -> dict[str, Any]:
        """Estimate whether a run fits in today's free quota: calls, tokens, waits or days, and the
        basis of the estimate. `project` is a repository path for project mode."""
        body: dict[str, Any] = {"org": org, "goal": goal}
        if project:
            body["project"] = project
        return await api.call("POST", "/forecast", json=body)

    @mcp.tool()
    async def cadre_start_run(org: str, goal: str,
                              roots: Annotated[ListRootsResult, Resolve(workspace_roots)],
                              project: str | None = None) -> dict[str, Any]:
        """Start a run. With project mode (`project-finisher`, or any org given a project), Cadre
        works on a new branch `cadre/<run-id>` in a git worktree and never touches the user's
        working tree. `project` defaults to the editor's workspace folder (a git repository); pass
        "" to build in a fresh workspace instead. Declared checks run only after the user approves
        them. Returns the run id; poll it with cadre_run_status."""
        if project is None:
            project = default_project(roots)
        # never over MCP (ADR-027): the API's allow_exec skips the exec approval, and the caller
        # is a model. 1.0.0 and 1.0.1 forwarded it, so a model could run checks unapproved.
        body: dict[str, Any] = {"org": org, "goal": goal, "allow_exec": False, "auto_approve": False}
        if project:
            body["project"] = project
        started = await api.call("POST", "/runs", json=body)
        out = {"id": started["id"], "project": project or None,
               "next": "call cadre_run_status with this id; approvals are the user's, not yours",
               "dashboard": dashboard}
        if api.started_here and os.name == "nt":
            out["note"] = (
                "Cadre's server was started by this editor session. On Windows the editor stops it "
                "when the session ends; the run then shows as interrupted and `cadre resume "
                f"{started['id']}` continues it without repeating finished steps. To keep runs going "
                "after the editor closes, leave `cadre serve` running yourself.")
        return out

    @mcp.tool()
    async def cadre_run_status(run_id: str) -> dict[str, Any]:
        """A run's status, the latest events, the files it changed, its branch, and anything it is
        waiting for. Approvals cannot be granted here."""
        run = await api.call("GET", f"/runs/{run_id}")
        events = await api.call("GET", f"/runs/{run_id}/events", params={"tail": STATUS_EVENTS})
        pending = [a for a in run.get("approvals", []) if a.get("status") == "pending"]
        result = run.get("result") or ""
        out: dict[str, Any] = {
            "id": run["id"], "org": run.get("org"), "status": run.get("status"),
            "error": run.get("error"), "branch": run.get("branch"),
            "resume_at": run.get("resume_at"), "totals": run.get("totals"),
            "files": [f.get("path") if isinstance(f, dict) else f for f in run.get("files", [])][:50],
            "latest_events": [format_event(e) for e in events],
            "result": result if len(result) <= RESULT_CHARS else result[:RESULT_CHARS] + "…",
        }
        if pending:
            out["waiting_for_approval"] = [
                {"id": a["id"], "kind": a.get("kind"), "prompt": (a.get("prompt") or "")[:600],
                 "how": f"the user runs `cadre approve {a['id']}` (or `--reject`), or uses the dashboard"}
                for a in pending]
        if run.get("branch"):
            out["review"] = f"git log --stat {run['branch']}  (Cadre never merges or pushes)"
        return out

    @mcp.tool()
    async def cadre_usage(days: int = 1) -> dict[str, Any]:
        """Requests and tokens used per provider and model over the last `days`, with each daily
        cap's share, and the models that are cooling down or out of quota right now."""
        ledger = await api.call("GET", "/usage", params={"days": max(1, min(days, 30))})
        quota = await api.call("GET", "/quota")
        blocked = [{"model": f"{q['provider']}/{q['model']}", "cooldown_s": q.get("cooldown_s"),
                    "day_requests": q.get("day_requests"), "resets_in_s": q.get("resets_in_s")}
                   for q in quota if q.get("cooldown_s") or q.get("headroom", 1) <= 0]
        return {"ledger": ledger, "blocked_now": blocked}

    mcp._cadre_api = api  # type: ignore[attr-defined]  # tests stub its calls
    return mcp


def main(port: int | None = None) -> None:
    build_server(Home().ensure(), port).run("stdio")
