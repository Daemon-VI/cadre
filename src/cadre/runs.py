"""Run lifecycle: create, execute, resume, cancel (FR-6).

A run's status is written by exactly one place — `RunManager.execute` — except for two
operator actions that must work across processes: cancel (`cancelling`, which the engine
notices at its next checkpoint) and the stale-run sweep (`interrupted`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import traceback
from collections.abc import Callable
from typing import Any

import httpx

from .clocks import ist
from .config import Home, build_router
from .demo import demo_providers
from .engine import (
    Approver,
    Engine,
    RunCancelled,
    RunContext,
    RunOptions,
    RunRejected,
    RunStopped,
    StepFailed,
    unapproved_steps,
)
from .forecast import estimate, forecast
from .org import CheckSpec, OrgError, find_org_text, load_org_text
from .project import (
    ProjectError,
    commits_on_branch,
    diff_stat,
    ensure_worktree,
    inspect,
    remove_worktree,
    repo_checks,
    review_commands,
)
from .providers import ProviderError
from .quota import QuotaBook
from .router import QuotaParked, Router
from .secrets import SecretStore
from .store import ACTIVE, Store

log = logging.getLogger(__name__)

TERMINAL = ("succeeded", "unapproved", "failed", "stopped", "rejected", "cancelled", "interrupted")
RESUMABLE = ("interrupted", "failed", "stopped", "cancelled", "unapproved", "parked")
#: when a live event stream should end: the run is not going to produce more events by itself
STREAM_END = (*TERMINAL, "parked")
PARK_JITTER = (30.0, 120.0)
HEARTBEAT_EVERY = 20.0  # well inside store.STALE_AFTER, so a long call never looks like a crash


class RunManager:
    def __init__(self, home: Home, store: Store | None = None, *,
                 secrets: SecretStore | None = None, router: Router | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 wall: Callable[[], float] = time.time):
        self.home = home.ensure()
        self.store = store or Store(home.db_path)
        self.secrets = secrets or SecretStore()
        self.quotas = QuotaBook(on_change=self.store.quota_save, loader=self.store.quota_load)
        self._router = router
        self._demo: Router | None = None
        self.transport = transport
        self.wall = wall
        self.warnings: list[str] = []
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self._shutting_down = False
        self._started: dict[str, float] = {}

    # ------------------------------------------------------------------ routers
    def router(self, demo: bool = False) -> Router:
        if demo:
            if self._demo is None:
                providers, models = demo_providers()
                self._demo = Router(providers, models, QuotaBook(), max_wait=5)
            return self._demo
        if self._router is None:
            self._router, self.warnings = build_router(
                self.home.load_config(), self.secrets, self.quotas, transport=self.transport)
        return self._router

    def load_keys(self) -> None:
        """Register every configured key with the redactor (a fresh process knows none yet)."""
        for pc in self.home.load_config().providers:
            if pc.key_ref:
                self.secrets.get(pc.key_ref, pc.env)

    def disabled_reason(self, provider_id: str) -> str | None:
        """Why the live router stopped using a provider this session (e.g. its key was rejected)."""
        return self._router.disabled.get(provider_id) if self._router else None

    async def reload(self) -> None:
        """Providers changed: the next run gets a fresh router (quota counters are kept)."""
        old, self._router = self._router, None
        if old is not None and not any(not t.done() for t in self.tasks.values()):
            await old.aclose()

    # ------------------------------------------------------------------ lifecycle
    def create(self, org: str, goal: str, options: RunOptions | None = None, *,
               demo: bool = False, org_yaml: str | None = None, project: str | None = None,
               base: str | None = None, allow_dirty: bool = False) -> str:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("a run needs a goal")
        self.load_keys()  # so a key pasted into a goal is redacted before it is stored
        if org_yaml is not None:
            source, text = "inline", org_yaml
        else:
            source, text = find_org_text(org, self.home.orgs_dir)
        spec = load_org_text(text)
        opts = (options or RunOptions()).as_dict()
        extra: dict[str, Any] = {}
        if project:
            info = inspect(project, base, allow_dirty)  # refuses non-repos and dirty trees (AC-8.1/8.2)
            extra["project"] = {**info.as_dict(), "checks": repo_checks(info.root, info.base)}
        rid = self.store.create_run(spec.name, text, goal, {**opts, "demo": demo, "source": source, **extra})
        if project:
            self.store.update_run(rid, project_path=extra["project"]["root"],
                                  base=extra["project"]["base"], branch=f"cadre/{rid}")
            if extra["project"]["dirty"]:
                self.store.add_event(rid, "project.dirty", data={
                    "note": "the working tree had uncommitted changes; the run starts from "
                            f"{extra['project']['base_label']} and does not see them"})
        return rid

    async def execute(self, run_id: str, approver: Approver | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        store = self.store
        opts = run["options"] or {}
        options = RunOptions(allow_exec=bool(opts.get("allow_exec")),
                             auto_approve=bool(opts.get("auto_approve")),
                             privacy=opts.get("privacy"))
        resumed = bool(store.step_paths(run_id))
        self._started[run_id] = time.monotonic()
        store.update_run(run_id, status="running", error=None, finished=None, resume_at=None)
        store.heartbeat(run_id)
        try:
            org = load_org_text(run["org_yaml"])
            project = None
            if opts.get("project"):
                org, project = self._prepare_project(run, org)
            router = self.router(bool(opts.get("demo")))
            if not router.usable():
                hint = "; ".join(self.warnings) or "no providers are configured"
                raise StepFailed(f"no usable model — {hint}. Add one with `cadre provider add groq`, "
                                 "or try the offline demo with --demo")
            ctx = RunContext(run_id, org, run["goal"], store, router,
                             self.home.runs_dir / run_id, options, approver, project=project)
            store.update_run(run_id, privacy="private" if ctx.private else "standard")
            ctx.emit("run.started", org=org.name, goal=run["goal"], resumed=resumed,
                     models=[m.key for m in router.usable(ctx.private)], warnings=self.warnings,
                     workspace=str(ctx.workspace.root), private=ctx.private)
            if not resumed:
                try:  # forecast vs actual is an M5/M11 measurement; never block a run on it
                    fc = forecast(estimate(store, org, run["goal"], run.get("project_path")),
                                  router, ctx.private)
                    ctx.emit("run.forecast", **fc.as_dict())
                except Exception as e:
                    ctx.emit("run.forecast", error=str(e))
            if ctx.private:
                excluded = router.excluded_for_privacy()
                if excluded:
                    ctx.emit("privacy.excluded", models=[
                        {"model": m.key, "trains_on_free_data": m.trains} for m in excluded])
                if not router.usable(private=True):
                    raise StepFailed(
                        "private run: every configured provider trains on prompts or does not say "
                        "(" + ", ".join(sorted({m.provider for m in excluded})) + "). Add Groq or "
                        "Cloudflare, or run without --private")
            beat = asyncio.create_task(self._beat(run_id))
            try:
                out = await Engine(ctx).run()
            finally:
                beat.cancel()
        except QuotaParked as e:
            return self._park(run_id, e)
        except RunStopped as e:
            return self._finish(run_id, "stopped", error=str(e))
        except RunRejected as e:
            return self._finish(run_id, "rejected", error=str(e))
        except RunCancelled as e:
            return self._finish(run_id, "cancelled", error=str(e))
        except asyncio.CancelledError:
            status = "interrupted" if self._shutting_down else "cancelled"
            self._finish(run_id, status, error="server shut down" if self._shutting_down
                         else "cancelled by the operator")
            raise
        except (ProviderError, StepFailed, OrgError, ProjectError, ValueError, KeyError) as e:
            return self._finish(run_id, "failed", error=str(e) or type(e).__name__)
        except Exception as e:  # keep the run record honest even for our own bugs
            store.add_event(run_id, "run.crashed", data={"trace": traceback.format_exc()[-4000:]})
            return self._finish(run_id, "failed", error=f"internal error: {type(e).__name__}: {e}")
        unapproved = unapproved_steps(store, run_id)
        status = "unapproved" if unapproved else "succeeded"
        return self._finish(run_id, status, result=out.text, unapproved=unapproved)

    async def _beat(self, run_id: str) -> None:
        """Heartbeat on a timer as well as on events: one model call or quota wait can pass
        STALE_AFTER without an event, and another process (`resume --due` from the scheduled
        task) would then mark a live run interrupted."""
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY)
            self.store.heartbeat(run_id)

    def _prepare_project(self, run: dict[str, Any], org):
        """Create or reuse the worktree and merge the repository's checks into the org."""
        p = run["options"]["project"]
        workspace = self.home.runs_dir / run["id"] / "workspace"
        how = ensure_worktree(p["root"], p["base"], run["branch"], workspace)
        repo = [CheckSpec.model_validate(c) for c in p.get("checks", [])]
        names = {c.name for c in org.checks}
        merged = list(org.checks) + [c for c in repo if c.name not in names]
        shadowed = sorted(c.name for c in repo if c.name in names)
        self.store.add_event(run["id"], "project.worktree", data={
            "how": how, "root": p["root"], "branch": run["branch"], "base": p["base"][:12],
            "repo_checks": [c.name for c in repo], "shadowed_by_org": shadowed})
        project = {"root": p["root"], "base": p["base"], "branch": run["branch"]}
        return org.model_copy(update={"checks": merged}), project

    def _park(self, run_id: str, e: QuotaParked) -> dict[str, Any]:
        """Daily limits: stop spending, remember when to come back (ADR-017)."""
        started = self._started.pop(run_id, None)
        if started is not None:
            self.store.add_active_seconds(run_id, time.monotonic() - started)
        resume_at = self.wall() + e.resume_in + random.uniform(*PARK_JITTER)
        self.store.update_run(run_id, status="parked", resume_at=resume_at, error=str(e))
        self.store.add_event(run_id, "run.parked", data={
            "resume_at": resume_at, "resume_at_ist": ist(resume_at), "blocks": e.blocks})
        return self.store.get_run(run_id) or {}

    def due(self, now: float | None = None) -> list[str]:
        return self.store.due_parked(self.wall() if now is None else now)

    async def resume_due(self, approver: Approver | None = None,
                         now: float | None = None) -> list[dict[str, Any]]:
        """Resume every parked run whose reset has passed, one at a time."""
        out = []
        for rid in self.due(now):
            if rid not in self.tasks:
                out.append(await self.execute(rid, approver))
        return out

    def add_allowance(self, run_id: str, calls: int = 0, tokens: int = 0) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        opts = dict(run["options"] or {})
        extra = dict(opts.get("budget_extra") or {})
        extra["calls"] = int(extra.get("calls", 0)) + max(0, calls)
        extra["tokens"] = int(extra.get("tokens", 0)) + max(0, tokens)
        opts["budget_extra"] = extra
        self.store.update_run(run_id, options=opts)
        self.store.add_event(run_id, "budget.extended", data=extra)

    def _finish(self, run_id: str, status: str, *, error: str | None = None, result: str | None = None,
                unapproved: list[str] | None = None) -> dict[str, Any]:
        store = self.store
        started = self._started.pop(run_id, None)
        if started is not None:
            store.add_active_seconds(run_id, time.monotonic() - started)
        store.cancel_pending_approvals(run_id)
        totals = store.usage_totals(run_id)
        run_dir = self.home.runs_dir / run_id
        summary = {**totals, "files": len(store.files(run_id)), "unapproved": unapproved or [],
                   "workspace": str(run_dir / "workspace")}
        artifacts = sorted(p.name for p in (run_dir / "artifacts").glob("*")) if (run_dir / "artifacts").exists() else []
        if artifacts:
            summary["artifacts"] = [str(run_dir / "artifacts" / a) for a in artifacts]
        row = store.get_run(run_id) or {}
        if row.get("project_path") and row.get("branch"):
            try:
                summary["project"] = {
                    "root": row["project_path"], "branch": row["branch"], "base": row["base"],
                    "commits": commits_on_branch(row["project_path"], row["base"], row["branch"]),
                    "diff_stat": diff_stat(row["project_path"], row["base"], row["branch"]),
                    "review": review_commands(row["project_path"], row["base"], row["branch"],
                                              run_dir / "workspace"),
                }
            except ProjectError as e:
                summary["project"] = {"error": str(e)}
        store.update_run(run_id, status=status, error=error, result=result, summary=summary,
                         finished=time.time())
        store.add_event(run_id, "run.finished", data={"status": status, "error": error, **summary})
        return store.get_run(run_id) or {}

    def start(self, run_id: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(self.execute(run_id), name=f"run-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda t: self.tasks.pop(run_id, None))
        return task

    def cancel(self, run_id: str) -> bool:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == "interrupted":
            self.store.update_run(run_id, status="cancelled", finished=time.time())
            return True
        if run["status"] not in ACTIVE:
            return False
        self.store.update_run(run_id, status="cancelling")
        task = self.tasks.get(run_id)
        if task is not None:
            task.cancel()
        return True

    def cleanup_candidates(self) -> list[dict[str, Any]]:
        """Finished project runs whose worktree still exists."""
        out = []
        for r in self.store.project_runs():
            ws = self.home.runs_dir / r["id"] / "workspace"
            if r["status"] in TERMINAL and r["status"] != "interrupted" and (ws / ".git").is_file():
                out.append({**r, "workspace": str(ws)})
        return out

    def cleanup(self, run_id: str) -> tuple[bool, str]:
        run = self.store.get_run(run_id)
        if not run or not run.get("project_path"):
            return False, "not a project run"
        ok, msg = remove_worktree(run["project_path"], self.home.runs_dir / run_id / "workspace")
        if ok:
            self.store.add_event(run_id, "project.cleaned", data={"branch_kept": run["branch"]})
        return ok, msg

    def resumable(self, run_id: str) -> bool:
        run = self.store.get_run(run_id)
        return bool(run and run["status"] in RESUMABLE)

    async def shutdown(self) -> None:
        self._shutting_down = True
        for task in list(self.tasks.values()):
            task.cancel()
        for task in list(self.tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._router is not None:
            await self._router.aclose()
