"""Workflow execution: the run context and the collaboration patterns (FR-4, FR-6).

Every step and sub-step has a deterministic path (`w/1/r2/review/qa`). A finished one is
stored under that path, and resuming a run re-walks the tree returning stored outputs, so
finished work is neither repeated nor re-billed (ADR-009).

The patterns hold three rules that are the point of the platform:
  * checks gate, reviewers advise           (ADR-005)
  * votes are counted by code               (ADR-007)
  * plans are validated before they run     (ADR-008)
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import string
import subprocess
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .agent import AgentResult, ask_json, run_agent, schema_text
from .clocks import DayClock, human
from .org import (
    AgentSpec,
    ApprovalStep,
    Budget,
    CouncilStep,
    ManagerStep,
    OrgSpec,
    ParallelStep,
    ReviewLoopStep,
    SequenceStep,
    render,
)
from .project import PROTECTED, ProjectError, commit_all
from .providers import ProviderError
from .router import CallRequest, CallResult, QuotaParked, Router
from .store import Store
from .tools import TOOLS, CheckResult, resolve_command, run_check_process
from .types import Message, ToolSpec, Usage
from .workspace import Workspace

# --------------------------------------------------------------------------- run control


class RunStopped(Exception):
    """A budget ran out. The message names which one."""


class RunRejected(Exception):
    """The operator rejected an approval gate."""


class RunCancelled(Exception):
    pass


class StepFailed(Exception):
    pass


@dataclass
class RunOptions:
    allow_exec: bool = False
    auto_approve: bool = False
    privacy: str | None = None  # overrides the org's `privacy` when set

    def as_dict(self) -> dict[str, Any]:
        return {"allow_exec": self.allow_exec, "auto_approve": self.auto_approve,
                "privacy": self.privacy}


Approver = Callable[[str, str, str | None], Awaitable[tuple[bool, str]]]


class BudgetMeter:
    """Run budgets. Cumulative across parks and resumes (ADR-017): counts start from what the run
    has already used, plus any allowance the owner added with `cadre resume --add-…`."""

    def __init__(self, budget: Budget, clock: Callable[[], float] = time.monotonic, *,
                 calls: int = 0, tokens: int = 0, active_seconds: float = 0.0,
                 extra_calls: int = 0, extra_tokens: int = 0, created: float | None = None,
                 wall: Callable[[], float] = time.time,
                 tokens_today: Callable[[], int] | None = None):
        self.budget = budget
        self.calls = calls
        self.tokens = tokens
        self.max_calls = budget.max_calls + extra_calls
        self.max_tokens = budget.max_tokens + extra_tokens
        self._before = active_seconds
        self._created = created
        self._wall = wall
        self._tokens_today = tokens_today
        self._clock = clock
        self._start = clock()
        self._paused_at: float | None = None
        self._paused_total = 0.0

    @property
    def minutes(self) -> float:
        now = self._paused_at if self._paused_at is not None else self._clock()
        return (self._before + now - self._start - self._paused_total) / 60

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self._clock()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._paused_total += self._clock() - self._paused_at
            self._paused_at = None

    def check(self) -> None:
        b = self.budget
        if self.calls >= self.max_calls:
            raise RunStopped(f"model-call budget reached ({self.calls}/{self.max_calls} calls)")
        if self.tokens >= self.max_tokens:
            raise RunStopped(f"token budget reached ({self.tokens}/{self.max_tokens} tokens)")
        if self.minutes >= b.max_minutes:
            raise RunStopped(f"time budget reached ({self.minutes:.1f}/{b.max_minutes} active minutes)")
        if b.max_days and self._created is not None:
            days = (self._wall() - self._created) / 86400
            if days >= b.max_days:
                raise RunStopped(f"day budget reached ({days:.1f}/{b.max_days:g} days since the run began)")
        if b.max_tokens_per_day and self._tokens_today is not None:
            used = self._tokens_today()
            if used >= b.max_tokens_per_day:
                wait = DayClock("UTC").seconds_until_reset(self._wall())
                raise QuotaParked(wait, [{"model": "run budget", "seconds": round(wait),
                                          "why": f"max_tokens_per_day reached ({used}/{b.max_tokens_per_day})",
                                          "frees_in": human(wait)}])

    def charge(self, usage: Usage) -> None:
        self.calls += 1
        self.tokens += usage.total


class RunContext:
    def __init__(self, run_id: str, org: OrgSpec, goal: str, store: Store, router: Router,
                 run_dir: Path, options: RunOptions | None = None,
                 approver: Approver | None = None, poll: float = 0.5,
                 project: dict[str, Any] | None = None):
        self.run_id, self.org, self.goal = run_id, org, goal
        self.store, self.router = store, router
        self.options = options or RunOptions()
        self.approver = approver
        self.poll = poll
        self.run_dir = run_dir
        #: set in project mode: root, base, branch (ADR-016)
        self.project = project
        self._commit_lock = asyncio.Lock()
        self.workspace = Workspace(
            run_dir, on_write=lambda p, v, sha, n, a: store.add_file(run_id, p, v, sha, n, a),
            protected=PROTECTED if project else ())
        row = store.get_run(run_id) or {}
        totals = store.usage_totals(run_id)
        extra = (row.get("options") or {}).get("budget_extra") or {}
        self.budget = BudgetMeter(
            org.budget, calls=totals["calls"], tokens=totals["prompt_tokens"] + totals["completion_tokens"],
            active_seconds=row.get("active_seconds") or 0.0, extra_calls=int(extra.get("calls", 0)),
            extra_tokens=int(extra.get("tokens", 0)), created=row.get("created"),
            tokens_today=lambda: store.tokens_since(run_id, DayClock("UTC").bucket_start(time.time())))
        self.outputs: dict[str, str] = {}
        self.prev = ""
        self.notes: list[tuple[str, str]] = []
        #: every model family each agent has used in this run (ADR-004)
        self.families: dict[str, list[str]] = {}
        self._exec_approved = self.options.allow_exec
        self.private = (self.options.privacy or org.privacy) == "private"
        self._last_beat = 0.0
        for e in store.events(run_id, limit=100_000, kinds=("note", "agent.call")):
            if e["kind"] == "note":
                self.notes.append((e["agent"] or "?", e["data"].get("text", "")))
            elif e["data"].get("family"):
                fams = self.families.setdefault(e["agent"], [])
                if e["data"]["family"] not in fams:
                    fams.append(e["data"]["family"])

    # ------------------------------------------------------------------ events
    def emit(self, event: str, /, agent: str | None = None, step: str | None = None,
             **data: Any) -> None:
        self.store.add_event(self.run_id, event, agent, step, data)
        now = time.monotonic()
        if now - self._last_beat > 5:
            self._last_beat = now
            self.store.heartbeat(self.run_id)

    def checkpoint(self) -> None:
        self.budget.check()
        run = self.store.get_run(self.run_id)
        if run and run["status"] == "cancelling":
            raise RunCancelled("cancelled by the operator")

    def variables(self) -> dict[str, str]:
        v = {"goal": self.goal, "prev": self.prev}
        v.update({f"out.{k}": t for k, t in self.outputs.items()})
        return v

    def render(self, text: str) -> str:
        return render(text, self.variables())

    def avoid_for(self, agent: AgentSpec) -> tuple[str, ...]:
        return tuple(f for d in agent.diverse_from for f in self.families.get(d, []))

    # ------------------------------------------------------------------ model calls
    async def call(self, agent: AgentSpec, messages: list[Message], specs: list[ToolSpec],
                   step: str, avoid: tuple[str, ...] = (), prefer: str | None = None) -> CallResult:
        self.checkpoint()
        req = CallRequest(messages=messages, tools=specs, tier=agent.tier,
                          allow_downgrade=agent.allow_downgrade,
                          avoid_families=tuple(dict.fromkeys(f for f in avoid if f)),
                          pin=agent.model, max_tokens=agent.max_output_tokens,
                          temperature=agent.temperature, label=agent.id,
                          private=self.private, prefer=prefer)
        res = await self.router.chat(
            req, emit=lambda kind, data: self.emit(kind, agent=agent.id, step=step, **data))
        u = res.response.usage
        self.budget.charge(u)
        self.store.add_usage(self.run_id, agent.id, res.entry.provider, res.entry.name,
                             u.prompt_tokens, u.completion_tokens)
        fams = self.families.setdefault(agent.id, [])
        if res.entry.family not in fams:
            fams.append(res.entry.family)
        self.emit("agent.call", agent=agent.id, step=step, model=res.entry.key,
                  family=res.entry.family, tokens_in=u.prompt_tokens, tokens_out=u.completion_tokens,
                  tools=[t.name for t in res.response.tool_calls],
                  independent=res.independent if req.avoid_families else None,
                  waited=round(res.waited, 1), fallbacks=res.fallbacks,
                  finish=res.response.finish_reason)
        if res.response.finish_reason == "length" and not res.response.tool_calls:
            self.emit("agent.truncated", agent=agent.id, step=step, model=res.entry.key,
                      tokens_out=u.completion_tokens, max_tokens=agent.max_output_tokens)
        return res

    # ------------------------------------------------------------------ humans
    async def approve(self, kind: str, prompt: str, agent: str | None, step: str | None
                      ) -> tuple[bool, str]:
        if self.options.auto_approve and kind == "gate":
            self.emit("approval.auto", agent=agent, step=step, kind=kind, prompt=prompt)
            return True, "auto-approved (--yes)"
        if self.options.auto_approve and kind == "question":
            answer = ("No operator is available for this run. Use your best judgement and "
                      "state the assumption you made.")
            self.emit("approval.auto", agent=agent, step=step, kind=kind, prompt=prompt)
            return True, answer
        aid = self.store.create_approval(self.run_id, kind, agent, step, prompt)
        self.emit("approval.requested", agent=agent, step=step, id=aid, kind=kind, prompt=prompt)
        self.budget.pause()
        try:
            if self.approver is not None:
                ok, answer = await self.approver(kind, prompt, agent)
                self.store.decide(aid, ok, answer)
            else:
                self.store.transition(self.run_id, "running", "waiting")
                while True:
                    row = self.store.get_approval(aid)
                    if row is None or row["status"] != "pending":
                        break
                    run = self.store.get_run(self.run_id)
                    if run and run["status"] == "cancelling":
                        raise RunCancelled("cancelled while waiting for approval")
                    self.store.heartbeat(self.run_id)
                    await asyncio.sleep(self.poll)
                ok = bool(row and row["status"] == "approved")
                answer = (row or {}).get("answer") or ""
                self.store.transition(self.run_id, "waiting", "running")
        finally:
            self.budget.resume()
        self.emit("approval.decided", agent=agent, step=step, id=aid, kind=kind, approved=ok,
                  answer=answer)
        return ok, answer

    async def ask_human(self, question: str, agent: str, step: str) -> str:
        ok, answer = await self.approve("question", question, agent, step)
        if ok and answer:
            return answer
        return "(the operator did not answer; use your best judgement and state your assumption)"

    # ------------------------------------------------------------------ project mode
    def write_artifact(self, name: str, content: str) -> None:
        """Engine-written records (plans, reports, decisions). In project mode they stay out of the
        owner's repository and live beside the run instead."""
        if not self.project:
            self.workspace.write(name, content, "engine")
            return
        target = self.run_dir / "artifacts" / Path(name).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.emit("artifact.written", path=str(target), name=target.name)

    async def commit_step(self, path: str, text: str) -> None:
        if not self.project:
            return
        first = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
        message = f"cadre({path}): {first}" if first else f"cadre({path})"
        if len(message) > 72:
            message = message[:71].rsplit(" ", 1)[0] + "…"
        async with self._commit_lock:
            try:
                sha = await asyncio.to_thread(commit_all, self.workspace.root, message)
            except ProjectError as e:
                self.emit("project.commit_failed", step=path, error=str(e))
                return
        if sha:
            self.emit("project.commit", step=path, sha=sha[:12], message=message)

    def check_names(self, names: list[str]) -> list[str]:
        if "all" in names:
            return [c.name for c in self.org.checks]
        return names

    # ------------------------------------------------------------------ tools
    def note_file(self, agent: str, step: str, path: str) -> None:
        self.emit("file.written", agent=agent, step=step, path=path)

    def post_note(self, agent: str, step: str, text: str) -> None:
        self.notes.append((agent, text))
        self.emit("note", agent=agent, step=step, text=text)

    async def run_check(self, name: str, agent: str, step: str) -> CheckResult:
        try:
            spec = self.org.check(name)
        except KeyError:
            known = ", ".join(c.name for c in self.org.checks) or "none"
            raise ValueError(f"no check called {name!r} (checks: {known})") from None
        if not self._exec_approved:
            # the command as it will actually run ({python} resolved), so the human approves
            # exactly what executes (ADR-006, ADR-029)
            listed = "\n".join(f"  {c.name}: {subprocess.list2cmdline(resolve_command(c.command))}"
                               for c in self.org.checks)
            where = (f"the worktree of {self.project['root']}" if self.project
                     else str(self.workspace.root))
            prompt = (f"Allow this run to execute its declared checks in {where}? "
                      f"Model-written code will run as you. Checks:\n{listed}")
            ok, _ = await self.approve("exec", prompt, agent, step)
            if not ok:
                result = CheckResult(name, False, None, "", 0.0,
                                     "execution was not approved by the operator")
                self.emit("check.finished", agent=agent, step=step, **result.as_dict())
                return result
            self._exec_approved = True
        self.emit("check.started", agent=agent, step=step, name=name, command=spec.command)
        result = await run_check_process(spec, self.workspace.root)
        self.emit("check.finished", agent=agent, step=step, **result.as_dict())
        return result


