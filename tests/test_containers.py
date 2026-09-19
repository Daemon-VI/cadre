"""The container runner for checks (FR-22, ADR-031): command line, validation, approval policy and
environment. No Docker needed; tests/test_containment.py runs the real thing in Linux CI."""

import os
import subprocess
from pathlib import Path

import pytest

from cadre import containers
from cadre.engine import CONTAINER_ONLY, RunOptions
from cadre.org import CheckSpec, OrgError, load_org_text
from cadre.runs import RunManager
from cadre.secrets import REDACTOR
from cadre.tools import CHECK_OUTPUT_CAP, run_check_process

from .conftest import by_agent, two_family_router

IMAGE = "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"


def spec(**kw) -> CheckSpec:
    base = {"name": "tests", "command": ["{python}", "-m", "unittest"], "runner": "docker", "image": IMAGE}
    return CheckSpec.model_validate({**base, **kw})


def pairs(argv: list[str]) -> list[tuple[str, str]]:
    return list(zip(argv, argv[1:], strict=False))


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "runs" / "r1" / "workspace"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------- the command line (AC-22.2)
def test_every_required_flag_is_on_the_command_line(ws):
    argv = containers.build_command("docker", spec(memory="256m", cpus=0.5, pids_limit=64), ws,
                                    "cadre-r1-tests-1-abcd", user="1000:1000")
    assert argv[:2] == ["docker", "run"]
    p = pairs(argv)
    for flag in (("--network", "none"), ("--tmpfs", containers.TMPFS), ("--cap-drop", "ALL"),
                 ("--security-opt", "no-new-privileges"), ("--pids-limit", "64"), ("--memory", "256m"),
                 ("--memory-swap", "256m"), ("--cpus", "0.5"), ("--user", "1000:1000"),
                 ("--name", "cadre-r1-tests-1-abcd"), ("--pull", "never"), ("--workdir", "/work")):
        assert flag in p, flag
    assert "--read-only" in argv and "--rm" in argv
    # the image, then the command with {python} meaning the image's interpreter
    assert argv[-4:] == [IMAGE, "python3", "-m", "unittest"]


def test_the_workspace_is_the_only_mount(ws):
    argv = containers.build_command("docker", spec(), ws, "n")
    mounts = [b for a, b in pairs(argv) if a == "--mount"]
    assert mounts == [f"type=bind,source={ws.resolve()},target=/work"]
    for never in ("-v", "--volume", "--privileged", "--cap-add", "--device", "--env-file", "--ipc", "--pid"):
        assert never not in argv, never
    joined = " ".join(argv)
    assert "docker.sock" not in joined and ".cadre" not in joined
    assert [b for a, b in pairs(argv) if a == "--network"] == ["none"]


def test_the_user_is_never_root(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 0, raising=False)
    assert containers.container_user() == containers.NOBODY
    monkeypatch.setattr(os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 118, raising=False)
    assert containers.container_user() == "1001:118"
    monkeypatch.delattr(os, "getuid", raising=False)
    assert containers.container_user() == containers.FALLBACK_USER  # Windows
    for user in (containers.NOBODY, "1001:118", containers.FALLBACK_USER):
        assert not user.startswith("0:")


def test_podman_takes_the_same_flags_and_keeps_the_owners_uid(ws):
    d = containers.build_command("docker", spec(), ws, "n", user="1000:1000")
    p = containers.build_command("podman", spec(runner="podman"), ws, "n", user="1000:1000")
    # the only difference: rootless Podman is told to keep the owner's uid
    assert p[0] == "podman" and [a for a in p[1:] if a not in ("--userns", "keep-id")] == d[1:]
    assert ("--userns", "keep-id") in pairs(p) and "--userns" not in d
    # no uid to keep on Windows, or as root
    assert "--userns" not in containers.build_command("podman", spec(runner="podman"), ws, "n",
                                                      user=containers.FALLBACK_USER)


def test_the_name_is_derived_from_the_run_id():
    a = containers.container_name("20260919-181806-989aa9", "unit tests", 3)
    assert a.startswith("cadre-20260919-181806-989aa9-unit-tests-3-") and " " not in a
    assert a != containers.container_name("20260919-181806-989aa9", "unit tests", 3)


@pytest.mark.parametrize("where", ["home", "root", "cadre_home", "git", "comma"])
def test_nothing_wider_than_a_workspace_is_mounted(where, tmp_path, monkeypatch):
    target = {"home": Path.home(), "root": Path(Path.home().anchor)}.get(where)
    if where == "cadre_home":
        target = tmp_path / "state"
        target.mkdir()
        monkeypatch.setenv("CADRE_HOME", str(target))
    elif where == "git":
        target = tmp_path / "repo"
        (target / ".git").mkdir(parents=True)
    elif where == "comma":
        target = tmp_path / "a,b"
        target.mkdir()
    with pytest.raises(containers.ContainerError):
        containers.mount_source(target)


