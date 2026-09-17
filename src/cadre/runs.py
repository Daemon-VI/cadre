"""Run lifecycle: create, execute, resume, cancel (FR-6).

A run's status is written by exactly one place — `RunManager.execute` — except for two
operator actions that must work across processes: cancel (`cancelling`, which the engine
notices at its next checkpoint) and the stale-run sweep (`interrupted`).
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any

import httpx

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
from .org import OrgError, find_org_text, load_org_text
from .providers import ProviderError
from .quota import QuotaBook
from .router import Router
from .secrets import SecretStore
from .store import ACTIVE, Store

log = logging.getLogger(__name__)

TERMINAL = ("succeeded", "unapproved", "failed", "stopped", "rejected", "cancelled", "interrupted")
RESUMABLE = ("interrupted", "failed", "stopped", "cancelled", "unapproved")


class RunManager:
    def __init__(self, home: Home, store: Store | None = None, *,
                 secrets: SecretStore | None = None, router: Router | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.home = home.ensure()
        self.store = store or Store(home.db_path)
        self.secrets = secrets or SecretStore()
        self.quotas = QuotaBook(on_change=self.store.quota_save, loader=self.store.quota_load)
        self._router = router
        self._demo: Router | None = None
        self.transport = transport
        self.warnings: list[str] = []
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self._shutting_down = False

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
               demo: bool = False, org_yaml: str | None = None) -> str:
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
        return self.store.create_run(spec.name, text, goal, {**opts, "demo": demo, "source": source})

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
        store.update_run(run_id, status="running", error=None, finished=None)
        store.heartbeat(run_id)
        try:
            org = load_org_text(run["org_yaml"])
            router = self.router(bool(opts.get("demo")))
            if not router.usable():
                hint = "; ".join(self.warnings) or "no providers are configured"
                raise StepFailed(f"no usable model — {hint}. Add one with `cadre provider add groq`, "
                                 "or try the offline demo with --demo")
            ctx = RunContext(run_id, org, run["goal"], store, router,
                             self.home.runs_dir / run_id, options, approver)
            store.update_run(run_id, privacy="private" if ctx.private else "standard")
            ctx.emit("run.started", org=org.name, goal=run["goal"], resumed=resumed,
                     models=[m.key for m in router.usable(ctx.private)], warnings=self.warnings,
                     workspace=str(ctx.workspace.root), private=ctx.private)
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
            out = await Engine(ctx).run()
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
        except (ProviderError, StepFailed, OrgError, ValueError, KeyError) as e:
            return self._finish(run_id, "failed", error=str(e) or type(e).__name__)
        except Exception as e:  # keep the run record honest even for our own bugs
            store.add_event(run_id, "run.crashed", data={"trace": traceback.format_exc()[-4000:]})
            return self._finish(run_id, "failed", error=f"internal error: {type(e).__name__}: {e}")
        unapproved = unapproved_steps(store, run_id)
        status = "unapproved" if unapproved else "succeeded"
        return self._finish(run_id, status, result=out.text, unapproved=unapproved)

    def _finish(self, run_id: str, status: str, *, error: str | None = None, result: str | None = None,
                unapproved: list[str] | None = None) -> dict[str, Any]:
        store = self.store
        store.cancel_pending_approvals(run_id)
        totals = store.usage_totals(run_id)
        summary = {**totals, "files": len(store.files(run_id)), "unapproved": unapproved or [],
                   "workspace": str(self.home.runs_dir / run_id / "workspace")}
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
