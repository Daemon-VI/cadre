"""Containment (FR-22, AC-22.8): real containers, run in Linux CI only.

Runs when CADRE_CONTAINMENT names the runtimes to test ("docker", "podman" or both, comma
separated) and IMAGE is already on the machine; Cadre itself never pulls. Each hostile check
must fail to do what it tries, and the normal check (the M11 capstone's unit converter, copied
verbatim into tests/fixtures/unit_converter) must pass inside the container. Each outcome is
appended to CADRE_CONTAINMENT_REPORT as a JSON line, which CI prints in the job summary.
"""

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from cadre import containers
from cadre.org import CheckSpec
from cadre.secrets import REDACTOR
from cadre.tools import CheckResult, run_check_process

IMAGE = ("docker.io/library/python:3.12-slim"
         "@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9")
RUNTIMES = [r.strip() for r in os.environ.get("CADRE_CONTAINMENT", "").split(",") if r.strip()]
FIXTURE = Path(__file__).parent / "fixtures" / "unit_converter"

pytestmark = pytest.mark.skipif(not RUNTIMES, reason="set CADRE_CONTAINMENT=docker (Linux CI only)")


@pytest.fixture(params=RUNTIMES or ["docker"])
def runtime(request) -> str:
    return request.param


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "runs" / "containment" / "workspace"
    d.mkdir(parents=True)
    return d


