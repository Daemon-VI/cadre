"""Project mode against throwaway git repositories (FR-8, NFR-10)."""

import subprocess
from pathlib import Path

import pytest

from cadre.engine import RunOptions
from cadre.project import ProjectError, owner_state
from cadre.providers import ProviderError
from cadre.runs import RunManager
from cadre.types import ChatResponse, ToolCall

from .conftest import by_agent, two_family_router


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "owner-repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    sh(r, "config", "user.name", "Fixture Owner")
    sh(r, "config", "user.email", "fixture-owner@example.invalid")
    sh(r, "config", "commit.gpgsign", "false")
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (r / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
    (r / ".cadre").mkdir()
    (r / ".cadre" / "checks.yaml").write_text(
        'checks:\n  - name: unit\n    command: ["{python}", "-m", "unittest", "-q"]\n    timeout: 60\n')
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "half-built calculator")
    sh(r, "checkout", "-q", "-b", "feature/owner-work")  # the owner is on their own branch
    return r


def call(tool, **args):
    return ChatResponse(tool_calls=[ToolCall(id=f"c-{tool}", name=tool, arguments=args)])


ORG = """
name: fixer
agents:
  - {id: eng, role: engineer, tools: [read_file, edit_file, write_file, run_check]}
  - {id: rev, role: reviewer, tools: [read_file]}
workflow:
  - id: fix
    builder: eng
    reviewers: [rev]
    checks: [all]
    max_rounds: 1
    task: make the tests pass
"""
APPROVE = '{"approve": true, "summary": "correct", "issues": []}'


def test_non_repo_is_refused(home, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ProjectError, match="not a git repository"):
        RunManager(home).create("", "g", org_yaml=ORG, project=str(plain))


