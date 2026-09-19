"""Distribution readiness (FR-14, FR-15): versioned API, allowed hosts, cross-platform
scheduler, headless keys."""

import json
import os
import plistlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cadre import scheduler
from cadre.api import allowed_host_names, create_app
from cadre.runs import RunManager
from cadre.secrets import SecretStore

from .conftest import MemorySecrets

TOKEN = "test-token-0123456789abcdef"
SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi-v1.json"


def make_app(home, **kw):
    return create_app(RunManager(home, secrets=MemorySecrets()), token=TOKEN, resume_every=None, **kw)


# ---------------------------------------------------------------- FR-15 versioned API
def test_v1_and_the_alias_share_one_lock(home):
    with TestClient(make_app(home), base_url="http://127.0.0.1:8765") as c:
        for prefix in ("/api/v1", "/api"):
            assert c.get(f"{prefix}/health").json()["ok"] is True
            assert c.get(f"{prefix}/runs").status_code == 401
            assert c.get(f"{prefix}/runs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def openapi_now(home) -> dict:
    doc = make_app(home).openapi()
    doc["info"].pop("version", None)  # a release bump is not an API change
    return doc


def test_openapi_snapshot_pins_the_v1_contract(home):
    doc = openapi_now(home)
    assert doc["paths"] and all(p.startswith("/api/v1/") for p in doc["paths"])
    if os.environ.get("CADRE_UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert SNAPSHOT.exists(), "run with CADRE_UPDATE_SNAPSHOTS=1 once to create the snapshot"
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert doc == saved, ("the /api/v1 contract changed; review the diff and regenerate with "
                          "CADRE_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_distribution.py")


# ---------------------------------------------------------------- FR-14.7 allowed hosts
def test_allowed_host_names_are_exact():
    assert allowed_host_names("127.0.0.1") == set()
    assert allowed_host_names("0.0.0.0") == set()           # binding everywhere trusts no name
    assert allowed_host_names("0.0.0.0", ["Cadre.tail1234.ts.net", "cadre"]) == {
        "cadre.tail1234.ts.net", "cadre"}
    assert allowed_host_names("100.64.1.2") == {"100.64.1.2"}
    for bad in ("*", "*.ts.net", "evil.com/x", "", "a b"):
        with pytest.raises(ValueError):
            allowed_host_names("127.0.0.1", [bad])


def test_an_allowed_host_is_accepted_and_others_still_refused(home):
    app = make_app(home, allowed_hosts=allowed_host_names("0.0.0.0", ["cadre.tail1234.ts.net"]))
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        ok = c.get("/api/v1/health", headers={"host": "CADRE.tail1234.ts.net:8765"})
        assert ok.status_code == 200
        assert c.get("/api/v1/health", headers={"host": "evil.example:8765"}).status_code == 421
        assert c.get("/api/v1/runs", headers={"host": "cadre.tail1234.ts.net"}).status_code == 401


def test_serve_passes_allowed_hosts_and_refuses_wildcards(home, monkeypatch):
    import uvicorn

    from cadre.cli import app

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(app=application, **kw))
    r = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0", "--allowed-host", "cadre"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "0.0.0.0" and "Accepting Host: cadre" in r.output
    r = CliRunner().invoke(app, ["serve", "--allowed-host", "*"])
    assert r.exit_code != 0 and "not a host name" in r.output


# ---------------------------------------------------------------- FR-14.5 scheduler everywhere
def test_linux_plan_is_a_systemd_user_timer(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("CADRE_HOME", "/data/cadre home")
    plan = scheduler.install_plan(15, platform="linux", user_home=tmp_path)
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    service, timer = plan.files[unit_dir / "cadre-resume.service"], plan.files[unit_dir / "cadre-resume.timer"]
    assert "Type=oneshot" in service and '"-m" "cadre.scheduled" "/data/cadre home"' in service
    assert "OnUnitActiveSec=15min" in timer and "Persistent=true" in timer and "WantedBy=timers.target" in timer
    assert plan.commands[-1] == ["systemctl", "--user", "enable", "--now", "cadre-resume.timer"]
    gone = scheduler.uninstall_plan(platform="linux", user_home=tmp_path)
    assert set(gone.remove) == set(plan.files)


def test_macos_plan_is_a_launchd_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("CADRE_HOME", raising=False)
    plan = scheduler.install_plan(30, platform="darwin", user_home=tmp_path)
    [(path, text)] = plan.files.items()
    assert path == tmp_path / "Library" / "LaunchAgents" / "io.github.daemon-vi.cadre.resume.plist"
    doc = plistlib.loads(text.encode())
    assert doc["StartInterval"] == 1800 and doc["ProgramArguments"][-2:] == ["-m", "cadre.scheduled"]
    assert plan.commands[-1][:3] == ["launchctl", "load", "-w"]


def test_apply_writes_runs_and_removes(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "run", lambda args: (ran.append(args) or (args[1] != "fail", "")))
    plan = scheduler.Plan(files={tmp_path / "a" / "unit": "x"}, commands=[["c", "fail"], ["c", "ok"]],
                          tolerate={0})
    assert scheduler.apply(plan)[0] and (tmp_path / "a" / "unit").read_text() == "x"
    assert scheduler.apply(scheduler.Plan(remove=[tmp_path / "a" / "unit"]))[0]
    assert not (tmp_path / "a" / "unit").exists()
    assert not scheduler.apply(scheduler.Plan(commands=[["c", "fail"], ["c", "ok"]]))[0]
    assert ran[-1] == ["c", "fail"]  # stopped at the first real failure


def test_every_minutes_is_bounded():
    for fn in (scheduler.systemd_units, scheduler.launchd_plist, scheduler.install_args):
        with pytest.raises(ValueError):
            fn(1)


# ---------------------------------------------------------------- FR-14.6 headless keys
def test_no_keyring_means_env_only(monkeypatch):
    import keyring

    def boom(*a, **k):
        raise AssertionError("the keychain must not be touched")

    monkeypatch.setattr(keyring, "get_password", boom)
    monkeypatch.setattr(keyring, "set_password", boom)
    monkeypatch.setenv("CADRE_NO_KEYRING", "1")
    monkeypatch.setenv("CADRE_KEY_GROQ", "gsk_" + "x" * 40)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "y" * 35)
    s = SecretStore()
    assert s.get("groq") == "gsk_" + "x" * 40
    assert s.get("gemini", "GEMINI_API_KEY") == "AIza" + "y" * 35
    assert s.get("mistral", "MISTRAL_API_KEY") is None
    with pytest.raises(RuntimeError, match="CADRE_KEY_GROQ"):
        s.set("groq", "new")


# ---------------------------------------------------------------- FR-14.3 history checks
def _tool(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_history_check_patterns_catch_what_they_guard():
    h = _tool("check_history")
    name = "M" + "PPS KANA" + "JIGUDA"  # assembled, so no tracked file holds the name
    assert h.PRIVATE.search(f"C:/Users/{name}/.claude") and h.PRIVATE.search(name.lower().replace(" ", "_"))
    assert not h.PRIVATE.search("Rithik Krishna")
    assert h.KEYS.search("gsk_" + "A1" * 26) and h.KEYS.search("AIza" + "b" * 35)
    assert not h.KEYS.search("AIza-test-not-a-real-key-000")
    # Google AI Studio's newer key format (seen 2026-09-19): "AQ." and 50 characters
    assert h.KEYS.search("AQ." + "Ab8_x-" * 8 + "z9") and not h.KEYS.search("AQ.short")


def test_licence_policy_flags_copyleft():
    lic = _tool("check_licences")
    for bad in ("GNU General Public License v3 (GPLv3)", "AGPL-3.0", "LGPL-2.1", "MPL-2.0"):
        assert lic.COPYLEFT.search(bad)
    for good in ("MIT", "BSD-3-Clause", "Apache-2.0 OR BSD-2-Clause", "ISC License (ISCL)"):
        assert not lic.COPYLEFT.search(good)


# ---------------------------------------------------------------- FR-16.3 standalone builds
def test_a_frozen_build_runs_checks_with_the_python_on_path(monkeypatch):
    import sys

    from cadre import tools

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(tools.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "python3" else None)
    assert tools.resolve_command(["{python}", "-m", "pytest"]) == ["/usr/bin/python3", "-m", "pytest"]
    monkeypatch.setenv("CADRE_HOME", "/data")
    assert scheduler.job_argv()[1:] == ["scheduled-run", "/data"]  # no `-m` inside a frozen build
    assert scheduler.resume_command().endswith('scheduled-run "/data"')


def test_version_flag():
    from cadre import __version__
    from cadre.cli import app

    r = CliRunner().invoke(app, ["--version"])
    assert r.exit_code == 0 and r.output.strip() == f"cadre {__version__}"


def test_add_from_env_adds_free_providers_only(home, monkeypatch):
    from cadre.cli import app
    from cadre.presets import PRESETS

    for p in PRESETS.values():
        if p.env:
            monkeypatch.delenv(p.env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "f" * 40)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "f" * 40)  # paid: never added this way
    r = CliRunner().invoke(app, ["provider", "add-from-env", "--no-test"])
    assert r.exit_code == 0, r.output
    ids = [p.id for p in home.load_config().providers]
    assert ids == ["groq"] and "gsk_" not in r.output
    raw = (home.root / "config.yaml").read_text(encoding="utf-8")
    assert "gsk_" not in raw  # the key itself stays in the environment


def test_add_from_env_with_test_skips_a_rejected_key(home, monkeypatch):
    # the Action's first real run (2026-09-19): a bad GEMINI_API_KEY secret was added unchecked, and
    # then every call tried five Gemini models first
    import cadre.cli as cli
    from cadre.presets import PRESETS

    for p in PRESETS.values():
        if p.env:
            monkeypatch.delenv(p.env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "f" * 40)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "f" * 35)

    class Fake:
        def __init__(self, pc, secrets):
            self.id = pc.id

        async def health(self):
            if self.id == "gemini":
                return False, "key rejected — 400: API key not valid. Please pass a valid API key."
            return True, "reachable, key accepted, 3 model(s) listed"

        async def list_models(self):
            return []

        async def aclose(self):
            pass

    monkeypatch.setattr(cli, "make_provider", Fake)
    r = CliRunner().invoke(cli.app, ["provider", "add-from-env", "--test"])
    assert r.exit_code == 0, r.output
    assert [p.id for p in home.load_config().providers] == ["groq"]
    assert "skipped gemini" in r.output and "AIza" not in r.output


