import asyncio

import pytest

from cadre.config import Home
from cadre.engine import RunOptions
from cadre.providers import ProviderError
from cadre.runs import RunManager
from cadre.store import Store

from .conftest import by_agent, two_family_router

SEQ_ORG = """
name: seq
agents: [{id: a1, role: first}, {id: a2, role: second}]
workflow:
  - {agent: a1, task: one}
  - {agent: a2, task: "two after {prev}"}
"""


@pytest.mark.parametrize("org,goal", [
    ("software-team", "a word counter"),
    ("decision-board", "Should we open an office in Pune?"),
    ("startup-company", "A note-taking app for students"),
    ("research-desk", "solid-state batteries"),
])
async def test_every_template_completes_in_demo_mode(home, org, goal):
    m = RunManager(home)
    rid = m.create(org, goal, RunOptions(allow_exec=True, auto_approve=True), demo=True)
    run = await m.execute(rid)
    assert run["status"] == "succeeded", run["error"]
    assert run["summary"]["calls"] > 0 and run["summary"]["files"] > 0
    kinds = {e["kind"] for e in m.store.events(rid, limit=10_000)}
    assert {"run.started", "agent.call", "run.finished"} <= kinds


async def test_resume_reuses_finished_steps_and_does_not_rebill(home):
    script = by_agent({"a1": ["first result"], "a2": [ProviderError("provider fell over")]})
    router, a, b = two_family_router(script)
    m = RunManager(home, router=router)
    rid = m.create("", "goal", org_yaml=SEQ_ORG)
    run = await m.execute(rid)
    assert run["status"] == "failed" and "fell over" in run["error"]
    calls_before = len(a.calls) + len(b.calls)
    script.queues["a2"].append("second result")
    assert m.resumable(rid)
    run = await m.execute(rid)
    assert run["status"] == "succeeded" and run["result"] == "second result"
    assert len(a.calls) + len(b.calls) == calls_before + 1  # only a2 ran again
    a2_prompt = (a.calls + b.calls)[-1]["messages"][1].content
    assert "two after first result" in a2_prompt
    assert any(e["data"].get("resumed") for e in m.store.events(rid, kinds=("run.started",)))


async def test_refused_execution_leaves_work_unapproved(home):
    org = """
name: t
agents: [{id: eng, role: e}]
checks: [{name: tests, command: ["{python}", "-c", "print(1)"]}]
workflow: {builder: eng, checks: [tests], max_rounds: 1, task: build}
"""

    async def deny(kind, prompt, agent):
        assert kind == "exec" and "Model-written code will run as you" in prompt
        return False, ""

    router, *_ = two_family_router(by_agent({"eng": ["built"]}))
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml=org)
    run = await m.execute(rid, approver=deny)
    assert run["status"] == "unapproved"
    check = m.store.events(rid, kinds=("check.finished",))[0]["data"]
    assert check["passed"] is False and "not approved" in check["note"]


async def test_cancel_from_another_process_stops_a_waiting_run(home):
    org = "name: t\nagents: [{id: a, role: r}]\nworkflow: [{approval: 'go?'}, {agent: a, task: x}]\n"
    router, *_ = two_family_router(by_agent({"a": ["never"]}))
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml=org)
    task = asyncio.create_task(m.execute(rid))
    other = RunManager(Home(home.root), Store(home.db_path))  # a second process, in effect
    for _ in range(300):
        if m.store.approvals(rid, pending_only=True):
            break
        await asyncio.sleep(0.01)
    assert m.store.get_run(rid)["status"] == "waiting"
    assert other.cancel(rid) is True
    run = await asyncio.wait_for(task, 10)
    assert run["status"] == "cancelled"
    assert m.store.approvals(rid)[0]["status"] == "cancelled"


async def test_no_configured_model_fails_with_directions(home):
    m = RunManager(home)
    rid = m.create("decision-board", "anything")
    run = await m.execute(rid)
    assert run["status"] == "failed"
    assert "cadre provider add" in run["error"] and "--demo" in run["error"]


def test_stale_active_runs_are_marked_interrupted(home):
    store = Store(home.db_path)
    rid = store.create_run("o", "y", "g", {})
    store.update_run(rid, status="running")
    store._x("UPDATE runs SET updated = updated - 1000 WHERE id=?", (rid,))
    assert store.mark_stale_interrupted() == [rid]
    assert store.get_run(rid)["status"] == "interrupted"
