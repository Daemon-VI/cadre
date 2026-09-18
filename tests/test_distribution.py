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


def test_licence_policy_flags_copyleft():
    lic = _tool("check_licences")
    for bad in ("GNU General Public License v3 (GPLv3)", "AGPL-3.0", "LGPL-2.1", "MPL-2.0"):
        assert lic.COPYLEFT.search(bad)
    for good in ("MIT", "BSD-3-Clause", "Apache-2.0 OR BSD-2-Clause", "ISC License (ISCL)"):
        assert not lic.COPYLEFT.search(good)
