"""CADRE_HOME, the provider/model configuration, and building a router from it.

`config.yaml` holds endpoints, models, tiers and limits — never a key (ADR-011). Everything a
run produces lives under the same home:

    ~/.cadre/
      config.yaml      providers and models
      cadre.sqlite     runs, events, usage, quota counters, approvals
      token            bearer token for the local API (ADR-010)
      orgs/            the owner's org files (templates ship inside the package)
      runs/<id>/       workspace/ and versions/ for each run
"""

from __future__ import annotations

import os
import re
import secrets as pysecrets
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field

from .clocks import validate
from .presets import PRESETS, ModelPreset, guess_family, guess_tier, is_chat_model
from .providers import LLMProvider, OpenAICompatProvider
from .quota import Limits, QuotaBook
from .router import ModelEntry, Router
from .secrets import SecretStore


class ModelConfig(BaseModel):
    name: str
    tier: str = "fast"
    family: str = ""
    protocol: str = "native"
    priority: int = 100
    enabled: bool = True
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    #: where the limits came from: docs / reported / guess / owner (set by the owner)
    source: str = ""

    def limits(self, reserve_pct: int = 0) -> Limits:
        """Limits as the router should see them; daily caps shrink by `reserve_pct` (FR-11)."""
        keep = 1 - max(0, min(90, reserve_pct)) / 100

        def cap(v: int | None) -> int | None:
            return None if v is None else max(1, int(v * keep))

        return Limits(rpm=self.rpm, rpd=cap(self.rpd), tpm=self.tpm, tpd=cap(self.tpd))


class ProviderConfig(BaseModel):
    id: str
    preset: str = "custom"
    label: str = ""
    base_url: str
    key_ref: str | None = None
    env: str | None = None
    local: bool = False
    enabled: bool = True
    params: dict[str, str] = Field(default_factory=dict)
    models: list[ModelConfig] = Field(default_factory=list)
    # None means "whatever the preset says" — a v0.1 config.yaml has none of these
    day_reset: str | None = None
    trains_on_free_data: str | None = None
    reserve_pct: int = 10
    #: extra request fields; None means the preset's (e.g. Gemini's reasoning_effort)
    request_params: dict[str, Any] | None = None

    def params_for_requests(self) -> dict[str, Any]:
        if self.request_params is not None:
            return self.request_params
        p = self._preset()
        return dict(p.request_params) if p else {}

    def _preset(self):
        return PRESETS.get(self.preset)

    def clock(self) -> str:
        p = self._preset()
        return validate(self.day_reset or (p.day_reset if p else "UTC"))

    def trains(self) -> str:
        if self.trains_on_free_data:
            return self.trains_on_free_data
        p = self._preset()
        if self.local:
            return "no"
        return p.trains_on_free_data if p else "unknown"

    def url(self) -> str:
        try:
            return self.base_url.format(**self.params)
        except KeyError as e:
            raise ValueError(f"provider {self.id} needs parameter {e.args[0]!r}") from None


class Settings(BaseModel):
    max_wait: float = 90.0
    max_total_wait: float = 900.0
    max_concurrency: int = 3
    port: int = 8765


class CadreConfig(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)

    def provider(self, pid: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == pid), None)


def model_from_preset(mp: ModelPreset, priority: int, preset_source: str = "") -> ModelConfig:
    return ModelConfig(name=mp.name, tier=mp.tier, family=mp.family or guess_family(mp.name),
                       protocol=mp.protocol, priority=priority,
                       rpm=mp.rpm, rpd=mp.rpd, tpm=mp.tpm, tpd=mp.tpd,
                       source=mp.source or preset_source)


def discovered_model(preset_id: str, name: str, priority: int) -> ModelConfig:
    p = PRESETS.get(preset_id)
    d = p.default_limits if p and p.default_limits else ModelPreset(name)
    return ModelConfig(name=name, tier=guess_tier(name), family=guess_family(name),
                       priority=priority, rpm=d.rpm, rpd=d.rpd, tpm=d.tpm, tpd=d.tpd,
                       source=(p.source if p and p.default_limits else "guess"))


def provider_from_preset(preset_id: str, *, pid: str | None = None, base_url: str | None = None,
                         params: dict[str, str] | None = None,
                         models: list[str] | None = None) -> ProviderConfig:
    p = PRESETS.get(preset_id)
    if p is None:
        raise ValueError(f"unknown preset {preset_id!r}; see `cadre provider presets`")
    pid = pid or p.id
    url = base_url or p.base_url
    if not url:
        raise ValueError("a custom provider needs --base-url")
    known = {m.name: m for m in p.models}
    chosen: list[ModelConfig] = []
    for i, name in enumerate(models if models else [m.name for m in p.models]):
        prio = (i + 1) * 10
        chosen.append(model_from_preset(known[name], prio, p.source) if name in known
                      else discovered_model(preset_id, name, prio))
    return ProviderConfig(id=pid, preset=p.id, label=p.label, base_url=url,
                          key_ref=None if p.local else pid, env=p.env, local=p.local,
                          params=params or {}, models=chosen)


