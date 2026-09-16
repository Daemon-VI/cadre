import math

import pytest

from cadre.quota import Limits, ModelQuota, QuotaBook
from cadre.types import RateInfo


class Clock:
    def __init__(self, t=1000.0, wall=1_758_000_000.0):
        self.t, self.w = t, wall

    def mono(self):
        return self.t

    def wall(self):
        return self.w

    def advance(self, s):
        self.t += s
        self.w += s


def q(limits, clock=None):
    clock = clock or Clock()
    return ModelQuota("p/m", limits, clock=clock.mono, wall=clock.wall), clock


def test_requests_per_minute_window_slides():
    quota, c = q(Limits(rpm=2))
    quota.reserve(10)
    c.advance(10)
    quota.reserve(10)
    wait, why = quota.wait_time(10)
    assert wait == pytest.approx(50) and "requests/min" in why
    c.advance(50)
    assert quota.wait_time(10)[0] == 0


def test_tokens_per_minute_waits_only_as_long_as_needed():
    quota, c = q(Limits(tpm=8000))
    r1 = quota.reserve(5000)
    quota.settle(r1, 5000)
    c.advance(20)
    quota.reserve(2000)
    wait, why = quota.wait_time(3000)  # 7000 used; 3000 more needs the first call to expire
    assert wait == pytest.approx(40) and "tokens/min" in why
    assert quota.wait_time(1000)[0] == 0


def test_request_larger_than_the_whole_minute_is_never_sent():
    quota, _ = q(Limits(tpm=8000))
    wait, why = quota.wait_time(9000)
    assert math.isinf(wait) and "larger" in why


def test_daily_limit_waits_until_utc_midnight_and_resets():
    c = Clock(wall=1_758_067_200.0 - 3600)  # one hour before a UTC midnight
    quota, _ = q(Limits(rpd=1), c)
    quota.settle(quota.reserve(10), 10)
    wait, why = quota.wait_time(10)
    assert wait == pytest.approx(3600) and "daily" in why
    c.advance(3601)
    assert quota.wait_time(10)[0] == 0 and quota.day_requests == 0


def test_release_undoes_a_refused_call_and_cool_blocks():
    quota, c = q(Limits(rpm=1))
    res = quota.reserve(10)
    quota.release(res)
    assert quota.wait_time(10)[0] == 0 and quota.day_requests == 0
    quota.cool(12)
    assert quota.wait_time(10) == (pytest.approx(12), "cooling down after a rate limit")


def test_provider_headers_override_the_preset():
    quota, c = q(Limits(tpm=100_000))
    quota.observe(RateInfo(remaining_tokens=50, reset_tokens_s=7, limit_tokens=6000))
    wait, why = quota.wait_time(400)
    assert wait == pytest.approx(7) and "provider reports" in why
    assert quota.limits.tpm == 6000


def test_daily_counters_persist_through_the_book():
    saved = {}
    book = QuotaBook(on_change=lambda k, d, r, t: saved.__setitem__((k, d), (r, t)))
    m = book.get("p/m", Limits(rpd=5))
    m.settle(m.reserve(100), 120)
    (key, day), value = next(iter(saved.items()))
    assert value == (1, 120)
    book2 = QuotaBook(loader=lambda k, d: saved.get((k, d), (0, 0)))
    assert book2.get("p/m", Limits(rpd=5)).day_requests == 1
