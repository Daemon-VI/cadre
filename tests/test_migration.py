import sqlite3

from cadre.store import SCHEMA_VERSION, Store

V01_SCHEMA = """
CREATE TABLE runs(
    id TEXT PRIMARY KEY, org TEXT, org_yaml TEXT, goal TEXT, status TEXT, options TEXT,
    created REAL, updated REAL, finished REAL, result TEXT, summary TEXT, error TEXT,
    owner_pid INTEGER);
CREATE TABLE events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, kind TEXT, agent TEXT,
    step TEXT, data TEXT);
CREATE TABLE step_results(
    run_id TEXT, path TEXT, text TEXT, data TEXT, ts REAL, PRIMARY KEY(run_id, path));
CREATE TABLE usage(
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, agent TEXT, provider TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, ts REAL);
CREATE TABLE quota_daily(
    key TEXT, day TEXT, requests INTEGER, tokens INTEGER, PRIMARY KEY(key, day));
CREATE TABLE approvals(
    id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, agent TEXT, step TEXT, prompt TEXT,
    status TEXT, answer TEXT, created REAL, decided REAL);
CREATE TABLE files(
    run_id TEXT, path TEXT, version INTEGER, sha256 TEXT, bytes INTEGER, agent TEXT, ts REAL,
    PRIMARY KEY(run_id, path, version));
"""


def test_v01_database_opens_and_keeps_rows(tmp_path):
    path = tmp_path / "cadre.sqlite"
    db = sqlite3.connect(path)
    db.executescript(V01_SCHEMA)
    db.execute("INSERT INTO runs(id, org, org_yaml, goal, status, options, created, updated) "
               "VALUES('r1', 'board', 'name: board', 'old goal', 'succeeded', '{}', 1, 2)")
    db.execute("INSERT INTO quota_daily VALUES('groq/m', '2026-09-16', 12, 3400)")
    db.execute("INSERT INTO usage(run_id, agent, provider, model, prompt_tokens, completion_tokens, ts) "
               "VALUES('r1', 'a', 'groq', 'm', 100, 20, 1)")
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    db.close()

    store = Store(path)
    assert store.version == SCHEMA_VERSION
    run = store.get_run("r1")
    assert run["goal"] == "old goal" and run["status"] == "succeeded"
    assert run["project_path"] is None and run["active_seconds"] == 0 and run["privacy"] is None
    assert store.quota_load("groq/m", "2026-09-16") == (12, 3400)
    assert store.usage_totals("r1") == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20,
                                        "memory_tokens": 0}
    store.update_run("r1", resume_at=123.0, branch="cadre/r1")
    assert store.get_run("r1")["branch"] == "cadre/r1"
    store.close()
    # opening again is a no-op
    again = Store(path)
    assert again.version == SCHEMA_VERSION and again.get_run("r1")["resume_at"] == 123.0
    again.close()
