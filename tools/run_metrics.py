"""Measurements for the live-verification tables in docs/PROJECT_STATE.md (M5, M11).

    uv run python tools/run_metrics.py <run-id> [<run-id> ...]

Reads only Cadre's own database; never touches keys.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict

from cadre.config import Home
from cadre.store import Store


def metrics(store: Store, rid: str) -> dict:
    run = store.get_run(rid)
    if run is None:
        raise SystemExit(f"no run {rid}")
    events = store.events(rid, limit=100_000)
    usage = store._all("SELECT agent, provider, model, prompt_tokens, completion_tokens, ts "
                       "FROM usage WHERE run_id=? ORDER BY id", (rid,))
    first_prompts = []
    open_steps: set[tuple[str, str]] = set()
    roles: dict[str, Counter] = defaultdict(Counter)
    independence = Counter()
    for e in events:
        k, d = e["kind"], e["data"]
        if k == "agent.start":
            open_steps.add((e["agent"], e["step"]))
        elif k == "agent.call":
            key = (e["agent"], e["step"])
            if key in open_steps:
                first_prompts.append(d["tokens_in"])
                open_steps.discard(key)
            roles[e["agent"]][d["model"]] += 1
            if d.get("independent") is not None:
                independence["independent" if d["independent"] else "not independent"] += 1
    kinds = Counter(e["kind"] for e in events)
    fallbacks = [e["data"].get("note", "") for e in events if e["kind"] == "route.fallback"]
    forecast = next((e["data"] for e in events if e["kind"] == "run.forecast"), None)
    totals = store.usage_totals(rid)
    wall = (run["finished"] or run["updated"]) - run["created"]
    return {
        "run": rid, "org": run["org"], "status": run["status"], "error": run["error"],
        "wall_s": round(wall), "active_s": round(run.get("active_seconds") or 0),
        "calls": totals["calls"], "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "median_first_prompt": statistics.median(first_prompts) if first_prompts else None,
        "median_prompt_all": statistics.median([u["prompt_tokens"] for u in usage]) if usage else None,
        "repairs": kinds["agent.repair"], "invalid": kinds["agent.invalid"],
        "waits": kinds["route.wait"], "fallbacks": len(fallbacks),
        "rate_limited": sum("rate limited" in f for f in fallbacks), "fallback_notes": fallbacks[:8],
        "parks": kinds["run.parked"], "tool_errors": sum(1 for e in events if e["kind"] == "agent.tool"
                                                          and not e["data"].get("ok")),
        "tools": Counter(e["data"]["tool"] for e in events if e["kind"] == "agent.tool"),
        "roles": {a: dict(c) for a, c in roles.items()},
        "independence": dict(independence),
        "forecast": forecast,
    }


def main() -> None:
    store = Store(Home().db_path)
    for rid in sys.argv[1:]:
        print(json.dumps(metrics(store, rid), indent=1, default=str))


if __name__ == "__main__":
    main()
