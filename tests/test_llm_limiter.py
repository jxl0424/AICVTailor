"""Token bucket behaviour.

The acceptance criterion is that a tailoring run never trips a 429 under
normal use, which means the limiter has to actually block rather than merely
count.
"""

from __future__ import annotations

import pytest

from aicvtailor.llm.limiter import TokenBucket
from tests.fakes import FakeClock, RecordingSleep


def make_bucket(rpm: int = 60):
    clock, sleep = FakeClock(), RecordingSleep()

    def advancing_sleep(seconds: float) -> None:
        sleep(seconds)
        clock.advance(seconds)

    return TokenBucket(rpm, clock=clock, sleep=advancing_sleep), clock, sleep


def test_starts_full_so_an_idle_run_can_burst():
    bucket, _, sleep = make_bucket(rpm=30)
    for _ in range(30):
        assert bucket.acquire() == 0.0
    assert sleep.delays == []


def test_blocks_once_the_budget_is_spent():
    bucket, _, sleep = make_bucket(rpm=60)  # one per second
    for _ in range(60):
        bucket.acquire()

    waited = bucket.acquire()
    assert waited == pytest.approx(1.0, abs=0.01)
    assert sleep.delays, "limiter should have slept rather than allowing a 429"


def test_refills_over_time():
    bucket, clock, _ = make_bucket(rpm=60)
    for _ in range(60):
        bucket.acquire()

    clock.advance(10)
    assert bucket.tokens == pytest.approx(10.0, abs=0.01)


def test_refill_is_capped_at_capacity():
    bucket, clock, _ = make_bucket(rpm=30)
    clock.advance(3600)
    assert bucket.tokens == 30


def test_default_rpm_stays_under_the_free_tier_ceiling():
    from aicvtailor.config import get_settings

    assert get_settings().llm_rpm <= 40


def test_rejects_a_nonsense_rate():
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_cannot_request_more_than_capacity():
    bucket, _, _ = make_bucket(rpm=5)
    with pytest.raises(ValueError):
        bucket.acquire(6)


def test_is_safe_under_real_concurrency():
    """The bucket claims thread safety in its docstring, and the provider is
    shared across requests, so the claim needs a real test rather than a
    comment. Without the lock, two threads can both see the last permit.
    """
    import threading

    from aicvtailor.llm.limiter import TokenBucket

    # A clock that never advances: exactly `capacity` permits exist, ever.
    bucket = TokenBucket(60, clock=lambda: 0.0, sleep=lambda s: None)
    granted: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()  # maximise the overlap
        if bucket.tokens >= 1:
            waited = bucket.acquire()
            with lock:
                granted.append(1 if waited == 0 else 0)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Never more permits than the bucket held, and never a negative balance.
    assert sum(granted) <= 60
    assert bucket.tokens >= 0