def record(runtime: str, attack: str, r: CheckResult, contained: bool, detail: str = "") -> None:
    line = {"runtime": runtime, "attack": attack, "contained": contained, "exit_code": r.exit_code,
            "note": r.note, "detail": detail, "output": r.output[-300:]}
    if path := os.environ.get("CADRE_CONTAINMENT_REPORT"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")


async def probe(runtime: str, ws: Path, code: str, **kw) -> tuple[CheckResult, str]:
    s = CheckSpec(name="probe", command=["{python}", "-c", code], runner=runtime, image=IMAGE,
                  timeout=kw.pop("timeout", 90), **kw)
    name = containers.container_name("containment", "probe", 0)
    return await run_check_process(s, ws, name), name


# 1 ------------------------------------------------------------------ the network
NET = """
import socket, urllib.request
reached = []
for how, go in (("tcp 1.1.1.1:443", lambda: socket.create_connection(("1.1.1.1", 443), timeout=5)),
                ("dns pypi.org", lambda: socket.getaddrinfo("pypi.org", 443)),
                ("http example.com", lambda: urllib.request.urlopen("http://example.com", timeout=5))):
    try:
        go(); reached.append(how)
    except Exception as e:
        print(how, "failed:", type(e).__name__, e)
print("REACHED", reached)
raise SystemExit(1 if reached else 0)
"""


async def test_the_network_is_unreachable(runtime, ws):
    # control: the host running the test can reach it, so the result below is not vacuous
    socket.create_connection(("1.1.1.1", 443), timeout=10).close()
    r, _ = await probe(runtime, ws, NET)
    ok = r.exit_code == 0 and "REACHED []" in r.output
    record(runtime, "reach the network", r, ok)
    assert ok, r.output


# 2 ------------------------------------------------------------------ writing outside /work
WRITE = """
import json, os
res = {"uid": os.getuid()}
for p in ("/etc/cadre-probe", "/cadre-probe", "/usr/lib/cadre-probe", "/home/cadre-probe", "/work/../cadre-probe"):
    try:
        open(p, "w").write("x"); res[p] = "WROTE"
    except OSError as e:
        res[p] = type(e).__name__
for p in ("/work/inside.txt", "/tmp/scratch.txt"):
    open(p, "w").write("fine"); res[p] = "WROTE"
print(json.dumps(res))
"""


async def test_nothing_outside_work_is_writable(runtime, ws):
    r, _ = await probe(runtime, ws, WRITE)
    res = json.loads(r.output.strip().splitlines()[-1])
    outside = {k: v for k, v in res.items() if k.startswith("/") and not k.startswith(("/work/i", "/tmp/"))}
    ok = (r.passed and all(v != "WROTE" for v in outside.values()) and res["uid"] != 0
          and (ws / "inside.txt").read_text() == "fine" and not (ws.parent / "cadre-probe").exists())
    record(runtime, "write outside /work", r, ok, json.dumps(res))
    assert ok, res


# 3 ------------------------------------------------------------------ a key in the host environment
PLANTED = "sk-containment-planted-0123456789abcdef"
ENV = f"""
import os
names = sorted(os.environ)
hits = [k for k, v in os.environ.items() if "{PLANTED[:12]}" in v]
try:
    init = open("/proc/1/environ", "rb").read().decode(errors="replace")
except OSError:
    init = ""
print("NAMES", names)
print("FOUND", hits, "{PLANTED[:12]}" in init)
"""


async def test_a_key_planted_in_the_host_environment_is_not_readable(runtime, ws, monkeypatch):
    REDACTOR.register(PLANTED)
    monkeypatch.setenv("CADRE_PLANTED_API_KEY", PLANTED)
    monkeypatch.setenv("PLANTED_INNOCENT", PLANTED)         # a known key under an innocent name…
    monkeypatch.setenv("PLANTED_ALLOWED", "not a secret")
    r, _ = await probe(runtime, ws, ENV, env=["PLANTED_INNOCENT", "PLANTED_ALLOWED"])  # …even allowlisted
    ok = r.passed and "FOUND [] False" in r.output and "PLANTED_ALLOWED" in r.output
    record(runtime, "read a key planted in the host environment", r, ok)
    assert ok, r.output


# 4 ------------------------------------------------------------------ a fork bomb
FORK = """
import os, time
n = 0
try:
    while True:
        if os.fork() == 0:
            time.sleep(60)
            os._exit(0)
        n += 1
except OSError as e:
    print("STOPPED after", n, "forks:", e)
"""


async def test_a_fork_bomb_stops_at_the_pids_limit(runtime, ws):
    r, _ = await probe(runtime, ws, FORK, pids_limit=64, timeout=60)
    n = int(r.output.split("STOPPED after", 1)[1].split()[0]) if "STOPPED after" in r.output else -1
    ok = 0 <= n < 64 and r.note == ""
    record(runtime, "fork bomb (pids_limit 64)", r, ok, f"{n} forks")
    assert ok, (r.note, r.output)


# 5 ------------------------------------------------------------------ memory past the cap
MEMORY = """
block = b"\\x01" * (512 * 1024 * 1024)   # 512 MiB, every page written
print("ALLOCATED", len(block))
"""


async def test_memory_past_the_cap_is_killed(runtime, ws):
    r, _ = await probe(runtime, ws, MEMORY, memory="128m", timeout=60)
    ok = not r.passed and "ALLOCATED" not in r.output and (r.exit_code == 137 or "MemoryError" in r.output)
    record(runtime, "allocate 512 MiB under a 128m cap", r, ok)
    assert ok, (r.exit_code, r.note, r.output)


# 6 ------------------------------------------------------------------ the timeout kills the container
async def test_a_timeout_kills_and_removes_the_container(runtime, ws):
    started = time.monotonic()
    r, name = await probe(runtime, ws, "import time; time.sleep(120)", timeout=5)
    gone = False
    for _ in range(20):
        ps = subprocess.run([runtime, "ps", "-a", "-q", "--filter", f"name={name}"],
                            capture_output=True, text=True)
        if not ps.stdout.strip():
            gone = True
            break
        await asyncio.sleep(1)
    ok = r.exit_code is None and "timed out after 5s" in r.note and gone and time.monotonic() - started < 60
    record(runtime, "outlive the timeout", r, ok, f"container gone: {gone}")
    assert ok, (r.note, gone)


# 7 ------------------------------------------------------------------ a normal check still works
async def test_the_unit_converter_tests_pass_inside_the_container(runtime, ws):
    for f in FIXTURE.iterdir():
        if f.is_file():
            shutil.copy(f, ws / f.name)
    s = CheckSpec(name="tests", command=["{python}", "-m", "unittest", "-v"], runner=runtime, image=IMAGE)
    r = await run_check_process(s, ws, containers.container_name("containment", "tests", 0))
    # Docker reports the image id as "sha256:<hex>", Podman as bare hex
    ok = r.passed and "OK" in r.output and bool(re.fullmatch(r"(sha256:)?[0-9a-f]{64}", r.container["image_id"]))
    record(runtime, "normal check: unit-converter unittest", r, ok, r.output.strip().splitlines()[-3:][0])
    assert ok, r.output
