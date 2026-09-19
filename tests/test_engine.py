import asyncio
import json

import pytest

from cadre.engine import (
    RunOptions,
    RunRejected,
    RunStopped,
    tally,
    topo_waves,
    unapproved_steps,
    validate_plan,
)
from cadre.providers import ProviderError
from cadre.types import ChatResponse, ToolCall

from .conftest import by_agent, make_engine, two_family_router


def write(path, content, cid="1"):
    return ChatResponse(tool_calls=[ToolCall(id=cid, name="write_file",
                                             arguments={"path": path, "content": content})])


# --------------------------------------------------------------------------- pure pieces

@pytest.mark.parametrize("votes,rule,winner", [
    ({"a": "A", "b": "A", "c": "B"}, "majority", "A"),
    ({"a": "A", "b": "B"}, "majority", None),
    ({"a": "A", "b": "B", "c": None}, "plurality", None),
    ({"a": "A", "b": "A", "c": "B"}, "supermajority", "A"),
    ({"a": "A", "b": "A", "c": "B", "d": "B"}, "supermajority", None),
    ({"a": "A", "b": "A"}, "unanimous", "A"),
    ({"a": "A", "b": None}, "unanimous", None),
    ({"a": None, "b": "Z"}, "majority", None),
])
def test_tally_rules(votes, rule, winner):
    t = tally(votes, ["A", "B"], rule)
    assert t["winner"] == winner
    assert t["cast"] == sum(1 for v in votes.values() if v in ("A", "B"))


def test_tally_records_abstentions_and_leaders():
    t = tally({"x": "A", "y": "B", "z": None}, ["A", "B", "C"], "majority")
    assert t["abstained"] == ["z"] and t["leaders"] == ["A", "B"] and t["counts"]["C"] == 0


def test_topo_waves_and_cycles():
    tasks = [{"id": "a", "depends_on": []}, {"id": "b", "depends_on": ["a"]},
             {"id": "c", "depends_on": []}, {"id": "d", "depends_on": ["b", "c"]}]
    assert [[t["id"] for t in w] for w in topo_waves(tasks)] == [["a", "c"], ["b"], ["d"]]
    with pytest.raises(ValueError, match="cycle"):
        topo_waves([{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}])


def test_validate_plan_reports_every_problem():
    _, errs = validate_plan({"tasks": [
        {"id": "t1", "title": "x", "assignee": "ghost"},
        {"id": "t1", "title": "", "assignee": "w", "depends_on": ["t9"]},
        {"id": "t3", "title": "z", "assignee": "w", "depends_on": "t3"},
    ]}, ["w"], 2)
    text = "\n".join(errs)
    for part in ("limit is 2", "'ghost' is not one of w", "used twice", "title is empty",
                 "unknown task 't9'", "t3 depends on itself"):
        assert part in text
    tasks, errs = validate_plan({"tasks": [{"title": "only", "assignee": "w"}]}, ["w"], 5)
    assert not errs and tasks[0]["id"] == "t1"
    assert validate_plan([], ["w"], 5)[1]


# --------------------------------------------------------------------------- review loop

REVIEW_ORG = """
name: t
agents:
  - {id: eng, role: engineer, tools: [write_file]}
  - {id: rev, role: reviewer, tools: [read_file]}
checks:
  - {name: tests, command: ["{python}", "check.py"]}
workflow:
  builder: eng
  reviewers: [rev]
  checks: [tests]
  max_rounds: 3
  task: make check.py pass
"""

APPROVE = '{"approve": true, "summary": "looks right", "issues": []}'