def test_dirty_tree_is_refused_unless_allowed(home, repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return 0\n")  # uncommitted owner work
    m = RunManager(home)
    with pytest.raises(ProjectError, match="uncommitted changes"):
        m.create("", "g", org_yaml=ORG, project=str(repo))
    rid = m.create("", "g", org_yaml=ORG, project=str(repo), allow_dirty=True)
    assert any("does not see them" in e["data"]["note"] for e in m.store.events(rid, kinds=("project.dirty",)))
    with pytest.raises(ProjectError, match="not a commit or branch"):
        m.create("", "g", org_yaml=ORG, project=str(repo), allow_dirty=True, base="no-such-branch")


async def test_run_commits_on_its_own_branch_and_leaves_the_owner_alone(home, repo):
    before = owner_state(str(repo))
    script = by_agent({
        "eng": [call("edit_file", path="calc.py", old="return a - b", new="return a + b"),
                call("run_check", name="unit"), "fixed add()"],
        "rev": [APPROVE],
    })
    router, *_ = two_family_router(script)
    m = RunManager(home, router=router)
    rid = m.create("", "make the tests pass", RunOptions(allow_exec=True), org_yaml=ORG, project=str(repo))
    run = await m.execute(rid)
    assert run["status"] == "succeeded", run["error"]

    # NFR-10: HEAD, current branch and status are exactly as the owner left them
    assert owner_state(str(repo)) == before
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"

    branch = f"cadre/{rid}"
    assert run["branch"] == branch
    log = sh(repo, "log", "--format=%s%n%b%n--%an <%ae>", f"main..{branch}")
    assert "cadre(w/0/r1/build): fixed add()" in log
    assert "Co-authored" not in log and "Generated" not in log
    assert "Fixture Owner <fixture-owner@example.invalid>" in log
    assert sh(repo, "show", f"{branch}:calc.py") == "def add(a, b):\n    return a + b\n"

    p = run["summary"]["project"]
    assert "calc.py" in p["diff_stat"] and p["commits"]
    assert branch in p["review"]["discard"] and "worktree remove" in p["review"]["discard"]
    checks = [e["data"] for e in m.store.events(rid, kinds=("check.finished",))]
    assert checks and all(c["passed"] for c in checks)
    worktree = m.store.events(rid, kinds=("project.worktree",))[0]["data"]
    assert worktree["repo_checks"] == ["unit"] and worktree["how"] == "created"


async def test_repo_checks_come_from_the_base_commit(home, repo):
    # the owner edits checks.yaml after committing; the run must use the committed version
    (repo / ".cadre" / "checks.yaml").write_text('checks:\n  - name: evil\n    command: ["whoami"]\n')
    m = RunManager(home)
    rid = m.create("", "g", org_yaml=ORG, project=str(repo), allow_dirty=True)
    checks = m.store.get_run(rid)["options"]["project"]["checks"]
    assert [c["name"] for c in checks] == ["unit"]


async def test_agents_cannot_write_cadre_dir(home, repo):
    script = by_agent({
        "eng": [call("write_file", path=".cadre/checks.yaml", content="checks: []"),
                call("edit_file", path=".cadre/checks.yaml", old="unit", new="x"),
                "tried"],
        "rev": [APPROVE],
    })
    router, *_ = two_family_router(script)
    m = RunManager(home, router=router)
    org = ORG.replace("checks: [all]", "checks: []").replace(", run_check]", "]")
    rid = m.create("", "g", RunOptions(allow_exec=True), org_yaml=org, project=str(repo))
    await m.execute(rid)
    tools = [e["data"] for e in m.store.events(rid, kinds=("agent.tool",))]
    assert [t["ok"] for t in tools] == [False, False]
    assert all("owner's configuration" in t["result"] for t in tools), tools


async def test_resumed_project_run_keeps_its_branch(home, repo):
    org = """
name: two-steps
agents: [{id: a, role: first, tools: [write_file]}, {id: b, role: second, tools: [write_file]}]
workflow:
  - {agent: a, task: one}
  - {agent: b, task: two}
"""
    script = by_agent({
        "a": [call("write_file", path="notes/one.md", content="one\n"), "wrote one"],
        "b": [ProviderError("rate limit storm")],
    })
    router, *_ = two_family_router(script)
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml=org, project=str(repo))
    run = await m.execute(rid)
    assert run["status"] == "failed"
    branch = f"cadre/{rid}"
    assert len(sh(repo, "log", "--format=%h", f"main..{branch}").split()) == 1

    script.queues["b"] += [call("write_file", path="notes/two.md", content="two\n"), "wrote two"]
    run = await m.execute(rid)
    assert run["status"] == "succeeded"
    subjects = sh(repo, "log", "--format=%s", f"main..{branch}").splitlines()
    assert subjects == ["cadre(w/1): wrote two", "cadre(w/0): wrote one"]
    how = [e["data"]["how"] for e in m.store.events(rid, kinds=("project.worktree",))]
    assert how == ["created", "reused"]


async def test_engine_records_stay_out_of_the_repo_and_cleanup_keeps_the_branch(home, repo):
    org = """
name: planner
agents: [{id: boss, role: manager}, {id: w, role: worker, tools: [write_file]}]
workflow: {manager: boss, workers: [w], task: "{goal}"}
"""
    plan = '{"tasks": [{"id": "t1", "title": "write it", "assignee": "w"}]}'
    script = by_agent({"boss": [plan, "all done"],
                       "w": [call("write_file", path="docs/out.md", content="x\n"), "done"]})
    router, *_ = two_family_router(script)
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml=org, project=str(repo))
    run = await m.execute(rid)
    assert run["status"] == "succeeded"
    tree = sh(repo, "ls-tree", "-r", "--name-only", f"cadre/{rid}").split()
    assert "docs/out.md" in tree and "REPORT.md" not in tree and "plan.json" not in tree
    artifacts = [Path(p).name for p in run["summary"]["artifacts"]]
    assert artifacts == ["REPORT.md", "plan.json"]

    assert [c["id"] for c in m.cleanup_candidates()] == [rid]
    ok, msg = m.cleanup(rid)
    assert ok, msg
    assert not (home.runs_dir / rid / "workspace" / ".git").exists()
    assert f"cadre/{rid}" in sh(repo, "branch", "--list", "cadre/*")
    assert m.cleanup_candidates() == []


