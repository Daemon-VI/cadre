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
import secrets as pysecrets
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, Field

from .presets import PRESETS, ModelPreset, guess_family, guess_tier
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

    def limits(self) -> Limits:
        return Limits(rpm=self.rpm, rpd=self.rpd, tpm=self.tpm, tpd=self.tpd)


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

    def url(self) -> str:
        try:
            return self.base_url.format(**self.params)
        except KeyError as e:
            raise ValueError(f"provider {self.id} needs parameter {e.args[0]!r}") from None


class Settings(BaseModel):
    max_wait: float = 90.0
    max_concurrency: int = 3
    port: int = 8765


class CadreConfig(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)

    def provider(self, pid: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == pid), None)


def model_from_preset(mp: ModelPreset, priority: int) -> ModelConfig:
    return ModelConfig(name=mp.name, tier=mp.tier, family=mp.family or guess_family(mp.name),
                       protocol=mp.protocol, priority=priority,
                       rpm=mp.rpm, rpd=mp.rpd, tpm=mp.tpm, tpd=mp.tpd)


def discovered_model(preset_id: str, name: str, priority: int) -> ModelConfig:
    p = PRESETS.get(preset_id)
    d = p.default_limits if p and p.default_limits else ModelPreset(name)
    return ModelConfig(name=name, tier=guess_tier(name), family=guess_family(name),
                       priority=priority, rpm=d.rpm, rpd=d.rpd, tpm=d.tpm, tpd=d.tpd)


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
        chosen.append(model_from_preset(known[name], prio) if name in known
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

    def ensure(self) -> Home:
        for d in (self.root, self.runs_dir, self.orgs_dir):
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
                                transport=transport)


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
                                     limits=mc.limits(), enabled=mc.enabled))
    providers.update(extra or {})
    models.extend(extra_models or [])
    router = Router(providers, models, quotas, max_wait=cfg.settings.max_wait,
                    max_concurrency=cfg.settings.max_concurrency)
    return router, warnings