# --------------------------------------------------------------------------- pure helpers


class StepOutput(BaseModel):
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


def rule_ok(rule: str, approvals: list[bool]) -> bool:
    if not approvals:
        return False
    if rule == "any":
        return any(approvals)
    if rule == "majority":
        return sum(approvals) * 2 > len(approvals)
    return all(approvals)


def tally(votes: dict[str, str | None], options: list[str], rule: str) -> dict[str, Any]:
    """Count votes. `winner` is None when the rule is not met; the chair then decides."""
    cast = {m: c for m, c in votes.items() if c in options}
    abstained = sorted(m for m in votes if m not in cast)
    counts = {o: 0 for o in options}
    for c in cast.values():
        counts[c] += 1
    n = len(cast)
    top = max(counts.values(), default=0)
    leaders = [o for o in options if counts[o] == top and top > 0]
    winner: str | None = None
    if n:
        if rule == "unanimous":
            winner = leaders[0] if top == n and not abstained else None
        elif rule == "supermajority":
            winner = leaders[0] if top >= math.ceil(2 * n / 3) and len(leaders) == 1 else None
        elif rule == "plurality":
            winner = leaders[0] if len(leaders) == 1 else None
        else:  # majority
            winner = leaders[0] if top * 2 > n else None
    return {"rule": rule, "counts": counts, "cast": n, "abstained": abstained,
            "leaders": leaders, "winner": winner}


