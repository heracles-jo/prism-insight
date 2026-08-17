"""Client-side rate limiting, so Toss rarely has to say no.

Toss meters per client × API group, and the group that matters is ORDER: 10
requests per second normally, but **3 per second between 09:00 and 09:10 KST**.
That window is exactly when PRISM's morning batch places its orders, so the
tightest limit of the day lands on the busiest minute of the day. Waiting for a
429 and reacting is too late there — by then the batch is already behind.

A token bucket rather than a fixed window because the documented limit is a
burst capacity that refills continuously, which is what `X-RateLimit-Limit`
(burst capacity) and `X-RateLimit-Reset` (seconds until one token refills)
describe.

Buckets are module-level and shared. Two traders in one process are still one
client to Toss, so per-instance limiters would each think they had the full
allowance and together exceed it.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

AUTH = "AUTH"
ORDER = "ORDER"
CHART = "CHART"
CONDITIONAL = "CONDITIONAL"
DEFAULT = "DEFAULT"

# Requests per second per group, from the published limits.
_RATES = {
    AUTH: 5.0,
    ORDER: 10.0,
    CHART: 20.0,
    CONDITIONAL: 5.0,
    DEFAULT: 10.0,
}

# The order limit during the opening rush.
_ORDER_PEAK_RATE = 3.0
_PEAK_START = datetime.time(9, 0)
_PEAK_END = datetime.time(9, 10)


def _now_kst() -> datetime.datetime:
    return datetime.datetime.now(KST)


def is_order_peak_window(now: datetime.datetime | None = None) -> bool:
    """True inside the 09:00–09:10 KST window where ORDER drops to 3/s."""
    current = (now or _now_kst()).timetz()
    return _PEAK_START <= current.replace(tzinfo=None) < _PEAK_END


def rate_for(group: str, now: datetime.datetime | None = None) -> float:
    """Allowed requests per second for `group` at this moment."""
    if group == ORDER and is_order_peak_window(now):
        return _ORDER_PEAK_RATE
    return _RATES.get(group, _RATES[DEFAULT])


class TokenBucket:
    """A refilling allowance for one API group.

    `acquire` blocks rather than raising. The caller is a trading batch with
    nothing useful to do while throttled, and a sleep here is much cheaper than
    a 429 plus a retry — the request never leaves the process.
    """

    def __init__(self, group: str, *, capacity: float | None = None):
        self.group = group
        self._capacity = capacity if capacity is not None else _RATES.get(group, _RATES[DEFAULT])
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, rate: float) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed <= 0:
            return
        # Capacity tracks the current rate so the bucket shrinks when the peak
        # window starts, instead of letting a full off-peak bucket drain at ten
        # per second into a three-per-second limit.
        capacity = max(rate, 1.0)
        self._tokens = min(capacity, self._tokens + elapsed * rate)
        self._updated = now

    def acquire(self, *, now: datetime.datetime | None = None, timeout: float = 30.0) -> float:
        """Block until a token is free. Returns seconds waited."""
        deadline = time.monotonic() + timeout
        waited = 0.0

        while True:
            with self._lock:
                rate = rate_for(self.group, now)
                self._refill(rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                shortfall = 1.0 - self._tokens
                delay = shortfall / rate if rate > 0 else timeout

            if time.monotonic() + delay > deadline:
                raise TimeoutError(
                    f"rate limit wait for group {self.group} exceeded {timeout}s"
                )
            if waited == 0.0:
                logger.debug(
                    "[TOSS_RATELIMIT] throttling group=%s delay=%.3fs", self.group, delay
                )
            time.sleep(delay)
            waited += delay

    def take_if_available(self, *, now: datetime.datetime | None = None) -> bool:
        """Non-blocking variant, for callers that would rather skip than wait."""
        with self._lock:
            self._refill(rate_for(self.group, now))
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


_buckets: dict[str, TokenBucket] = {}
_buckets_lock = threading.Lock()


def bucket_for(group: str) -> TokenBucket:
    """The process-wide bucket for `group`, created on first use."""
    with _buckets_lock:
        existing = _buckets.get(group)
        if existing is None:
            existing = TokenBucket(group)
            _buckets[group] = existing
        return existing


def reset_buckets() -> None:
    """Drop all buckets. For tests — production has no reason to call this."""
    with _buckets_lock:
        _buckets.clear()
