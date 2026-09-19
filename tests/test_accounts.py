"""Users, roles, tokens and the audit log (M13, FR-23, ADR-032/033)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from cadre.accounts import CAPABILITIES, Accounts, User, can, token_hash
from cadre.api import create_app
from cadre.runs import RunManager
from cadre.store import SCHEMA_VERSION, Store

from .conftest import MemorySecrets


# ---------------------------------------------------------------- roles
def test_roles_are_nested_least_to_most():
    assert can("viewer", "read") and not can("viewer", "run") and not can("viewer", "approve")
    assert can("member", "run") and can("member", "approve") and not can("member", "providers")
    assert all(can("admin", c) for c in ("read", "run", "approve", "providers", "admin"))
    # every role can read; only admin manages providers and users
    assert all("read" in caps for caps in CAPABILITIES.values())
    assert {r for r in CAPABILITIES if can(r, "admin")} == {"admin"}
    assert User("v", "V", "viewer").capabilities == ["read"]


# ---------------------------------------------------------------- tokens are hashed at rest
def test_tokens_are_stored_hashed_never_in_the_clear(store):
    accounts = Accounts(store)
    accounts.create_user("bob", "Bob", "member")
    secret = accounts.mint_token("bob", "laptop")
    assert len(secret) > 30
    # the raw secret is nowhere in the database; only its SHA-256 is
    dump = "\n".join(row[0] for row in store._db.execute(
        "SELECT hash || '|' || COALESCE(label,'') || '|' || id FROM tokens"))
    assert secret not in dump and token_hash(secret) in dump
    assert accounts.resolve(secret).id == "bob"
    assert accounts.resolve(secret[:-1] + ("x" if secret[-1] != "x" else "y")) is None
    assert accounts.resolve("") is None and accounts.resolve(None) is None


def test_a_revoked_or_disabled_user_cannot_authenticate(store):
    accounts = Accounts(store)
    accounts.create_user("bob", "Bob", "member")
    t1 = accounts.mint_token("bob")
    t2 = accounts.mint_token("bob")
    tid = [r["id"] for r in accounts.list_tokens("bob")][0]
    accounts.revoke_token(tid)
    assert (accounts.resolve(t1) is None) != (accounts.resolve(t2) is None)  # exactly one revoked
    live = t2 if accounts.resolve(t2) else t1
    accounts.set_disabled("bob", True)
    assert accounts.resolve(live) is None                                    # disabled: no token works
    accounts.set_disabled("bob", False)
    assert accounts.resolve(live).id == "bob"


# ---------------------------------------------------------------- bootstrap and guards
def test_bootstrap_turns_the_owner_token_into_the_admin(store):
    accounts = Accounts(store)
    accounts.bootstrap("owner-secret-token-xyz")
    assert accounts.resolve("owner-secret-token-xyz").role == "admin"
    accounts.bootstrap("a-different-token")           # idempotent: only the first token wins
    assert accounts.resolve("a-different-token") is None
    assert store.user_count() == 1


def test_the_last_enabled_admin_cannot_be_removed(store):
    accounts = Accounts(store)
    accounts.bootstrap("owner-token-123456789")
    with pytest.raises(Exception, match="only enabled admin"):
        accounts.set_disabled("owner", True)
    with pytest.raises(Exception, match="only enabled admin"):
        accounts.set_role("owner", "member")
    accounts.create_user("bob", "Bob", "admin")       # a second admin frees the first
    accounts.set_role("owner", "member")
    assert accounts.list_users()[0]["role"] == "member"


def test_bad_user_input_is_refused(store):
    accounts = Accounts(store)
    with pytest.raises(Exception, match="not a valid user id"):
        accounts.create_user("Bad Id!", "x", "member")
    with pytest.raises(Exception, match="role must be one of"):
        accounts.create_user("bob", "Bob", "superuser")
    accounts.create_user("bob", "Bob", "member")
    with pytest.raises(Exception, match="already exists"):
        accounts.create_user("bob", "Bob", "member")


# ---------------------------------------------------------------- audit log
def test_every_account_change_is_audited(store):
    accounts = Accounts(store)
    accounts.bootstrap("owner-token-abcdef123")
    admin = accounts.resolve("owner-token-abcdef123")
    accounts.create_user("bob", "Bob", "member", actor=admin)
    tid_secret = accounts.mint_token("bob", "ci", actor=admin)  # noqa: F841
    tid = accounts.list_tokens("bob")[0]["id"]
    accounts.revoke_token(tid, actor=admin)
    actions = [(e["actor"], e["action"], e["target"]) for e in accounts.audit_log()]
    assert ("owner", "user.created", "bob") in actions
    assert ("owner", "token.created", tid) in actions
    assert ("owner", "token.revoked", tid) in actions
    assert ("system", "user.bootstrapped", "owner") in actions


# ---------------------------------------------------------------- migration from v2
def test_a_v2_database_migrates_and_keeps_its_rows(tmp_path):
    p = tmp_path / "old.sqlite"
    db = sqlite3.connect(p)
    db.executescript(
        "CREATE TABLE runs(id TEXT PRIMARY KEY, org TEXT, org_yaml TEXT, goal TEXT, status TEXT, "
        "options TEXT, created REAL, updated REAL, finished REAL, result TEXT, summary TEXT, "
        "error TEXT, owner_pid INTEGER, project_path TEXT, base TEXT, branch TEXT, resume_at REAL, "
        "active_seconds REAL DEFAULT 0, privacy TEXT);"
        "INSERT INTO runs(id, org, status) VALUES('r-old', 'decision-board', 'succeeded');"
        "PRAGMA user_version=2;")
    db.commit()
    db.close()
    store = Store(p)
    assert store.version == SCHEMA_VERSION == 5
    assert store.get_run("r-old")["status"] == "succeeded"          # untouched
    cols = {r[1] for r in store._db.execute("PRAGMA table_info(runs)")}
    assert {"owner_user", "owner_team"} <= cols
    accounts = Accounts(store)                                       # the new tables exist and work
    accounts.create_user("bob", "Bob", "member")
    accounts.create_team("eng", "Engineering")
    accounts.add_member("eng", "bob")
    assert [u["id"] for u in accounts.list_users()] == ["bob"]
    assert store.user_teams("bob") == ["eng"]
    store.close()


# ---------------------------------------------------------------- API: RBAC
TOKEN = "admin-owner-token-0123456789"


@pytest.fixture
def rbac(home):
    manager = RunManager(home, secrets=MemorySecrets())
    app = create_app(manager, token=TOKEN, resume_every=None)
    accounts = manager.accounts
    accounts.create_user("mem", "Mem", "member")
    accounts.create_user("vw", "View", "viewer")
    tokens = {"admin": TOKEN, "member": accounts.mint_token("mem"), "viewer": accounts.mint_token("vw")}
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c, tokens


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_health_is_open_but_everything_else_needs_a_token(rbac):
    c, tokens = rbac
    assert c.get("/api/v1/health").json()["ok"] is True
    assert c.get("/api/v1/runs").status_code == 401
    assert c.get("/api/v1/runs", headers=hdr("not-a-real-token")).status_code == 401


def test_me_reports_the_caller(rbac):
    c, tokens = rbac
    for role, tok in tokens.items():
        me = c.get("/api/v1/me", headers=hdr(tok)).json()
        assert me["role"] == role and "read" in me["capabilities"]


def test_a_viewer_can_read_but_not_run_or_manage(rbac):
    c, tokens = rbac
    v = hdr(tokens["viewer"])
    assert c.get("/api/v1/runs", headers=v).status_code == 200
    assert c.get("/api/v1/quota", headers=v).status_code == 200
    assert c.post("/api/v1/runs", json={"org": "decision-board", "goal": "x", "demo": True},
                  headers=v).status_code == 403
    assert c.get("/api/v1/users", headers=v).status_code == 403
    assert c.get("/api/v1/audit", headers=v).status_code == 403
    assert c.post("/api/v1/providers", json={"preset": "groq"}, headers=v).status_code == 403


def test_a_member_may_run_and_approve_but_not_manage_users_or_providers(rbac):
    c, tokens = rbac
    m = hdr(tokens["member"])
    r = c.post("/api/v1/runs", json={"org": "decision-board", "goal": "pick one", "demo": True}, headers=m)
    assert r.status_code == 200, r.text
    assert c.get("/api/v1/users", headers=m).status_code == 403
    assert c.post("/api/v1/providers", json={"preset": "groq"}, headers=m).status_code == 403
    # the run records who started it, and the audit log names the member
    rid = r.json()["id"]
    assert c.get(f"/api/v1/runs/{rid}", headers=m).status_code == 200
    audit = c.get("/api/v1/audit", headers=hdr(tokens["admin"])).json()
    assert any(e["action"] == "run.started" and e["actor"] == "mem" and e["target"] == rid for e in audit)


def test_only_an_admin_sees_users_and_the_audit_log(rbac):
    c, tokens = rbac
    a = hdr(tokens["admin"])
    users = c.get("/api/v1/users", headers=a).json()
    assert {u["id"] for u in users} == {"owner", "mem", "vw"}
    assert c.get("/api/v1/audit", headers=a).status_code == 200
    # no token secret is ever returned by any endpoint
    assert all("hash" not in u and "token" not in u for u in users)


# ================================================================ M13 phase 2: teams
from cadre.accounts import AccountError, model_allowed  # noqa: E402


def test_model_allowance_matching():
    assert model_allowed([], "groq", "groq/x") is True          # empty = all
    assert model_allowed(["*"], "groq", "groq/x") is True
    assert model_allowed(["groq"], "groq", "groq/llama") is True
    assert model_allowed(["groq/llama"], "groq", "groq/llama") is True
    assert model_allowed(["groq"], "gemini", "gemini/pro") is False
    assert model_allowed(["groq/a"], "groq", "groq/b") is False


def test_teams_membership_and_budget_store(store):
    accounts = Accounts(store)
    accounts.create_user("bob", "Bob", "member")
    accounts.create_user("amy", "Amy", "member")
    accounts.create_team("eng", "Engineering")
    accounts.add_member("eng", "bob")
    accounts.add_member("eng", "amy")
    assert set(accounts.team("eng")["members"]) == {"bob", "amy"}
    assert accounts.user_teams("bob") == ["eng"]
    accounts.remove_member("eng", "amy")
    assert accounts.team("eng")["members"] == ["bob"]
    accounts.set_budget("eng", runs_per_day=5, tokens_per_day=100000, max_concurrent=2)
    assert store.get_budget("eng") == {"runs_per_day": 5, "tokens_per_day": 100000, "max_concurrent": 2}
    accounts.set_allow("eng", ["groq", "groq", "  gemini/pro  "])
    assert accounts.team("eng")["allow"] == ["gemini/pro", "groq"]   # deduped, trimmed, sorted


def test_team_operations_validate(store):
    accounts = Accounts(store)
    with pytest.raises(AccountError, match="not a valid team id"):
        accounts.create_team("Bad Team!")
    accounts.create_team("eng")
    with pytest.raises(AccountError, match="already exists"):
        accounts.create_team("eng")
    with pytest.raises(AccountError, match="no user"):
        accounts.add_member("eng", "ghost")
    with pytest.raises(AccountError, match="not a member"):
        accounts.remove_member("eng", "ghost")
    accounts.create_user("bob", "Bob", "member")
    with pytest.raises(AccountError, match="cannot be negative"):
        accounts.set_budget("eng", runs_per_day=-1, tokens_per_day=None, max_concurrent=None)


def test_team_for_run_picks_the_right_team(store):
    accounts = Accounts(store)
    accounts.create_user("bob", "Bob", "member")
    accounts.create_team("eng")
    accounts.create_team("ops")
    bob = accounts.resolve_user("bob")
    assert accounts.team_for_run(bob, None) is None                 # in no team: personal
    accounts.add_member("eng", "bob")
    assert accounts.team_for_run(bob, None) == "eng"                # in one: that one
    accounts.add_member("ops", "bob")
    assert accounts.team_for_run(bob, None) is None                # in many: personal unless named
    assert accounts.team_for_run(bob, "ops") == "ops"
    with pytest.raises(AccountError, match="no team"):
        accounts.team_for_run(bob, "nope")             # a team that does not exist
    accounts.create_team("secret")
    with pytest.raises(AccountError, match="not a member"):
        accounts.team_for_run(bob, "secret")           # a team bob is not in
    admin = User("root", "Root", "admin")
    assert accounts.team_for_run(admin, "secret") == "secret"       # admins may use any team


def test_may_act_on_run_is_scoped_to_owner_team_or_admin(store):
    accounts = Accounts(store)
    accounts.create_user("bob", "Bob", "member")
    accounts.create_user("amy", "Amy", "member")
    accounts.create_team("eng")
    accounts.add_member("eng", "bob")
    bob, amy = accounts.resolve_user("bob"), accounts.resolve_user("amy")
    admin = User("root", "Root", "admin")
    team_run = {"owner_user": "bob", "owner_team": "eng"}
    assert accounts.may_act_on_run(bob, team_run) is True          # a teammate
    assert accounts.may_act_on_run(amy, team_run) is False         # outside the team
    assert accounts.may_act_on_run(admin, team_run) is True        # admin
    personal = {"owner_user": "bob", "owner_team": None}
    assert accounts.may_act_on_run(amy, personal) is False         # not the owner
    assert accounts.may_act_on_run(bob, personal) is True
    ownerless = {"owner_user": None, "owner_team": None}           # pre-accounts / CLI run
    assert accounts.may_act_on_run(amy, ownerless) is True         # any member+
    assert accounts.may_act_on_run(User("v", "V", "viewer"), ownerless) is False


# ---------------------------------------------------------------- teams over the run manager / API
def test_team_budget_gate_and_allowance_at_run_start(home):
    manager = RunManager(home, secrets=MemorySecrets())
    create_app(manager, token="tok-admin-000000000000", resume_every=None)  # bootstrap owner
    a = manager.accounts
    a.create_user("bob", "Bob", "member")
    a.create_team("eng")
    a.add_member("eng", "bob")
    a.set_budget("eng", runs_per_day=1, tokens_per_day=None, max_concurrent=None)
    # a demo run for the team is recorded against it
    rid = manager.create("decision-board", "pick one", demo=True, owner="bob")
    assert manager.store.get_run(rid)["owner_team"] == "eng"
    # the second run today exceeds runs_per_day=1
    with pytest.raises(AccountError, match="runs for today"):
        manager.create("decision-board", "again", demo=True, owner="bob")
    # an allowance that matches nothing configured fails fast (non-demo)
    a.set_budget("eng", None, None, None)
    a.set_allow("eng", ["no-such-provider"])
    with pytest.raises(ValueError, match="matches no configured model"):
        manager.create("decision-board", "x", owner="bob")


@pytest.fixture
def teamed(home):
    manager = RunManager(home, secrets=MemorySecrets())
    app = create_app(manager, token=TOKEN, resume_every=None)
    a = manager.accounts
    a.create_user("bob", "Bob", "member")
    a.create_user("amy", "Amy", "member")
    a.create_team("eng")
    a.add_member("eng", "bob")
    tokens = {"admin": TOKEN, "bob": a.mint_token("bob"), "amy": a.mint_token("amy")}
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c, tokens, manager


def test_me_lists_the_callers_teams(teamed):
    c, tokens, _ = teamed
    assert c.get("/api/v1/me", headers=hdr(tokens["bob"])).json()["teams"] == ["eng"]
    assert c.get("/api/v1/me", headers=hdr(tokens["amy"])).json()["teams"] == []


def test_only_a_teammate_or_admin_can_cancel_a_team_run(teamed):
    c, tokens, manager = teamed
    r = c.post("/api/v1/runs", json={"org": "decision-board", "goal": "x", "demo": True, "team": "eng"},
               headers=hdr(tokens["bob"]))
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert manager.store.get_run(rid)["owner_team"] == "eng"
    assert c.post(f"/api/v1/runs/{rid}/cancel", headers=hdr(tokens["amy"])).status_code == 403
    assert c.post(f"/api/v1/runs/{rid}/cancel", headers=hdr(tokens["admin"])).status_code == 200


def test_starting_a_run_for_a_team_you_are_not_in_is_refused(teamed):
    c, tokens, _ = teamed
    r = c.post("/api/v1/runs", json={"org": "decision-board", "goal": "x", "demo": True, "team": "eng"},
               headers=hdr(tokens["amy"]))
    assert r.status_code == 403 and "not a member" in r.json()["detail"]


def test_admin_sees_teams_but_a_member_does_not(teamed):
    c, tokens, _ = teamed
    teams = c.get("/api/v1/teams", headers=hdr(tokens["admin"])).json()
    assert {t["id"] for t in teams} == {"eng"} and teams[0]["members"] == ["bob"]
    assert c.get("/api/v1/teams", headers=hdr(tokens["bob"])).status_code == 403