def test_a_worktree_with_a_git_file_is_mountable(ws):
    (ws / ".git").write_text("gitdir: /elsewhere/.git/worktrees/r1\n")
    assert containers.mount_source(ws) == str(ws.resolve())


# ---------------------------------------------------------------- environment (AC-22.3)
def test_only_allowlisted_names_pass_and_keys_never_do(ws, monkeypatch):
    planted = "sk-planted-0123456789abcdef"
    REDACTOR.register(planted)
    monkeypatch.setenv("LANG_FOR_TESTS", "C.UTF-8")
    monkeypatch.setenv("INNOCENT_NAME", planted)           # a known key under an innocent name
    monkeypatch.setenv("NOT_LISTED", "x")
    s = spec(env=["LANG_FOR_TESTS", "INNOCENT_NAME", "MISSING"])
    names = containers.passed_env(s)
    assert names == ["LANG_FOR_TESTS"]
    argv = containers.build_command("docker", s, ws, "n", names)
    envs = [b for a, b in pairs(argv) if a == "--env"]
    assert envs == ["HOME=/tmp", "PYTHONDONTWRITEBYTECODE=1", "LANG_FOR_TESTS"]  # by name, no value
    assert planted not in " ".join(argv) and "NOT_LISTED" not in " ".join(argv)


def test_a_credential_name_cannot_be_allowlisted():
    for name in ("GROQ_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
        with pytest.raises(ValueError, match="credential"):
            spec(env=[name])


# ---------------------------------------------------------------- validation (AC-22.1, AC-22.5)
def test_a_container_check_needs_a_pinned_image():
    with pytest.raises(ValueError, match="needs `image:`"):
        CheckSpec(name="t", command=["x"], runner="docker")
    with pytest.raises(ValueError, match="not pinned"):
        spec(image="python:3.12-slim")
    loose = spec(image="python:3.12-slim", allow_unpinned=True)
    assert "NOT pinned" in containers.describe(loose)
    assert "NOT pinned" not in containers.describe(spec())


def test_container_settings_on_a_subprocess_check_are_refused():
    with pytest.raises(ValueError, match="only applies"):
        CheckSpec(name="t", command=["x"], image=IMAGE)
    with pytest.raises(ValueError, match="only applies"):
        CheckSpec(name="t", command=["x"], env=["LANG"])
    with pytest.raises(ValueError):
        spec(memory="lots")


def test_org_files_carry_the_runner():
    org = load_org_text(f"""
name: t
agents: [{{id: eng, role: e}}]
checks:
  - {{name: boxed, command: ["{{python}}", "-m", "unittest"], runner: docker, image: "{IMAGE}", memory: 1g}}
  - {{name: plain, command: ["{{python}}", "-V"]}}
workflow: {{builder: eng, checks: [boxed, plain], task: build}}
""")
    assert [c.runner for c in org.checks] == ["docker", "subprocess"]
    with pytest.raises(OrgError, match="pinned"):
        load_org_text("name: t\nagents: [{id: eng, role: e}]\n"
                      "checks: [{name: b, command: [x], runner: podman, image: 'alpine:3'}]\n"
                      "workflow: {agent: eng, task: t}\n")


# ---------------------------------------------------------------- running (AC-22.4, AC-22.6)
class FakeRuntime:
    """Stands in for the docker CLI: records every command line."""

    def __init__(self, inspect=(0, "sha256:local-id\n", ""), run=(0, b"ok\n"), timeout=False):
        self.calls: list[list[str]] = []
        self.inspect, self.run, self.timeout = inspect, run, timeout

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[1:3] == ["image", "inspect"]:
            code, out, err = self.inspect
            return subprocess.CompletedProcess(argv, code, out, err)
        if argv[1] == "kill":
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, kw["timeout"], output=b"partial")
        assert "env" in kw and not any(REDACTOR.redact(v) != v for v in kw["env"].values())
        return subprocess.CompletedProcess(argv, self.run[0], self.run[1])


async def test_a_missing_image_fails_with_the_pull_command_and_nothing_runs(ws, monkeypatch):
    fake = FakeRuntime(inspect=(1, "", "Error: No such image: " + IMAGE))
    monkeypatch.setattr(containers.subprocess, "run", fake)
    r = await run_check_process(spec(), ws, "n")
    assert not r.passed and r.exit_code is None
    assert f"Run: docker pull {IMAGE}" in r.note and "never pulls" in r.note
    assert [c[1] for c in fake.calls] == ["image"]           # no run, no pull


async def test_a_missing_runtime_is_named(ws, monkeypatch):
    def missing(argv, **kw):
        raise FileNotFoundError(argv[0])
    monkeypatch.setattr(containers.subprocess, "run", missing)
    r = await run_check_process(spec(runner="podman"), ws, "n")
    assert not r.passed and "podman is not installed" in r.note