def test_result_json_for_scripts(home, tmp_path):
    from cadre.cli import app

    out = tmp_path / "result.json"
    r = CliRunner().invoke(app, ["run", "decision-board", "Open a second office?", "--demo", "--quiet",
                                 "--result-json", str(out)])
    assert r.exit_code == 0, r.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "succeeded" and data["totals"]["calls"] > 0 and data["usage"]
    assert set(data) >= {"id", "result", "branch", "commits", "diff_stat", "resume_at_ist"}


# ---------------------------------------------------------------- FR-18 GitHub Action
def test_action_formats_outputs_body_and_parked_comment(home, tmp_path):
    import yaml

    from cadre.cli import app

    out = tmp_path / "result.json"
    CliRunner().invoke(app, ["run", "decision-board", "Open a second office?", "--demo", "--quiet",
                             "--result-json", str(out)])
    pr = _tool_at("action", "pr_body")
    r = json.loads(out.read_text(encoding="utf-8"))
    assert pr.outputs(r).splitlines()[1] == "status=succeeded"
    body = pr.body(r)
    assert "| **total** |" in body and r["id"] in body and len(body) < 65_536
    assert "Closes #" not in body and pr.body(r, "4").endswith("Closes #4\n")  # merging closes the issue
    r.update(status="parked", resume_at_ist="19 Sep 05:30 IST")
    assert "19 Sep 05:30 IST" in pr.comment(r)
    # the example workflow only lets trusted people trigger it, with the least permissions
    wf = yaml.safe_load((Path(__file__).parents[1] / "examples" / "github-action" / "cadre.yml").read_text())
    cond = wf["jobs"]["cadre"]["if"]
    assert cond.count('["OWNER","MEMBER","COLLABORATOR"]') == 2 and "author_association" in cond
    assert wf["permissions"] == {"contents": "write", "pull-requests": "write", "issues": "write"}
    action = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text(encoding="utf-8"))
    runs = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
    assert "${{ inputs.goal }}" not in runs  # the goal reaches shells only through env vars
    assert "add-from-env --test" in runs  # a bad secret is caught before the run, not on every call


