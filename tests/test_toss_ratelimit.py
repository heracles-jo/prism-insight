"""Rate limiting, especially the window PRISM actually runs in.

Toss drops the ORDER limit from 10/s to 3/s between 09:00 and 09:10 KST, which
is exactly when the morning batch places orders. Getting that window wrong does
not fail loudly — it produces 429s during the one ten-minute stretch of the day
that matters, so it is tested directly rather than inferred.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.brokers.toss import ratelimit

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture(autouse=True)
def _fresh_buckets():
    ratelimit.reset_buckets()
    yield
    ratelimit.reset_buckets()


def at(hour, minute, second=0):
    return datetime.datetime(2026, 8, 17, hour, minute, second, tzinfo=KST)


# ── The peak window ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "moment, expected",
    [
        (at(8, 59, 59), False),
        (at(9, 0, 0), True),    # inclusive start
        (at(9, 5), True),
        (at(9, 9, 59), True),
        (at(9, 10, 0), False),  # exclusive end
        (at(15, 0), False),
        (at(0, 0), False),
    ],
)
def test_the_peak_window_boundaries(moment, expected):
    assert ratelimit.is_order_peak_window(moment) is expected


def test_order_rate_drops_inside_the_window():
    assert ratelimit.rate_for(ratelimit.ORDER, at(9, 5)) == 3.0
    assert ratelimit.rate_for(ratelimit.ORDER, at(10, 0)) == 10.0


def test_only_the_order_group_is_affected_by_the_window():
    """The peak restriction is documented for order creation alone."""
    for group in (ratelimit.AUTH, ratelimit.CHART, ratelimit.CONDITIONAL, ratelimit.DEFAULT):
        assert ratelimit.rate_for(group, at(9, 5)) == ratelimit.rate_for(group, at(14, 0))


def test_published_rates():
    off_peak = at(14, 0)
    assert ratelimit.rate_for(ratelimit.AUTH, off_peak) == 5.0
    assert ratelimit.rate_for(ratelimit.ORDER, off_peak) == 10.0
    assert ratelimit.rate_for(ratelimit.CHART, off_peak) == 20.0
    assert ratelimit.rate_for(ratelimit.CONDITIONAL, off_peak) == 5.0


def test_an_unknown_group_falls_back_to_the_default_rate():
    """Toss may add groups; an unknown one must not be treated as unlimited."""
    assert ratelimit.rate_for("SOMETHING_NEW", at(14, 0)) == ratelimit.rate_for(
        ratelimit.DEFAULT, at(14, 0)
    )


# ── Bucket behaviour ─────────────────────────────────────────────────────────


def test_a_fresh_bucket_allows_a_burst_up_to_capacity():
    bucket = ratelimit.TokenBucket(ratelimit.ORDER)
    off_peak = at(14, 0)

    for _ in range(10):
        assert bucket.take_if_available(now=off_peak) is True
    assert bucket.take_if_available(now=off_peak) is False


def test_capacity_shrinks_when_the_peak_window_starts():
    """A full off-peak bucket must not drain at 10/s into a 3/s limit."""
    bucket = ratelimit.TokenBucket(ratelimit.ORDER)

    granted = 0
    while bucket.take_if_available(now=at(9, 5)):
        granted += 1

    assert granted <= 3, f"allowed {granted} requests against a 3/s peak limit"


def test_acquire_blocks_rather_than_raising_and_reports_the_wait():
    bucket = ratelimit.TokenBucket(ratelimit.AUTH)
    off_peak = at(14, 0)

    for _ in range(5):
        bucket.acquire(now=off_peak, timeout=5.0)

    waited = bucket.acquire(now=off_peak, timeout=5.0)
    assert waited > 0, "the sixth call in a 5/s bucket should have waited"


def test_acquire_times_out_instead_of_blocking_forever():
    bucket = ratelimit.TokenBucket(ratelimit.ORDER)
    peak = at(9, 5)

    with pytest.raises(TimeoutError):
        for _ in range(50):
            bucket.acquire(now=peak, timeout=0.05)


def test_tokens_refill_over_time():
    import time

    bucket = ratelimit.TokenBucket(ratelimit.CHART)
    off_peak = at(14, 0)

    while bucket.take_if_available(now=off_peak):
        pass
    assert bucket.take_if_available(now=off_peak) is False

    time.sleep(0.1)  # 20/s refills 2 tokens in 100ms
    assert bucket.take_if_available(now=off_peak) is True


# ── Sharing ──────────────────────────────────────────────────────────────────


def test_buckets_are_shared_process_wide():
    """Two traders are still one client to Toss."""
    assert ratelimit.bucket_for(ratelimit.ORDER) is ratelimit.bucket_for(ratelimit.ORDER)
    assert ratelimit.bucket_for(ratelimit.ORDER) is not ratelimit.bucket_for(ratelimit.AUTH)


def test_two_clients_share_one_order_allowance():
    """The failure this prevents: each limiter thinking it has the full rate."""
    off_peak = at(14, 0)
    first = ratelimit.bucket_for(ratelimit.ORDER)
    second = ratelimit.bucket_for(ratelimit.ORDER)

    granted = 0
    while first.take_if_available(now=off_peak):
        granted += 1
    assert second.take_if_available(now=off_peak) is False
    assert granted == 10
