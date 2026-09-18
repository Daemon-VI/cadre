"""Multi-day runs with a fake clock (FR-9, ADR-017)."""

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cadre import scheduler
from cadre.api import create_app
from cadre.providers import ProviderError, ScriptedProvider
from cadre.quota import Limits, QuotaBook
from cadre.router import ModelEntry, Router
from cadre.runs import RunManager
from cadre.types import ChatResponse, Usage

from .conftest import by_agent

THREE = """
name: three
agents: [{id: a, role: r}, {id: b, role: r}, {id: c, role: r}]
workflow:
  - {agent: a, task: one}
  - {agent: b, task: two}
  - {agent: c, task: three}
"""


class Clock:
    def __init__(self, iso: str):
        self.wall = datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp()
        self.mono = 1_000.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


def router_for(provider, clock: Clock, limits: Limits, **kw) -> Router:
    book = QuotaBook(clock=lambda: clock.mono, wall=lambda: clock.wall)
    return Router({"p": provider}, [ModelEntry("p", "m", tier="strong", family="f", limits=limits)],
                  book, **kw)


async def test_daily_limit_parks_then_resumes_without_rebilling(home):
    clock = Clock("2026-09-17T22:00:00")
    provider = ScriptedProvider("p", by_agent({"a": ["one done"], "b": ["two done"], "c": ["three done"]}))
    m = RunManager(home, router=router_for(provider, clock, Limits(rpd=2)), wall=lambda: clock.wall)
    rid = m.create("", "a three-step job", org_yaml=THREE)

    run = await m.execute(rid)
    assert run["status"] == "parked", run["error"]
    assert "daily limit of 2 requests" in run["error"]
    # midnight UTC is two hours away, plus 30-120 s of jitter
    assert 7200 + 30 <= run["resume_at"] - clock.wall <= 7200 + 120
    parked = m.store.events(rid, kinds=("run.parked",))[0]["data"]
    assert parked["blocks"][0]["model"] == "p/m" and parked["resume_at_ist"].endswith("IST")
    assert len(provider.calls) == 2

    assert m.due() == [] and m.resumable(rid)
    clock.advance(run["resume_at"] - clock.wall + 1)
    assert m.due() == [rid]
    [done] = await m.resume_due()
    assert done["status"] == "succeeded" and done["result"] == "three done"
    assert len(provider.calls) == 3  # steps one and two were not billed again
    assert m.store.usage_totals(rid)["calls"] == 3
    assert done["resume_at"] is None


async def test_minute_limits_still_wait(home):
    clock = Clock("2026-09-17T10:00:00")

    async def sleep(seconds):
        clock.advance(seconds)

    provider = ScriptedProvider("p", by_agent({"a": ["1"], "b": ["2"], "c": ["3"]}))
    m = RunManager(home, router=router_for(provider, clock, Limits(rpm=1), sleep=sleep))
    rid = m.create("", "g", org_yaml=THREE)
    run = await m.execute(rid)
    assert run["status"] == "succeeded"
    assert len(m.store.events(rid, kinds=("route.wait",))) == 2
    assert not m.store.events(rid, kinds=("run.parked",))


async def test_budgets_accumulate_across_resumes(home):
    org = THREE.replace("name: three", "name: three\nbudget: {max_calls: 2}")
    provider = ScriptedProvider("p", by_agent({
        "a": ["1"], "b": [ProviderError("host down"), "2"], "c": ["3"]}))
    clock = Clock("2026-09-17T10:00:00")
    m = RunManager(home, router=router_for(provider, clock, Limits()))
    rid = m.create("", "g", org_yaml=org)
    assert (await m.execute(rid))["status"] == "failed"      # 1 call used
    run = await m.execute(rid)                                # b uses the 2nd call; c would be the 3rd
    assert run["status"] == "stopped" and "model-call budget reached (2/2 calls)" in run["error"]
    m.add_allowance(rid, calls=1)
    run = await m.execute(rid)
    assert run["status"] == "succeeded" and len(provider.calls) == 4  # 3 answers + 1 failed attempt
    assert m.store.get_run(rid)["active_seconds"] > 0


