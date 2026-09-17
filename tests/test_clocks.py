from datetime import UTC, datetime

import pytest

from cadre.clocks import DayClock, validate
from cadre.quota import Limits, ModelQuota, QuotaBook


def ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


class Wall:
    def __init__(self, t):
        self.t = t
        self.mono = 1000.0

    def __call__(self):
        return self.t

    def clock(self):
        return self.mono

    def advance(self, s):
        self.t += s
        self.mono += s


def test_pacific_day_key_and_reset():
    pacific = DayClock("America/Los_Angeles")
    # 06:00 UTC on 17 Sep is 23:00 PDT on 16 Sep: Google's day ends in an hour
    now = ts("2026-09-17T06:00:00")
    assert pacific.key(now) == "2026-09-16@America/Los_Angeles"
    assert pacific.seconds_until_reset(now) == pytest.approx(3600)
    assert pacific.key(now + 7200) == "2026-09-17@America/Los_Angeles"


def test_utc_is_unchanged_for_v01_rows():
    now = ts("2026-09-17T06:00:00")
    assert DayClock("UTC").key(now) == "2026-09-17"
    assert DayClock().seconds_until_reset(now) == pytest.approx(18 * 3600)


def test_unknown_clock_is_refused():
    assert validate("utc") == "UTC" and validate("Rolling") == "rolling"
    with pytest.raises(ValueError, match="unknown day_reset"):
        validate("Mars/Olympus")


def test_daily_limit_on_a_pacific_clock_waits_for_pacific_midnight():
    wall = Wall(ts("2026-09-17T06:00:00"))
    q = ModelQuota("gemini/x", Limits(rpd=1), clock=wall.clock, wall=wall,
                   day_clock=DayClock("America/Los_Angeles"))
    q.settle(q.reserve(10), 10)
    b = q.block(10)
    assert b.kind == "daily" and b.wait == pytest.approx(3600)
    wall.advance(3601)
    assert q.block(10).wait == 0


def test_rolling_window_frees_hour_by_hour():
    wall = Wall(ts("2026-09-17T10:30:00"))
    q = ModelQuota("groq/x", Limits(rpd=2), clock=wall.clock, wall=wall, day_clock=DayClock("rolling"))
    q.settle(q.reserve(5), 5)
    wall.advance(3600)  # 11:30, a second hourly bucket
    q.settle(q.reserve(5), 5)
    b = q.block(5)
    # the 10:00 bucket leaves the window at 10:00 + 1 h + 24 h = 11:00 tomorrow
    assert b.kind == "daily"
    assert wall.t + b.wait == pytest.approx(ts("2026-09-18T11:00:00"))
    wall.advance(b.wait + 1)
    assert q.block(5).wait == 0 and q.day_requests == 1


def test_utc_counts_carry_over_when_the_clock_changes():
    now = ts("2026-09-17T12:00:00")
    stored = {("gemini/m", "2026-09-17"): (7, 700)}
    saved = {}
    book = QuotaBook(wall=lambda: now, loader=lambda k, d: stored.get((k, d), (0, 0)),
                     on_change=lambda k, d, r, t: saved.__setitem__((k, d), (r, t)))
    q = book.get("gemini/m", Limits(rpd=20), DayClock("America/Los_Angeles"))
    assert (q.day_requests, q.day_tokens) == (7, 700)
    assert saved == {("gemini/m", "2026-09-17@America/Los_Angeles"): (7, 700)}
    # once the new key exists it is used directly
    stored.update(saved)
    stored[("gemini/m", "2026-09-17")] = (99, 9900)
    again = QuotaBook(wall=lambda: now, loader=lambda k, d: stored.get((k, d), (0, 0)))
    assert again.get("gemini/m", Limits(), DayClock("America/Los_Angeles")).day_requests == 7


def test_rolling_counters_reload_from_hourly_rows():
    now = ts("2026-09-17T12:10:00")
    rows = {("groq/m", "2026-09-17T11@rolling"): (3, 30), ("groq/m", "2026-09-16T09@rolling"): (5, 50)}
    book = QuotaBook(wall=lambda: now, loader=lambda k, d: rows.get((k, d), (0, 0)))
    q = book.get("groq/m", Limits(), DayClock("rolling"))
    assert q.day_requests == 3  # 09:00 yesterday is more than 25 hours ago