async def test_failing_check_blocks_an_approving_reviewer_then_the_fix_passes(tmp_path, store):
    script = by_agent({
        "eng": [write("check.py", "raise SystemExit(1)"), "done",
                write("check.py", "print('ok')", "2"), "fixed"],
        "rev": [APPROVE, APPROVE],
    })
    router, a, b = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, REVIEW_ORG, router, options=RunOptions(allow_exec=True))
    out = await engine.run()
    assert out.data["approved"] is True and out.data["rounds"] == 2
    rounds = [e["data"] for e in store.events(ctx.run_id, kinds=("review.round",))]
    assert rounds[0]["approved"] is False and rounds[0]["gated_by_checks"] is True
    round2_prompt = [c for c in a.calls if "round 2" in c["messages"][1].content]
    assert round2_prompt and "check tests FAILED" in round2_prompt[0]["messages"][1].content
    # the reviewer ran on the other model family
    assert out.data["verdicts"][0]["independent"] is True
    assert all(c["model"] == "big-b" for c in b.calls)


async def test_review_loop_gives_up_after_max_rounds(tmp_path, store):
    reject = '{"approve": false, "summary": "no", "issues": [{"severity": "major", "detail": "wrong"}]}'
    org = REVIEW_ORG.replace("checks: [tests]", "checks: []").replace("max_rounds: 3", "max_rounds: 2")
    script = by_agent({"eng": ["v1", "v2"], "rev": [reject, reject]})
    router, *_ = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, org, router)
    out = await engine.run()
    assert out.data["approved"] is False and out.data["rounds"] == 2
    assert out.data["open_issues"][0]["detail"] == "wrong"
    assert unapproved_steps(store, ctx.run_id) == ["w"]


async def test_unreadable_verdict_counts_as_not_approved(tmp_path, store):
    org = REVIEW_ORG.replace("checks: [tests]", "checks: []").replace("max_rounds: 3", "max_rounds: 1")
    router, *_ = two_family_router(by_agent({"eng": ["v1"], "rev": ["looks good to me", "yes it is fine"]}))
    engine, ctx = make_engine(tmp_path, store, org, router)
    out = await engine.run()
    assert out.data["approved"] is False and out.data["verdicts"][0]["valid"] is False
    assert store.events(ctx.run_id, kinds=("agent.repair",))


# --------------------------------------------------------------------------- council

COUNCIL_ORG = """
name: board
agents:
  - {id: m1, role: finance}
  - {id: m2, role: tech}
  - {id: m3, role: sales}
  - {id: chair, role: chair}
workflow:
  members: [m1, m2, m3]
  chair: chair
  rule: majority
"""


def proposal(rec):
    return json.dumps({"position": f"I like {rec}", "options": ["X", "Y"], "recommendation": rec,
                       "reasoning": "because"})


async def test_council_tie_goes_to_the_chair_and_bad_votes_abstain(tmp_path, store):
    script = by_agent({
        "m1": [proposal("X"), '{"choice": "A", "confidence": 0.9, "reason": "cheaper"}'],
        "m2": [proposal("Y"), '{"choice": "option b", "confidence": 2, "reason": "faster"}'],
        "m3": [proposal("X"), "I vote for the first one", "still prose"],
        "chair": ['{"options": [{"title": "X", "summary": "do x"}, {"title": "Y", "summary": "do y"}]}',
                  '{"choice": "B", "reason": "speed matters most"}', "The board chose Y."],
    })
    router, *_ = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, COUNCIL_ORG, router, goal="X or Y?")
    out = await engine.run()
    d = out.data
    assert d["tally"]["counts"] == {"A": 1, "B": 1} and d["tally"]["abstained"] == ["m3"]
    assert d["winner"] == "B" and d["decided_by"] == "chair"
    assert d["votes"]["m2"]["confidence"] == 1.0
    doc = ctx.workspace.read("DECISION.md")
    assert "# Decision: Y" in doc and "Dissent: m1" in doc and "| m3 | abstained |" in doc
    assert json.loads(ctx.workspace.read("decision.json"))["winner"] == "B"
    # members were spread across model families for their proposals
    fams = [store.get_step(ctx.run_id, f"w/propose/{m}")[1]["family"] for m in ("m1", "m2")]
    assert fams[0] != fams[1]


# --------------------------------------------------------------------------- manager

MANAGER_ORG = """
name: co
agents:
  - {id: boss, role: manager}
  - {id: w1, role: researcher}
  - {id: w2, role: writer}
workflow:
  manager: boss
  workers: [w1, w2]
  task: "{goal}"
"""