class Home:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("CADRE_HOME") or Path.home() / ".cadre")

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def db_path(self) -> Path:
        return self.root / "cadre.sqlite"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def orgs_dir(self) -> Path:
        return self.root / "orgs"

    @property
    def token_path(self) -> Path:
        return self.root / "token"

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    def ensure(self) -> Home:
        for d in (self.root, self.runs_dir, self.orgs_dir, self.memory_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.token_path.exists():
            self.token_path.write_text(pysecrets.token_urlsafe(32), encoding="utf-8")
            try:
                os.chmod(self.token_path, 0o600)
            except OSError:
                pass
        if not self.config_path.exists():
            self.save_config(CadreConfig())
        return self

    def token(self) -> str:
        self.ensure()
        return self.token_path.read_text(encoding="utf-8").strip()

    def load_config(self) -> CadreConfig:
        if not self.config_path.exists():
            return CadreConfig()
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return CadreConfig.model_validate(data)

    def save_config(self, cfg: CadreConfig) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(cfg.model_dump(exclude_none=True), sort_keys=False)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text("# Cadre providers and models. Keys are NOT stored here.\n" + text,
                       encoding="utf-8")
        tmp.replace(self.config_path)


def make_provider(pc: ProviderConfig, store: SecretStore,
                  transport: httpx.AsyncBaseTransport | None = None) -> OpenAICompatProvider:
    key = store.get(pc.key_ref, pc.env) if pc.key_ref else None
    return OpenAICompatProvider(pc.id, pc.url(), key, label=pc.label or pc.id, local=pc.local,
                                transport=transport, extra_body=pc.params_for_requests(),
                                unsigned_tool_call_extra=(PRESETS[pc.preset].unsigned_tool_call_extra
                                                          if pc.preset in PRESETS else None))


def build_router(cfg: CadreConfig, store: SecretStore | None = None,
                 quotas: QuotaBook | None = None, *,
                 extra: dict[str, LLMProvider] | None = None,
                 extra_models: list[ModelEntry] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> tuple[Router, list[str]]:
    """A router over every enabled provider that has a key. Returns (router, warnings)."""
    store = store or SecretStore()
    providers: dict[str, LLMProvider] = {}
    models: list[ModelEntry] = []
    warnings: list[str] = []
    for pc in cfg.providers:
        if not pc.enabled:
            continue
        try:
            prov = make_provider(pc, store, transport)
        except ValueError as e:
            warnings.append(str(e))
            continue
        if not pc.local and not prov.has_key:
            warnings.append(f"{pc.id}: no key found — skipped (add one with `cadre provider key {pc.id}`)")
            continue
        providers[pc.id] = prov
        for mc in pc.models:
            models.append(ModelEntry(provider=pc.id, name=mc.name, tier=mc.tier,
                                     family=mc.family or guess_family(mc.name),
                                     protocol=mc.protocol, priority=mc.priority,
                                     limits=mc.limits(pc.reserve_pct), enabled=mc.enabled,
                                     trains=pc.trains(), day_reset=pc.clock()))
    providers.update(extra or {})
    models.extend(extra_models or [])
    router = Router(providers, models, quotas, max_wait=cfg.settings.max_wait,
                    max_total_wait=cfg.settings.max_total_wait,
                    max_concurrency=cfg.settings.max_concurrency)
    return router, warnings


class ModelDiff(BaseModel):
    provider: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    kept: list[str] = Field(default_factory=list)
    error: str = ""


def diff_models(pc: ProviderConfig, remote: list[str]) -> ModelDiff:
    """What the provider serves today against what `config.yaml` lists (FR-10, AC-10.3)."""
    p = PRESETS.get(pc.preset)
    names = [n for n in remote if is_chat_model(n)]
    if p and p.discover_filter:
        names = [n for n in names if re.search(p.discover_filter, n)]
    elif p and p.models:
        # a listed catalogue (Gemini, Groq): only the free chat models the preset knows about
        known = {m.name for m in p.models}
        names = [n for n in names if n in known]
    configured = [m.name for m in pc.models]
    return ModelDiff(provider=pc.id,
                     added=sorted(set(names) - set(configured)),
                     removed=sorted(set(configured) - set(remote)) if remote else [],
                     kept=sorted(set(configured) & set(remote)))


def apply_diff(pc: ProviderConfig, diff: ModelDiff) -> None:
    """Add new models and disable vanished ones. Never touches an existing model's limits."""
    p = PRESETS.get(pc.preset)
    known = {m.name: m for m in (p.models if p else ())}
    base = max((m.priority for m in pc.models), default=0)
    for i, name in enumerate(diff.added):
        prio = base + (i + 1) * 10
        pc.models.append(model_from_preset(known[name], prio, p.source) if name in known
                         else discovered_model(pc.preset, name, prio))
    for m in pc.models:
        if m.name in diff.removed:
            m.enabled = False


async def refresh_provider(pc: ProviderConfig, store: SecretStore | None = None,
                           transport: httpx.AsyncBaseTransport | None = None) -> ModelDiff:
    """Ask the endpoint what it serves and compare (never changes anything by itself)."""
    prov = make_provider(pc, store or SecretStore(), transport)
    try:
        remote = await prov.list_models()
    except Exception as e:  # a provider that cannot list still gets a report line
        return ModelDiff(provider=pc.id, error=str(e) or type(e).__name__)
    finally:
        await prov.aclose()
    return diff_models(pc, remote)
