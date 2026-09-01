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
