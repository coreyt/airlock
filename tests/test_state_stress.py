"""Stress and lock-scope tests for airlock.fast.state.StateStore.

These tests are marked ``stress`` and are excluded from the default pytest run.
Invoke explicitly with ``pytest -m stress``.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import pytest

from airlock.fast.state import ClientState, StateStore
from airlock.fast.threat_detector import (
    BASE_BACKOFF_S,
    DECAY_FACTOR,
    MAX_BACKOFF_S,
    THREAT_BLOCK_THRESHOLD,
)


class _CountingLock:
    """Wraps a real lock and counts every ``__enter__`` / ``acquire`` call.

    Under an ``RLock``, a single outer ``with`` plus two nested reentrant
    acquisitions from helper methods will report a count of 3. Under a
    plain ``Lock`` where the outer method does NOT wrap the body, the
    count will be 2 (one per inner helper).
    """

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


@pytest.mark.stress
def test_record_provider_rate_limit_single_outer_lock_scope():
    store = StateStore()
    store._lock = _CountingLock(store._lock)

    store.record_provider_rate_limit(
        client_id="client-a",
        provider="openai",
        timestamp=time.time(),
        reason="quota_exceeded",
        error_type="RateLimitError",
    )

    # Outer `with self._lock:` + nested re-entry from get_client_provider
    # and get_provider = 3 acquires under RLock. A missing outer wrap would
    # count only 2.
    assert store._lock.acquire_count == 3


@pytest.mark.stress
def test_record_provider_request_single_outer_lock_scope():
    store = StateStore()
    store._lock = _CountingLock(store._lock)

    store.record_provider_request("client-a", "openai", time.time())

    # Same re-entry pattern: outer wrap + get_provider + get_client_provider.
    assert store._lock.acquire_count == 3


@pytest.mark.stress
def test_ingest_jsonl_record_single_outer_lock_scope():
    """ingest_jsonl_record mutates model + client + provider state in one
    composite operation. All mutations must happen under a single outer
    ``with self._lock:`` block so the composite view is atomic."""
    store = StateStore()
    store._lock = _CountingLock(store._lock)

    record = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "model": "claude-sonnet",
        "airlock_client": "client-a",
        "airlock_provider": "anthropic",
        "success": True,
        "duration_ms": 42.0,
        "call_type": "completion",
    }

    store.ingest_jsonl_record(record)

    # With a single outer ``with self._lock:`` wrap, the count is:
    #   1 (outer) + 1 (get_model) + 1 (get_client)
    #   + 3 (record_provider_request: outer + get_provider + get_client_provider)
    #   + 3 (record_provider_success: outer + get_provider + get_client_provider)
    #   + 1 (record_call_type)
    #   = 10
    # Without the outer wrap (bug), the count drops to 9.
    assert store._lock.acquire_count == 10, (
        f"expected 10 acquires (outer wrap + nested helpers), got "
        f"{store._lock.acquire_count} — composite ingest_jsonl_record body "
        f"is not wrapped in a single outer lock"
    )


@pytest.mark.stress
def test_record_threat_score_concurrent_no_lost_update():
    """Concurrent same-client threat-score updates must not lose the high write.

    Decay is neutralised (request_times preset so elapsed clamps to 0.01), so the
    final accumulated score converges deterministically to ~0.9 under the atomic
    RMW. A lost update of the high write would drop it toward the 0.1 cluster.
    """
    store = StateStore()
    client = ClientState(client_id="race")
    now = 1_000_000.0
    client.request_times = deque([now, now], maxlen=1000)

    iters = 50
    low_threads = 4

    def worker(score: float) -> None:
        for _ in range(iters):
            store.record_threat_score(
                client,
                score,
                now,
                decay_base=DECAY_FACTOR,
                threshold=THREAT_BLOCK_THRESHOLD,
                base_backoff_s=BASE_BACKOFF_S,
                max_backoff_s=MAX_BACKOFF_S,
            )

    threads = [threading.Thread(target=worker, args=(0.9,))]
    threads += [threading.Thread(target=worker, args=(0.1,)) for _ in range(low_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert 0.75 <= client.threat_score <= 0.9


@pytest.mark.stress
def test_record_provider_rate_limit_concurrent_consistency():
    store = StateStore()
    provider = "openai"
    # Keep total calls under MAX_SAMPLES (1000) so the provider-level
    # rate_limit_events deque does not drop old entries mid-test.
    num_threads = 20
    calls_per_thread = 40

    def worker(tid: int) -> None:
        for i in range(calls_per_thread):
            store.record_provider_rate_limit(
                client_id=f"client-{tid}-{i % 5}",
                provider=provider,
                timestamp=time.time(),
                reason="quota_exceeded",
                error_type="RateLimitError",
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    provider_state = store.get_provider(provider)
    # Each thread uses client ids in {client-<tid>-0..4} => 5 distinct per thread,
    # total = num_threads * 5 distinct clients.
    expected_distinct = num_threads * 5
    impacted = provider_state.impacted_clients()
    assert len(impacted) == expected_distinct

    # Sum of per-client rate-limit counts must equal the provider's total.
    total_calls = num_threads * calls_per_thread
    per_client_total = 0
    for tid in range(num_threads):
        for i in range(5):
            cp = store.get_client_provider(f"client-{tid}-{i}", provider)
            per_client_total += len(cp.rate_limit_times)
    assert per_client_total == total_calls
    assert len(provider_state.rate_limit_events) == total_calls
