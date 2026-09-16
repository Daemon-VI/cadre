import pytest

from cadre.org import OrgError, find_org_text, load_org_text, render, template_names

BASE = """
name: t
agents:
  - {id: a, role: builder, tools: [write_file]}
  - {id: b, role: reviewer}
"""


def errors(text):
    with pytest.raises(OrgError) as info:
        load_org_text(text)
    return "\n".join(info.value.errors)


def test_every_shipped_template_is_valid():
    names = template_names()
    assert set(names) >= {"software-team", "decision-board", "startup-company", "research-desk"}
    kinds = {load_org_text(find_org_text(n)[1]).workflow.type for n in names}
    assert kinds >= {"council", "sequence"}


def test_shorthand_steps_are_inferred():
    org = load_org_text(BASE + """
workflow:
  - {agent: a, task: go}
  - {builder: a, reviewers: [b], task: build}
  - approval: "ship it?"
""")
    assert [s.type for s in org.workflow.steps] == ["agent", "review_loop", "approval"]


def test_references_are_checked_with_paths():
    text = errors(BASE + """
checks: []
workflow:
  - {agent: ghost, task: x}
  - {builder: a, reviewers: [a], checks: [tests], task: y}
  - {members: [a, nobody], chair: b}
  - {manager: b, workers: [zed], task: "{out.missing}"}
""")
    assert "workflow.steps[0].agent: unknown agent 'ghost'" in text
    assert "cannot review its own work" in text
    assert "unknown check 'tests'" in text
    assert "workflow.steps[2].members[1]: unknown agent 'nobody'" in text
    assert "workflow.steps[3].workers[0]: unknown agent 'zed'" in text
    assert "{out.missing} refers to no step" in text


def test_unknown_tool_and_run_check_without_checks():
    text = errors("""
name: t
agents:
  - {id: a, role: r, tools: [shell, run_check]}
workflow: {agent: a, task: x}
""")
    assert "unknown tool 'shell'" in text and "run_check needs at least one entry" in text


def test_missing_type_is_explained():
    assert "a step needs a type" in errors(BASE + "workflow: [{foo: 1}]")


def test_schema_errors_name_the_field():
    assert "agents.0.id" in errors("name: t\nagents: [{id: 'Bad Id', role: r}]\nworkflow: {agent: x, task: y}")


def test_render_fills_only_known_variables():
    out = render("goal={goal} prev={prev} spec={out.spec} code={'k': 1} {other}",
                 {"goal": "G", "prev": "P", "out.spec": "S"})
    assert out == "goal=G prev=P spec=S code={'k': 1} {other}"
