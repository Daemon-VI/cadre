"""`cadre mcp` (FR-17, ADR-027): five tools, a thin client of the API, no approvals over MCP."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.types import ListRootsResult, Root  # noqa: E402

from cadre import mcp_server  # noqa: E402
from cadre.api import create_app  # noqa: E402
from cadre.providers import ScriptedProvider  # noqa: E402
from cadre.quota import Limits, QuotaBook  # noqa: E402
from cadre.router import ModelEntry, Router  # noqa: E402
from cadre.runs import RunManager  # noqa: E402

from .conftest import MemorySecrets, by_agent  # noqa: E402

GATED = """
name: gated
description: one step, then a human gate
agents: [{id: a, role: writer}]
workflow:
  - {agent: a, task: "write about {goal}"}
  - approval: "Ship it?"
"""
FIVE = {"cadre_list_orgs", "cadre_forecast", "cadre_start_run", "cadre_run_status", "cadre_usage"}


def wire(home, replies=None):
    """An MCP server whose API is the real FastAPI app, in process."""
    (home.orgs_dir / "gated.yaml").write_text(GATED, encoding="utf-8")
    provider = ScriptedProvider("p", by_agent(replies or {"a": ["drafted"]}))
    router = Router({"p": provider}, [ModelEntry("p", "m", tier="strong", family="f", limits=Limits())],
                    QuotaBook())
    manager = RunManager(home, router=router, secrets=MemorySecrets())
    app = create_app(manager, token=home.token(), resume_every=None)
    api = mcp_server.Api(home, 8765, transport=httpx.ASGITransport(app=app), autostart=False)
    return mcp_server.build_server(home, api=api), manager


def payload(result):
    if getattr(result, "structured_content", None) is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def all_text(result) -> str:
    return json.dumps(result.model_dump(mode="json"))


async def test_exactly_five_tools_and_none_can_approve(home):
    server, _ = wire(home)
    async with Client(server, raise_exceptions=True) as c:
        tools = (await c.list_tools()).tools
    assert {t.name for t in tools} == FIVE
    for t in tools:
        schema = json.dumps(t.input_schema if hasattr(t, "input_schema") else t.inputSchema)
        assert "approve" not in t.name and "auto_approve" not in schema
        # allow_exec skips the exec approval; 1.0.0/1.0.1 let cadre_start_run set it (ADR-027)
        assert "allow_exec" not in schema


async def test_start_run_can_never_skip_the_exec_approval(home, monkeypatch):
    server, _ = wire(home)
    sent = {}

    async def fake_call(method, path, **kw):
        sent.update(kw.get("json") or {})
        return {"id": "r1"}

    monkeypatch.setattr(server_api(server), "call", fake_call)
    async with Client(server) as c:
        await c.call_tool("cadre_start_run", {"org": "software-team", "goal": "x", "project": ""})
        assert sent["allow_exec"] is False and sent["auto_approve"] is False
        sent.clear()
        # a model that asks anyway is refused, or the argument is dropped; either way nothing skips
        r = await c.call_tool("cadre_start_run", {"org": "software-team", "goal": "x", "project": "",
                                                  "allow_exec": True})
    assert r.is_error or sent["allow_exec"] is False


async def test_a_run_waits_for_a_human_and_mcp_cannot_open_the_gate(home):
    server, manager = wire(home)
    token = home.token()
    async with Client(server, raise_exceptions=True) as c:
        orgs = payload(await c.call_tool("cadre_list_orgs", {}))
        assert any(o["name"] == "gated" and o["valid"] for o in orgs)
        started = await c.call_tool("cadre_start_run", {"org": "gated", "goal": "tea", "project": ""})
        rid = payload(started)["id"]
        texts = [all_text(started)]
        for _ in range(100):
            status = await c.call_tool("cadre_run_status", {"run_id": rid})
            if payload(status).get("waiting_for_approval"):
                break
            await asyncio.sleep(0.05)
        texts.append(all_text(status))
        s = payload(status)
        assert s["status"] == "waiting"
        [waiting] = s["waiting_for_approval"]
        assert waiting["kind"] == "gate" and f"cadre approve {waiting['id']}" in waiting["how"]
        assert any("approval.requested" in line for line in s["latest_events"])
        texts.append(all_text(await c.call_tool("cadre_usage", {"days": 1})))
    assert all(token not in t for t in texts)  # AC-17.5
    assert manager.store.get_approval(waiting["id"])["status"] == "pending"
    manager.store.decide(waiting["id"], False, "no")  # let the run finish before the loop closes
    for _ in range(100):
        if manager.store.get_run(rid)["status"] not in ("waiting", "running"):
            break
        await asyncio.sleep(0.05)
    await manager.shutdown()


async def test_start_run_defaults_to_the_hosts_first_root(home, tmp_path, monkeypatch):
    for var in mcp_server.PROJECT_VARS:  # Claude Code sets CLAUDE_PROJECT_DIR for its own children
        monkeypatch.delenv(var, raising=False)
    server, _ = wire(home)
    sent = {}

    async def fake_call(method, path, **kw):
        sent.update(kw.get("json") or {})
        return {"id": "r1"}

    monkeypatch.setattr(server_api(server), "call", fake_call)
    repo = tmp_path / "my repo"

    async def roots(context):
        return ListRootsResult(roots=[Root(uri=repo.as_uri())])

    async with Client(server, raise_exceptions=True, list_roots_callback=roots) as c:
        await c.call_tool("cadre_start_run", {"org": "project-finisher", "goal": "finish it"})
    assert Path(sent["project"]) == repo and sent["auto_approve"] is False

    # a host that shares no roots: the folder the server was started in, if it is a git repository
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    sent.clear()
    async with Client(server, raise_exceptions=True) as c:
        await c.call_tool("cadre_start_run", {"org": "software-team", "goal": "a cli"})
    assert "project" not in sent

    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    monkeypatch.chdir(repo / "src")
    sent.clear()
    async with Client(server, raise_exceptions=True) as c:
        await c.call_tool("cadre_start_run", {"org": "project-finisher", "goal": "finish it"})
        assert Path(sent["project"]) == repo
        sent.clear()
        await c.call_tool("cadre_start_run", {"org": "software-team", "goal": "x", "project": ""})
    assert "project" not in sent  # "" means a fresh workspace, even inside a repository


def server_api(server):
    """The Api instance the tools close over (so a test can stub its transport-level call)."""
    return server._cadre_api


async def test_autostart_starts_serve_when_nothing_answers(home, monkeypatch):
    up = {"on": False}

    def handler(request):
        return httpx.Response(200 if up["on"] else 503, json={"ok": up["on"]})

    api = mcp_server.Api(home, 8799, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api, "start_server", lambda: up.update(on=True))
    await api.ensure()
    assert up["on"]
    assert api.serve_argv()[-3:] == ["serve", "--port", "8799"]


def test_file_uris_become_paths():
    assert mcp_server.path_from_uri("https://example.com/x") is None
    p = mcp_server.path_from_uri(Path.cwd().as_uri())
    assert Path(p) == Path.cwd()


def test_events_are_one_short_line_each():
    line = mcp_server.format_event({"kind": "agent.finished", "step": "s1", "agent": "a",
                                    "data": {"text": "x" * 500, "model": "p/m"}})
    assert line.startswith("agent.finished [s1/a] text=") and len(line) <= 200


def test_project_vars_come_before_roots_and_cwd(tmp_path, monkeypatch):
    for var in mcp_server.PROJECT_VARS:
        monkeypatch.delenv(var, raising=False)
    roots = ListRootsResult(roots=[Root(uri=(tmp_path / "root").as_uri())])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "claude"))
    assert mcp_server.default_project(roots) == str(tmp_path / "claude")
    monkeypatch.setenv("CADRE_PROJECT", str(tmp_path / "explicit"))
    assert mcp_server.default_project(roots) == str(tmp_path / "explicit")
    monkeypatch.delenv("CADRE_PROJECT")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert Path(mcp_server.default_project(roots)) == tmp_path / "root"
    (tmp_path / "root").mkdir()
    assert mcp_server.default_project(None, cwd=tmp_path / "root") is None  # not a repository