def topo_waves(tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Kahn's algorithm in layers: every task in a wave depends only on earlier waves."""
    by_id = {t["id"]: t for t in tasks}
    indeg = {t["id"]: len(set(t["depends_on"])) for t in tasks}
    waves: list[list[dict[str, Any]]] = []
    ready = [tid for tid, d in indeg.items() if d == 0]
    seen = 0
    while ready:
        waves.append([by_id[t] for t in ready])
        seen += len(ready)
        nxt = []
        for t in tasks:
            if t["id"] in indeg and indeg[t["id"]] > 0:
                done = len([d for d in set(t["depends_on"]) if d in ready])
                if done:
                    indeg[t["id"]] -= done
                    if indeg[t["id"]] == 0:
                        nxt.append(t["id"])
        ready = nxt
    if seen != len(tasks):
        stuck = sorted(tid for tid, d in indeg.items() if d > 0)
        raise ValueError(f"dependency cycle among: {', '.join(stuck)}")
    return waves


_TASK_ID = re.compile(r"[^A-Za-z0-9_-]")


def validate_plan(obj: Any, workers: list[str], max_tasks: int) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(obj, dict) or not isinstance(obj.get("tasks"), list):
        return [], ['expected an object like {"tasks": [...]}']
    raw = obj["tasks"]
    errors: list[str] = []
    if not raw:
        errors.append("the plan has no tasks")
    if len(raw) > max_tasks:
        errors.append(f"the plan has {len(raw)} tasks; the limit is {max_tasks}")
    tasks: list[dict[str, Any]] = []
    ids: set[str] = set()
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            errors.append(f"task #{i + 1} is not an object")
            continue
        tid = _TASK_ID.sub("-", str(t.get("id") or f"t{i + 1}").strip())[:32] or f"t{i + 1}"
        if tid in ids:
            errors.append(f"task id {tid!r} is used twice")
        ids.add(tid)
        assignee = str(t.get("assignee") or "").strip()
        if assignee not in workers:
            errors.append(f"task {tid}: assignee {assignee!r} is not one of {', '.join(workers)}")
        deps = t.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            errors.append(f"task {tid}: depends_on must be a list")
            deps = []
        title = str(t.get("title") or "").strip()
        if not title:
            errors.append(f"task {tid}: title is empty")
        tasks.append({"id": tid, "title": title[:200], "assignee": assignee,
                      "depends_on": [_TASK_ID.sub("-", str(d).strip()) for d in deps],
                      "details": str(t.get("details") or t.get("description") or "")[:3000]})
    for t in tasks:
        for d in t["depends_on"]:
            if d == t["id"]:
                errors.append(f"task {d} depends on itself")
            elif d not in ids:
                errors.append(f"task {t['id']} depends on unknown task {d!r}")
    if not errors:
        try:
            topo_waves(tasks)
        except ValueError as e:
            errors.append(str(e))
    return tasks, errors


async def gather_strict(coros: list[Coroutine[Any, Any, Any]]) -> list[Any]:
    """Run concurrently; on the first failure cancel the rest and raise that failure itself."""
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(c) for c in coros]
    except BaseExceptionGroup as eg:
        leaves: list[BaseException] = []

        def flatten(g: BaseExceptionGroup) -> None:
            for e in g.exceptions:
                if isinstance(e, BaseExceptionGroup):
                    flatten(e)
                else:
                    leaves.append(e)

        flatten(eg)
        real = [e for e in leaves if not isinstance(e, asyncio.CancelledError)]
        raise (real or leaves)[0] from None
    return [t.result() for t in tasks]


def norm_choice(choice: Any, ids: list[str]) -> str:
    """'b', 'Option B', 'B.' and 'B: Build it' all mean B; 'BUILD' does not."""
    c = re.sub(r"^OPTION\s+", "", str(choice or "").strip().upper())
    if c[:1] in ids and (len(c) == 1 or not c[1].isalnum()):
        return c[:1]
    return c


def _letters(n: int) -> list[str]:
    return list(string.ascii_uppercase[:n])


