"""Per-model rate limiting (ADR-002, ADR-003).

Each (provider, model) pair has a limiter that knows its published limits — requests and tokens
per minute and per day — and whatever the provider last said in its rate-limit headers. The
router asks one question of it: *how long until a call of about N tokens may start?*

Minute windows are sliding (a call counts for 60 s after it starts). Day windows reset at UTC
midnight, which is what most providers document; where one differs, the headers correct it.
The limiter is only touched from the event loop, and the router never awaits between
`wait_time` and `reserve`, so no lock is needed.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .types import RateInfo

WINDOW = 60.0


@dataclass
class Limits:
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None


class Reservation:
    __slots__ = ("est", "entry", "day")

    def __init__(self, est: int, entry: list[float], day: str):
        self.est, self.entry, self.day = est, entry, day


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def _until_midnight(ts: float) -> float:
    now = datetime.fromtimestamp(ts, UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


class ModelQuota:
    def __init__(self, key: str, limits: Limits, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 on_change: Callable[[str, str, int, int], None] | None = None):
        self.key = key
        self.limits = limits
        self._clock, self._wall, self._on_change = clock, wall, on_change
        self._calls: deque[list[float]] = deque()  # [start, tokens]
        self.day = _utc_day(wall())
        self.day_requests = 0
        self.day_tokens = 0
        self.cooldown_until = 0.0
        self._hdr_tokens: tuple[int, float] | None = None
        self._hdr_requests: tuple[int, float] | None = None
        self.inflight = 0

    # ------------------------------------------------------------------ bookkeeping
    def load_day(self, day: str, requests: int, tokens: int) -> None:
        if day == self.day:
            self.day_requests, self.day_tokens = requests, tokens

    def _roll(self, now: float) -> None:
        while self._calls and self._calls[0][0] <= now - WINDOW:
            self._calls.popleft()
        today = _utc_day(self._wall())
        if today != self.day:
            self.day, self.day_requests, self.day_tokens = today, 0, 0

    def _changed(self) -> None:
        if self._on_change:
            self._on_change(self.key, self.day, self.day_requests, self.day_tokens)

    @property
    def minute_requests(self) -> int:
        self._roll(self._clock())
        return len(self._calls)

    @property
    def minute_tokens(self) -> int:
        self._roll(self._clock())
        return int(sum(c[1] for c in self._calls))

    # ------------------------------------------------------------------ the question
    def wait_time(self, est: int) -> tuple[float, str]:
        """Seconds until a call of ~`est` tokens may start, and why. `inf` means never."""
        now = self._clock()
        self._roll(now)
        L = self.limits
        if L.tpm and est > L.tpm:
            return math.inf, f"a ~{est}-token request is larger than this model's {L.tpm} tokens/min"
        if L.tpd and est > L.tpd:
            return math.inf, f"a ~{est}-token request is larger than this model's {L.tpd} tokens/day"
        waits: list[tuple[float, str]] = [(0.0, "")]
        midnight = _until_midnight(self._wall())
        if L.rpd and self.day_requests >= L.rpd:
            waits.append((midnight, f"daily limit of {L.rpd} requests reached"))
        if L.tpd and self.day_tokens + est > L.tpd:
            waits.append((midnight, f"daily limit of {L.tpd} tokens reached"))
        if self.cooldown_until > now:
            waits.append((self.cooldown_until - now, "cooling down after a rate limit"))
        if L.rpm and len(self._calls) >= L.rpm:
            oldest = self._calls[len(self._calls) - L.rpm][0]
            waits.append((oldest + WINDOW - now, f"{L.rpm} requests/min in use"))
        if L.tpm:
            used = sum(c[1] for c in self._calls)
            if used + est > L.tpm:
                freed = 0.0
                for start, tokens in self._calls:
                    freed += tokens
                    if used - freed + est <= L.tpm:
                        waits.append((start + WINDOW - now, f"{L.tpm} tokens/min in use"))
                        break
        if self._hdr_tokens and now < self._hdr_tokens[1] and self._hdr_tokens[0] < est:
            waits.append((self._hdr_tokens[1] - now, "provider reports its token window is spent"))
        if self._hdr_requests and now < self._hdr_requests[1] and self._hdr_requests[0] <= 0:
            waits.append((self._hdr_requests[1] - now, "provider reports its request window is spent"))
        wait, why = max(waits, key=lambda w: w[0])
        return max(0.0, wait), why

    def headroom(self) -> float:
        """0..1 — how much of the tightest per-minute budget is still free."""
        self._roll(self._clock())
        fracs = [1.0]
        if self.limits.rpm:
            fracs.append(1 - len(self._calls) / self.limits.rpm)
        if self.limits.tpm:
            fracs.append(1 - sum(c[1] for c in self._calls) / self.limits.tpm)
        if self.limits.rpd:
            fracs.append(1 - self.day_requests / self.limits.rpd)
        return max(0.0, min(fracs))

    # ------------------------------------------------------------------ accounting
    def reserve(self, est: int) -> Reservation:
        now = self._clock()
        self._roll(now)
        entry = [now, float(est)]
        self._calls.append(entry)
        self.day_requests += 1
        self.day_tokens += est
        self.inflight += 1
        return Reservation(est, entry, self.day)

    def settle(self, res: Reservation, actual_tokens: int) -> None:
        """The call finished and the provider counted it."""
        res.entry[1] = float(actual_tokens)
        if res.day == self.day:
            self.day_tokens += actual_tokens - res.est
        self.inflight -= 1
        if self._hdr_tokens:
            remaining, until = self._hdr_tokens
            self._hdr_tokens = (remaining - actual_tokens, until)
        self._changed()

    def release(self, res: Reservation) -> None:
        """The call was refused before it counted (rate limit, auth, connection)."""
        try:
            self._calls.remove(res.entry)
        except ValueError:
            pass
        if res.day == self.day:
            self.day_requests = max(0, self.day_requests - 1)
            self.day_tokens = max(0, self.day_tokens - res.est)
        self.inflight -= 1

    def cool(self, seconds: float) -> None:
        self.cooldown_until = max(self.cooldown_until, self._clock() + max(0.0, seconds))

    def observe(self, rate: RateInfo | None) -> None:
        if rate is None:
            return
        now = self._clock()
        if rate.remaining_tokens is not None and rate.reset_tokens_s is not None:
            self._hdr_tokens = (rate.remaining_tokens, now + rate.reset_tokens_s)
        if rate.remaining_requests is not None and rate.reset_requests_s is not None:
            self._hdr_requests = (rate.remaining_requests, now + rate.reset_requests_s)
        if rate.limit_tokens and rate.limit_tokens != self.limits.tpm:
            # OpenAI-convention hosts report tokens-per-minute here; trust the host
            self.limits.tpm = rate.limit_tokens

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        self._roll(now)
        return {
            "key": self.key,
            "limits": vars(self.limits).copy(),
            "minute_requests": len(self._calls),
            "minute_tokens": int(sum(c[1] for c in self._calls)),
            "day": self.day,
            "day_requests": self.day_requests,
            "day_tokens": self.day_tokens,
            "cooldown_s": round(max(0.0, self.cooldown_until - now), 1),
            "headroom": round(self.headroom(), 3),
        }


class QuotaBook:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 on_change: Callable[[str, str, int, int], None] | None = None,
                 loader: Callable[[str, str], tuple[int, int]] | None = None):
        self._q: dict[str, ModelQuota] = {}
        self._clock, self._wall, self._on_change, self._loader = clock, wall, on_change, loader

    def get(self, key: str, limits: Limits) -> ModelQuota:
        q = self._q.get(key)
        if q is None:
            q = ModelQuota(key, limits, clock=self._clock, wall=self._wall, on_change=self._on_change)
            if self._loader:
                q.load_day(q.day, *self._loader(key, q.day))
            self._q[key] = q
        return q

    def all(self) -> list[ModelQuota]:
        return list(self._q.values())
