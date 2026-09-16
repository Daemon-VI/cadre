import os
import sys

import pytest

from cadre.org import CheckSpec
from cadre.secrets import REDACTOR, scrubbed_env
from cadre.tools import run_check_process
from cadre.workspace import Workspace, WorkspaceError


@pytest.mark.parametrize("bad", [
    "../escape.txt", "a/../../escape.txt", "/etc/passwd", "C:/Windows/win.ini", "C:evil",
    "~/x", "file.txt:stream", "nul", "con.txt", ".git/config", "", "   "])
def test_paths_that_leave_the_workspace_are_refused(tmp_path, bad):
    ws = Workspace(tmp_path / "run")
    with pytest.raises(WorkspaceError):
        ws.write(bad, "x", "agent")


def test_symlink_out_of_the_workspace_is_refused(tmp_path):
    ws = Workspace(tmp_path / "run")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, ws.root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs privileges on this machine")
    with pytest.raises(WorkspaceError):
        ws.write("link/x.txt", "x", "agent")


def test_writes_are_versioned_and_reported(tmp_path):
    seen = []
    ws = Workspace(tmp_path / "run", on_write=lambda *a: seen.append(a))
    assert ws.write("src\\app.py", "v1", "eng")["version"] == 1
    info = ws.write("src/app.py", "v2", "eng")
    assert info["version"] == 2 and ws.read("src/app.py") == "v2"
    assert sorted(p.name for p in ws.versions_dir.iterdir()) == ["src__app.py@1", "src__app.py@2"]
    assert seen[-1][0] == "src/app.py" and seen[-1][4] == "eng"
    # a new Workspace object on the same run keeps counting (resume)
    assert Workspace(tmp_path / "run").write("src/app.py", "v3", "eng")["version"] == 3


def test_binary_and_oversize_files(tmp_path):
    ws = Workspace(tmp_path / "run")
    (ws.root / "b.bin").write_bytes(b"\x00\x01")
    with pytest.raises(WorkspaceError):
        ws.read("b.bin")
    with pytest.raises(WorkspaceError):
        ws.write("big.txt", "x" * 300_000, "a")


def test_check_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_live_value_123456")
    monkeypatch.setenv("INNOCENT", "planted-secret-value-999")
    monkeypatch.setenv("PLAIN", "hello")
    REDACTOR.register("planted-secret-value-999")
    env = scrubbed_env()
    assert "GROQ_API_KEY" not in env and "INNOCENT" not in env and env["PLAIN"] == "hello"


async def test_checks_run_fail_and_time_out(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "tok-should-not-leak-000")
    (tmp_path / "ok.py").write_text("import os, sys\nprint('token' , os.environ.get('SOME_TOKEN'))\n")
    ok = await run_check_process(CheckSpec(name="ok", command=["{python}", "ok.py"]), tmp_path)
    assert ok.passed and "token None" in ok.output
    bad = await run_check_process(CheckSpec(name="bad", command=[sys.executable, "-c", "raise SystemExit(3)"]),
                                  tmp_path)
    assert not bad.passed and bad.exit_code == 3
    slow = await run_check_process(CheckSpec(name="slow", timeout=1,
                                             command=[sys.executable, "-c", "import time; time.sleep(5)"]),
                                   tmp_path)
    assert not slow.passed and "timed out" in slow.note
    missing = await run_check_process(CheckSpec(name="m", command=["definitely-not-a-command-xyz"]), tmp_path)
    assert not missing.passed and "not found" in missing.note
