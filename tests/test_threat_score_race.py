"""No-lost-update tests for the atomic threat_score read-modify-write.

The threat detector's accumulated ``threat_score`` RMW used to run outside any
lock, so concurrent same-client requests could lose an update. These tests pin
the atomic ``StateStore.record_threat_score`` mutator: the RMW must run under a
single outer lock acquisition, and concurrent updates must not be lost.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from airlock.fast.state import ClientState, StateStore
from airlock.fast.threat_detector import (
    BASE_BACKOFF_S,
    DECAY_FACTOR,
    MAX_BACKOFF_S,
    THREAT_BLOCK_THRESHOLD,
)


class _CountingLock:
    """Wraps a real lock and counts every ``__enter__`` / ``acquire`` call."""

    def __init__(self, inner):
        self._inner = inner
        self.acquire_count = 0
        self.release_count = 0

    def __enter__(self):
        self.acquire_count += 1
        self._inner.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release_count += 1
        self._inner.release()
        return False

    def acquire(self, *args, **kwargs):
        self.acquire_count += 1
        return self._inner.acquire(*args, **kwargs)

    def release(self):
        self.release_count += 1
        return self._inner.release()


def _call(store: StateStore, client: ClientState, score: float, now: float):
    return store.record_threat_score(
        client,
        score,
        now,
        decay_base=DECAY_FACTOR,
        threshold=THREAT_BLOCK_THRESHOLD,
        base_backoff_s=BASE_BACKOFF_S,
        max_backoff_s=MAX_BACKOFF_S,
    )


def test_record_threat_score_single_outer_lock_scope():
    """The whole RMW body must run under exactly one outer lock acquisition."""
    store = StateStore()
    store._lock = _CountingLock(store._lock)
    client = ClientState(client_id="race")

    combined, blocked, backoff = _call(store, client, 0.5, now=1_000_000.0)

    assert store._lock.acquire_count == 1
    assert client.threat_score == combined
    assert blocked is False
    assert backoff == 0.0


def test_record_threat_score_matches_legacy_formula():
    """Single-threaded result is identical to the pre-refactor inline RMW."""
    store = StateStore()
    client = ClientState(client_id="single")
    client.threat_score = 0.8  # empty request_times => elapsed=1.0, decay=0.977

    combined, blocked, backoff = _call(store, client, 0.0, now=1_000_000.0)

    expected = 0.8 * (DECAY_FACTOR**1.0)  # 0.7816
    assert abs(combined - expected) < 1e-9
    assert client.threat_score == combined
    assert blocked is True
    assert backoff == 256.0


def test_no_lost_update_concurrent():
    """A high score hammered concurrently with low scores must not be lost.

    Decay is neutralised (request_times preset so elapsed clamps to 0.01,
    decay ~= 0.99977 per call), so under the lock the final score converges
    deterministically to ~0.9. Without the atomic RMW a lost high write would
    drop the final score toward the 0.1 cluster.
    """
    store = StateStore()
    client = ClientState(client_id="race")
    now = 1_000_000.0
    # Two equal timestamps => elapsed = 0 -> clamped to 0.01 -> decay ~= 0.99977.
    client.request_times = deque([now, now], maxlen=1000)

    iters = 30
    low_threads = 3
    high = 0.9
    low = 0.1

    def high_worker():
        for _ in range(iters):
            _call(store, client, high, now)

    def low_worker():
        for _ in range(iters):
            _call(store, client, low, now)

    threads = [threading.Thread(target=high_worker)]
    threads += [threading.Thread(target=low_worker) for _ in range(low_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Upper bound is exactly high (decay<1 keeps prev*decay below it); lower bound
    # is high*decay**(total-1) ~= 0.876 with these counts. No lost update => ~0.9.
    assert 0.8 <= client.threat_score <= 0.9


def test_record_threat_score_rmw_is_serialized():
    """Two concurrent same-client calls must never overlap inside the RMW.

    This is the deterministic counterpart to the stress test above: a duck-typed
    client whose ``threat_score`` getter widens the read→write window with a sleep
    and tracks how many threads are inside the RMW at once. Because
    ``record_threat_score`` holds ``StateStore._lock`` across the whole
    read-modify-write, the observed concurrency depth can never exceed 1 — an
    unlocked implementation would deterministically observe 2 (the injected sleep
    guarantees overlap), so this test would FAIL against the old outside-the-lock
    RMW rather than merely depend on scheduling.
    """
    store = StateStore()
    depth_lock = threading.Lock()
    state = {"depth": 0, "max_depth": 0}

    class _Probe:
        """Minimal duck-typed ClientState exercising the RMW's attribute access."""

        request_times: list = []  # empty => elapsed=1.0 branch
        backoff_until = 0.0

        def __init__(self):
            self._score = 0.0

        @property
        def threat_score(self):
            # Entering the read side of the read-modify-write.
            with depth_lock:
                state["depth"] += 1
                state["max_depth"] = max(state["max_depth"], state["depth"])
            time.sleep(0.02)  # widen the window so an unlocked RMW would overlap
            return self._score

        @threat_score.setter
        def threat_score(self, value):
            self._score = value
            # Exiting the write side of the read-modify-write.
            with depth_lock:
                state["depth"] -= 1

    probe = _Probe()
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()  # release both threads simultaneously
        # score below threshold => not blocked => no backoff_until write needed
        _call(store, probe, 0.1, now=1_000_000.0)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max_depth"] == 1, (
        f"threat_score RMW overlapped (depth={state['max_depth']}) — not atomic"
    )
