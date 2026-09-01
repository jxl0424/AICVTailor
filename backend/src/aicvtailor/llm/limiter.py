"""Client-side rate limiting.

The NIM free tier sits around 40 requests per minute. Tripping it costs a
retry cycle and slows a tailoring run more than simply pacing the calls does,
so the limiter runs below the ceiling by default and blocks rather than
letting a 429 happen.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    """A refilling bucket of request permits, safe across threads.

    Capacity equals the per-minute budget, so a run that has been idle can
    burst its whole allowance at once and then settles to the steady rate.
    """

    def __init__(
        self,
        rpm: int,
        *,
        capacity: int | None = None,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.rpm = rpm
        self.capacity = capacity if capacity is not None else rpm
        self._tokens = float(self.capacity)
        self._rate = rpm / 60.0
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self._rate)
            self._updated = now

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def acquire(self, amount: int = 1) -> float:
        """Take permits, waiting if necessary. Returns seconds spent waiting."""
        if amount > self.capacity:
            raise ValueError(f"cannot acquire {amount} from a bucket of {self.capacity}")

        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= amount:
                    self._tokens -= amount
                    return waited
                shortfall = amount - self._tokens
                delay = shortfall / self._rate

            self._sleep(delay)
            waited += delay
