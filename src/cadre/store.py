"""SQLite persistence (ADR-012). One file, WAL, a lock around every statement.

Everything written here passes through the redactor first, so a key that reaches an error
message or a tool result never reaches the disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .secrets import REDACTOR

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    id TEXT PRIMARY KEY, org TEXT, org_yaml TEXT, goal TEXT, status TEXT, options TEXT,
    created REAL, updated REAL, finished REAL, result TEXT, summary TEXT, error TEXT,
    owner_pid INTEGER);
CREATE TABLE IF NOT EXISTS events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, kind TEXT, agent TEXT,
    step TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS step_results(
    run_id TEXT, path TEXT, text TEXT, data TEXT, ts REAL, PRIMARY KEY(run_id, path));
CREATE TABLE IF NOT EXISTS usage(
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, agent TEXT, provider TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS usage_run ON usage(run_id);
CREATE TABLE IF NOT EXISTS quota_daily(
    key TEXT, day TEXT, requests INTEGER, tokens INTEGER, PRIMARY KEY(key, day));
CREATE TABLE IF NOT EXISTS approvals(
    id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, agent TEXT, step TEXT, prompt TEXT,
    status TEXT, answer TEXT, created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS files(
    run_id TEXT, path TEXT, version INTEGER, sha256 TEXT, bytes INTEGER, agent TEXT, ts REAL,
    PRIMARY KEY(run_id, path, version));
"""

#: v1.0 columns added to v0.1's `runs` table (ADR-016, 017, 020); all nullable or defaulted
RUN_COLUMNS = {"project_path": "TEXT", "base": "TEXT", "branch": "TEXT", "resume_at": "REAL",
               "active_seconds": "REAL DEFAULT 0", "privacy": "TEXT"}
SCHEMA_VERSION = 2

ACTIVE = ("queued", "running", "waiting")
STALE_AFTER = 90.0  # seconds without a heartbeat before an active run counts as interrupted


def _dumps(obj: Any) -> str:
    return json.dumps(REDACTOR.redact_obj(obj), ensure_ascii=False, default=str)