async def test_manager_repairs_its_plan_and_skips_dependents_of_a_failed_task(tmp_path, store):
    bad = '{"tasks": [{"id": "t1", "title": "research", "assignee": "ghost"}]}'
    good = json.dumps({"tasks": [
        {"id": "t1", "title": "research", "assignee": "w1"},
        {"id": "t2", "title": "write it up", "assignee": "w2", "depends_on": ["t1"]},
        {"id": "t3", "title": "draft intro", "assignee": "w2"}]})
    script = by_agent({
        "boss": [bad, good, "Final report: t1 failed, so t2 was skipped."],
        "w1": [ProviderError("upstream exploded")],
        "w2": ["intro drafted"],
    })
    router, *_ = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, MANAGER_ORG, router, goal="launch")
    out = await engine.run()
    status = {t["id"]: t["status"] for t in out.data["tasks"]}
    assert status == {"t1": "failed", "t2": "skipped", "t3": "done"}
    assert out.data["approved"] is False
    assert "t1" in ctx.workspace.read("REPORT.md") and "skipped" in ctx.workspace.read("REPORT.md")
    assert json.loads(ctx.workspace.read("plan.json"))["tasks"][0]["assignee"] == "w1"
    assert store.events(ctx.run_id, kinds=("agent.repair",))


async def test_manager_with_reviewer_reviews_each_task(tmp_path, store):
    org = MANAGER_ORG.replace("  - {id: w2, role: writer}", "  - {id: w2, role: writer}\n  - {id: qa, role: qa}") \
                     .replace("workers: [w1, w2]", "workers: [w1, w2]\n  reviewer: qa")
    plan = json.dumps({"tasks": [{"id": "a", "title": "one", "assignee": "w1"}]})
    script = by_agent({"boss": [plan, "all good"], "w1": ["did one"], "qa": [APPROVE]})
    router, *_ = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, org, router)
    out = await engine.run()
    assert out.data["approved"] is True and out.data["tasks"][0]["status"] == "done"
    assert store.get_step(ctx.run_id, "w/task/a/r1/review/qa") is not None


# --------------------------------------------------------------------------- control

async def test_budget_stops_the_run_and_names_the_budget(tmp_path, store):
    org = """
name: t
budget: {max_calls: 1}
agents: [{id: a, role: r, tools: [write_file]}]
workflow: {agent: a, task: go}
"""
    router, *_ = two_family_router(by_agent({"a": [write("x.md", "x"), "done"]}))
    engine, _ = make_engine(tmp_path, store, org, router)
    with pytest.raises(RunStopped, match="model-call budget"):
        await engine.run()


async def test_tool_outside_the_agents_list_is_refused(tmp_path, store):
    org = "name: t\nagents: [{id: a, role: r, tools: [read_file]}]\nworkflow: {agent: a, task: go}\n"
    router, *_ = two_family_router(by_agent({"a": [write("x.md", "x"), "gave up"]}))
    engine, ctx = make_engine(tmp_path, store, org, router)
    await engine.run()
    ev = store.events(ctx.run_id, kinds=("agent.tool",))[0]["data"]
    assert ev["ok"] is False and "not available to you" in ev["result"]
    assert not (ctx.workspace.root / "x.md").exists()


APPROVAL_ORG = """
name: t
agents: [{id: a, role: r}]
workflow:
  - approval: "ship {goal}?"
  - {agent: a, task: go}
"""


async def test_approval_gate_waits_for_a_decision_in_the_store(tmp_path, store):
    router, *_ = two_family_router(by_agent({"a": ["shipped"]}))
    engine, ctx = make_engine(tmp_path, store, APPROVAL_ORG, router, goal="v1")
    task = asyncio.create_task(engine.run())
    for _ in range(200):
        pending = store.approvals(ctx.run_id, pending_only=True)
        if pending:
            break
        await asyncio.sleep(0.01)
    assert pending[0]["prompt"] == "ship v1?"
    store.decide(pending[0]["id"], True, "go ahead")
    out = await asyncio.wait_for(task, 5)
    assert out.text == "shipped"


