"""Provider day clocks (ADR-018, FR-10).

A provider's "day" is whatever its documentation says it is:

    UTC                  a calendar day in UTC (Cloudflare, OpenRouter)
    <IANA zone>          a calendar day in that zone (Google AI Studio: America/Los_Angeles)
    rolling              a sliding 24 hours, kept as hourly buckets (Groq: no clock is published)

Day keys are what the store's `quota_daily.day` column holds. UTC keeps v0.1's plain
`YYYY-MM-DD`, so old counters stay valid; the other clocks add a suffix so keys never collide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

IST = ZoneInfo("Asia/Kolkata")
HOUR = 3600.0
DAY = 86400.0


def validate(spec: str | None) -> str:
    spec = (spec or "UTC").strip()
    if spec.upper() == "UTC":
        return "UTC"
    if spec.lower() == "rolling":
        return "rolling"
    try:
        ZoneInfo(spec)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown day_reset {spec!r}: use UTC, rolling, or an IANA zone") from None
    return spec


@dataclass(frozen=True)
class DayClock:
    spec: str = "UTC"

    @property
    def rolling(self) -> bool:
        return self.spec == "rolling"

    def _zone(self):
        return UTC if self.spec in ("UTC", "rolling") else ZoneInfo(self.spec)

    def key(self, ts: float) -> str:
        """The counter bucket a request at `ts` belongs to."""
        if self.rolling:
            return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H@rolling")
        day = datetime.fromtimestamp(ts, self._zone()).strftime("%Y-%m-%d")
        return day if self.spec == "UTC" else f"{day}@{self.spec}"

    def bucket_start(self, ts: float) -> float:
        """Start (epoch seconds) of the bucket containing `ts`."""
        if self.rolling:
            return ts - (ts % HOUR)
        local = datetime.fromtimestamp(ts, self._zone())
        return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def next_reset(self, ts: float) -> float:
        """When a calendar clock's day ends. For `rolling`, 24 h after `ts`'s hour ends (worst case)."""
        if self.rolling:
            return self.bucket_start(ts) + HOUR + DAY
        local = datetime.fromtimestamp(ts, self._zone())
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # a DST change can make the local day 23 or 25 hours; fromisoformat keeps the zone right
        return midnight.timestamp()

    def seconds_until_reset(self, ts: float) -> float:
        return max(0.0, self.next_reset(ts) - ts)

    def window_keys(self, ts: float) -> list[str]:
        """Buckets that count towards today's limit at `ts` (one for calendar clocks, 25 for rolling)."""
        if not self.rolling:
            return [self.key(ts)]
        start = self.bucket_start(ts)
        return [self.key(start - i * HOUR) for i in range(24, -1, -1)]

    def in_window(self, bucket_ts: float, now: float) -> bool:
        """Whether a rolling bucket starting at `bucket_ts` may still hold requests from the last 24 h."""
        return bucket_ts + HOUR > now - DAY


def key_start(key: str) -> float | None:
    """Epoch start of a rolling hour key (`YYYY-MM-DDTHH@rolling`)."""
    if not key.endswith("@rolling"):
        return None
    return datetime.strptime(key[:-8], "%Y-%m-%dT%H").replace(tzinfo=UTC).timestamp()


def utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%d %b %H:%M IST")


def human(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"
