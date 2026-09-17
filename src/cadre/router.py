"""Model routing: which model serves this call, and what to do when it can't (ADR-002, ADR-004).

Candidates are grouped in preference order and the first group that can serve the call within
`max_wait` wins; inside a group the soonest-available model wins, then configured priority,
then the most headroom:

    1. requested tier, different family from `avoid_families`   (independent)
    2. other allowed tier, different family                       (independent, downgraded)
    3. requested tier, any family                                 (not independent — recorded)
    4. other allowed tier, any family

Independence outranks tier on purpose: a smaller model from another family catches more of a
builder's mistakes than a second copy of the builder.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .clocks import DayClock
from .providers import (
    AuthFailed,
    BadRequest,
    LLMProvider,
    MalformedOutput,
    ProviderError,
    RateLimited,
    RequestTooLarge,
    ToolsUnsupported,
    Unavailable,
    estimate_tokens,
)
from .quota import Limits, QuotaBook
from .types import ChatResponse, Message, ToolSpec

Emit = Callable[[str, dict[str, Any]], None]


@dataclass
class ModelEntry:
    provider: str
    name: str
    tier: str = "fast"
    family: str = ""
    protocol: str = "native"
    priority: int = 100  # lower is preferred
    limits: Limits = field(default_factory=Limits)
    enabled: bool = True
    trains: str = "unknown"   # does the provider's free tier train on prompts? (ADR-020)
    day_reset: str = "UTC"    # the provider's daily clock (ADR-018)

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.name}"


@dataclass
class CallRequest:
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    tier: str = "strong"
    allow_downgrade: bool = True
    avoid_families: tuple[str, ...] = ()
    pin: str | None = None
    max_tokens: int = 1500
    temperature: float = 0.3
    label: str = ""
    private: bool = False  # only providers that do not train on prompts (ADR-020)


@dataclass
class CallResult:
    response: ChatResponse
    entry: ModelEntry
    independent: bool
    waited: float
    fallbacks: list[str]


class NoModelAvailable(ProviderError):
    pass


def _human(seconds: float) -> str:
    if math.isinf(seconds):
        return "never"
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class Router:
    def __init__(self, providers: dict[str, LLMProvider], models: list[ModelEntry],
                 quotas: QuotaBook | None = None, *, max_wait: float = 90.0,
                 max_failures: int = 6, max_concurrency: int = 3,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        self.providers = providers
        self.models = models
        self.quotas = quotas or QuotaBook()
        self.max_wait = max_wait
        self.max_failures = max_failures
        self._sleep = sleep
        self._sem = asyncio.Semaphore(max_concurrency)
        self.disabled: dict[str, str] = {}  # provider id -> reason, for this session

    def quota(self, m: ModelEntry):
        return self.quotas.get(m.key, m.limits, DayClock(m.day_reset))

    def usable(self, private: bool = False) -> list[ModelEntry]:
        return [m for m in self.models
                if m.enabled and m.provider in self.providers and m.provider not in self.disabled
                and (not private or m.trains == "no")]

    def excluded_for_privacy(self) -> list[ModelEntry]:
        return [m for m in self.usable() if m.trains != "no"]

    def groups(self, req: CallRequest, exclude: set[str]) -> list[tuple[list[ModelEntry], bool]]:
        pool = [m for m in self.usable(req.private) if m.key not in exclude]
        if req.pin:
            pool = [m for m in pool if req.pin in (m.key, m.name)]
            return [(pool, not req.avoid_families or all(
                m.family not in req.avoid_families for m in pool))]
        other = "fast" if req.tier == "strong" else "strong"
        tiers = [req.tier] + ([other] if req.tier == "fast" or req.allow_downgrade else [])
        out: list[tuple[list[ModelEntry], bool]] = []
        avoid = set(req.avoid_families)
        if avoid:
            for t in tiers:
                out.append(([m for m in pool if m.tier == t and m.family not in avoid], True))
        for t in tiers:
            out.append(([m for m in pool if m.tier == t], not avoid))
        return [(g, ind) for g, ind in out if g]

    async def chat(self, req: CallRequest, emit: Emit | None = None) -> CallResult:
        emit = emit or (lambda kind, data: None)
        async with self._sem:
            return await self._chat(req, emit)

    async def _chat(self, req: CallRequest, emit: Emit) -> CallResult:
        exclude: set[str] = set()
        malformed: set[str] = set()
        fallbacks: list[str] = []
        failures = 0
        waited = 0.0
        est = estimate_tokens(req.messages, req.tools) + req.max_tokens
        while True:
            groups = self.groups(req, exclude)
            if not groups:
                raise NoModelAvailable(self._nothing_left(req, fallbacks))
            choice = None
            blocked: list[str] = []
            for group, independent in groups:
                ranked = []
                for m in group:
                    wait, why = self.quota(m).wait_time(est)
                    if wait > self.max_wait:
                        blocked.append(f"{m.key}: {why} (frees in {_human(wait)})")
                        continue
                    ranked.append((wait, m.priority, -self.quota(m).headroom(), m.key, m, why))
                if ranked:
                    ranked.sort(key=lambda r: r[:4])
                    choice = (ranked[0][0], ranked[0][4], independent, ranked[0][5])
                    break
            if choice is None:
                raise NoModelAvailable("no model can take this call now — " + "; ".join(
                    dict.fromkeys(blocked)))
            wait, m, independent, why = choice
            if wait > 0:
                emit("route.wait", {"model": m.key, "seconds": round(wait, 1), "reason": why})
                await self._sleep(wait + 0.05)
                waited += wait
                if waited > self.max_wait * 3:
                    raise NoModelAvailable(f"waited {int(waited)}s for capacity and gave up")
                continue

            q = self.quota(m)
            res = q.reserve(est)
            provider = self.providers[m.provider]
            try:
                resp = await provider.chat(m.name, req.messages, req.tools, protocol=m.protocol,
                                           max_tokens=req.max_tokens, temperature=req.temperature)
            except RateLimited as e:
                q.release(res)
                q.cool(e.retry_after if e.retry_after is not None else 20.0)
                note = f"{m.key}: rate limited"
            except AuthFailed as e:
                q.release(res)
                self.disabled[m.provider] = str(e)
                note = f"{m.provider}: key rejected — provider disabled for this session"
            except RequestTooLarge:
                q.release(res)
                exclude.add(m.key)
                note = f"{m.key}: request too large for this model"
            except ToolsUnsupported:
                q.release(res)
                m.protocol = "json"
                note = f"{m.key}: no function calling — switched to the JSON tool protocol"
            except MalformedOutput:
                q.settle(res, est)
                if m.key in malformed:
                    exclude.add(m.key)
                malformed.add(m.key)
                note = f"{m.key}: produced a malformed tool call"
            except Unavailable as e:
                q.release(res)
                q.cool(min(60.0, 5.0 * (failures + 1)))
                note = f"{m.key}: unavailable ({e})"
            except BadRequest as e:
                q.release(res)
                exclude.add(m.key)
                note = f"{m.key}: rejected the request ({e})"
            except asyncio.CancelledError:
                q.release(res)
                raise
            else:
                q.settle(res, resp.usage.total)
                q.observe(resp.rate)
                return CallResult(resp, m, independent, waited, fallbacks)
            failures += 1
            fallbacks.append(note)
            emit("route.fallback", {"model": m.key, "note": note, "failure": failures})
            if failures >= self.max_failures:
                raise NoModelAvailable(f"{failures} failed attempts: " + "; ".join(fallbacks))

    def _nothing_left(self, req: CallRequest, fallbacks: list[str]) -> str:
        if not self.models:
            return "no models are configured — add a provider with `cadre provider add`"
        if req.pin and not any(req.pin in (m.key, m.name) for m in self.models):
            return f"pinned model {req.pin!r} is not configured"
        if req.private and not self.usable(private=True):
            names = ", ".join(f"{m.key} (trains: {m.trains})" for m in self.excluded_for_privacy())
            return f"private run: no model whose provider does not train on prompts — excluded {names}"
        parts = [f"{p}: {why}" for p, why in self.disabled.items()]
        parts += fallbacks
        return "no usable model left" + (" — " + "; ".join(parts) if parts else "")

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()
