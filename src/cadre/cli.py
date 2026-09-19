"""Command line (FR-7.1).

    cadre init                               create ~/.cadre
    cadre provider add groq                  add a free model by adding its key (hidden prompt)
    cadre run software-team "a CSV to Markdown converter" --allow-exec
    cadre run decision-board "Should we …?" --demo      (offline, no key)
    cadre serve / cadre ui                   the dashboard
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .accounts import ROLES, AccountError, Accounts
from .clocks import DayClock, ist
from .config import (
    Home,
    apply_diff,
    discovered_model,
    make_provider,
    provider_from_preset,
    refresh_provider,
)
from .engine import CONTAINER_ONLY, RunOptions
from .forecast import estimate, forecast, usage_ledger
from .org import OrgError, find_org_text, load_org_text, template_names
from .presets import CHECKED, PRESETS
from .project import ProjectError
from .providers import ProviderError
from .runs import RunManager
from .secrets import SecretStore, env_name
from .store import Store

for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

app = typer.Typer(help="Cadre — run an organisation of AI agents on free model APIs.",
                  no_args_is_help=True, add_completion=False)
provider_app = typer.Typer(help="Model providers and their keys.", no_args_is_help=True)
runs_app = typer.Typer(help="Recent runs, and cleanup of finished project worktrees.")
org_app = typer.Typer(help="Organisation files.", no_args_is_help=True)
scheduler_app = typer.Typer(help="Resume parked runs on a schedule (asks before installing).",
                            no_args_is_help=True)
user_app = typer.Typer(help="Users and their roles (M13). Run locally, as the owner.",
                       no_args_is_help=True)
token_app = typer.Typer(help="API tokens for users.", no_args_is_help=True)


def _print_version(value: bool) -> None:
    if value:
        con.print(f"cadre {__version__}")
        raise typer.Exit()


@app.callback()
def _root(version: bool = typer.Option(False, "--version", help="print the version and exit",
                                       is_eager=True, callback=_print_version)) -> None:
    """Cadre — run an organisation of AI agents on free model APIs."""


app.add_typer(provider_app, name="provider")
app.add_typer(org_app, name="org")
app.add_typer(runs_app, name="runs")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(user_app, name="user")
app.add_typer(token_app, name="token")
con = Console(highlight=False)


def home() -> Home:
    return Home().ensure()


def fail(msg: str, code: int = 1) -> None:
    con.print(f"[red]error:[/] {msg}")
    raise typer.Exit(code)


def run_async(coro):
    return asyncio.run(coro)


@app.command()
def version() -> None:
    """Print the version."""
    con.print(f"cadre {__version__}")


@app.command()
def init() -> None:
    """Create the Cadre home directory and API token."""
    h = home()
    con.print(f"Cadre home: [bold]{h.root}[/]")
    con.print("Next: [bold]cadre provider add groq[/] (free key, no card) — or try "
              "[bold]cadre run decision-board \"Should we hire a designer?\" --demo[/]")


# ------------------------------------------------------------------ providers
@app.command()
def presets() -> None:
    """List the providers Cadre knows how to add, with their free-tier notes."""
    t = Table(title=f"Provider presets (limits checked {CHECKED})", show_lines=False)
    for col in ("id", "provider", "free", "limits from", "key variable", "note"):
        t.add_column(col)
    for p in PRESETS.values():
        t.add_row(p.id, p.label, "local" if p.local else ("yes" if p.free else "paid"), p.source,
                  p.env or "-", p.note[:70])
    con.print(t)


def _read_key(pid: str, env: str | None, key_stdin: bool) -> str | None:
    store = SecretStore()
    if key_stdin:
        return sys.stdin.readline().strip() or None
    found = store.where(pid, env)
    if found:
        # `add` never overwrites a key it finds: say how to replace one (a rotated key re-added
        # with `add` silently kept the old one)
        if found.startswith("env:"):
            con.print(f"Using the key already available from {found}. To replace it, change "
                      f"{found[4:]}; the environment comes before the stored key.")
        else:
            con.print(f"Using the key already stored in the OS credential store. To replace it, "
                      f"run `cadre provider key {pid}`.")
        return None
    if not sys.stdin.isatty():
        # a hidden prompt with nobody at the keyboard hangs forever; say where we looked instead
        fail(f"no key found for {pid} (looked in {env_name(pid)}"
             + (f", {env}" if env else "") + f" and the OS credential store entry cadre/{pid}). "
             f"Run `uv run cadre provider add {pid}` in an interactive terminal, or use --key-stdin.")
    return typer.prompt(f"API key for {pid} (input hidden)", hide_input=True).strip() or None


@provider_app.command("add")
def provider_add(
    preset: str = typer.Argument(..., help="groq, gemini, openrouter, mistral, … (see `cadre presets`)"),
    pid: str | None = typer.Option(None, "--id", help="name for this provider (default: the preset)"),
    model: list[str] = typer.Option(None, "--model", "-m", help="model id; repeatable"),
    param: list[str] = typer.Option(None, "--param", help="key=value, e.g. account_id=… for Cloudflare"),
    base_url: str | None = typer.Option(None, help="for the `custom` preset"),
    key_stdin: bool = typer.Option(False, "--key-stdin", help="read the key from standard input"),
    test: bool = typer.Option(True, help="check the key against the endpoint"),
) -> None:
    """Add a model provider. The key goes to the OS credential store, never to a file."""
    h = home()
    if preset not in PRESETS:
        fail(f"unknown preset {preset!r}; see `cadre presets`")
    p = PRESETS[preset]
    params = dict(kv.split("=", 1) for kv in (param or []) if "=" in kv)
    try:
        pc = provider_from_preset(preset, pid=pid, base_url=base_url, params=params, models=model or None)
    except ValueError as e:
        fail(str(e))
    if not p.local:
        if p.signup:
            con.print(f"Get a key at {p.signup}")
        key = _read_key(pc.id, pc.env, key_stdin)
        if key:
            try:
                where = SecretStore().set(pc.key_ref or pc.id, key)
            except RuntimeError as e:
                fail(str(e))
            con.print(f"Key stored in the {where}.")

    _complete_provider(h, preset, pc, test)


def _complete_provider(h: Home, preset: str, pc, test: bool, drop_rejected: bool = False) -> bool:
    """Test the key, discover models when the preset lists none, and save the provider. With
    `drop_rejected`, a key the provider rejects is not saved (False is returned)."""
    p = PRESETS[preset]
    rejected = False

    async def finish() -> None:
        nonlocal rejected
        prov = make_provider(pc, SecretStore())
        try:
            if test:
                ok, detail = await prov.health()
                con.print(f"{'[green]OK[/]' if ok else '[red]problem[/]'}: {detail}")
                rejected = not ok and "key rejected" in detail
                if rejected and drop_rejected:
                    return
            if not pc.models:
                try:
                    names = await prov.list_models()
                except ProviderError as e:
                    con.print(f"[yellow]could not list models:[/] {e}")
                    names = []
                if p.discover_filter:
                    names = [n for n in names if re.search(p.discover_filter, n)]
                pc.models = [discovered_model(preset, n, (i + 1) * 10) for i, n in enumerate(names[:6])]
        finally:
            await prov.aclose()

    run_async(finish())
    if rejected and drop_rejected:
        return False
    cfg = h.load_config()
    cfg.providers = [x for x in cfg.providers if x.id != pc.id] + [pc]
    h.save_config(cfg)
    con.print(f"Added [bold]{pc.id}[/] with {len(pc.models)} model(s): "
              + ", ".join(f"{m.name} ({m.tier})" for m in pc.models))
    if not pc.models:
        con.print("Add models with [bold]cadre provider set-model[/].")
    if p.note:
        con.print(f"[dim]{p.note}[/]")
    return True


@provider_app.command("add-from-env")
def provider_add_from_env(test: bool = typer.Option(False, "--test/--no-test",
                                                    help="check each key against its endpoint")) -> None:
    """Add every free provider whose key is already in the environment (`CADRE_KEY_<ID>` or the
    preset's usual variable). For CI runners and containers, where there is no keychain and no
    one to type at a prompt. Paid presets are never added this way."""
    h = home()
    added = []
    for name, p in PRESETS.items():
        if p.local or not p.free or name == "custom":
            continue
        if not (os.environ.get(env_name(name)) or (p.env and os.environ.get(p.env))):
            continue
        try:
            pc = provider_from_preset(name)
        except ValueError as e:
            con.print(f"[yellow]skipped {name}:[/] {e}")
            continue
        con.print(f"Using the key for {name} from the environment.")
        if _complete_provider(h, name, pc, test, drop_rejected=True):
            added.append(name)
        else:
            con.print(f"[yellow]skipped {name}:[/] its key was rejected; check the secret")
    if not added:
        fail("no provider key found in the environment (e.g. GROQ_API_KEY, GEMINI_API_KEY)")


@provider_app.command("key")
def provider_key(pid: str, key_stdin: bool = typer.Option(False, "--key-stdin")) -> None:
    """Set or replace a provider's key (hidden prompt)."""
    pc = home().load_config().provider(pid)
    if pc is None:
        fail(f"no provider {pid!r}")
    key = (sys.stdin.readline() if key_stdin else
           typer.prompt(f"API key for {pid} (input hidden)", hide_input=True)).strip()
    if not key:
        fail("empty key")
    try:
        SecretStore().set(pc.key_ref or pid, key)
    except RuntimeError as e:
        fail(str(e))
    con.print("Key stored. Restart `cadre serve` if it is running.")


@provider_app.command("list")
def provider_list() -> None:
    """Show configured providers, where each key comes from, and their models."""
    cfg = home().load_config()
    if not cfg.providers:
        con.print("No providers yet. Try [bold]cadre provider add groq[/].")
        return
    store = SecretStore()
    t = Table()
    for col in ("provider", "key", "model", "tier", "family", "rpm", "rpd", "tpm", "tpd", "source"):
        t.add_column(col)
    for pc in cfg.providers:
        con.print(f"[bold]{pc.id}[/]: day resets {pc.clock()} · trains on free data: "
                  f"{pc.trains()} · reserve {pc.reserve_pct}%")
        where = store.where(pc.key_ref, pc.env) if pc.key_ref else "not needed"
        for i, m in enumerate(pc.models or [None]):
            if m is None:
                t.add_row(pc.id, where or "[red]missing[/]", "-", "", "", "", "", "", "", "")
                continue
            t.add_row(pc.id if i == 0 else "", (where or "[red]missing[/]") if i == 0 else "",
                      m.name + ("" if m.enabled else " (off)"), m.tier, m.family,
                      *[str(v) if v else "-" for v in (m.rpm, m.rpd, m.tpm, m.tpd)], m.source or "-")
    con.print(t)


@provider_app.command("test")
def provider_test(pid: str) -> None:
    """Check that a provider answers and accepts its key."""
    pc = home().load_config().provider(pid)
    if pc is None:
        fail(f"no provider {pid!r}")

    async def go():
        prov = make_provider(pc, SecretStore())
        try:
            return await prov.health()
        finally:
            await prov.aclose()

    ok, detail = run_async(go())
    con.print(f"{'[green]OK[/]' if ok else '[red]problem[/]'}: {detail}")
    raise typer.Exit(0 if ok else 1)


@provider_app.command("models")
def provider_models(pid: str, all_models: bool = typer.Option(False, "--all")) -> None:
    """Ask the endpoint which models it serves today."""
    pc = home().load_config().provider(pid)
    if pc is None:
        fail(f"no provider {pid!r}")

    async def go():
        prov = make_provider(pc, SecretStore())
        try:
            return await prov.list_models()
        finally:
            await prov.aclose()

    try:
        names = run_async(go())
    except ProviderError as e:
        fail(str(e))
    flt = PRESETS[pc.preset].discover_filter if pc.preset in PRESETS else None
    if flt and not all_models:
        names = [n for n in names if re.search(flt, n)]
    configured = {m.name for m in pc.models}
    for n in names:
        con.print(("* " if n in configured else "  ") + n)
    con.print(f"[dim]{len(names)} model(s); * = configured[/]")


@provider_app.command("refresh")
def provider_refresh(pid: str | None = typer.Argument(None, help="one provider; default all"),
                     apply: bool = typer.Option(False, "--apply",
                                                help="add new models, disable vanished ones")) -> None:
    """Compare each provider's current catalogue with config.yaml (limits you set are never changed)."""
    h = home()
    cfg = h.load_config()
    targets = [cfg.provider(pid)] if pid else list(cfg.providers)
    if pid and targets[0] is None:
        fail(f"no provider {pid!r}")
    changed = False
    for pc in targets:
        d = run_async(refresh_provider(pc))
        if d.error:
            con.print(f"[yellow]{pc.id}: could not list models — {d.error}[/]", markup=True)
            continue
        con.print(f"[bold]{pc.id}[/]: {len(d.kept)} unchanged")
        for n in d.added:
            con.print(f"  [green]+ {n}[/] (new at the provider)")
        for n in d.removed:
            con.print(f"  [red]- {n}[/] (no longer listed)")
        if apply and (d.added or d.removed):
            apply_diff(pc, d)
            changed = True
    if changed:
        h.save_config(cfg)
        con.print("config.yaml updated (new models added, vanished ones disabled).")
    elif not apply:
        con.print("[dim]Nothing changed. Use --apply to add new models and disable vanished ones.[/]")


@provider_app.command("set-model")
def provider_set_model(
    pid: str, model: str,
    tier: str | None = typer.Option(None, help="strong | fast"),
    family: str | None = None,
    priority: int | None = None,
    protocol: str | None = typer.Option(None, help="native | json"),
    rpm: int | None = None, rpd: int | None = None, tpm: int | None = None, tpd: int | None = None,
    enable: bool | None = typer.Option(None, "--enable/--disable"),
    remove: bool = typer.Option(False, "--remove"),
) -> None:
    """Add a model to a provider, or change its tier, family, limits or state."""
    h = home()
    cfg = h.load_config()
    pc = cfg.provider(pid)
    if pc is None:
        fail(f"no provider {pid!r}")
    existing = next((m for m in pc.models if m.name == model), None)
    if remove:
        pc.models = [m for m in pc.models if m.name != model]
    else:
        if existing is None:
            existing = discovered_model(pc.preset, model, (len(pc.models) + 1) * 10)
            pc.models.append(existing)
        for field, value in dict(tier=tier, family=family, priority=priority, protocol=protocol,
                                 rpm=rpm, rpd=rpd, tpm=tpm, tpd=tpd, enabled=enable).items():
            if value is not None:
                setattr(existing, field, value)
    h.save_config(cfg)
    con.print(f"{pid}: " + ", ".join(f"{m.name} ({m.tier}, {m.family})" for m in pc.models))


@provider_app.command("remove")
def provider_remove(pid: str, keep_key: bool = typer.Option(False, "--keep-key")) -> None:
    """Remove a provider (and its stored key)."""
    h = home()
    cfg = h.load_config()
    pc = cfg.provider(pid)
    if pc is None:
        fail(f"no provider {pid!r}")
    cfg.providers = [p for p in cfg.providers if p.id != pid]
    h.save_config(cfg)
    if pc.key_ref and not keep_key:
        SecretStore().delete(pc.key_ref)
    con.print(f"Removed {pid}. (A key in {env_name(pid)} or {pc.env} is not touched.)")


@app.command()
def quota() -> None:
    """Today's usage against each model's free limits (as this machine has counted it)."""
    h = home()
    cfg = h.load_config()
    store = Store(h.db_path)
    now = time.time()
    t = Table(title="Usage in each provider's current day")
    for col in ("model", "tier", "day", "requests", "rpd", "tokens", "tpd", "resets"):
        t.add_column(col)
    for pc in cfg.providers:
        clock = DayClock(pc.clock())
        for m in pc.models:
            key = f"{pc.id}/{m.name}"
            req = tok = 0
            for day in clock.window_keys(now):
                r, k = store.quota_load(key, day)
                req, tok = req + r, tok + k
            t.add_row(key, m.tier, clock.key(now), str(req), str(m.rpd or "-"), str(tok),
                      str(m.tpd or "-"), ist(clock.next_reset(now)))
    con.print(t)


@app.command()
def usage(days: int = typer.Option(7, "--days", "-d", help="how many days back")) -> None:
    """Requests and tokens per day × provider × model, with the share of each daily cap."""
    h = home()
    rows = usage_ledger(Store(h.db_path), h.load_config(), days)
    if not rows:
        con.print(f"No usage recorded in the last {days} day(s).")
        return
    t = Table(title=f"Usage, last {days} day(s) — share is of the full daily cap")
    for col in ("day", "provider", "model", "requests", "rpd", "tokens", "tpd", "share", "next reset"):
        t.add_column(col)
    for r in rows:
        share = "-" if r["share"] is None else f"{r['share'] * 100:.0f}%"
        t.add_row(r["day"], r["provider"], r["model"], f"{r['requests']:,}", str(r["rpd"] or "-"),
                  f"{r['tokens']:,}", str(r["tpd"] or "-"), share, r["next_reset"] or "")
    con.print(t)


def _forecast_lines(manager: RunManager, org_text: str, goal: str, private: bool, demo: bool,
                    project: str | None = None) -> list[str]:
    org = load_org_text(org_text)
    est = estimate(manager.store, org, goal, project)
    router = manager.router(demo)
    return forecast(est, router, private or org.privacy == "private").lines()


@app.command("forecast")
def forecast_cmd(org: str, goal: str,
                 private: bool = typer.Option(False, "--private"),
                 demo: bool = typer.Option(False, "--demo"),
                 project: str | None = typer.Option(None, "--project",
                                                    help="accepted for symmetry with `run`")) -> None:
    """Will this job fit in the quota left today? Says what the estimate is based on."""
    h = home()
    manager = RunManager(h)
    try:
        _, text = find_org_text(org, h.orgs_dir)
        for line in _forecast_lines(manager, text, goal, private, demo, project):
            con.print(line, markup=False)
    except (OrgError, FileNotFoundError) as e:
        fail(str(e))


# ------------------------------------------------------------------ orgs
@org_app.command("list")
def org_list() -> None:
    """Templates and your own organisations."""
    h = home()
    for p in sorted(h.orgs_dir.glob("*.y*ml")):
        con.print(f"[bold]{p.stem}[/]  (yours)")
    for name in template_names():
        org = load_org_text(find_org_text(name)[1])
        con.print(f"[bold]{name}[/]  template · {org.workflow.type} · {len(org.agents)} agents — "
                  f"{org.description.strip()[:80]}")


@org_app.command("show")
def org_show(name: str) -> None:
    """Print an organisation file."""
    try:
        source, text = find_org_text(name, home().orgs_dir)
    except FileNotFoundError as e:
        fail(str(e))
    con.print(f"[dim]# {source}[/]")
    con.print(text, markup=False)


@org_app.command("validate")
def org_validate(path: str) -> None:
    """Validate an organisation file without calling any model."""
    try:
        source, text = find_org_text(path, home().orgs_dir)
        org = load_org_text(text)
    except (FileNotFoundError, OrgError) as e:
        fail(str(e))
    con.print(f"[green]valid[/]: {org.name} — {len(org.agents)} agents, {org.workflow.type} workflow, "
              f"{len(org.checks)} checks ({source})")


@org_app.command("new")
def org_new(name: str, template: str = typer.Option("software-team", "--from")) -> None:
    """Copy a template into ~/.cadre/orgs/<name>.yaml to edit."""
    h = home()
    if not re.match(r"^[a-z][a-z0-9_-]{0,31}$", name):
        fail("name must be lowercase letters, digits, - or _")
    target = h.orgs_dir / f"{name}.yaml"
    if target.exists():
        fail(f"{target} already exists")
    try:
        _, text = find_org_text(template)
    except FileNotFoundError as e:
        fail(str(e))
    target.write_text(re.sub(r"^name: .*$", f"name: {name}", text, count=1, flags=re.M), encoding="utf-8")
    con.print(f"Created {target}. Edit it, then: cadre run {name} \"<goal>\"")


# ------------------------------------------------------------------ runs
def _line(e: dict[str, Any]) -> str | None:
    d = e["data"]
    who = e["agent"] or ""
    k = e["kind"]
    t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
    tag = f"[dim]{t}[/] [bold cyan]{who:>10}[/]" if who else f"[dim]{t}[/] {'':>10}"
    esc = lambda s: str(s).replace("[", r"\[")  # noqa: E731
    if k == "run.started":
        return f"{tag} run started · {esc(d['org'])} · models: {esc(', '.join(d['models']))}" + (
            " · resumed" if d.get("resumed") else "")
    if k == "step.started":
        return f"{tag} [magenta]▶ {d['type']}[/] {esc(d.get('title', ''))} [dim]{e['step']}[/]"
    if k == "agent.call":
        extra = ""
        if d.get("independent") is False:
            extra = " [yellow](not independent — only one model family available)[/]"
        if d.get("waited"):
            extra += f" [yellow]waited {d['waited']}s[/]"
        tools = f" → {', '.join(d['tools'])}" if d.get("tools") else ""
        return f"{tag} [dim]{esc(d['model'])} {d['tokens_in']}→{d['tokens_out']} tok{tools}[/]{extra}"
    if k == "agent.tool":
        mark = "[green]✓[/]" if d["ok"] else "[red]✗[/]"
        arg = d["args"].get("path") or d["args"].get("name") or ""
        return f"{tag}   {mark} {d['tool']} {esc(arg)}" + ("" if d["ok"] else f" [red]{esc(d['result'][:120])}[/]")
    if k == "agent.answer":
        text = d["text"].replace("\n", " ")
        return f"{tag}   [white]{esc(text[:160])}{'…' if len(text) > 160 else ''}[/]"
    if k in ("route.wait", "route.fallback"):
        return f"{tag}   [yellow]{esc(d.get('reason') or d.get('note'))}[/]" + (
            f" ({d['seconds']}s)" if "seconds" in d else "")
    if k == "check.finished":
        colour = "green" if d["passed"] else "red"
        ran = "" if d.get("runner", "subprocess") == "subprocess" else f" in {d['runner']}"
        return f"{tag}   [{colour}]check {d['name']} {'passed' if d['passed'] else 'FAILED'}[/]{ran} {esc(d.get('note', ''))}"
    if k == "review.round":
        checks = " ".join(f"{c['name']}={'ok' if c['passed'] else 'FAIL'}" for c in d["checks"])
        verdicts = " ".join(f"{v['reviewer']}={'approve' if v['approve'] else 'changes'}" for v in d["verdicts"])
        colour = "green" if d["approved"] else "yellow"
        return f"{tag} [{colour}]review round {d['round']}: {'APPROVED' if d['approved'] else 'changes requested'}[/] {checks} {verdicts}"
    if k == "council.tally":
        counts = " ".join(f"{o}:{n}" for o, n in d["counts"].items())
        return f"{tag} [green]vote ({d['rule']}): {counts} → {d['winner']} (decided by {d['decided_by']})[/]"
    if k == "plan.created":
        return f"{tag} plan: " + "; ".join(f"{t_['id']}→{t_['assignee']}: {esc(t_['title'])}" for t_ in d["tasks"])
    if k in ("task.failed", "task.skipped"):
        return f"{tag} [yellow]{k} {d['task']}: {esc(d.get('error') or d.get('reason'))}[/]"
    if k == "note":
        return f"{tag}   [blue]note:[/] {esc(d['text'][:200])}"
    if k == "agent.invalid":
        return f"{tag}   [red]unusable reply: {esc(d['error'])}[/]"
    if k == "run.finished":
        colour = "green" if d["status"] == "succeeded" else "yellow"
        return (f"{tag} [{colour}]run {d['status']}[/]" + (f": {esc(d['error'])}" if d.get("error") else "")
                + f" · {d['calls']} calls · {d['prompt_tokens']}+{d['completion_tokens']} tokens")
    return None


async def _follow(store: Store, run_id: str, done: asyncio.Event, quiet: bool) -> None:
    cursor = 0
    while True:
        batch = store.events(run_id, cursor, 200)
        for e in batch:
            cursor = e["seq"]
            if not quiet:
                line = _line(e)
                if line:
                    con.print(line)
        if not batch and done.is_set():
            return
        await asyncio.sleep(0.25)


def _terminal_approver(no_input: bool):
    async def approver(kind: str, prompt: str, agent: str | None) -> tuple[bool, str]:
        if no_input:
            raise typer.Exit(2)
        con.print()
        title = {"exec": "Run code?", "question": f"{agent} asks", "gate": "Approval needed"}[kind]
        con.print(f"[bold yellow]{title}[/]\n{prompt}", markup=True)
        if kind == "question":
            answer = await asyncio.to_thread(typer.prompt, "Your answer", default="")
            return True, answer
        ok = await asyncio.to_thread(typer.confirm, "Approve?", default=False)
        return ok, ""
    return approver


def _execute(manager: RunManager, run_id: str, quiet: bool, wait_elsewhere: bool) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        done = asyncio.Event()
        follower = asyncio.create_task(_follow(manager.store, run_id, done, quiet))
        try:
            run = await manager.execute(run_id, None if wait_elsewhere else _terminal_approver(False))
        finally:
            done.set()
            await follower
            await manager.shutdown()
        return run

    return asyncio.run(main())


def _report(h: Home, run: dict[str, Any]) -> None:
    con.print()
    con.print(f"Run [bold]{run['id']}[/] — {run['status']}")
    if run.get("error"):
        con.print(f"[yellow]{run['error']}[/]")
    s = run.get("summary") or {}
    if s:
        con.print(f"Workspace: {s.get('workspace')}")
        if s.get("unapproved"):
            con.print(f"[yellow]Not approved: {', '.join(s['unapproved'])}[/]")
    for path in s.get("artifacts", []):
        con.print(f"Record: {path}")
    p = s.get("project")
    if p and not p.get("error"):
        con.print(f"\nBranch [bold]{p['branch']}[/] in {p['root']} — {len(p['commits'])} commit(s):",
                  highlight=False)
        for c in p["commits"][:20]:
            con.print(f"  {c}", markup=False)
        if p["diff_stat"]:
            con.print(p["diff_stat"], markup=False)
        con.print("Review it:", markup=False)
        con.print(f"  {p['review']['log']}\n  {p['review']['diff']}", markup=False)
        con.print("Throw it away:", markup=False)
        con.print(f"  {p['review']['discard']}", markup=False)
        con.print("[dim]Cadre never merges or pushes; the branch is yours to review.[/]")
    elif p:
        con.print(f"[yellow]{p['error']}[/]")
    if run["status"] == "parked":
        con.print(f"[yellow]Parked until about {ist(run['resume_at'])}[/] — daily limits. "
                  "`cadre serve` resumes it automatically, or run `cadre resume --due` after that "
                  "(`cadre scheduler install` can do this on a schedule).")
    elif run["status"] in ("interrupted", "failed", "stopped", "cancelled", "unapproved"):
        hint = " --add-calls 20" if run["status"] == "stopped" and "call budget" in (run.get("error") or "") else ""
        con.print(f"Resume with: cadre resume {run['id']}{hint}")


@app.command()
def run(
    org: str = typer.Argument(..., help="template name, your org name, or a .yaml path"),
    goal: str = typer.Argument(..., help="what the organisation should achieve"),
    allow_exec: bool = typer.Option(False, "--allow-exec", help="let checks run model-written code without asking"),
    allow_container_exec: bool = typer.Option(
        False, "--allow-container-exec",
        help="let checks that run in a container with no network run without asking; checks that run as you still ask"),
    yes: bool = typer.Option(False, "--yes", "-y", help="auto-approve gates and questions"),
    demo: bool = typer.Option(False, "--demo", help="offline scripted model; no key needed"),
    private: bool = typer.Option(False, "--private",
                                 help="never use a provider whose free tier trains on prompts"),
    project: str | None = typer.Option(None, "--project",
                                       help="an existing git repository to work on (a branch is delivered)"),
    base: str | None = typer.Option(None, "--base", help="branch or commit to start from (default HEAD)"),
    allow_dirty: bool = typer.Option(False, "--allow-dirty",
                                     help="start from HEAD even if the working tree has uncommitted changes"),
    wait_elsewhere: bool = typer.Option(False, "--approve-elsewhere",
                                        help="leave approvals to the dashboard / `cadre approve`"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    show_result: bool = typer.Option(True, "--result/--no-result"),
    result_json: Path | None = typer.Option(None, "--result-json",
                                            help="also write the outcome as JSON here (for CI and scripts)"),
) -> None:
    """Run an organisation on a goal, streaming what every agent does."""
    h = home()
    manager = RunManager(h)
    try:
        exec_policy: bool | str = True if allow_exec else (CONTAINER_ONLY if allow_container_exec else False)
        run_id = manager.create(org, goal, RunOptions(allow_exec=exec_policy, auto_approve=yes,
                                                      privacy="private" if private else None),
                                demo=demo, project=project, base=base, allow_dirty=allow_dirty)
    except (OrgError, FileNotFoundError, ProjectError, ValueError) as e:
        fail(str(e))
    con.print(f"Run [bold]{run_id}[/] · {org}{' · demo mode' if demo else ''}")
    if not quiet:
        try:
            row = manager.store.get_run(run_id)
            for line in _forecast_lines(manager, row["org_yaml"], goal, private, demo,
                                        row.get("project_path")):
                con.print(f"[dim]{line}[/]", markup=True, highlight=False)
        except Exception as e:  # a forecast must never stop a run from starting
            con.print(f"[dim]Forecast unavailable: {e}[/]")
    result = _execute(manager, run_id, quiet, wait_elsewhere)
    if show_result and result.get("result"):
        con.print()
        con.rule("result")
        con.print(result["result"], markup=False)
        con.rule()
    _report(h, result)
    if result_json:
        _write_result_json(manager.store, result, result_json)
    raise typer.Exit(0 if result["status"] in ("succeeded", "parked") else 1)


def _write_result_json(store: Store, run: dict[str, Any], path: Path) -> None:
    """The outcome in one file: status, result, branch and usage (the GitHub Action reads it)."""
    project = (run.get("summary") or {}).get("project") or {}
    out = {"id": run["id"], "org": run.get("org"), "goal": run.get("goal"), "status": run["status"],
           "error": run.get("error"), "result": run.get("result") or "",
           "resume_at": run.get("resume_at"),
           "resume_at_ist": ist(run["resume_at"]) if run.get("resume_at") else None,
           "branch": project.get("branch") or run.get("branch"), "commits": project.get("commits", []),
           "diff_stat": project.get("diff_stat", ""), "totals": store.usage_totals(run["id"]),
           "usage": store.usage_by_agent(run["id"])}
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")


@app.command()
def resume(run_id: str | None = typer.Argument(None, help="the run to continue"),
           due: bool = typer.Option(False, "--due", help="resume every parked run whose reset has passed"),
           add_calls: int = typer.Option(0, "--add-calls", help="raise the run's model-call budget"),
           add_tokens: int = typer.Option(0, "--add-tokens", help="raise the run's token budget"),
           quiet: bool = typer.Option(False, "--quiet", "-q"),
           wait_elsewhere: bool = typer.Option(False, "--approve-elsewhere")) -> None:
    """Continue an interrupted, failed, stopped, unapproved or parked run. Finished steps are reused
    and not billed again; budgets count everything the run has already used."""
    h = home()
    manager = RunManager(h)
    manager.store.mark_stale_interrupted()
    if due:
        ids = manager.due()
        if not ids:
            parked = manager.store.parked()
            con.print("No parked run is due." + (f" Next: {parked[0]['id']} at {ist(parked[0]['resume_at'])}"
                                                 if parked else ""))
            return
        worst = 0
        for rid in ids:
            con.print(f"Resuming parked run {rid}")
            result = _execute(RunManager(h), rid, quiet, True)
            _report(h, result)
            worst = max(worst, 0 if result["status"] in ("succeeded", "parked") else 1)
        raise typer.Exit(worst)
    if not run_id:
        fail("give a run id, or --due")
    if not manager.resumable(run_id):
        r = manager.store.get_run(run_id)
        fail(f"run {run_id} is {r['status'] if r else 'unknown'} and cannot be resumed")
    if add_calls or add_tokens:
        manager.add_allowance(run_id, add_calls, add_tokens)
        con.print(f"Budget raised by {add_calls} calls and {add_tokens} tokens.")
    result = _execute(manager, run_id, quiet, wait_elsewhere)
    _report(h, result)
    raise typer.Exit(0 if result["status"] in ("succeeded", "parked") else 1)


@runs_app.callback(invoke_without_command=True)
def runs(ctx: typer.Context, limit: int = 20) -> None:
    """Recent runs."""
    if ctx.invoked_subcommand:
        return
    store = Store(home().db_path)
    store.mark_stale_interrupted()
    t = Table()
    for col in ("id", "org", "status", "calls", "tokens", "goal"):
        t.add_column(col)
    for r in store.list_runs(limit):
        t.add_row(r["id"], r["org"], r["status"], str(r["calls"]),
                  str(r["prompt_tokens"] + r["completion_tokens"]), r["goal"][:60])
    con.print(t)


@runs_app.command("cleanup")
def runs_cleanup(yes: bool = typer.Option(False, "--yes", "-y", help="do not ask for confirmation")) -> None:
    """Remove the git worktrees of finished project runs. Their branches are kept for review."""
    manager = RunManager(home())
    items = manager.cleanup_candidates()
    if not items:
        con.print("No finished project runs have a worktree left.")
        return
    for r in items:
        con.print(f"{r['id']}  {r['status']:<11} {r['branch']}  in {r['project_path']}", markup=False)
    if not yes and not typer.confirm(f"Remove {len(items)} worktree(s)? Branches are kept.", default=False):
        con.print("Nothing removed.")
        return
    for r in items:
        ok, msg = manager.cleanup(r["id"])
        con.print(f"{r['id']}: {msg}", markup=False)


@app.command()
def show(run_id: str, events: bool = typer.Option(False, "--events", help="print the whole timeline"),
         as_json: bool = typer.Option(False, "--json")) -> None:
    """Details of one run: status, usage per agent, files, result."""
    h = home()
    store = Store(h.db_path)
    r = store.get_run(run_id)
    if r is None:
        fail(f"no run {run_id}")
    if as_json:
        r.pop("org_yaml", None)
        r["usage"] = store.usage_by_agent(run_id)
        r["files"] = store.files(run_id)
        print(json.dumps(r, indent=2, default=str))
        return
    con.print(f"[bold]{r['id']}[/] · {r['org']} · {r['status']}")
    con.print(f"Goal: {r['goal']}", markup=False)
    if r["error"]:
        con.print(f"[yellow]{r['error']}[/]", markup=False)
    t = Table(title="usage")
    for col in ("agent", "provider", "model", "calls", "in", "out"):
        t.add_column(col)
    for u in store.usage_by_agent(run_id):
        t.add_row(u["agent"], u["provider"], u["model"], str(u["calls"]),
                  str(u["prompt_tokens"]), str(u["completion_tokens"]))
    con.print(t)
    for f in store.files(run_id):
        con.print(f"  {f['path']}  v{f['versions']}  {f['bytes']} B  by {f['agent']}")
    if events:
        for e in store.events(run_id, limit=100_000):
            line = _line(e)
            if line:
                con.print(line)
    if r["result"]:
        con.rule("result")
        con.print(r["result"], markup=False)


@app.command()
def cancel(run_id: str) -> None:
    """Cancel a run (works across processes: the run stops at its next step)."""
    h = home()
    manager = RunManager(h)
    try:
        ok = manager.cancel(run_id)
    except KeyError:
        fail(f"no run {run_id}")
    con.print("cancelling" if ok else "the run is not active")


@app.command()
def approvals() -> None:
    """Pending approvals and questions."""
    store = Store(home().db_path)
    items = store.approvals(pending_only=True)
    if not items:
        con.print("Nothing is waiting.")
    for a in items:
        con.print(f"[bold]{a['id']}[/] · run {a['run_id']} · {a['kind']} · {a['agent'] or ''}")
        con.print(f"  {a['prompt']}", markup=False)


@app.command()
def approve(approval_id: str, reject: bool = typer.Option(False, "--reject"),
            answer: str = typer.Option("", "--answer", "-a")) -> None:
    """Approve (or --reject) a pending approval; --answer replies to a question."""
    store = Store(home().db_path)
    if not store.decide(approval_id, not reject, answer):
        fail("no pending approval with that id")
    con.print("rejected" if reject else "approved")


@scheduler_app.command("install")
def scheduler_install(every: int = typer.Option(30, "--every", help="minutes between checks"),
                      yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Resume parked runs on a schedule: Task Scheduler (Windows), a systemd user timer (Linux) or
    a launchd agent (macOS)."""
    from . import scheduler

    plan = scheduler.install_plan(every)
    con.print("This will register a scheduled job on this machine:", markup=False)
    con.print(plan.describe(), markup=False)
    if not yes and not typer.confirm("Install it?", default=False):
        con.print("Not installed.")
        raise typer.Exit(1)
    ok, out = scheduler.apply(plan)
    con.print(out, markup=False)
    raise typer.Exit(0 if ok else 1)


@scheduler_app.command("uninstall")
def scheduler_uninstall() -> None:
    """Remove the scheduled job."""
    from . import scheduler

    ok, out = scheduler.apply(scheduler.uninstall_plan())
    con.print(out, markup=False)
    raise typer.Exit(0 if ok else 1)


@app.command("mcp")
def mcp_cmd(port: int | None = typer.Option(None, help="the port `cadre serve` uses (default from config)")
            ) -> None:
    """Serve MCP over stdio for AI editors (Claude Code, VS Code, Cursor, Windsurf, Antigravity).

    Starts `cadre serve` in the background when it is not running. Needs the `mcp` extra:
    `uvx --from "cadre-ai[mcp]" cadre mcp`."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        print("error: `cadre mcp` needs the mcp extra; run it as "
              '`uvx --from "cadre-ai[mcp]" cadre mcp` or `pip install "cadre-ai[mcp]"`', file=sys.stderr)
        raise typer.Exit(1) from None
    mcp_main(port)  # stdout carries the protocol from here on: print nothing


@app.command("scheduled-run", hidden=True)
def scheduled_run(home_dir: str | None = typer.Argument(None)) -> None:
    """What the scheduled job runs in a standalone build (no `python -m` there)."""
    from . import scheduled

    raise typer.Exit(scheduled.main([home_dir] if home_dir else []))


@scheduler_app.command("status")
def scheduler_status() -> None:
    """Show whether the scheduled job exists, and which parked runs are waiting."""
    from . import scheduler

    ok, out = scheduler.run(scheduler.status_command())
    con.print(out if ok else "The scheduled job is not installed.", markup=False)
    for r in Store(home().db_path).parked():
        con.print(f"parked: {r['id']} ({r['org']}) resumes ~{ist(r['resume_at'])}", markup=False)


# ------------------------------------------------------------------ server
@app.command()
def serve(host: str = "127.0.0.1", port: int | None = None,
          allowed_host: list[str] = typer.Option(  # noqa: B008
              [], "--allowed-host", help="also accept this Host header (a Tailscale name, a "
              "container's service name); repeatable, exact names only"),
          open_browser: bool = typer.Option(False, "--open")) -> None:
    """Start the API and dashboard (loopback only unless --host says otherwise)."""
    import uvicorn

    from .api import allowed_host_names, create_app

    h = home()
    port = port or h.load_config().settings.port
    try:
        extra = allowed_host_names(host, allowed_host)
    except ValueError as e:
        fail(str(e))
    if host not in ("127.0.0.1", "localhost", "::1"):
        con.print("[yellow]warning:[/] listening beyond this machine. Anyone who can reach the port "
                  "and holds the token can start runs that execute code. Prefer an SSH tunnel.")
    if extra:
        con.print("Accepting Host: " + ", ".join(sorted(extra)), markup=False)
    manager = RunManager(h)
    manager.router()
    for w in manager.warnings:
        con.print(f"[yellow]{w}[/]")
    url = f"http://127.0.0.1:{port}/#token={h.token()}"
    con.print(f"Dashboard: http://127.0.0.1:{port}  (open it with `cadre ui`)")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(manager, allowed_hosts=extra), host=host, port=port,
                log_level="warning", access_log=False)


@app.command()
def ui(port: int | None = None, print_only: bool = typer.Option(False, "--print")) -> None:
    """Open the dashboard in a browser, passing the token in the URL fragment."""
    h = home()
    port = port or h.load_config().settings.port
    url = f"http://127.0.0.1:{port}/#token={h.token()}"
    if print_only:
        print(url)
        return
    webbrowser.open(url)
    con.print(f"Opened http://127.0.0.1:{port} (start the server with `cadre serve` if it is not running)")


def main() -> None:  # pragma: no cover
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    app()


if __name__ == "__main__":  # pragma: no cover
    main()


# --------------------------------------------------------------------------- users and tokens (M13)
def _accounts() -> Accounts:
    """Accounts over the local database, with the owner bootstrapped from CADRE_HOME/token so an
    existing single-user install always has its admin."""
    h = home()
    accounts = Accounts(Store(h.db_path))
    accounts.bootstrap(h.token())
    return accounts


@user_app.command("list")
def user_list() -> None:
    """Show users and their roles."""
    accounts = _accounts()
    users = accounts.list_users()
    if not users:
        con.print("No users yet.")
        return
    t = Table()
    for col in ("id", "name", "role", "status"):
        t.add_column(col)
    for u in users:
        t.add_row(u["id"], u["name"] or "", u["role"], "disabled" if u["disabled"] else "active")
    con.print(t)


@user_app.command("add")
def user_add(
    uid: str = typer.Argument(..., help="a short id, e.g. bob"),
    role: str = typer.Option("member", "--role", help=f"one of {', '.join(ROLES)}"),
    name: str = typer.Option("", "--name", help="display name"),
) -> None:
    """Create a user. Give them a token with `cadre token new <id>`."""
    try:
        _accounts().create_user(uid, name, role)
    except AccountError as e:
        fail(str(e))
    con.print(f"Added [bold]{uid}[/] ({role}). Create a token: [bold]cadre token new {uid}[/].")


@user_app.command("role")
def user_role(uid: str = typer.Argument(...), role: str = typer.Argument(..., help=", ".join(ROLES))) -> None:
    """Change a user's role."""
    try:
        _accounts().set_role(uid, role)
    except AccountError as e:
        fail(str(e))
    con.print(f"{uid} is now [bold]{role}[/].")


@user_app.command("disable")
def user_disable(uid: str = typer.Argument(...)) -> None:
    """Disable a user (their tokens stop working) without deleting their history."""
    try:
        _accounts().set_disabled(uid, True)
    except AccountError as e:
        fail(str(e))
    con.print(f"{uid} is disabled.")


@user_app.command("enable")
def user_enable(uid: str = typer.Argument(...)) -> None:
    """Re-enable a disabled user."""
    try:
        _accounts().set_disabled(uid, False)
    except AccountError as e:
        fail(str(e))
    con.print(f"{uid} is enabled.")


@token_app.command("new")
def token_new(
    uid: str = typer.Argument(..., help="the user the token is for"),
    label: str = typer.Option("", "--label", help="a note, e.g. 'laptop' or 'ci'"),
) -> None:
    """Mint a token for a user and print it ONCE. Only its hash is stored; it cannot be shown again.
    Hand it to the user over a private channel; they send it as `Authorization: Bearer <token>`."""
    try:
        secret = _accounts().mint_token(uid, label)
    except AccountError as e:
        fail(str(e))
    con.print(f"Token for [bold]{uid}[/] (shown once — copy it now):")
    con.print(secret, soft_wrap=True)


@token_app.command("list")
def token_list(uid: str = typer.Argument(None, help="only this user's tokens")) -> None:
    """Show tokens (never the secrets)."""
    rows = _accounts().list_tokens(uid)
    if not rows:
        con.print("No tokens.")
        return
    t = Table()
    for col in ("id", "user", "label", "status", "last used"):
        t.add_column(col)
    for r in rows:
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_used"])) if r["last_used"] else "never"
        t.add_row(r["id"], r["user_id"], r["label"] or "", "revoked" if r["revoked"] else "live", last)
    con.print(t)


@token_app.command("revoke")
def token_revoke(tid: str = typer.Argument(..., help="the token id from `cadre token list`")) -> None:
    """Revoke a token immediately."""
    try:
        _accounts().revoke_token(tid)
    except AccountError as e:
        fail(str(e))
    con.print(f"Revoked {tid}.")


@app.command()
def audit(limit: int = typer.Option(50, "--limit", help="how many recent entries")) -> None:
    """The audit log: who did what, most recent first (M13)."""
    rows = _accounts().audit_log(max(1, min(limit, 1000)))
    if not rows:
        con.print("Nothing audited yet.")
        return
    t = Table()
    for col in ("when", "actor", "action", "target"):
        t.add_column(col)
    for r in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
        t.add_row(when, r["actor"], r["action"], r["target"] or "")
    con.print(t)