def _loads(text: str | None) -> Any:
    return json.loads(text) if text else None


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None,
                                   timeout=30)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=30000")
            self._db.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Bring a v0.1 database (user_version 0) up to date without touching its rows."""
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(runs)")}
        for name, decl in RUN_COLUMNS.items():
            if name not in cols:
                self._db.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")
        if version < SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @property
    def version(self) -> int:
        with self._lock:
            return self._db.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute(sql, args).fetchone()
        return dict(r) if r else None

    # ------------------------------------------------------------------ runs
    def create_run(self, org: str, org_yaml: str, goal: str, options: dict[str, Any]) -> str:
        rid = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = time.time()
        self._x("INSERT INTO runs(id, org, org_yaml, goal, status, options, created, updated) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rid, org, org_yaml, REDACTOR.redact(goal), "queued", _dumps(options), now, now))
        return rid

    def update_run(self, rid: str, **fields: Any) -> None:
        fields["updated"] = time.time()
        for k in ("result", "summary", "error"):
            if k in fields and fields[k] is not None:
                fields[k] = (_dumps(fields[k]) if k == "summary"
                             else REDACTOR.redact(str(fields[k])))
        if "options" in fields:
            fields["options"] = _dumps(fields["options"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), rid))

    def transition(self, rid: str, from_status: str, to_status: str) -> bool:
        """Change status only if it is still `from_status` (so a cancel is never overwritten)."""
        cur = self._x("UPDATE runs SET status=?, updated=? WHERE id=? AND status=?",
                      (to_status, time.time(), rid, from_status))
        return cur.rowcount == 1

    def heartbeat(self, rid: str) -> None:
        self._x("UPDATE runs SET updated=?, owner_pid=? WHERE id=?", (time.time(), os.getpid(), rid))

    def get_run(self, rid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM runs WHERE id=?", (rid,))
        if row:
            row["options"] = _loads(row["options"]) or {}
            row["summary"] = _loads(row["summary"])
        return row

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._all("SELECT id, org, goal, status, created, updated, finished, error "
                         "FROM runs ORDER BY created DESC LIMIT ?", (limit,))
        for r in rows:
            r.update(self.usage_totals(r["id"]))
        return rows

    def tokens_since(self, rid: str, since: float) -> int:
        row = self._one("SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS t FROM usage "
                        "WHERE run_id=? AND ts>=?", (rid, since))
        return int(row["t"]) if row else 0

    def due_parked(self, now: float) -> list[str]:
        rows = self._all("SELECT id FROM runs WHERE status='parked' AND resume_at IS NOT NULL "
                         "AND resume_at<=? ORDER BY resume_at", (now,))
        return [r["id"] for r in rows]

    def parked(self) -> list[dict[str, Any]]:
        return self._all("SELECT id, org, goal, resume_at, error FROM runs WHERE status='parked' "
                         "ORDER BY resume_at")

    def project_runs(self) -> list[dict[str, Any]]:
        return self._all("SELECT id, org, status, project_path, branch, base, created FROM runs "
                         "WHERE project_path IS NOT NULL ORDER BY created")

    def mark_stale_interrupted(self) -> list[str]:
        """Active runs whose owner stopped heartbeating were interrupted by a crash or restart."""
        cutoff = time.time() - STALE_AFTER
        rows = self._all(f"SELECT id FROM runs WHERE status IN {ACTIVE} AND updated < ?", (cutoff,))
        for r in rows:
            self._x("UPDATE runs SET status='interrupted', updated=? WHERE id=?",
                    (time.time(), r["id"]))
        return [r["id"] for r in rows]

    # ------------------------------------------------------------------ events
    def add_event(self, rid: str, kind: str, agent: str | None = None, step: str | None = None,
                  data: dict[str, Any] | None = None) -> int:
        cur = self._x("INSERT INTO events(run_id, ts, kind, agent, step, data) VALUES(?,?,?,?,?,?)",
                      (rid, time.time(), kind, agent, step, _dumps(data or {})))
        return int(cur.lastrowid or 0)

    def events(self, rid: str, after: int = 0, limit: int = 500,
               kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE run_id=? AND seq>?"
        args: tuple = (rid, after)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        rows = self._all(sql + " ORDER BY seq LIMIT ?", (*args, limit))
        for r in rows:
            r["data"] = _loads(r["data"]) or {}
        return rows

    # ------------------------------------------------------------------ resume cache
    def put_step(self, rid: str, path: str, text: str, data: dict[str, Any]) -> None:
        self._x("INSERT OR REPLACE INTO step_results(run_id, path, text, data, ts) VALUES(?,?,?,?,?)",
                (rid, path, REDACTOR.redact(text), _dumps(data), time.time()))

    def get_step(self, rid: str, path: str) -> tuple[str, dict[str, Any]] | None:
        row = self._one("SELECT text, data FROM step_results WHERE run_id=? AND path=?", (rid, path))
        return (row["text"], _loads(row["data"]) or {}) if row else None

    def step_paths(self, rid: str) -> set[str]:
        return {r["path"] for r in self._all("SELECT path FROM step_results WHERE run_id=?", (rid,))}

    # ------------------------------------------------------------------ usage
    def add_usage(self, rid: str, agent: str, provider: str, model: str, prompt: int,
                  completion: int) -> None:
        self._x("INSERT INTO usage(run_id, agent, provider, model, prompt_tokens, "
                "completion_tokens, ts) VALUES(?,?,?,?,?,?,?)",
                (rid, agent, provider, model, prompt, completion, time.time()))

    def usage_by_agent(self, rid: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT agent, provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens FROM usage WHERE run_id=? "
            "GROUP BY agent, provider, model ORDER BY agent", (rid,))

    def usage_totals(self, rid: str) -> dict[str, int]:
        row = self._one("SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS p, "
                        "COALESCE(SUM(completion_tokens),0) AS c FROM usage WHERE run_id=?", (rid,))
        row = row or {"calls": 0, "p": 0, "c": 0}
        return {"calls": row["calls"], "prompt_tokens": row["p"], "completion_tokens": row["c"]}

    # ------------------------------------------------------------------ quota
    def quota_load(self, key: str, day: str) -> tuple[int, int]:
        row = self._one("SELECT requests, tokens FROM quota_daily WHERE key=? AND day=?", (key, day))
        return (row["requests"], row["tokens"]) if row else (0, 0)

    def quota_rows(self, since_day: str) -> list[dict[str, Any]]:
        """Every counter row whose day key starts on or after `since_day` (YYYY-MM-DD)."""
        return self._all("SELECT key, day, requests, tokens FROM quota_daily WHERE substr(day, 1, 10) >= ? "
                         "ORDER BY day", (since_day,))

    def history(self, org: str, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        """Finished runs of an org with their usage totals — the forecast's measured basis."""
        marks = ",".join("?" * len(statuses))
        rows = self._all(f"SELECT id, status, options, active_seconds, created FROM runs "
                         f"WHERE org=? AND status IN ({marks}) ORDER BY created", (org, *statuses))
        for r in rows:
            r["options"] = _loads(r["options"]) or {}
            r.update(self.usage_totals(r["id"]))
        return rows

    def add_active_seconds(self, rid: str, seconds: float) -> None:
        self._x("UPDATE runs SET active_seconds = COALESCE(active_seconds, 0) + ? WHERE id=?",
                (max(0.0, seconds), rid))

    def quota_save(self, key: str, day: str, requests: int, tokens: int) -> None:
        self._x("INSERT OR REPLACE INTO quota_daily(key, day, requests, tokens) VALUES(?,?,?,?)",
                (key, day, requests, tokens))

    # ------------------------------------------------------------------ approvals
    def create_approval(self, rid: str, kind: str, agent: str | None, step: str | None,
                        prompt: str) -> str:
        aid = uuid.uuid4().hex[:10]
        self._x("INSERT INTO approvals(id, run_id, kind, agent, step, prompt, status, created) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (aid, rid, kind, agent, step, REDACTOR.redact(prompt), "pending", time.time()))
        return aid

    def decide(self, aid: str, approve: bool, answer: str = "") -> bool:
        cur = self._x("UPDATE approvals SET status=?, answer=?, decided=? "
                      "WHERE id=? AND status='pending'",
                      ("approved" if approve else "rejected", REDACTOR.redact(answer),
                       time.time(), aid))
        return cur.rowcount == 1

    def get_approval(self, aid: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM approvals WHERE id=?", (aid,))

    def approvals(self, rid: str | None = None, pending_only: bool = False) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM approvals WHERE 1=1", ()
        if rid:
            sql, args = sql + " AND run_id=?", (rid,)
        if pending_only:
            sql += " AND status='pending'"
        return self._all(sql + " ORDER BY created", args)

    def cancel_pending_approvals(self, rid: str) -> None:
        self._x("UPDATE approvals SET status='cancelled', decided=? "
                "WHERE run_id=? AND status='pending'", (time.time(), rid))

    # ------------------------------------------------------------------ files
    def add_file(self, rid: str, path: str, version: int, sha: str, size: int, agent: str) -> None:
        self._x("INSERT OR REPLACE INTO files(run_id, path, version, sha256, bytes, agent, ts) "
                "VALUES(?,?,?,?,?,?,?)", (rid, path, version, sha, size, agent, time.time()))

    def files(self, rid: str) -> list[dict[str, Any]]:
        return self._all("SELECT path, MAX(version) AS versions, bytes, agent, sha256, ts "
                         "FROM files WHERE run_id=? GROUP BY path ORDER BY path", (rid,))
