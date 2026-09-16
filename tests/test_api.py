import json
import time

import pytest
from fastapi.testclient import TestClient

from cadre.api import create_app
from cadre.runs import TERMINAL, RunManager

from .conftest import MemorySecrets

TOKEN = "test-token-0123456789abcdef"


@pytest.fixture
def client(home):
    secrets = MemorySecrets()
    manager = RunManager(home, secrets=secrets)
    app = create_app(manager, token=TOKEN)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        c.secrets = secrets
        c.home = home
        yield c


def wait_for(client, rid, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{rid}").json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_every_api_call_needs_the_token(client):
    assert client.get("/api/health", headers={"Authorization": ""}).status_code == 200
    for path in ("/api/runs", "/api/providers", "/api/orgs", "/api/approvals", "/api/quota"):
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/runs", json={"org": "decision-board", "goal": "x", "demo": True},
                       headers={"Authorization": ""}).status_code == 401


def test_foreign_host_header_is_refused(client):
    r = client.get("/api/runs", headers={"Host": "evil.example"})
    assert r.status_code == 421


def test_dashboard_is_served_with_a_strict_policy(client):
    r = client.get("/", headers={"Authorization": ""})
    assert r.status_code == 200 and "<title>Cadre</title>" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "access-control-allow-origin" not in r.headers
    js = client.get("/static/app.js").text
    assert "innerHTML" not in js and "eval(" not in js
    assert client.get("/static/../api.py").status_code == 404


def test_demo_run_end_to_end_over_the_api(client):
    rid = client.post("/api/runs", json={"org": "decision-board", "goal": "Hire a designer?",
                                         "demo": True}).json()["id"]
    run = wait_for(client, rid)
    assert run["status"] == "succeeded"
    assert run["totals"]["calls"] > 0 and any(f["path"] == "DECISION.md" for f in run["files"])
    assert "Vote (counted by Cadre" in client.get(f"/api/runs/{rid}/files/DECISION.md").text
    assert client.get(f"/api/runs/{rid}/files/../../token").status_code == 404
    events = client.get(f"/api/runs/{rid}/events").json()
    assert events[0]["kind"] == "run.started" and events[-1]["kind"] == "run.finished"
    stream = client.get(f"/api/runs/{rid}/stream?after={events[-3]['seq']}")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: run.finished" in stream.text and "event: end" in stream.text
    assert client.post(f"/api/runs/{rid}/resume").status_code == 409


def test_bad_org_and_goal_are_rejected(client):
    r = client.post("/api/runs", json={"yaml": "name: x\nagents: []\nworkflow: {agent: a, task: b}",
                                       "goal": "g"})
    assert r.status_code == 422 and r.json()["detail"]["errors"]
    assert client.post("/api/runs", json={"org": "decision-board", "goal": "  "}).status_code == 400
    v = client.post("/api/orgs/validate", json={"yaml": "name: x\nagents: [{id: a, role: r}]\n"
                                                         "workflow: {agent: b, task: t}"}).json()
    assert v["valid"] is False and "unknown agent 'b'" in v["errors"][0]


def test_keys_go_in_and_never_come_out(client):
    key = "gsk_live_do_not_echo_1234567890"
    r = client.post("/api/providers", json={"preset": "groq", "key": key})
    assert r.status_code == 200 and key not in r.text
    assert r.json()["key"] == "keyring" and r.json()["models"][0]["tpm"] == 8000
    assert client.secrets.data["groq"] == key
    listing = client.get("/api/providers")
    assert key not in listing.text and listing.json()[0]["id"] == "groq"
    assert key not in (client.home.config_path.read_text(encoding="utf-8"))
    quota = client.get("/api/quota").json()
    assert {q["model"] for q in quota} == {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}
    assert client.delete("/api/providers/groq").json()["key_deleted"] is True
    assert "groq" not in client.secrets.data


def test_saving_an_org(client):
    good = client.get("/api/orgs/decision-board").json()["yaml"]
    assert client.put("/api/orgs/Bad Name", json={"yaml": good}).status_code in (400, 404)
    assert client.put("/api/orgs/board", json={"yaml": "nonsense: ["}).status_code == 422
    assert client.put("/api/orgs/board", json={"yaml": good}).json()["valid"] is True
    names = {o["name"]: o["source"] for o in client.get("/api/orgs").json()}
    assert names["board"] == "yours" and names["decision-board"] == "template"


def test_approvals_can_be_decided_once(client):
    org = "name: t\nagents: [{id: a, role: r}]\nworkflow: [{approval: 'ship?'}, {agent: a, task: x}]\n"
    rid = client.post("/api/runs", json={"yaml": org, "goal": "g", "demo": True}).json()["id"]
    for _ in range(100):
        pending = client.get("/api/approvals").json()
        if pending:
            break
        time.sleep(0.05)
    aid = pending[0]["id"]
    assert client.post(f"/api/approvals/{aid}", json={"approve": True}).status_code == 200
    assert client.post(f"/api/approvals/{aid}", json={"approve": False}).status_code == 409
    assert wait_for(client, rid)["status"] == "succeeded"
    decided = [e for e in client.get(f"/api/runs/{rid}/events").json() if e["kind"] == "approval.decided"]
    assert json.dumps(decided[0]["data"]).count("true") >= 1