# ---------------------------------------------------------------- ADR-031: a check can write .git
# A check (in a container or not) can write anything in the workspace, the worktree's `.git` file
# included. Cadre's own git runs on the host after every step, so it must never follow what the
# check left there: a planted git config would run its command as the owner, outside any container.
def _worktree(repo: Path, tmp_path: Path) -> Path:
    from cadre.project import ensure_worktree

    ws = tmp_path / "run" / "workspace"
    ensure_worktree(str(repo), "main", "cadre/t1", ws)
    return ws


def _hostile(gitdir: Path, marker: Path) -> None:
    """What a check can plant: a git config and a hook that each run a command."""
    cmd = f'echo pwned > "{marker.as_posix()}"'
    hooks = gitdir / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text(f"#!/bin/sh\n{cmd}\n", newline="\n")
    (hooks / "pre-commit").chmod(0o755)
    for key, value in (("core.fsmonitor", cmd), ("core.hooksPath", hooks.as_posix())):
        subprocess.run(["git", "config", "-f", str(gitdir / "config"), key, value], check=True)


def _plant(ws: Path, how: str, marker: Path) -> None:
    if how == "git directory":        # replace the .git file with a repository of the check's own
        (ws / ".git").unlink()
        subprocess.run(["git", "init", "-q", str(ws)], check=True, capture_output=True)
        _hostile(ws / ".git", marker)
    else:                             # keep a .git file, but point it at a planted gitdir
        subprocess.run(["git", "init", "-q", "--bare", str(ws / ".evil")], check=True, capture_output=True)
        _hostile(ws / ".evil", marker)
        (ws / ".git").unlink()        # a check truncates it; on Windows a .git file resists truncation
        (ws / ".git").write_text("gitdir: .evil\n")
    (ws / "changed.txt").write_text("work")


@pytest.mark.parametrize("how", ["git directory", "repointed file"])
def test_cadres_commit_never_runs_what_a_check_planted_in_git(repo, tmp_path, how):
    from cadre.project import commit_all, worktree_git_dir

    ws = _worktree(repo, tmp_path)
    real = worktree_git_dir(str(repo), ws)
    marker = tmp_path / "PWNED"
    _plant(ws, how, marker)
    # control: plain git in the tampered worktree does run the planted command
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=ws, capture_output=True)
    if not marker.exists():
        pytest.skip("this git ran neither core.fsmonitor nor the hook; the attack is not reproducible here")
    marker.unlink()
    sha = commit_all(str(repo), ws, "cadre(test): after a hostile check")
    assert not marker.exists(), f"{how}: Cadre's git ran a command the check planted"
    assert (ws / ".git").is_file() and worktree_git_dir(str(repo), ws) == real
    assert sha and sh(repo, "rev-parse", "cadre/t1").strip() == sha
    assert "changed.txt" in sh(repo, "show", "--name-only", "--format=", "cadre/t1")


async def test_a_check_that_changes_git_is_failed_and_the_pointer_restored(home, repo):
    org = ORG.replace("workflow:", """checks:
  - {name: tamper, command: ["{python}", "-c", "import os; os.remove('.git'); open('.git','w').write('gitdir: .evil')"]}
workflow:""", 1)
    script = by_agent({
        "eng": [call("edit_file", path="calc.py", old="return a - b", new="return a + b"), "fixed add()"],
        "rev": [APPROVE],
    })
    router, *_ = two_family_router(script)
    m = RunManager(home, router=router)
    rid = m.create("", "make the tests pass", RunOptions(allow_exec=True), org_yaml=org, project=str(repo))
    run = await m.execute(rid)
    checks = {e["data"]["name"]: e["data"] for e in m.store.events(rid, kinds=("check.finished",))}
    assert checks["tamper"]["passed"] is False and "put it back" in checks["tamper"]["note"]
    assert checks["unit"]["passed"] is True
    assert m.store.events(rid, kinds=("project.git_restored",))
    ws = home.runs_dir / rid / "workspace"
    assert (ws / ".git").read_text().startswith("gitdir:") and ".evil" not in (ws / ".git").read_text()
    assert run["status"] in ("unapproved", "failed", "succeeded")
