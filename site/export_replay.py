"""Export one real run's stored event log for the docs site's replay page (FR-20).

    uv run python site/export_replay.py                 # the M5 decision-board run ...230536
    uv run python site/export_replay.py 230536 --db PATH --out PATH

Reads Cadre's SQLite store read-only (CADRE_HOME, default ~/.cadre). Keeps only the fields the page
shows, rewrites local paths to a neutral ~/.cadre/... form, cuts long texts to about 600 characters,
and refuses to write anything if a key shape, the API token, or the account's user name survives.
The site build never opens the database: it reads the JSON this writes, which is committed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

SITE = Path(__file__).resolve().parent
DEFAULT_OUT = SITE / "assets" / "replay-decision-board.json"
TEXT_CAP = 600
SHORT_CAP = 300

# Windows and POSIX absolute paths. A Windows path's middle segments may contain spaces (user names
# often do); the last segment stops at whitespace or a quote.
WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\/\n'\"<>|:*?]+[\\/])*[^\\/\s'\"<>|:*?,;)\]]*")
POSIX_PATH = re.compile(r"(?<![\w.~:/])/(?:home|Users)/[^\s'\"<>|,;)\]]*")

# Anything that must not survive into a public file. Written so this file never matches itself.
FORBIDDEN = {
    "Groq key prefix": re.compile(r"g[s]k_", re.I),
    "Google key prefix": re.compile(r"A[I]za", re.I),
    "OpenAI-style key prefix": re.compile(r"(?<![A-Za-z])s[k]-", re.I),
    "OpenRouter key": re.compile(r"s[k]-or-v1-", re.I),
    "Hugging Face token": re.compile(r"\bh[f]_[A-Za-z0-9]{20,}"),
    "NVIDIA key": re.compile(r"nv[a]pi-", re.I),
    "home folder name": re.compile(r"ri[s]hi", re.I),
    "laptop account name": re.compile(r"m[p]ps[\s_-]*kana[j]iguda|kana[j]", re.I),
    "Windows user path": re.compile(r"users[\\/]", re.I),
    "Windows app-data folder": re.compile(r"app[d]ata", re.I),
    ".cadre outside ~/.cadre": re.compile(r"(?<!~/)\.cadre\b"),
}


def cadre_home() -> Path:
    return Path(os.environ.get("CADRE_HOME") or Path.home() / ".cadre")


def neutral_path(raw: str) -> str:
    """A local absolute path, as the page may show it."""
    p = raw.replace("\\", "/")
    low = p.lower()
    at = low.find("/.cadre/")
    if at >= 0:
        return "~/.cadre/" + p[at + len("/.cadre/"):]
    if low.endswith("/.cadre"):
        return "~/.cadre"
    home = str(cadre_home()).replace("\\", "/").rstrip("/")
    if home and low.startswith(home.lower() + "/"):
        return "~/.cadre/" + p[len(home) + 1:]
    name = p.rstrip("/").rsplit("/", 1)[-1]
    return f"~/.../{name}" if name else "~/..."


def scrub(s: str) -> str:
    home = str(cadre_home())
    for variant in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        s = re.sub(re.escape(variant), "~/.cadre", s, flags=re.I)
    s = WIN_PATH.sub(lambda m: neutral_path(m.group(0)), s)
    s = POSIX_PATH.sub(lambda m: neutral_path(m.group(0)), s)
    s = re.sub(r"~/\.cadre[^\s'\"<>|,;)\]]*", lambda m: m.group(0).replace("\\", "/"), s)
    return s


def clip(s: str | None, cap: int = TEXT_CAP) -> str | None:
    if s is None:
        return None
    s = scrub(str(s))
    return s if len(s) <= cap else s[:cap].rstrip() + " …"


def vote_of(text: str) -> dict | None:
    """A council vote is strict JSON by design (ADR-007); show its three fields."""
    try:
        v = json.loads(text)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            return None
        try:
            v = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(v, dict) or "choice" not in v:
        return None
    return {"choice": str(v.get("choice")), "confidence": v.get("confidence"),
            "reason": clip(v.get("reason"), SHORT_CAP)}


def slim(kind: str, agent: str | None, step: str | None, d: dict, t: float) -> dict:
    e: dict = {"t": round(t, 2), "kind": kind}
    if agent:
        e["agent"] = agent
    if step:
        e["step"] = step
    if kind == "run.started":
        e.update(org=d.get("org"), goal=clip(d.get("goal")), models=d.get("models", []),
                 workspace=clip(d.get("workspace")), private=bool(d.get("private")),
                 resumed=bool(d.get("resumed")))
    elif kind == "run.forecast":
        est = d.get("estimate") or {}
        e.update(verdict=d.get("verdict"), headline=d.get("headline"), basis=est.get("basis"),
                 calls=est.get("calls"), tokens=est.get("tokens"))
    elif kind in ("step.started", "step.finished"):
        e["type"] = d.get("type")
        if kind == "step.finished":
            e.update(approved=d.get("approved"), text=clip(d.get("text")))
    elif kind == "agent.start":
        e["task"] = clip(d.get("task"))
    elif kind == "agent.call":
        e.update(model=d.get("model"), family=d.get("family"), tokens_in=d.get("tokens_in"),
                 tokens_out=d.get("tokens_out"), independent=d.get("independent"),
                 waited=d.get("waited"), fallbacks=len(d.get("fallbacks") or []),
                 tools=len(d.get("tools") or []), finish=d.get("finish"))
    elif kind == "agent.answer":
        e.update(model=d.get("model"), text=clip(d.get("text")))
        if step and "/vote/" in f"/{step}/":
            vote = vote_of(d.get("text", ""))
            if vote:
                e["vote"] = vote
    elif kind == "route.wait":
        e.update(model=d.get("model"), seconds=d.get("seconds"), reason=clip(d.get("reason"), SHORT_CAP))
    elif kind == "route.fallback":
        e.update(model=d.get("model"), note=clip(d.get("note")), failure=d.get("failure"))
    elif kind == "council.tally":
        e.update(rule=d.get("rule"), counts=d.get("counts"), abstained=d.get("abstained", []),
                 winner=d.get("winner"), decided_by=d.get("decided_by"))
    elif kind == "run.finished":
        e.update(status=d.get("status"), calls=d.get("calls"), tokens_in=d.get("prompt_tokens"),
                 tokens_out=d.get("completion_tokens"), files=d.get("files"),
                 error=clip(d.get("error"), SHORT_CAP))
    else:  # a kind this page does not know: a few short scalar fields, nothing nested
        kept = {k: v for k, v in d.items() if isinstance(v, (int, float, bool))
                or (isinstance(v, str) and k in ("text", "tool", "name", "status", "reason", "note"))}
        for k, v in list(kept.items())[:4]:
            e[k] = clip(v, SHORT_CAP) if isinstance(v, str) else v
    return e


def export(db: Path, suffix: str) -> dict:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    runs = con.execute("SELECT id, org, goal, status, created, finished, updated FROM runs "
                       "WHERE id LIKE ?", (f"%{suffix}%",)).fetchall()
    if len(runs) != 1:
        raise SystemExit(f"expected one run matching {suffix!r}, found {len(runs)}")
    run = runs[0]
    rows = con.execute("SELECT seq, ts, kind, agent, step, data FROM events WHERE run_id=? ORDER BY seq",
                       (run["id"],)).fetchall()
    if not rows:
        raise SystemExit(f"run {run['id']} has no events")
    t0 = rows[0]["ts"]
    events = [slim(r["kind"], r["agent"], r["step"], json.loads(r["data"] or "{}"), r["ts"] - t0) for r in rows]
    calls = [e for e in events if e["kind"] == "agent.call"]
    models: dict[str, int] = {}
    for c in calls:
        models[c["model"]] = models.get(c["model"], 0) + 1
    done = next((e for e in reversed(events) if e["kind"] == "run.finished"), {})
    started = datetime.fromtimestamp(run["created"], UTC)
    return {
        "format": 1,
        "source": "Cadre's stored event log (SQLite table `events`), exported by site/export_replay.py. "
                  "Paths rewritten, long texts cut to about 600 characters, other fields dropped.",
        "run": {
            "id": run["id"], "short": "…" + run["id"].split("-")[1], "org": run["org"],
            "goal": clip(run["goal"]), "status": run["status"],
            "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date": f"{run['id'][:4]}-{run['id'][4:6]}-{run['id'][6:8]}",
            "duration_s": events[-1]["t"], "events": len(events), "calls": len(calls),
            "tokens_in": done.get("tokens_in", sum(c["tokens_in"] or 0 for c in calls)),
            "tokens_out": done.get("tokens_out", sum(c["tokens_out"] or 0 for c in calls)),
            "models_used": models,
        },
        "events": events,
    }


def findings(text: str) -> list[str]:
    """Names of what must not be published and was found (never the matched text itself)."""
    found = [name for name, rx in FORBIDDEN.items() if rx.search(text)]
    user = Path.home().name
    if user and user.lower() in text.lower():
        found.append("this account's user name")
    token_file = cadre_home() / "token"
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token and token in text:
        found.append("the API token")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("suffix", nargs="?", default="230536", help="part of the run id (default 230536)")
    ap.add_argument("--db", type=Path, default=cadre_home() / "cadre.sqlite")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    data = export(args.db, args.suffix)
    lines = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in data["events"])
    head = {k: v for k, v in data.items() if k != "events"}
    text = json.dumps(head, ensure_ascii=False, indent=1)[:-2] + ',\n "events": [\n' + lines + "\n ]\n}\n"
    json.loads(text)  # still valid JSON
    bad = findings(text)
    if bad:
        print("refusing to write: found " + ", ".join(bad), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    r = data["run"]
    print(f"{args.out.name}: run {r['id']}, {r['events']} events, {r['calls']} calls, "
          f"{r['duration_s']} s, {len(text.encode('utf-8')):,} bytes; scrub check clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