def _brief(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# --------------------------------------------------------------------------- engine

PROPOSAL = {"position": "your stance in one paragraph",
            "options": ["a distinct course of action", "another one"],
            "recommendation": "the option you favour",
            "reasoning": "why, including the main risks"}
OPTIONS = {"options": [{"title": "short name", "summary": "what choosing this means in practice"}]}
VOTE = {"choice": "A", "confidence": 0.7, "reason": "one or two sentences"}
VERDICT = {"approve": False, "summary": "one or two sentences",
           "issues": [{"severity": "blocker | major | minor", "file": "path, or empty",
                       "detail": "what is wrong and how to fix it"}]}
PLAN = {"tasks": [{"id": "t1", "title": "short imperative title", "assignee": "<worker id>",
                   "depends_on": [], "details": "exactly what to produce, including file names"}]}

READ_ONLY = ("list_files", "read_file")
INLINE_REVIEW_CHARS = 12_000
INLINE_FILE_CHARS = 6_000


class Engine:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.org = ctx.org
        self.store = ctx.store
        for step, path in self._static(self.org.workflow, "w"):
            if step.id:
                hit = self.store.get_step(ctx.run_id, path)
                if hit:
                    ctx.outputs[step.id] = hit[0]

    def _static(self, step: Any, path: str):
        yield step, path
        if isinstance(step, (SequenceStep, ParallelStep)):
            for i, child in enumerate(step.steps):
                yield from self._static(child, f"{path}/{i}")

    async def run(self) -> StepOutput:
        return await self.step(self.org.workflow, "w")

    async def cached(self, path: str, factory: Callable[[], Awaitable[StepOutput]]) -> StepOutput:
        hit = self.store.get_step(self.ctx.run_id, path)
        if hit is not None:
            return StepOutput(text=hit[0], data=hit[1])
        out = await factory()
        self.store.put_step(self.ctx.run_id, path, out.text, out.data)
        await self.ctx.commit_step(path, out.text)
        return out

    async def step(self, s: Any, path: str) -> StepOutput:
        handler = {
            "agent": self._agent, "sequence": self._sequence, "parallel": self._parallel,
            "review_loop": self._review_loop, "council": self._council,
            "manager": self._manager, "approval": self._approval,
        }[s.type]
        if self.store.get_step(self.ctx.run_id, path) is not None:
            self.ctx.emit("step.reused", step=path, type=s.type)

        async def go() -> StepOutput:
            self.ctx.emit("step.started", step=path, type=s.type, title=s.title or s.id or "")
            out = await handler(s, path)
            self.ctx.emit("step.finished", step=path, type=s.type,
                          approved=out.data.get("approved"), text=_brief(out.text, 300))
            return out

        out = await self.cached(path, go)
        if s.id:
            self.ctx.outputs[s.id] = out.text
        return out

    async def agent_out(self, agent: AgentSpec, task: str, step: str, *, context: str = "",
                        avoid: tuple[str, ...] = (), tools: list[str] | None = None) -> StepOutput:
        r: AgentResult = await run_agent(self.ctx, agent, task, step=step, context=context,
                                         avoid=avoid, tools=tools)
        return StepOutput(text=r.text, data={"kind": "agent", "family": r.family, **r.data()})

    # ------------------------------------------------------------------ simple steps
    async def _agent(self, s: Any, path: str) -> StepOutput:
        a = self.org.agent(s.agent)
        return await self.agent_out(a, self.ctx.render(s.task), path, avoid=self.ctx.avoid_for(a))

    async def _sequence(self, s: SequenceStep, path: str) -> StepOutput:
        out = StepOutput()
        for i, child in enumerate(s.steps):
            out = await self.step(child, f"{path}/{i}")
            self.ctx.prev = out.text
        return StepOutput(text=out.text, data={"kind": "sequence", "steps": len(s.steps)})

    async def _parallel(self, s: ParallelStep, path: str) -> StepOutput:
        sem = asyncio.Semaphore(self.org.budget.max_parallel)

        async def one(i: int, child: Any) -> StepOutput:
            async with sem:
                return await self.step(child, f"{path}/{i}")

        results = await gather_strict([one(i, c) for i, c in enumerate(s.steps)])
        labels = [c.title or c.id or getattr(c, "agent", "") or f"branch {i + 1}"
                  for i, c in enumerate(s.steps)]
        joined = "\n\n".join(f"### {lab}\n{r.text}" for lab, r in zip(labels, results, strict=True))
        if s.join:
            j = self.org.agent(s.join)
            out = await self.cached(f"{path}/join", lambda: self.agent_out(
                j, self.ctx.render(s.join_task), f"{path}/join",
                context=f"RESULTS TO COMBINE:\n{joined}"))
            return StepOutput(text=out.text, data={"kind": "parallel", "branches": len(results)})
        return StepOutput(text=joined, data={"kind": "parallel", "branches": len(results)})

    async def _approval(self, s: ApprovalStep, path: str) -> StepOutput:
        ok, answer = await self.ctx.approve("gate", self.ctx.render(s.prompt), None, path)
        if not ok:
            raise RunRejected(answer or "rejected by the operator")
        return StepOutput(text=answer or "approved", data={"kind": "approval", "approved": True})

    # ------------------------------------------------------------------ review loop
    async def _review_loop(self, s: ReviewLoopStep, path: str) -> StepOutput:
        return await self.review_cycle(self.org.agent(s.builder), s.reviewers, s.checks,
                                       self.ctx.render(s.task), s.max_rounds, s.rule, path)

    async def run_checks(self, names: list[str], step: str) -> StepOutput:
        results = []
        for n in self.ctx.check_names(names):
            r = await self.ctx.run_check(n, "engine", step)
            results.append(r.as_dict())
        return StepOutput(text="; ".join(f"{r['name']}: {'pass' if r['passed'] else 'FAIL'}"
                                         for r in results), data={"results": results})

    async def review_cycle(self, builder: AgentSpec, reviewer_ids: list[str], checks: list[str],
                           task: str, max_rounds: int, rule: str, path: str,
                           context: str = "") -> StepOutput:
        feedback = ""
        build = StepOutput()
        approved = False
        verdicts: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        rnd = 0
        for rnd in range(1, max_rounds + 1):
            rp = f"{path}/r{rnd}"
            btask = task if rnd == 1 else (
                f"{task}\n\nThis is round {rnd} of {max_rounds}. Your previous attempt was not "
                f"accepted. Fix every problem listed below, then summarise what you changed.\n\n"
                f"{feedback}")
            build = await self.cached(f"{rp}/build", lambda: self.agent_out(
                builder, btask, f"{rp}/build", context=context,
                avoid=self.ctx.avoid_for(builder)))
            checks_out = await self.cached(f"{rp}/checks", lambda: self.run_checks(checks, f"{rp}/checks"))
            results = checks_out.data["results"]
            checks_ok = all(r["passed"] for r in results)
            bfams = build.data.get("families") or [build.data.get("family", "")]
            verdicts = []
            for rid in reviewer_ids:
                reviewer = self.org.agent(rid)
                avoid = (*bfams, *self.ctx.avoid_for(reviewer))
                v = await self.cached(f"{rp}/review/{rid}", lambda: self.review(
                    reviewer, builder, task, build, results, avoid, f"{rp}/review/{rid}"))
                verdicts.append(v.data)
            reviewers_ok = rule_ok(rule, [bool(v["approve"]) for v in verdicts]) if reviewer_ids else True
            approved = checks_ok and reviewers_ok
            self.ctx.emit(
                "review.round", step=rp, round=rnd, approved=approved, builder=builder.id,
                checks=[{"name": r["name"], "passed": r["passed"]} for r in results],
                verdicts=[{"reviewer": v["reviewer"], "approve": v["approve"],
                           "issues": len(v["issues"]), "independent": v.get("independent")}
                          for v in verdicts],
                gated_by_checks=reviewers_ok and not checks_ok and bool(reviewer_ids))
            if approved:
                break
            feedback = self.feedback(results, verdicts)
        open_issues = [] if approved else [
            {"reviewer": v["reviewer"], **i} for v in verdicts for i in v["issues"]] + [
            {"check": r["name"], "detail": r["note"] or f"exit {r['exit_code']}"}
            for r in results if not r["passed"]]
        return StepOutput(text=build.text, data={
            "kind": "review_loop", "approved": approved, "rounds": rnd, "builder": builder.id,
            "files": build.data.get("files", []),
            "checks": [{"name": r["name"], "passed": r["passed"]} for r in results],
            "verdicts": verdicts, "open_issues": open_issues[:20]})

    async def review(self, reviewer: AgentSpec, builder: AgentSpec, task: str, build: StepOutput,
                     checks: list[dict[str, Any]], avoid: tuple[str, ...], step: str) -> StepOutput:
        if checks:
            check_text = "\n".join(
                f"- {r['name']}: {'PASSED' if r['passed'] else 'FAILED'}"
                + (f" ({r['note']})" if r["note"] else "")
                + ("" if r["passed"] else f"\n{_brief(r['output_tail'], 800)}") for r in checks)
        else:
            check_text = "(no automated checks for this task)"
        files = ", ".join(build.data.get("files", [])) or "(none reported)"
        prompt = (f"Review {builder.id}'s work on this task.\n\nTASK:\n{task}\n\n"
                  f"{builder.id.upper()} REPORTS:\n{_brief(build.text, 3000)}\n\n"
                  f"FILES WRITTEN THIS ROUND: {files}\n\n{self.inline_files(build)}"
                  f"AUTOMATED CHECKS:\n{check_text}\n\n"
                  "Read whatever you need, then give your verdict. Approve only if the work fully "
                  "meets the task. Minor issues alone should not block approval. A failing check "
                  "means the work is not done.")

        def valid(obj: Any) -> str | None:
            if not isinstance(obj, dict) or not isinstance(obj.get("approve"), bool):
                return 'expected {"approve": true|false, ...}'
            if not isinstance(obj.get("issues", []), list):
                return "issues must be a list"
            return None

        tools = [t for t in reviewer.tools if TOOLS[t].perm == "read"]
        obj, res = await ask_json(self.ctx, reviewer, prompt, schema_text(VERDICT), valid,
                                  step=step, avoid=avoid, tools=tools)
        if obj is None:
            verdict = {"approve": False, "summary": "verdict unreadable — counted as not approved",
                       "issues": [], "valid": False}
        else:
            issues = []
            for i in obj.get("issues") or []:
                if isinstance(i, dict) and str(i.get("detail", "")).strip():
                    issues.append({"severity": str(i.get("severity", "major"))[:10],
                                   "file": str(i.get("file", ""))[:200],
                                   "detail": str(i["detail"])[:800]})
                elif isinstance(i, str) and i.strip():
                    issues.append({"severity": "major", "file": "", "detail": i[:800]})
            verdict = {"approve": obj["approve"], "summary": str(obj.get("summary", ""))[:600],
                       "issues": issues[:15], "valid": True}
        verdict.update(reviewer=reviewer.id, model=res.model, independent=res.independent)
        return StepOutput(text=verdict["summary"], data=verdict)

    def inline_files(self, build: StepOutput) -> str:
        """The files the builder wrote, inline and capped (M5, 2026-09-17).

        Live QA reviewers spent most of their calls re-reading deliverables, and every tool turn
        resends everything read so far; one call with the content in the prompt costs less and
        needs no wait on the reviewer's model. Larger files are cut; the read tools remain.
        """
        parts, budget = [], INLINE_REVIEW_CHARS
        for path in build.data.get("files", [])[:4]:
            try:
                text = self.ctx.workspace.read(path, max_chars=min(INLINE_FILE_CHARS, budget))
            except ValueError:
                continue
            if budget <= 0:
                break
            budget -= len(text)
            parts.append(f"--- {path} ---\n{text}")
        if not parts:
            return ""
        return ("CONTENT OF THOSE FILES (longer files are cut; use read_file for the rest):\n"
                + "\n".join(parts) + "\n\n")

    @staticmethod
    def feedback(results: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> str:
        lines = ["FEEDBACK FROM THE LAST ROUND:"]
        for r in results:
            if not r["passed"]:
                lines.append(f"- check {r['name']} FAILED"
                             + (f" ({r['note']})" if r["note"] else f" (exit {r['exit_code']})")
                             + f":\n{_brief(r['output_tail'], 1200)}")
        for v in verdicts:
            if not v["approve"]:
                lines.append(f"- {v['reviewer']} did not approve: {v['summary']}")
                for i in v["issues"]:
                    where = f" [{i['file']}]" if i.get("file") else ""
                    lines.append(f"  - ({i['severity']}){where} {i['detail']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ council
    async def _council(self, s: CouncilStep, path: str) -> StepOutput:
        members = [self.org.agent(m) for m in s.members]
        chair = self.org.agent(s.chair)
        question = self.ctx.render(s.question)
        proposals: dict[str, dict[str, Any]] = {}
        avoids: dict[str, tuple[str, ...]] = {}
        used: list[str] = []
        for m in members:
            avoids[m.id] = (*used, *self.ctx.avoid_for(m))
            out = await self.cached(f"{path}/propose/{m.id}", lambda: self.propose(
                m, question, [], avoids[m.id], f"{path}/propose/{m.id}"))
            proposals[m.id] = out.data
            for fam in out.data.get("families") or [out.data.get("family")]:
                if fam and fam not in used:
                    used.append(fam)
        for r in range(1, s.rounds + 1):
            revised: dict[str, dict[str, Any]] = {}
            for m in members:
                others = [p for mid, p in proposals.items() if mid != m.id]
                out = await self.cached(f"{path}/critique{r}/{m.id}", lambda: self.propose(
                    m, question, others, avoids[m.id], f"{path}/critique{r}/{m.id}",
                    own=proposals[m.id]))
                revised[m.id] = out.data
            proposals = revised
        opts = await self.cached(f"{path}/options", lambda: self.consolidate(
            chair, question, proposals, s.max_options, f"{path}/options"))
        options = opts.data["options"]
        ids = [o["id"] for o in options]
        votes: dict[str, dict[str, Any]] = {}
        for m in members:
            out = await self.cached(f"{path}/vote/{m.id}", lambda: self.vote(
                m, question, options, proposals[m.id], avoids[m.id], f"{path}/vote/{m.id}"))
            votes[m.id] = out.data
        result = tally({m: v["choice"] for m, v in votes.items()}, ids, s.rule)
        winner, decided_by = result["winner"], "vote"
        if winner is None:
            tb = await self.cached(f"{path}/tiebreak", lambda: self.tiebreak(
                chair, question, options, result, votes, f"{path}/tiebreak"))
            winner, decided_by = tb.data["choice"], tb.data["decided_by"]
        self.ctx.emit("council.tally", step=path, rule=s.rule, counts=result["counts"],
                      abstained=result["abstained"], winner=winner, decided_by=decided_by)
        chosen = next(o for o in options if o["id"] == winner)
        table = self.tally_markdown(options, votes, result, winner, decided_by)
        memo = await self.cached(f"{path}/memo", lambda: self.agent_out(
            chair, (f"Write the decision memo for the organisation. The decision is option "
                    f"{winner}: {chosen['title']}. You must not change the outcome. Use the "
                    "sections: Decision, Why, Options considered, Dissent and risks, Next steps. "
                    "Be concise."),
            f"{path}/memo", tools=[],
            context=f"QUESTION:\n{question}\n\n{table}\n\nPROPOSALS:\n"
                    + self.proposals_text(proposals, anonymous=False)))
        decision = {"question": question, "rule": s.rule, "options": options,
                    "votes": votes, "tally": result, "winner": winner,
                    "winner_title": chosen["title"], "decided_by": decided_by,
                    "options_by": opts.data.get("by", "chair")}
        doc = f"# Decision: {chosen['title']}\n\n{memo.text}\n\n---\n\n{table}\n"

        async def record() -> StepOutput:
            self.ctx.write_artifact("DECISION.md", doc)
            self.ctx.write_artifact("decision.json", json.dumps(decision, indent=2))
            return StepOutput(text="recorded")

        await self.cached(f"{path}/record", record)
        return StepOutput(text=doc, data={"kind": "council", **decision})

    @staticmethod
    def proposals_text(proposals: dict[str, dict[str, Any]] | list[dict[str, Any]],
                       anonymous: bool = True) -> str:
        items = list(proposals.values()) if isinstance(proposals, dict) else proposals
        names = list(proposals.keys()) if isinstance(proposals, dict) and not anonymous else None
        out = []
        for i, p in enumerate(items):
            who = f"Member {string.ascii_uppercase[i]}" if names is None else names[i]
            opts = "; ".join(p.get("options", []))
            out.append(f"{who}:\n  position: {_brief(p.get('position', ''), 600)}\n"
                       f"  options: {_brief(opts, 400)}\n"
                       f"  recommends: {_brief(p.get('recommendation', ''), 200)}\n"
                       f"  reasoning: {_brief(p.get('reasoning', ''), 600)}")
        return "\n".join(out)

    async def propose(self, m: AgentSpec, question: str, others: list[dict[str, Any]],
                      avoid: tuple[str, ...], step: str,
                      own: dict[str, Any] | None = None) -> StepOutput:
        if others:
            task = (f"The organisation must decide:\n{question}\n\nYour earlier proposal:\n"
                    f"{self.proposals_text([own or {}])}\n\nOther members proposed (anonymised):\n"
                    f"{self.proposals_text(others)}\n\nCritique their reasoning honestly, then give "
                    "your revised proposal. Change your mind only for a good reason.")
        else:
            task = (f"The organisation must decide:\n{question}\n\nWithout seeing anyone else's view, "
                    "set out the realistic options and the one you recommend from your role's "
                    "perspective.")

        def valid(obj: Any) -> str | None:
            if not isinstance(obj, dict):
                return "expected a JSON object"
            if not str(obj.get("recommendation", "")).strip():
                return "recommendation is required"
            return None

        tools = [t for t in m.tools if TOOLS[t].perm == "read"]
        obj, res = await ask_json(self.ctx, m, task, schema_text(PROPOSAL), valid, step=step,
                                  avoid=avoid, tools=tools)
        if obj is None:
            data: dict[str, Any] = {"position": _brief(res.text, 600), "options": [],
                                    "recommendation": "", "reasoning": "", "valid": False}
        else:
            raw_opts = obj.get("options") or []
            data = {"position": str(obj.get("position", ""))[:1500],
                    "options": [str(o)[:200] for o in raw_opts if str(o).strip()][:6]
                    if isinstance(raw_opts, list) else [],
                    "recommendation": str(obj["recommendation"])[:300],
                    "reasoning": str(obj.get("reasoning", ""))[:1500], "valid": True}
        data.update(member=m.id, model=res.model, family=res.family, families=res.families)
        return StepOutput(text=data["recommendation"], data=data)

    async def consolidate(self, chair: AgentSpec, question: str,
                          proposals: dict[str, dict[str, Any]], max_options: int,
                          step: str) -> StepOutput:
        def valid(obj: Any) -> str | None:
            if not isinstance(obj, dict) or not isinstance(obj.get("options"), list):
                return 'expected {"options": [...]}'
            good = [o for o in obj["options"] if isinstance(o, dict) and str(o.get("title", "")).strip()]
            if not 2 <= len(good) <= max_options:
                return f"give between 2 and {max_options} options, each with a title"
            return None

        task = (f"The organisation must decide:\n{question}\n\nMerge the members' proposals below "
                f"into between 2 and {max_options} clearly distinct options to vote on. Every "
                "member's recommendation must be represented. Do not argue for any option.\n\n"
                + self.proposals_text(proposals))
        obj, _ = await ask_json(self.ctx, chair, task, schema_text(OPTIONS), valid, step=step,
                                tools=[])
        by = "chair"
        if obj is None:
            by = "fallback"
            titles: list[str] = []
            for p in proposals.values():
                for t in [p.get("recommendation", "")] + list(p.get("options", [])):
                    t = str(t).strip()
                    if t and t.lower() not in {x.lower() for x in titles}:
                        titles.append(t)
            titles = titles[:max_options]
            if len(titles) < 2:
                titles.append("Keep the status quo")
            raw = [{"title": t, "summary": ""} for t in titles]
        else:
            raw = [o for o in obj["options"] if isinstance(o, dict) and str(o.get("title", "")).strip()]
        options = [{"id": letter, "title": str(o["title"])[:160], "summary": str(o.get("summary", ""))[:600]}
                   for letter, o in zip(_letters(len(raw)), raw, strict=True)]
        return StepOutput(text="; ".join(f"{o['id']}: {o['title']}" for o in options),
                          data={"options": options, "by": by})

    async def vote(self, m: AgentSpec, question: str, options: list[dict[str, Any]],
                   own: dict[str, Any], avoid: tuple[str, ...], step: str) -> StepOutput:
        ids = [o["id"] for o in options]

        def valid(obj: Any) -> str | None:
            if not isinstance(obj, dict) or norm_choice(obj.get("choice"), ids) not in ids:
                return f"choice must be one of {', '.join(ids)}"
            return None

        listing = "\n".join(f"{o['id']}. {o['title']} — {o['summary']}" for o in options)
        task = (f"The organisation must decide:\n{question}\n\nOPTIONS:\n{listing}\n\n"
                f"Your own recommendation was: {own.get('recommendation', '(none)')}\n\n"
                "Vote for exactly one option.")
        obj, res = await ask_json(self.ctx, m, task, schema_text(VOTE), valid, step=step,
                                  avoid=avoid, tools=[])
        if obj is None:
            data = {"choice": None, "confidence": 0.0,
                    "reason": "vote unreadable — recorded as an abstention", "valid": False}
        else:
            try:
                conf = min(1.0, max(0.0, float(obj.get("confidence", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            data = {"choice": norm_choice(obj["choice"], ids), "confidence": conf,
                    "reason": str(obj.get("reason", ""))[:600], "valid": True}
        data.update(member=m.id, model=res.model)
        return StepOutput(text=str(data["choice"]), data=data)

    async def tiebreak(self, chair: AgentSpec, question: str, options: list[dict[str, Any]],
                       result: dict[str, Any], votes: dict[str, dict[str, Any]],
                       step: str) -> StepOutput:
        candidates = result["leaders"] or [o["id"] for o in options]

        def valid(obj: Any) -> str | None:
            if not isinstance(obj, dict) or norm_choice(obj.get("choice"), candidates) not in candidates:
                return f"choice must be one of {', '.join(candidates)}"
            return None

        reasons = "\n".join(f"- {m} voted {v['choice'] or 'nothing (abstained)'}: {v['reason']}"
                            for m, v in votes.items())
        listing = "\n".join(f"{o['id']}. {o['title']} — {o['summary']}" for o in options
                            if o["id"] in candidates)
        task = (f"The vote on this question did not meet the '{result['rule']}' rule, so as chair "
                f"you must decide between the leading options.\n\nQUESTION:\n{question}\n\n"
                f"OPTIONS:\n{listing}\n\nVOTES:\n{reasons}")
        obj, _ = await ask_json(self.ctx, chair, task, schema_text({"choice": candidates[0],
                                                                    "reason": "why"}),
                                valid, step=step, tools=[])
        if obj is None:
            choice, by, reason = sorted(candidates)[0], "fallback", "chair reply unusable"
        else:
            choice = norm_choice(obj["choice"], candidates)
            by, reason = "chair", str(obj.get("reason", ""))
        return StepOutput(text=choice, data={"choice": choice, "decided_by": by, "reason": reason[:600]})

    @staticmethod
    def tally_markdown(options: list[dict[str, Any]], votes: dict[str, dict[str, Any]],
                       result: dict[str, Any], winner: str, decided_by: str) -> str:
        lines = ["## Vote (counted by Cadre, not by a model)", "",
                 f"Rule: **{result['rule']}** · votes cast: {result['cast']} · "
                 f"decided by: **{decided_by}**", "", "| Option | Votes |", "|---|---|"]
        for o in options:
            mark = " (chosen)" if o["id"] == winner else ""
            lines.append(f"| {o['id']}. {o['title']}{mark} | {result['counts'][o['id']]} |")
        lines += ["", "| Member | Choice | Confidence | Reason |", "|---|---|---|---|"]
        for m, v in votes.items():
            reason = str(v.get("reason", "")).replace("|", "/").replace("\n", " ")
            lines.append(f"| {m} | {v['choice'] or 'abstained'} | {v['confidence']:.2f} | {reason} |")
        dissent = [m for m, v in votes.items() if v["choice"] and v["choice"] != winner]
        lines += ["", f"Dissent: {', '.join(dissent) if dissent else 'none'}"]
        return "\n".join(lines)

    # ------------------------------------------------------------------ manager
    async def _manager(self, s: ManagerStep, path: str) -> StepOutput:
        mgr = self.org.agent(s.manager)
        task = self.ctx.render(s.task)
        plan = await self.cached(f"{path}/plan", lambda: self.plan(
            mgr, task, s.workers, s.max_tasks, f"{path}/plan"))
        tasks: list[dict[str, Any]] = plan.data["tasks"]
        by_id = {t["id"]: t for t in tasks}
        status: dict[str, str] = {}
        results: dict[str, StepOutput] = {}
        sem = asyncio.Semaphore(self.org.budget.max_parallel)
        plan_text = "\n".join(f"- {t['id']} [{t['assignee']}] {t['title']}" for t in tasks)

        async def do(t: dict[str, Any]) -> None:
            tid = t["id"]
            if any(status.get(d) != "done" for d in t["depends_on"]):
                status[tid] = "skipped"
                self.ctx.emit("task.skipped", step=f"{path}/task/{tid}", task=tid,
                              reason="a task it depends on did not finish")
                return
            async with sem:
                self.ctx.emit("task.started", agent=t["assignee"], step=f"{path}/task/{tid}",
                              task=tid, title=t["title"])
                try:
                    out = await self.cached(f"{path}/task/{tid}", lambda: self.work(
                        t, s, path, plan_text, by_id, results))
                except (ProviderError, StepFailed) as e:
                    status[tid] = "failed"
                    self.ctx.emit("task.failed", agent=t["assignee"], step=f"{path}/task/{tid}",
                                  task=tid, error=str(e))
                    return
            results[tid] = out
            status[tid] = "done"
            self.ctx.emit("task.finished", agent=t["assignee"], step=f"{path}/task/{tid}",
                          task=tid, approved=out.data.get("approved"))

        for wave in topo_waves(tasks):
            await gather_strict([do(t) for t in wave])

        rows = []
        for t in tasks:
            tid = t["id"]
            st = status.get(tid, "skipped")
            approved = st == "done" and results[tid].data.get("approved", True) is not False
            rows.append({**t, "status": st, "approved": approved})
        summary = "\n\n".join(
            f"### {r['id']} — {r['title']} ({r['assignee']}): {r['status']}"
            + ("" if r["approved"] or r["status"] != "done" else " — NOT approved by review")
            + (f"\n{_brief(results[r['id']].text, 2000)}" if r["id"] in results else "")
            for r in rows)
        final = await self.cached(f"{path}/integrate", lambda: self.agent_out(
            mgr, ("Integrate your team's results into the final report for this work. State "
                  "plainly any task that failed, was skipped, or was not approved.\n\n"
                  f"THE WORK:\n{task}"),
            f"{path}/integrate", context=f"TASK RESULTS:\n{summary}",
            tools=[t for t in mgr.tools if TOOLS[t].perm == "read"]))
        report = f"# Report\n\n{final.text}\n\n---\n\n## Task ledger (recorded by Cadre)\n\n" + "\n".join(
            f"- `{r['id']}` {r['title']} — **{r['assignee']}** — {r['status']}"
            + ("" if r["approved"] or r["status"] != "done" else " (not approved)") for r in rows)

        async def record() -> StepOutput:
            self.ctx.write_artifact("REPORT.md", report)
            return StepOutput(text="recorded")

        await self.cached(f"{path}/record", record)
        all_ok = all(r["approved"] for r in rows)
        return StepOutput(text=final.text, data={"kind": "manager", "approved": all_ok,
                                                 "tasks": rows})

    async def plan(self, mgr: AgentSpec, task: str, workers: list[str], max_tasks: int,
                   step: str) -> StepOutput:
        roster = "\n".join(
            f"- {w}: {self.org.agent(w).role}"
            + (f" — {self.org.agent(w).instructions.strip().splitlines()[0][:160]}"
               if self.org.agent(w).instructions.strip() else "")
            for w in workers)
        prompt = (f"Break this work into at most {max_tasks} tasks and assign each one to the "
                  f"best-suited worker.\n\nTHE WORK:\n{task}\n\nWORKERS YOU CAN ASSIGN:\n{roster}\n\n"
                  "Rules: exactly one assignee per task, taken from the list above; use depends_on "
                  "only when a task needs another task's output; prefer fewer, larger tasks; every "
                  "task must produce something concrete, usually a named file.")
        errors: list[str] = []

        def valid(obj: Any) -> str | None:
            nonlocal errors
            _, errors = validate_plan(obj, workers, max_tasks)
            return "; ".join(errors) if errors else None

        obj, res = await ask_json(self.ctx, mgr, prompt, schema_text(PLAN), valid, step=step,
                                  tools=[t for t in mgr.tools if TOOLS[t].perm == "read"])
        if obj is None:
            raise StepFailed(f"{mgr.id} did not produce a valid plan: {'; '.join(errors) or 'no JSON'}")
        tasks, _ = validate_plan(obj, workers, max_tasks)
        self.ctx.write_artifact("plan.json", json.dumps({"tasks": tasks}, indent=2))
        self.ctx.emit("plan.created", agent=mgr.id, step=step,
                      tasks=[{k: t[k] for k in ("id", "title", "assignee", "depends_on")} for t in tasks])
        return StepOutput(text="\n".join(f"{t['id']}: {t['title']} -> {t['assignee']}" for t in tasks),
                          data={"tasks": tasks, "model": res.model})

    async def work(self, t: dict[str, Any], s: ManagerStep, path: str, plan_text: str,
                   by_id: dict[str, dict[str, Any]], results: dict[str, StepOutput]) -> StepOutput:
        worker = self.org.agent(t["assignee"])
        deps = "\n\n".join(
            f"OUTPUT OF {d} ({by_id[d]['title']}, by {by_id[d]['assignee']}):\n"
            f"{_brief(results[d].text, 2500)}" for d in t["depends_on"] if d in results)
        context = f"THE TEAM PLAN:\n{plan_text}" + (f"\n\n{deps}" if deps else "")
        wtask = f"{t['title']}\n\n{t['details']}".strip()
        tp = f"{path}/task/{t['id']}"
        reviewers = [r for r in [s.reviewer] if r and r != worker.id]
        if reviewers or s.checks:
            out = await self.review_cycle(worker, reviewers, s.checks, wtask, s.review_rounds,
                                          "all", tp, context=context)
        else:
            out = await self.agent_out(worker, wtask, f"{tp}/work", context=context,
                                       avoid=self.ctx.avoid_for(worker))
        out.data.update(task=t["id"], title=t["title"], assignee=worker.id)
        return out


def unapproved_steps(store: Store, run_id: str) -> list[str]:
    """Review loops and manager steps that finished without approval, by path."""
    out = []
    for path in sorted(store.step_paths(run_id)):
        hit = store.get_step(run_id, path)
        if hit and hit[1].get("kind") in ("review_loop", "manager") and hit[1].get("approved") is False:
            out.append(path)
    return out
