"""Per-model rate limiting (ADR-002, ADR-003, ADR-017, ADR-018).

Each (provider, model) pair has a limiter that knows its published limits — requests and tokens
per minute and per day — and whatever the provider last said in its rate-limit headers. The
router asks one question of it: *how long until a call of about N tokens may start, and why?*

Minute windows slide (a call counts for 60 s after it starts). Day windows follow the provider's
own clock (`clocks.DayClock`): a calendar day in UTC or a named zone, or a rolling 24 hours kept
as hourly buckets. The limiter is only touched from the event loop, and the router never awaits
between `block` and `reserve`, so no lock is needed.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .clocks import DayClock, key_start, utc_day
from .types import RateInfo

WINDOW = 60.0

#: why a call cannot start yet — the router parks a run only on `daily` (ADR-017)
MINUTE, COOLDOWN, DAILY, NEVER, NONE = "minute", "cooldown", "daily", "never", ""


@dataclass
class Limits:
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None


@dataclass
class Block:
    wait: float
    why: str
    kind: str


class Reservation:
    __slots__ = ("est", "entry", "day")

    def __init__(self, est: int, entry: list[float], day: str):
        self.est, self.entry, self.day = est, entry, day


Saver = Callable[[str, str, int, int], None]
Loader = Callable[[str, str], tuple[int, int]]


class ModelQuota:
    def __init__(self, key: str, limits: Limits, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 on_change: Saver | None = None,
                 day_clock: DayClock | None = None):
        self.key = key
        self.limits = limits
        self.day_clock = day_clock or DayClock()
        self._clock, self._wall, self._on_change = clock, wall, on_change
        self._calls: deque[list[float]] = deque()  # [start, tokens]
        self._buckets: dict[str, list[int]] = {}   # day key -> [requests, tokens]
        self.cooldown_until = 0.0
        self._hdr_tokens: tuple[int, float] | None = None
        self._hdr_requests: tuple[int, float] | None = None
        self.inflight = 0

    # ------------------------------------------------------------------ day buckets
    @property
    def day(self) -> str:
        return self.day_clock.key(self._wall())

    def load_day(self, day: str, requests: int, tokens: int) -> None:
        if requests or tokens:
            self._buckets[day] = [requests, tokens]
        self._prune()

    def _live_keys(self) -> list[str]:
        now = self._wall()
        if not self.day_clock.rolling:
            return [self.day_clock.key(now)]
        return [k for k in self._buckets if self.day_clock.in_window(key_start(k) or 0.0, now)]

    def _prune(self) -> None:
        live = set(self._live_keys())
        for k in [k for k in self._buckets if k not in live]:
            del self._buckets[k]

    @property
    def day_requests(self) -> int:
        self._prune()
        return sum(b[0] for b in self._buckets.values())

    @property
    def day_tokens(self) -> int:
        self._prune()
        return sum(b[1] for b in self._buckets.values())

    def _daily_wait(self, need_requests: int, need_tokens: int) -> float:
        """Seconds until the daily window has room for this call."""
        now = self._wall()
        if not self.day_clock.rolling:
            return self.day_clock.seconds_until_reset(now)
        # rolling: old hourly buckets fall out one by one; find the first moment enough has left
        req, tok = self.day_requests, self.day_tokens
        for k in sorted(self._buckets, key=lambda k: key_start(k) or 0.0):
            r, t = self._buckets[k]
            req, tok = req - r, tok - t
            ok_r = not self.limits.rpd or req + need_requests <= self.limits.rpd
            ok_t = not self.limits.tpd or tok + need_tokens <= self.limits.tpd
            if ok_r and ok_t:
                return max(0.0, (key_start(k) or now) + 3600 + 86400 - now)
        return self.day_clock.seconds_until_reset(now)

    def _changed(self, day: str) -> None:
        if self._on_change:
            r, t = self._buckets.get(day, [0, 0])
            self._on_change(self.key, day, r, t)

    # ------------------------------------------------------------------ minute window
    def _roll(self, now: float) -> None:
        while self._calls and self._calls[0][0] <= now - WINDOW:
            self._calls.popleft()

    @property
    def minute_requests(self) -> int:
        self._roll(self._clock())
        return len(self._calls)

    @property
    def minute_tokens(self) -> int:
        self._roll(self._clock())
        return int(sum(c[1] for c in self._calls))

    # ------------------------------------------------------------------ the question
    def block(self, est: int) -> Block:
        """How long until a call of ~`est` tokens may start, why, and what kind of block it is."""
        now = self._clock()
        self._roll(now)
        L = self.limits
        if L.tpm and est > L.tpm:
            return Block(math.inf, f"a ~{est}-token request is larger than this model's {L.tpm} tokens/min", NEVER)
        if L.tpd and est > L.tpd:
            return Block(math.inf, f"a ~{est}-token request is larger than this model's {L.tpd} tokens/day", NEVER)
        blocks: list[Block] = [Block(0.0, "", NONE)]
        if (L.rpd and self.day_requests >= L.rpd) or (L.tpd and self.day_tokens + est > L.tpd):
            what = (f"daily limit of {L.rpd} requests reached" if L.rpd and self.day_requests >= L.rpd
                    else f"daily limit of {L.tpd} tokens reached")
            blocks.append(Block(self._daily_wait(1, est), what, DAILY))
        if self.cooldown_until > now:
            blocks.append(Block(self.cooldown_until - now, "cooling down after a rate limit", COOLDOWN))
        if L.rpm and len(self._calls) >= L.rpm:
            oldest = self._calls[len(self._calls) - L.rpm][0]
            blocks.append(Block(oldest + WINDOW - now, f"{L.rpm} requests/min in use", MINUTE))
        if L.tpm:
            used = sum(c[1] for c in self._calls)
            if used + est > L.tpm:
                freed = 0.0
                for start, tokens in self._calls:
                    freed += tokens
                    if used - freed + est <= L.tpm:
                        blocks.append(Block(start + WINDOW - now, f"{L.tpm} tokens/min in use", MINUTE))
                        break
        if self._hdr_tokens and now < self._hdr_tokens[1] and self._hdr_tokens[0] < est:
            blocks.append(Block(self._hdr_tokens[1] - now, "provider reports its token window is spent", MINUTE))
        if self._hdr_requests and now < self._hdr_requests[1] and self._hdr_requests[0] <= 0:
            wait = self._hdr_requests[1] - now
            # Groq's request headers describe the *daily* window; a long reset is a daily block
            blocks.append(Block(wait, "provider reports its request window is spent",
                                DAILY if wait > 5 * WINDOW else MINUTE))
        worst = max(blocks, key=lambda b: b.wait)
        worst.wait = max(0.0, worst.wait)
        return worst

    def wait_time(self, est: int) -> tuple[float, str]:
        b = self.block(est)
        return b.wait, b.why

    def headroom(self) -> float:
        """0..1 — how much of the tightest budget is still free."""
        self._roll(self._clock())
        fracs = [1.0]
        if self.limits.rpm:
            fracs.append(1 - len(self._calls) / self.limits.rpm)
        if self.limits.tpm:
            fracs.append(1 - sum(c[1] for c in self._calls) / self.limits.tpm)
        if self.limits.rpd:
            fracs.append(1 - self.day_requests / self.limits.rpd)
        if self.limits.tpd:
            fracs.append(1 - self.day_tokens / self.limits.tpd)
        return max(0.0, min(fracs))

    # ------------------------------------------------------------------ accounting
    def reserve(self, est: int) -> Reservation:
        now = self._clock()
        self._roll(now)
        entry = [now, float(est)]
        self._calls.append(entry)
        day = self.day
        bucket = self._buckets.setdefault(day, [0, 0])
        bucket[0] += 1
        bucket[1] += est
        self.inflight += 1
        return Reservation(est, entry, day)

    def settle(self, res: Reservation, actual_tokens: int) -> None:
        """The call finished and the provider counted it."""
        res.entry[1] = float(actual_tokens)
        if res.day in self._buckets:
            self._buckets[res.day][1] = max(0, self._buckets[res.day][1] + actual_tokens - res.est)
        self.inflight -= 1
        if self._hdr_tokens:
            remaining, until = self._hdr_tokens
            self._hdr_tokens = (remaining - actual_tokens, until)
        self._changed(res.day)

    def release(self, res: Reservation) -> None:
        """The call was refused before it counted (rate limit, auth, connection)."""
        try:
            self._calls.remove(res.entry)
        except ValueError:
            pass
        if res.day in self._buckets:
            b = self._buckets[res.day]
            b[0] = max(0, b[0] - 1)
            b[1] = max(0, b[1] - res.est)
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
        wall = self._wall()
        return {
            "key": self.key,
            "limits": vars(self.limits).copy(),
            "minute_requests": len(self._calls),
            "minute_tokens": int(sum(c[1] for c in self._calls)),
            "day": self.day,
            "day_reset": self.day_clock.spec,
            "resets_in_s": round(self.day_clock.seconds_until_reset(wall)),
            "day_requests": self.day_requests,
            "day_tokens": self.day_tokens,
            "cooldown_s": round(max(0.0, self.cooldown_until - now), 1),
            "headroom": round(self.headroom(), 3),
        }


class QuotaBook:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 on_change: Saver | None = None, loader: Loader | None = None):
        self._q: dict[str, ModelQuota] = {}
        self._clock, self._wall, self._on_change, self._loader = clock, wall, on_change, loader

    def get(self, key: str, limits: Limits, day_clock: DayClock | None = None) -> ModelQuota:
        q = self._q.get(key)
        if q is None:
            q = ModelQuota(key, limits, clock=self._clock, wall=self._wall,
                           on_change=self._on_change, day_clock=day_clock)
            if self._loader:
                self._load(q)
            self._q[key] = q
        return q

    def _load(self, q: ModelQuota) -> None:
        now = self._wall()
        found = False
        for day in q.day_clock.window_keys(now):
            r, t = self._loader(q.key, day)
            if r or t:
                q.load_day(day, r, t)
                found = True
        if not found and q.day_clock.spec != "UTC":
            # the clock changed since these counters were written (v0.1 counted in UTC):
            # carry today's UTC counts into the current bucket rather than forget them
            r, t = self._loader(q.key, utc_day(now))
            if r or t:
                q.load_day(q.day, r, t)
                if self._on_change:
                    self._on_change(q.key, q.day, r, t)

    def all(self) -> list[ModelQuota]:
        return list(self._q.values())
