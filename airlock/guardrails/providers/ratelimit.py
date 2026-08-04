"""Token bucket for provider call rates.

Two call sites want opposite behavior when the budget is exhausted, so both are
offered explicitly:

``try_acquire`` — **fail fast**, for the request hot path. The semantic guard
runs ``during_call``, so waiting for a token would add latency to a live
request. Worse, when a provider quota is already exhausted, queuing turns a
rate-limit problem into a latency problem and piles more load onto an API that
is refusing us. Skipping the call and reporting *unavailable* is the honest
outcome, and it costs nothing.

``acquire`` — **pace and wait**, for offline batch work such as corpus
benchmarking, where wall-clock time is not a user-visible cost and completing
every sample matters more than finishing quickly.

The bucket is per-provider-instance and process-local. It is a guard against
*our own* bursts, not a distributed quota manager: several Airlock processes
sharing one provider project can still collectively exceed the quota, which is
what the provider's own 429 handling remains for.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Classic token bucket over a per-minute budget.

    ``burst`` caps how many calls may fire back-to-back before pacing applies.
    It defaults to one second's worth of budget so a short spike is absorbed
    without letting a full minute's allowance leave at once.
    """

    def __init__(self, per_minute: float, burst: float | None = None) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self._rate_per_second = per_minute / 60.0
        self._capacity = burst if burst is not None else max(1.0, per_minute / 60.0)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> float:
        return self._capacity

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._rate_per_second
            )
            self._updated = now

    async def try_acquire(self) -> bool:
        """Take a token if one is available. Never waits."""
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    async def acquire(self) -> float:
        """Wait until a token is available. Returns seconds spent waiting.

        Intended for batch callers only — see the module docstring.
        """
        waited = 0.0
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                deficit = 1.0 - self._tokens
                delay = deficit / self._rate_per_second
            await asyncio.sleep(delay)
            waited += delay