def test_action_opens_no_pr_for_an_empty_branch_and_the_comment_carries_the_report():
    import yaml

    pr = _tool_at("action", "pr_body")
    r = {"id": "20260919-051254-c887fb", "org": "project-finisher", "goal": "finish it",
         "status": "unapproved", "error": None, "result": "Task w/0: tests still fail (2 of 7).",
         "branch": "cadre/20260919-051254-c887fb", "commits": [], "diff_stat": "",
         "totals": {"calls": 9, "prompt_tokens": 12_000, "completion_tokens": 900}, "usage": []}
    assert "commits=0" in pr.outputs(r).splitlines()
    # no pull request: the issue comment must say so and carry the report and usage itself
    c = pr.comment(r, pr_url="")
    assert "no pull request" in c.lower() and "tests still fail (2 of 7)" in c and "| **total** |" in c
    # with a pull request, the comment links it instead of repeating the report
    c = pr.comment(dict(r, commits=["abc123 fix"]), pr_url="https://github.com/o/r/pull/2")
    assert "https://github.com/o/r/pull/2" in c and "tests still fail" not in c
    assert len(pr.comment(dict(r, result="x" * 100_000), pr_url="")) < 65_536

    action = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text(encoding="utf-8"))
    steps = {s.get("id") or s.get("name"): s for s in action["runs"]["steps"]}
    assert "steps.run.outputs.commits != '0'" in steps["pr"]["if"]
    assert "PR_URL" in steps["Report on the issue"]["env"]
    # the timeline goes to the (secret-masked) log so a failed run can be diagnosed afterwards
    assert any("--events" in s.get("run", "") for s in action["runs"]["steps"])


