"""REST API and dashboard (FR-7.2, FR-7.3, ADR-010).

This port can start runs that execute code, so it is locked:
  * binds to loopback unless told otherwise,
  * every /api call needs `Authorization: Bearer <token>` (except /api/health),
  * the Host header must be a loopback name (DNS-rebinding defence),
  * no CORS headers, a strict CSP, and the event stream is read with fetch + headers so the
    token never appears in a URL or an access log.
Keys are accepted on POST and never returned by any endpoint.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets as pysecrets
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import (
    ModelConfig,
    apply_diff,
    discovered_model,
    make_provider,
    provider_from_preset,
    refresh_provider,
)
from .engine import RunOptions
from .forecast import estimate, forecast, usage_ledger
from .org import OrgError, find_org_text, load_org_text, template_names
from .presets import CHECKED, PRESETS
from .providers import ProviderError
from .runs import TERMINAL, RunManager
from .store import ACTIVE
from .workspace import Workspace, WorkspaceError

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class ProviderIn(BaseModel):
    preset: str
    id: str | None = None
    key: str | None = None
    models: list[str] | None = None
    params: dict[str, str] = Field(default_factory=dict)
    base_url: str | None = None


class ModelsIn(BaseModel):
    models: list[ModelConfig]


class OrgIn(BaseModel):
    yaml: str


class RunIn(BaseModel):
    org: str | None = None
    yaml: str | None = None
    goal: str
    allow_exec: bool = False
    auto_approve: bool = False
    demo: bool = False
    privacy: str | None = None
    project: str | None = None
    base: str | None = None
    allow_dirty: bool = False


class ForecastIn(BaseModel):
    org: str | None = None
    yaml: str | None = None
    goal: str
    privacy: str | None = None
    demo: bool = False


class DecisionIn(BaseModel):
    approve: bool
    answer: str = ""


def _host_ok(host: str | None, extra: set[str]) -> bool:
    if not host:
        return False
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host[1:].split("]")[0]
    return name in LOOPBACK or name in extra


def create_app(manager: RunManager, *, token: str | None = None,
               allowed_hosts: set[str] | None = None) -> FastAPI:
    token = token or manager.home.token()
    extra_hosts = allowed_hosts or set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.store.mark_stale_interrupted()
        yield
        await manager.shutdown()

    app = FastAPI(title="Cadre", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not _host_ok(request.headers.get("host"), extra_hosts):
            return PlainTextResponse("host not allowed", status_code=421)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-store"
        return response

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not pysecrets.compare_digest(value.strip(), token):
            raise HTTPException(401, "missing or wrong bearer token", {"WWW-Authenticate": "Bearer"})

    secured = [Depends(auth)]
    store = manager.store
    home = manager.home

    def run_or_404(rid: str) -> dict[str, Any]:
        run = store.get_run(rid)
        if run is None:
            raise HTTPException(404, "no such run")
        return run

    # ------------------------------------------------------------------ dashboard
    web = resources.files("cadre") / "web"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(web.joinpath("index.html").read_text(encoding="utf-8"))

    @app.get("/static/{name}", include_in_schema=False)
    async def static(name: str) -> Response:
        types = {"app.js": "text/javascript", "style.css": "text/css", "favicon.svg": "image/svg+xml"}
        if name not in types:
            raise HTTPException(404)
        return Response(web.joinpath(name).read_bytes(), media_type=types[name])

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/presets", dependencies=secured)
    async def presets() -> dict[str, Any]:
        return {"checked": CHECKED, "presets": [
            {"id": p.id, "label": p.label, "free": p.free, "local": p.local, "signup": p.signup,
             "source": p.source, "note": p.note, "params": list(p.params),
             "discover": bool(p.discover_filter) or not p.models,
             "day_reset": p.day_reset, "trains_on_free_data": p.trains_on_free_data,
             "policy_source": p.policy_source,
             "models": [m.name for m in p.models]} for p in PRESETS.values()]}

    # ------------------------------------------------------------------ providers
    def provider_view(pc) -> dict[str, Any]:
        where = manager.secrets.where(pc.key_ref, pc.env) if pc.key_ref else "not needed"
        return {"id": pc.id, "preset": pc.preset, "label": pc.label, "base_url": pc.base_url,
                "local": pc.local, "enabled": pc.enabled, "key": where or "missing",
                "disabled_reason": manager.disabled_reason(pc.id),
                "day_reset": pc.clock(), "trains_on_free_data": pc.trains(),
                "reserve_pct": pc.reserve_pct,
                "models": [m.model_dump() for m in pc.models]}

    @app.get("/api/providers", dependencies=secured)
    async def providers() -> list[dict[str, Any]]:
        return [provider_view(pc) for pc in home.load_config().providers]

    @app.post("/api/providers", dependencies=secured)
    async def add_provider(body: ProviderIn) -> dict[str, Any]:
        cfg = home.load_config()
        pid = body.id or body.preset
        if not SLUG.match(pid):
            raise HTTPException(400, "provider id must be lowercase letters, digits, - or _")
        try:
            pc = provider_from_preset(body.preset, pid=pid, base_url=body.base_url,
                                      params=body.params, models=body.models)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if body.key:
            try:
                manager.secrets.set(pc.key_ref or pid, body.key)
            except RuntimeError as e:
                raise HTTPException(400, str(e)) from None
        warning = ""
        if not pc.models:
            prov = make_provider(pc, manager.secrets)
            try:
                names = await prov.list_models()
            except Exception as e:
                names = []
                warning = (f"could not list models ({e}); set them with "
                           f"PUT /api/providers/{pid}/models")
            finally:
                await prov.aclose()
            flt = PRESETS[pc.preset].discover_filter
            if flt:
                names = [n for n in names if re.search(flt, n)]
            pc.models = [discovered_model(pc.preset, n, (i + 1) * 10) for i, n in enumerate(names[:6])]
            if not pc.models and not warning:
                warning = "the endpoint listed no usable models"
        cfg.providers = [p for p in cfg.providers if p.id != pid] + [pc]
        home.save_config(cfg)
        await manager.reload()
        return {**provider_view(pc), "warning": warning}

    @app.put("/api/providers/{pid}/models", dependencies=secured)
    async def set_models(pid: str, body: ModelsIn) -> dict[str, Any]:
        cfg = home.load_config()
        pc = cfg.provider(pid)
        if pc is None:
            raise HTTPException(404, "no such provider")
        pc.models = body.models
        home.save_config(cfg)
        await manager.reload()
        return provider_view(pc)

    @app.post("/api/providers/{pid}/test", dependencies=secured)
    async def test_provider(pid: str) -> dict[str, Any]:
        pc = home.load_config().provider(pid)
        if pc is None:
            raise HTTPException(404, "no such provider")
        prov = make_provider(pc, manager.secrets)
        try:
            ok, detail = await prov.health()
        finally:
            await prov.aclose()
        return {"ok": ok, "detail": detail}

    @app.get("/api/providers/{pid}/models", dependencies=secured)
    async def remote_models(pid: str) -> dict[str, Any]:
        pc = home.load_config().provider(pid)
        if pc is None:
            raise HTTPException(404, "no such provider")
        prov = make_provider(pc, manager.secrets)
        try:
            names = await prov.list_models()
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None
        finally:
            await prov.aclose()
        flt = PRESETS[pc.preset].discover_filter if pc.preset in PRESETS else None
        return {"models": names, "suggested": [n for n in names if not flt or re.search(flt, n)]}

    @app.post("/api/providers/{pid}/refresh", dependencies=secured)
    async def refresh(pid: str, apply: bool = False) -> dict[str, Any]:
        cfg = home.load_config()
        pc = cfg.provider(pid)
        if pc is None:
            raise HTTPException(404, "no such provider")
        diff = await refresh_provider(pc, manager.secrets, manager.transport)
        if apply and not diff.error and (diff.added or diff.removed):
            apply_diff(pc, diff)
            home.save_config(cfg)
            await manager.reload()
        return {**diff.model_dump(), "applied": apply and not diff.error}

    @app.delete("/api/providers/{pid}", dependencies=secured)
    async def remove_provider(pid: str) -> dict[str, Any]:
        cfg = home.load_config()
        pc = cfg.provider(pid)
        if pc is None:
            raise HTTPException(404, "no such provider")
        cfg.providers = [p for p in cfg.providers if p.id != pid]
        home.save_config(cfg)
        removed_key = manager.secrets.delete(pc.key_ref) if pc.key_ref else False
        await manager.reload()
        return {"removed": pid, "key_deleted": removed_key}

    @app.get("/api/quota", dependencies=secured)
    async def quota() -> list[dict[str, Any]]:
        router = manager.router()
        rows = []
        for m in router.models:
            snap = router.quota(m).snapshot()
            snap.update(provider=m.provider, model=m.name, tier=m.tier, family=m.family,
                        trains_on_free_data=m.trains,
                        usable=m.provider in router.providers and m.provider not in router.disabled)
            rows.append(snap)
        return rows

    @app.get("/api/usage", dependencies=secured)
    async def usage(days: int = 7) -> list[dict[str, Any]]:
        return usage_ledger(store, home.load_config(), min(max(days, 1), 90))

    @app.post("/api/forecast", dependencies=secured)
    async def forecast_endpoint(body: ForecastIn) -> dict[str, Any]:
        try:
            text = body.yaml if body.yaml is not None else find_org_text(body.org or "", home.orgs_dir)[1]
            org = load_org_text(text)
        except OrgError as e:
            raise HTTPException(422, {"errors": e.errors}) from None
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        est = estimate(store, org, body.goal)
        private = (body.privacy or org.privacy) == "private"
        result = forecast(est, manager.router(body.demo), private)
        return {**result.as_dict(), "lines": result.lines()}

    # ------------------------------------------------------------------ orgs
    def org_summary(name: str, source: str, text: str) -> dict[str, Any]:
        try:
            org = load_org_text(text)
        except OrgError as e:
            return {"name": name, "source": source, "valid": False, "errors": e.errors}
        return {"name": name, "source": source, "valid": True, "title": org.name,
                "description": org.description, "workflow": org.workflow.type,
                "agents": [{"id": a.id, "role": a.role, "tier": a.tier, "tools": a.tools}
                           for a in org.agents],
                "checks": [c.name for c in org.checks], "budget": org.budget.model_dump()}

    @app.get("/api/orgs", dependencies=secured)
    async def orgs() -> list[dict[str, Any]]:
        out = []
        for p in sorted(home.orgs_dir.glob("*.y*ml")):
            out.append(org_summary(p.stem, "yours", p.read_text(encoding="utf-8")))
        for name in template_names():
            _, text = find_org_text(name)
            out.append(org_summary(name, "template", text))
        return out

    @app.get("/api/orgs/{name}", dependencies=secured)
    async def org(name: str) -> dict[str, Any]:
        try:
            source, text = find_org_text(name, home.orgs_dir)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        return {**org_summary(name, source, text), "yaml": text}

    @app.post("/api/orgs/validate", dependencies=secured)
    async def validate_org(body: OrgIn) -> dict[str, Any]:
        return org_summary("draft", "draft", body.yaml)

    @app.put("/api/orgs/{name}", dependencies=secured)
    async def save_org(name: str, body: OrgIn) -> dict[str, Any]:
        if not SLUG.match(name):
            raise HTTPException(400, "org name must be lowercase letters, digits, - or _")
        summary = org_summary(name, "yours", body.yaml)
        if not summary["valid"]:
            raise HTTPException(422, {"errors": summary["errors"]})
        (home.orgs_dir / f"{name}.yaml").write_text(body.yaml, encoding="utf-8")
        return summary

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs", dependencies=secured)
    async def start_run(body: RunIn) -> dict[str, Any]:
        if not body.org and not body.yaml:
            raise HTTPException(400, "give an org name or org yaml")
        try:
            if body.privacy not in (None, "standard", "private"):
                raise ValueError("privacy must be standard or private")
            rid = manager.create(body.org or "", body.goal,
                                 RunOptions(allow_exec=body.allow_exec, auto_approve=body.auto_approve,
                                            privacy=body.privacy),
                                 demo=body.demo, org_yaml=body.yaml, project=body.project,
                                 base=body.base, allow_dirty=body.allow_dirty)
        except OrgError as e:
            raise HTTPException(422, {"errors": e.errors}) from None
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e)) from None
        manager.start(rid)
        return {"id": rid}

    @app.get("/api/runs", dependencies=secured)
    async def runs(limit: int = 50) -> list[dict[str, Any]]:
        return store.list_runs(min(max(limit, 1), 500))

    @app.get("/api/runs/{rid}", dependencies=secured)
    async def run(rid: str) -> dict[str, Any]:
        r = run_or_404(rid)
        r.pop("org_yaml", None)
        r["usage"] = store.usage_by_agent(rid)
        r["totals"] = store.usage_totals(rid)
        r["files"] = store.files(rid)
        r["approvals"] = store.approvals(rid)
        r["live"] = rid in manager.tasks
        folder = home.runs_dir / rid / "artifacts"
        r["artifacts"] = sorted(p.name for p in folder.glob("*")) if folder.exists() else []
        return r

    @app.get("/api/runs/{rid}/org", dependencies=secured)
    async def run_org(rid: str) -> PlainTextResponse:
        return PlainTextResponse(run_or_404(rid)["org_yaml"])

    @app.get("/api/runs/{rid}/events", dependencies=secured)
    async def events(rid: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        run_or_404(rid)
        return store.events(rid, after, min(max(limit, 1), 2000))

    @app.get("/api/runs/{rid}/stream", dependencies=secured)
    async def stream(rid: str, after: int = 0) -> StreamingResponse:
        run_or_404(rid)

        async def gen():
            cursor, idle = after, 0.0
            while True:
                batch = store.events(rid, cursor, 200)
                for e in batch:
                    cursor = e["seq"]
                    yield f"id: {cursor}\nevent: {e['kind']}\ndata: {json.dumps(e, default=str)}\n\n"
                if batch:
                    idle = 0.0
                    continue
                status = (store.get_run(rid) or {}).get("status")
                if status in TERMINAL:
                    yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                    return
                await asyncio.sleep(0.5)
                idle += 0.5
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/runs/{rid}/cancel", dependencies=secured)
    async def cancel(rid: str) -> dict[str, Any]:
        run_or_404(rid)
        return {"cancelled": manager.cancel(rid)}

    @app.post("/api/runs/{rid}/resume", dependencies=secured)
    async def resume(rid: str) -> dict[str, Any]:
        r = run_or_404(rid)
        if rid in manager.tasks or r["status"] in ACTIVE:
            raise HTTPException(409, "the run is still active")
        if not manager.resumable(rid):
            raise HTTPException(409, f"a {r['status']} run cannot be resumed")
        manager.start(rid)
        return {"id": rid, "resumed": True}

    @app.get("/api/runs/{rid}/artifacts/{name}", dependencies=secured)
    async def artifact(rid: str, name: str) -> PlainTextResponse:
        run_or_404(rid)
        folder = (home.runs_dir / rid / "artifacts").resolve()
        target = (folder / name).resolve()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name) or target.parent != folder or not target.is_file():
            raise HTTPException(404, "no such artifact")
        return PlainTextResponse(target.read_text(encoding="utf-8", errors="replace"))

    @app.get("/api/runs/{rid}/files/{path:path}", dependencies=secured)
    async def file(rid: str, path: str) -> PlainTextResponse:
        run_or_404(rid)
        ws = Workspace(home.runs_dir / rid)
        try:
            return PlainTextResponse(ws.read(path, max_chars=200_000))
        except WorkspaceError as e:
            raise HTTPException(404, str(e)) from None

    # ------------------------------------------------------------------ approvals
    @app.get("/api/approvals", dependencies=secured)
    async def approvals(pending: bool = True, run: str | None = None) -> list[dict[str, Any]]:
        return store.approvals(run, pending_only=pending)

    @app.post("/api/approvals/{aid}", dependencies=secured)
    async def decide(aid: str, body: DecisionIn) -> dict[str, Any]:
        if store.get_approval(aid) is None:
            raise HTTPException(404, "no such approval")
        if not store.decide(aid, body.approve, body.answer):
            raise HTTPException(409, "already decided")
        return {"id": aid, "approved": body.approve}

    @app.exception_handler(KeyError)
    async def key_error(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)

    return app