async def test_rejected_gate_stops_the_run(tmp_path, store):
    async def no(kind, prompt, agent):
        return False, "not today"

    router, *_ = two_family_router(by_agent({}))
    engine, _ = make_engine(tmp_path, store, APPROVAL_ORG, router, approver=no)
    with pytest.raises(RunRejected, match="not today"):
        await engine.run()


async def test_a_refused_exec_approval_is_not_asked_again_in_the_same_run(tmp_path, store):
    """After the operator refuses execution, later run_check calls in the same run fail
    immediately without asking again (ADR-035). Only a new run asks."""
    asked = 0

    async def refuse(kind, prompt, agent):
        nonlocal asked
        if kind == "exec":
            asked += 1
        return False, "not this time"

    router, *_ = two_family_router(by_agent({}))
    _, ctx = make_engine(tmp_path, store, REVIEW_ORG, router, approver=refuse)
    first = await ctx.run_check("tests", "eng", "s")
    second = await ctx.run_check("tests", "eng", "s")
    assert asked == 1                          # asked once, not once per check
    assert not first.passed and not second.passed
    assert "refused earlier in this run" in second.note
    assert "new run" in second.note


async def test_parallel_join_and_named_outputs(tmp_path, store):
    org = """
name: t
agents: [{id: a, role: r}, {id: b, role: r}, {id: j, role: editor}, {id: f, role: final}]
workflow:
  - id: both
    type: parallel
    join: j
    steps:
      - {agent: a, title: left, task: left}
      - {agent: b, title: right, task: right}
  - {agent: f, task: "polish: {out.both}"}
"""
    router, *_ = two_family_router(by_agent({"a": ["L"], "b": ["R"], "j": ["L+R"], "f": ["final"]}))
    engine, ctx = make_engine(tmp_path, store, org, router)
    out = await engine.run()
    assert out.text == "final"
    join_prompt = store.events(ctx.run_id, kinds=("agent.start",))
    assert any(e["agent"] == "f" and "polish: L+R" in e["data"]["task"] for e in join_prompt)


async def test_truncated_replies_are_recorded(tmp_path, store):
    org = "name: t\nagents: [{id: a, role: r}]\nworkflow: {agent: a, task: go}\n"
    cut = ChatResponse(content='{"options": [{"title": "half', finish_reason="length")
    router, *_ = two_family_router(by_agent({"a": [cut]}))
    engine, ctx = make_engine(tmp_path, store, org, router)
    await engine.run()
    ev = store.events(ctx.run_id, kinds=("agent.truncated",))
    assert ev and ev[0]["agent"] == "a" and ev[0]["data"]["max_tokens"] == 1500
    call = store.events(ctx.run_id, kinds=("agent.call",))[0]["data"]
    assert call["finish"] == "length"


# --------------------------------------------------------------------------- seen live (M5)

def test_deliverables_are_read_from_the_real_template_tasks():
    from cadre.agent import deliverables

    assert deliverables("Analyse the market for: X. Write market.md.") == ["market.md"]
    assert deliverables("Merge market.md, tech.md and risks.md into BRIEF.md (under 800 words)") == ["BRIEF.md"]
    assert deliverables("Write SPEC.md for this goal: a CSV tool") == ["SPEC.md"]
    assert deliverables("Implement SPEC.md: the code in app.py and the tests in test_app.py.") == []


ANALYST_ORG = """
name: desk
agents: [{id: market, role: analyst, tools: [write_file]}]
workflow: {agent: market, task: "Analyse the market for: X. Write market.md."}
"""
REPORT = "# Market Analysis\n\n" + "A long market analysis paragraph. " * 12


