"""Usage ledger and forecast (FR-11, ADR-019, NFR-11).

The ledger is the store's per-model day counters, grouped by date. The forecast answers "will
this job fit in the quota left?" from measured history when there is any, and otherwise from
the size of the org file — and always says which of the two it used.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .agent import system_prompt, user_prompt
from .clocks import DayClock, ist
from .config import CadreConfig
from .org import (
    AgentSpec,
    ApprovalStep,
    CouncilStep,
    ManagerStep,
    OrgSpec,
    ParallelStep,
    ReviewLoopStep,
    SequenceStep,
)
from .providers import estimate_tokens
from .router import ModelEntry, Router
from .store import Store
from .tools import specs_for
from .types import Message

#: pacing below this many minutes still counts as "fits now"
NOW_MINUTES = 2.0
# Per-call token model, calibrated on the first live runs (M5, 2026-09-17, Groq + Gemini):
# agents that only write or talk sent about twice their base prompt per call; agents that read
# other agents' files (editors, checkers, QA) sent 3.8-4.8k; writers produced 450-830 output
# tokens a call, readers 120-210, council members about 450.
IN_GROWTH = 2.0
READ_CONTEXT = 3000       # without a project; with one, its text size (clamped) is used
READ_CONTEXT_MIN = 500
OUT_WRITER = 700
OUT_READER = 200
OUT_OTHER = 450
READ_TOOLS = frozenset({"list_files", "read_file", "search"})
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
MEASURED_STATUSES = ("succeeded", "unapproved")


# --------------------------------------------------------------------------- ledger

def usage_ledger(store: Store, cfg: CadreConfig, days: int = 7, now: float | None = None) -> list[dict[str, Any]]:
    """Requests, tokens and share of the daily cap per day × provider × model, newest first."""
    now = time.time() if now is None else now
    since = (datetime.fromtimestamp(now, UTC) - timedelta(days=max(1, days) - 1)).strftime("%Y-%m-%d")
    limits: dict[str, tuple[Any, Any, DayClock]] = {}
    for pc in cfg.providers:
        clock = DayClock(pc.clock())
        for m in pc.models:
            lim = m.limits(pc.reserve_pct)
            limits[f"{pc.id}/{m.name}"] = (m.rpd, m.tpd, clock, lim)
    grouped: dict[tuple[str, str], list[int]] = {}
    for row in store.quota_rows(since):
        date = row["day"][:10]
        g = grouped.setdefault((date, row["key"]), [0, 0])
        g[0] += row["requests"]
        g[1] += row["tokens"]
    out = []
    for (date, key), (req, tok) in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True):
        provider, _, model = key.partition("/")
        rpd, tpd, clock, _lim = limits.get(key, (None, None, None, None))
        shares = [req / rpd if rpd else 0.0, tok / tpd if tpd else 0.0]
        current = clock is not None and clock.key(now)[:10] == date
        out.append({
            "day": date, "provider": provider, "model": model, "requests": req, "tokens": tok,
            "rpd": rpd, "tpd": tpd, "share": round(max(shares), 3) if (rpd or tpd) else None,
            "day_reset": clock.spec if clock else None,
            "next_reset": ist(clock.next_reset(now)) if current else None,
        })
    return out


# --------------------------------------------------------------------------- estimates

def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; 0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return float(ordered[rank - 1])


@dataclass
class Estimate:
    calls: float
    calls_p90: float
    tokens: float
    tokens_p90: float
    minutes: float | None
    n: int
    largest_call: int
    basis: str
    per_step: list[str] = field(default_factory=list)
    #: estimated memory tokens carried across the run (FR-24); 0 when memory is empty or off
    memory_tokens: int = 0


def measured(store: Store, org_name: str) -> list[dict[str, Any]]:
    rows = store.history(org_name, MEASURED_STATUSES)
    return [r for r in rows if not (r.get("options") or {}).get("demo") and r["calls"] > 0]


def _agent_call_tokens(org: OrgSpec, agent: AgentSpec, goal: str, task: str = "",
                       tools: list[str] | None = None) -> int:
    """Prompt tokens of one call to this agent, assembled exactly as a run would assemble it."""
    ctx = SimpleNamespace(org=org, goal=goal, notes=[],
                          workspace=SimpleNamespace(listing=lambda limit=40: "(empty)"))
    names = agent.tools if tools is None else tools
    msgs = [Message(role="system", content=system_prompt(ctx, agent, None)),
            Message(role="user", content=user_prompt(ctx, task or "(task)", ""))]
    return estimate_tokens(msgs, specs_for(names))


def project_text_tokens(root: str | Path) -> int:
    """Tokens of text a reader could pull in from a project (tracked text files, ~4 chars/token)."""
    from .project import git

    try:
        names = git(["ls-files"], root).stdout.splitlines()
    except Exception:
        return READ_CONTEXT
    total = 0
    for n in names:
        p = Path(root) / n
        try:
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".pdf", ".ico", ".exe"}:
                continue
            total += p.stat().st_size
        except OSError:
            continue
    return total // 4


def from_template(org: OrgSpec, goal: str, largest_call: int,
                  read_context: int | None = None, mem_per_call: int = 0) -> Estimate:
    """Calls and tokens implied by the workflow tree (no history). Assumptions are stated in ADR-019:
    an agent with tools takes 3 calls (act, act, answer), without tools 1; a strict-JSON answer
    needs a repair turn one time in four; review loops need 2 rounds typically, all rounds at p90.
    `mem_per_call` is the memory block size added to each builder/manager/worker call (FR-24)."""
    lines: list[str] = []
    mem = [0.0]  # memory tokens accumulated across memory-receiving calls (p50 basis)
    context = READ_CONTEXT if read_context is None else max(READ_CONTEXT_MIN, min(READ_CONTEXT, read_context))
    if read_context is not None:
        lines.append(f"project text ~{read_context:,} tokens (readers assumed to carry {context:,})")

    def agent_calls(a: AgentSpec, json_answer: bool = False) -> float:
        # writers measured 2-8 calls a task (act, save, answer, and sometimes a nudge)
        return (4.0 if WRITE_TOOLS.intersection(a.tools) else 3.0 if a.tools else 1.0)             + (0.25 if json_answer else 0.0)

    def walk(step, depth: int = 0) -> tuple[float, float, float, float]:
        """(calls, calls_p90, tokens, tokens_p90)"""
        def cost(a: AgentSpec, calls: float, calls_p90: float, task: str = "", memory: bool = False):
            base = _agent_call_tokens(org, a, goal, task)
            reads = bool(READ_TOOLS.intersection(a.tools))
            writes = bool(WRITE_TOOLS.intersection(a.tools))
            mem_here = mem_per_call if memory else 0
            if mem_here:
                mem[0] += calls * mem_here
            per = (base * IN_GROWTH + mem_here + (context if reads else 0)
                   + (OUT_WRITER if writes else OUT_READER if reads else OUT_OTHER))
            return calls, calls_p90, calls * per, calls_p90 * per

        match step:
            case SequenceStep() | ParallelStep():
                tot = [0.0, 0.0, 0.0, 0.0]
                for child in step.steps:
                    for i, v in enumerate(walk(child, depth + 1)):
                        tot[i] += v
                if isinstance(step, ParallelStep) and step.join:
                    for i, v in enumerate(cost(org.agent(step.join), 1, 1)):
                        tot[i] += v
                return tuple(tot)  # type: ignore[return-value]
            case ReviewLoopStep():
                b = org.agent(step.builder)
                one = list(cost(b, agent_calls(b), agent_calls(b), step.task, memory=True))
                for r in step.reviewers:
                    ra = org.agent(r)
                    rc = (2.0 if ra.tools else 1.0) + 0.25
                    for i, v in enumerate(cost(ra, rc, rc + 2)):
                        one[i] += v
                typical, worst = min(2, step.max_rounds), step.max_rounds
                lines.append(f"review loop {step.builder}: {typical}-{worst} rounds")
                return one[0] * typical, one[1] * worst, one[2] * typical, one[3] * worst
            case CouncilStep():
                n = len(step.members)
                chair = org.agent(step.chair)
                tot = [0.0, 0.0, 0.0, 0.0]
                for m in step.members:
                    ma = org.agent(m)
                    per_member = (1 + step.rounds) * 1.25 + 1.25  # proposals, critiques, vote
                    for i, v in enumerate(cost(ma, per_member, per_member)):
                        tot[i] += v
                for i, v in enumerate(cost(chair, 3.25, 3.5)):  # options, memo, sometimes tie-break
                    tot[i] += v
                lines.append(f"council of {n}: {step.rounds} critique round(s)")
                return tuple(tot)  # type: ignore[return-value]
            case ManagerStep():
                mgr = org.agent(step.manager)
                # live runs planned 1 task for one worker and 4 for four (2026-09-17)
                tasks = min(step.max_tasks, max(1, len(step.workers)))
                tot = list(cost(mgr, 2.25, 2.5, memory=True))  # plan (+repair) and integration
                worker_calls = sum(agent_calls(org.agent(w)) for w in step.workers) / len(step.workers)
                w0 = org.agent(step.workers[0])
                for i, v in enumerate(cost(w0, worker_calls * tasks, (worker_calls + 2) * step.max_tasks,
                                           memory=True)):
                    tot[i] += v
                if step.reviewer:
                    ra = org.agent(step.reviewer)
                    for i, v in enumerate(cost(ra, 2.25 * tasks, 4.25 * step.max_tasks * step.review_rounds)):
                        tot[i] += v
                lines.append(f"manager {step.manager}: ~{tasks} tasks (up to {step.max_tasks})")
                return tuple(tot)  # type: ignore[return-value]
            case ApprovalStep():
                return 0.0, 0.0, 0.0, 0.0
            case _:  # agent step
                a = org.agent(step.agent)
                return cost(a, agent_calls(a), agent_calls(a) + 1, step.task)

    calls, calls_p90, tokens, tokens_p90 = walk(org.workflow)
    if mem[0] > 0:
        lines.append(f"memory ~{mem_per_call:,} tokens/call to builders and managers "
                     f"(~{round(mem[0]):,} tokens total)")
    return Estimate(round(calls, 1), round(calls_p90, 1), round(tokens), round(tokens_p90), None, 0,
                    largest_call, "no history, estimated from template size", lines,
                    memory_tokens=round(mem[0]))


def largest_call(org: OrgSpec, goal: str) -> int:
    """The biggest single request the router would reserve for this org (prompt + max output)."""
    return max(_agent_call_tokens(org, a, goal) + a.max_output_tokens for a in org.agents)


def estimate(store: Store, org: OrgSpec, goal: str, project: str | Path | None = None,
             mem_per_call: int = 0) -> Estimate:
    big = largest_call(org, goal)
    hist = measured(store, org.name)
    if not hist:
        return from_template(org, goal, big,
                             project_text_tokens(project) if project else None,
                             mem_per_call=mem_per_call)
    calls = [r["calls"] for r in hist]
    tokens = [r["prompt_tokens"] + r["completion_tokens"] for r in hist]
    mins = [r["active_seconds"] / 60 for r in hist if r.get("active_seconds")]
    return Estimate(percentile(calls, 50), percentile(calls, 90), percentile(tokens, 50),
                    percentile(tokens, 90), percentile(mins, 50) if mins else None, len(hist), big,
                    f"measured, n = {len(hist)}")


# --------------------------------------------------------------------------- verdict

@dataclass
class Forecast:
    verdict: str          # fits_now | fits_today | needs_days | cannot_run
    headline: str
    estimate: Estimate
    requests_left: float
    tokens_left: float
    wait_minutes: float = 0.0
    days: int = 0
    reasons: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        e = self.estimate
        out = [f"Forecast: {self.headline}",
               f"  basis: {e.basis}",
               f"  calls: median {e.calls:g}, p90 {e.calls_p90:g} · tokens: median {int(e.tokens):,}, "
               f"p90 {int(e.tokens_p90):,}"]
        if e.minutes is not None:
            out.append(f"  active time: median {e.minutes:.1f} min")
        left_r = "unlimited" if math.isinf(self.requests_left) else f"{int(self.requests_left):,}"
        left_t = "unlimited" if math.isinf(self.tokens_left) else f"{int(self.tokens_left):,}"
        out.append(f"  left today across eligible models: {left_r} requests, {left_t} tokens")
        out += [f"  {r}" for r in self.reasons + e.per_step]
        return out

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in vars(self).items() if k != "estimate"}
        d["estimate"] = vars(self.estimate)
        for k in ("requests_left", "tokens_left"):
            if math.isinf(d[k]):
                d[k] = None
        return d


def forecast(est: Estimate, router: Router, private: bool = False) -> Forecast:
    models: list[ModelEntry] = router.usable(private)
    if not models:
        why = ("no model is eligible for a private run" if private and router.usable()
               else "no usable model — add a provider with `cadre provider add`")
        return Forecast("cannot_run", f"cannot run: {why}", est, 0, 0, reasons=[why])
    fits_somewhere = [m for m in models if not m.limits.tpm or est.largest_call <= m.limits.tpm]
    if not fits_somewhere:
        why = (f"one call can reserve ~{est.largest_call:,} tokens, more than every model's "
               f"tokens-per-minute limit (largest {max(m.limits.tpm or 0 for m in models):,}); "
               "lower max_output_tokens or shorten instructions")
        return Forecast("cannot_run", f"cannot run: {why}", est, 0, 0, reasons=[why])

    req_left = tok_left = 0.0
    daily_req = daily_tok = 0.0
    rpm_total = tpm_total = 0.0
    for m in fits_somewhere:
        q = router.quota(m)
        L = m.limits
        req_left += math.inf if not L.rpd else max(0, L.rpd - q.day_requests)
        tok_left += math.inf if not L.tpd else max(0, L.tpd - q.day_tokens)
        daily_req += math.inf if not L.rpd else L.rpd
        daily_tok += math.inf if not L.tpd else L.tpd
        rpm_total += math.inf if not L.rpm else L.rpm
        tpm_total += math.inf if not L.tpm else L.tpm
    # a model with no token cap still has a request cap: bound tokens by requests × call size
    if math.isinf(tok_left) and not math.isinf(req_left):
        tok_left = req_left * max(1, est.tokens_p90 / max(1.0, est.calls_p90))
    need_calls, need_tokens = est.calls_p90, est.tokens_p90
    pace = max(need_tokens / tpm_total if tpm_total else 0.0,
               need_calls / rpm_total if rpm_total else 0.0)
    if need_calls <= req_left and need_tokens <= tok_left:
        if pace <= NOW_MINUTES:
            return Forecast("fits_now", "fits now", est, req_left, tok_left, pace)
        minutes = math.ceil(pace)
        return Forecast("fits_today", f"fits today after ~{minutes} min of waits", est,
                        req_left, tok_left, minutes,
                        reasons=[f"per-minute limits pace the job to about {minutes} minutes"])
    rest_calls = max(0.0, need_calls - req_left)
    rest_tokens = max(0.0, need_tokens - tok_left)
    extra = max(rest_calls / daily_req if daily_req else 0.0,
                rest_tokens / daily_tok if daily_tok and not math.isinf(daily_tok) else 0.0)
    days = 1 + max(1, math.ceil(extra))
    return Forecast("needs_days", f"needs ~{days} days (runs park at each daily limit)", est,
                    req_left, tok_left, pace, days,
                    reasons=[f"today's remaining quota covers {int(min(req_left, need_calls)):,} of "
                             f"~{int(need_calls):,} calls"])