async def test_max_days_stops_the_run(home):
    provider = ScriptedProvider("p", by_agent({"a": ["1"]}))
    m = RunManager(home, router=router_for(provider, Clock("2026-09-17T10:00:00"), Limits()))
    rid = m.create("", "g", org_yaml=THREE.replace("name: three", "name: three\nbudget: {max_days: 2}"))
    m.store._x("UPDATE runs SET created = ? WHERE id = ?", (time.time() - 3 * 86400, rid))
    run = await m.execute(rid)
    assert run["status"] == "stopped" and "day budget reached" in run["error"]
    assert provider.calls == []


async def test_max_tokens_per_day_parks_until_tomorrow(home):
    reply = ChatResponse(content="done", usage=Usage(prompt_tokens=140, completion_tokens=10))
    provider = ScriptedProvider("p", by_agent({"a": [reply]}))
    m = RunManager(home, router=router_for(provider, Clock("2026-09-17T10:00:00"), Limits()))
    org = THREE.replace("name: three", "name: three\nbudget: {max_tokens_per_day: 100}")
    rid = m.create("", "g", org_yaml=org)
    run = await m.execute(rid)
    assert run["status"] == "parked" and "max_tokens_per_day reached (150/100)" in run["error"]
    assert len(provider.calls) == 1


def test_serve_resumes_due_parked_runs(home):
    provider = ScriptedProvider("p", by_agent({"a": ["1"], "b": ["2"], "c": ["3"]}))
    router = Router({"p": provider}, [ModelEntry("p", "m", tier="strong", family="f", limits=Limits())],
                    QuotaBook())
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml=THREE)
    m.store.update_run(rid, status="parked", resume_at=time.time() - 5)
    app = create_app(m, token="t" * 20, resume_every=0.05)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        for _ in range(100):
            if m.store.get_run(rid)["status"] == "succeeded":
                break
            time.sleep(0.05)
        assert m.store.get_run(rid)["status"] == "succeeded"
        assert c.get("/api/runs").status_code == 401  # still locked while it works


def test_scheduler_command_is_shown_not_run(monkeypatch):
    monkeypatch.delenv("CADRE_HOME", raising=False)
    args = scheduler.install_args(30)
    assert args[:6] == ["schtasks", "/Create", "/SC", "MINUTE", "/MO", "30"]
    assert args[args.index("/TN") + 1] == scheduler.TASK_NAME
    task = args[args.index("/TR") + 1]
    assert task.endswith("-m cadre.scheduled") and "cmd" not in task
    monkeypatch.setenv("CADRE_HOME", r"C:\somewhere\.cadre")
    assert scheduler.resume_command().endswith(r'-m cadre.scheduled "C:\somewhere\.cadre"')
    with pytest.raises(ValueError):
        scheduler.install_args(1)
    assert scheduler.uninstall_args()[:2] == ["schtasks", "/Delete"]


def test_scheduled_entry_point_logs_instead_of_printing(home, capsys):
    from cadre import scheduled

    assert scheduled.main([str(home.root)]) == 0
    log = (home.root / "logs" / "scheduler.log").read_text(encoding="utf-8")
    assert "resume --due" in log and "No parked run is due." in log and log.endswith("exit 0\n")
    assert capsys.readouterr().out == ""  # pythonw has no stdout; nothing may go there


async def test_a_long_model_call_keeps_the_run_alive(home, monkeypatch):
    """A call longer than STALE_AFTER must not let another process mark the run interrupted."""
    import asyncio

    from cadre import runs

    monkeypatch.setattr(runs, "HEARTBEAT_EVERY", 0.02)
    seen = {}

    async def slow(model, messages, tools):
        rid = m.store.list_runs()[0]["id"]
        before = m.store.get_run(rid)["updated"]
        await asyncio.sleep(0.2)  # no events during this call
        seen["advanced"] = m.store.get_run(rid)["updated"] > before
        return "done"

    one = "name: one\nagents: [{id: a, role: r}]\nworkflow:\n  - {agent: a, task: one}\n"
    m = RunManager(home, router=router_for(ScriptedProvider("p", slow), Clock("2026-09-17T10:00:00"),
                                           Limits()))
    rid = m.create("", "g", org_yaml=one)
    assert (await m.execute(rid))["status"] == "succeeded"
    assert seen["advanced"]