async def test_an_answer_that_should_have_been_a_file_is_nudged_then_saved(tmp_path, store):
    # 2026-09-17: gpt-oss-20b answered with the whole report as text and never called write_file
    router, *_ = two_family_router(by_agent({"market": [REPORT, REPORT]}))
    engine, ctx = make_engine(tmp_path, store, ANALYST_ORG, router)
    out = await engine.run()
    assert store.events(ctx.run_id, kinds=("agent.nudged",))[0]["data"]["missing"] == ["market.md"]
    saved = store.events(ctx.run_id, kinds=("agent.deliverable_saved",))
    assert saved and saved[0]["data"]["path"] == "market.md"
    assert ctx.workspace.read("market.md").startswith("# Market Analysis")
    assert out.data["files"] == ["market.md"]


async def test_a_nudged_agent_that_writes_the_file_is_not_overridden(tmp_path, store):
    router, *_ = two_family_router(by_agent({"market": [
        REPORT, write("market.md", "# written by the agent\n"), "saved it"]}))
    engine, ctx = make_engine(tmp_path, store, ANALYST_ORG, router)
    await engine.run()
    assert ctx.workspace.read("market.md") == "# written by the agent\n"
    assert not store.events(ctx.run_id, kinds=("agent.deliverable_saved",))


async def test_repeated_identical_reads_are_not_executed_again(tmp_path, store):
    # 2026-09-17: gpt-oss-120b called list_files eight times in a row and ran out of turns
    org = "name: t\nagents: [{id: ed, role: editor, tools: [list_files]}]\nworkflow: {agent: ed, task: look}\n"
    ls = ChatResponse(tool_calls=[ToolCall(id="l", name="list_files", arguments={})])
    router, a, _ = two_family_router(by_agent({"ed": [ls, ls, ls, "done"]}))
    engine, ctx = make_engine(tmp_path, store, org, router)
    await engine.run()
    assert len(store.events(ctx.run_id, kinds=("agent.tool",))) == 1
    assert len(store.events(ctx.run_id, kinds=("agent.repeat",))) == 2
    last_tool_msg = [m for m in a.calls[-1]["messages"] if m.role == "tool"][-1]
    assert "already called list_files" in last_tool_msg.content


async def test_a_review_avoids_every_family_the_builder_used(tmp_path, store):
    # 2026-09-17: the editor worked on gpt-oss and answered on gemini; the checker then avoided
    # only gemini and reviewed on gpt-oss while the run said "independent"
    from cadre.providers import ScriptedProvider
    from cadre.quota import Limits, QuotaBook
    from cadre.router import ModelEntry, Router

    script = by_agent({"eng": [write("x.md", "x"), "done"], "rev": [APPROVE]})
    p = ScriptedProvider("p", script)
    models = [ModelEntry("p", "a", tier="strong", family="alpha", priority=1, limits=Limits(rpm=1)),
              ModelEntry("p", "b", tier="strong", family="beta", priority=2, limits=Limits()),
              ModelEntry("p", "c", tier="strong", family="gamma", priority=3, limits=Limits())]
    router = Router({"p": p}, models, QuotaBook(), max_wait=1)
    org = REVIEW_ORG.replace("checks: [tests]", "checks: []").replace("max_rounds: 3", "max_rounds: 1")
    engine, ctx = make_engine(tmp_path, store, org, router)
    out = await engine.run()
    used = [c["model"] for c in p.calls]
    assert used[:2] == ["a", "b"]           # alpha is out of requests/min, so the builder moved on
    assert used[2] == "c"                   # the reviewer avoided alpha and beta
    assert out.data["verdicts"][0]["independent"] is True


async def test_reviewers_see_the_written_files_inline(tmp_path, store):
    # 2026-09-17: live QA spent most of its calls re-reading deliverables with read_file
    org = REVIEW_ORG.replace("checks: [tests]", "checks: []").replace("max_rounds: 3", "max_rounds: 1")
    script = by_agent({"eng": [write("plan.md", "# Plan\nship it in two weeks\n"), "done"],
                       "rev": [APPROVE]})
    router, a, b = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, org, router)
    await engine.run()
    review_prompt = b.calls[0]["messages"][1].content
    assert "--- plan.md ---" in review_prompt and "ship it in two weeks" in review_prompt