def _github_tag_filter(pattern):
    """GitHub's tag filter syntax (`*`, `?`, `+`, `[...]`, all else literal) as a full-match regex."""
    import re

    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "[":
            j = pattern.index("]", i)
            out, i = out + pattern[i:j + 1], j + 1
            continue
        out += {"*": "[^/]*", "?": "?", "+": "+"}.get(c, re.escape(c))
        i += 1
    return re.compile(out)


def test_release_fires_only_on_full_versions_not_on_action_major_tags():
    import yaml

    wf = yaml.safe_load((Path(__file__).parents[1] / ".github" / "workflows" / "release.yml").read_text())
    patterns = [_github_tag_filter(p) for p in wf[True]["push"]["tags"]]  # YAML 1.1 reads `on` as True

    def fires(tag):
        return any(p.fullmatch(tag) for p in patterns)

    assert fires("v1.1.0") and fires("v0.1.0") and fires("v1.1.0rc1")
    # `uses: Daemon-VI/cadre@v1` needs a moving v1 tag; pushing it must never publish anything
    assert not fires("v1") and not fires("v1.1") and not fires("vscode-v0.1.0")


def _tool_at(folder, name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / folder / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_exec_approval_shows_the_command_that_will_really_run(home):
    import sys

    seen = []

    async def deny(kind, prompt, agent):
        seen.append((kind, prompt))
        return False, "no"

    m = RunManager(home, secrets=MemorySecrets())
    rid = m.create("software-team", "a word counter", demo=True)
    await m.execute(rid, approver=deny)
    [prompt] = [p for k, p in seen if k == "exec"][:1]
    assert "{python}" not in prompt and sys.executable in prompt.replace('"', "")
    assert m.store.list_runs()[0].keys() >= {"branch", "project_path", "resume_at"}


def test_pr_title_is_the_goals_first_line_cut_at_a_word():
    # a labelled issue's goal is "title\n\nbody"; `head -c 60` put the newline into PR #5's title
    import yaml

    pr = _tool_at("action", "pr_body")
    t = pr.title("Add a one-line module docstring to textstats/__init__.py\n\nA one-line docstring at")
    assert t == "Cadre: Add a one-line module docstring to textstats/__init__.py"
    long = pr.title("Implement top_words and reading_time in textstats/core.py as described in the issue")
    assert long == "Cadre: Implement top_words and reading_time in textstats/core.py as described…"
    assert pr.title("é" * 100).endswith("…")  # cut by characters, never mid-way through a UTF-8 byte
    assert pr.title("  \n\n") == "Cadre: work from an issue"
    action = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text(encoding="utf-8"))
    runs = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
    assert "head -c 60" not in runs and "pr_body.py\" title" in runs