async def test_exit_code_output_cap_and_what_was_used_are_recorded(ws, monkeypatch):
    fake = FakeRuntime(run=(3, b"x" * (CHECK_OUTPUT_CAP + 50)))
    monkeypatch.setattr(containers.subprocess, "run", fake)
    r = await run_check_process(spec(memory="1g", cpus=2, pids_limit=100), ws, "cadre-r1-tests-1-ab")
    assert not r.passed and r.exit_code == 3
    assert r.output.startswith("[… 50 characters cut …]")
    d = r.as_dict()
    assert d["runner"] == "docker"
    want = {"image": IMAGE, "image_id": "sha256:local-id", "pinned": True, "network": "none",
            "memory": "1g", "cpus": 2.0, "pids_limit": 100, "name": "cadre-r1-tests-1-ab"}
    assert d["container"].items() >= want.items()
    assert fake.calls[-1][:2] == ["docker", "run"]
    ok = FakeRuntime(run=(0, b"Ran 9 tests\nOK\n"))
    monkeypatch.setattr(containers.subprocess, "run", ok)
    assert (await run_check_process(spec(), ws, "n")).passed


async def test_a_timeout_kills_the_named_container(ws, monkeypatch):
    fake = FakeRuntime(timeout=True)
    monkeypatch.setattr(containers.subprocess, "run", fake)
    r = await run_check_process(spec(timeout=5), ws, "cadre-r1-tests-1-ab")
    assert not r.passed and r.exit_code is None and "timed out after 5s" in r.note
    assert fake.calls[-1] == ["docker", "kill", "cadre-r1-tests-1-ab"]
    assert r.output == "partial"


def test_exit_137_is_explained():
    assert "memory cap" in containers.exit_note(137, "docker")
    assert "exit 125" in containers.exit_note(125, "docker") and containers.exit_note(1, "docker") == ""


# ---------------------------------------------------------------- approval policy (AC-22.7)
ORG = f"""
name: t
agents: [{{id: eng, role: e}}]
checks:
  - {{name: boxed, command: ["{{python}}", "-m", "unittest"], runner: docker, image: "{IMAGE}"}}
  - {{name: plain, command: ["{{python}}", "-c", "print(1)"]}}
workflow: {{builder: eng, checks: [CHECKS], max_rounds: 1, task: build}}
"""


async def run_with(home, monkeypatch, checks: str, allow_exec) -> tuple[list, list]:
    ran: list[str] = []

    def contained(s, cwd, name):
        ran.append(s.name)
        return 0, "ok", "", {"image": s.image, "name": name}
    monkeypatch.setattr(containers, "run_contained", contained)
    asked: list[str] = []

    async def deny(kind, prompt, agent):
        asked.append(prompt)
        return False, ""

    router, *_ = two_family_router(by_agent({"eng": ["built"]}))
    m = RunManager(home, router=router)
    rid = m.create("", "g", RunOptions(allow_exec=allow_exec), org_yaml=ORG.replace("CHECKS", checks))
    await m.execute(rid, approver=deny)
    events = m.store.events(rid, kinds=("check.started", "check.finished"))
    return asked, [(e["kind"], e["data"]) for e in events] + [("ran", n) for n in ran]


async def test_container_only_runs_a_contained_check_without_asking(home, monkeypatch):
    asked, events = await run_with(home, monkeypatch, "boxed", CONTAINER_ONLY)
    assert asked == []
    started = [d for k, d in events if k == "check.started"][0]
    assert started["runner"] == "docker" and started["image"] == IMAGE and started["auto_approved"] is True
    assert ("ran", "boxed") in events


async def test_container_only_still_asks_before_a_check_that_runs_as_the_owner(home, monkeypatch):
    asked, events = await run_with(home, monkeypatch, "plain", CONTAINER_ONLY)
    assert len(asked) == 1 and "Model-written code will run as you" in asked[0]
    finished = [d for k, d in events if k == "check.finished"][0]
    assert finished["runner"] == "subprocess" and "not approved" in finished["note"]


async def test_by_default_a_contained_check_asks_and_the_prompt_says_where_it_runs(home, monkeypatch):
    asked, events = await run_with(home, monkeypatch, "boxed", False)
    assert len(asked) == 1 and ("ran", "boxed") not in events
    p = asked[0]
    assert f"in docker, image {IMAGE}; no network" in p and "runs as you, on this machine" in p
    assert "boxed: python3 -m unittest" in p                   # {python} as the image will run it


def test_the_stored_option_survives_a_resume(home):
    m = RunManager(home)
    rid = m.create("decision-board", "g", RunOptions(allow_exec=CONTAINER_ONLY), demo=True)
    assert m.store.get_run(rid)["options"]["allow_exec"] == CONTAINER_ONLY
    from cadre.engine import allow_exec_option
    assert allow_exec_option("container_only") == CONTAINER_ONLY
    assert allow_exec_option(True) is True and allow_exec_option(None) is False
    assert allow_exec_option("yes") is True  # any other truthy value meant "allow" before 1.1
