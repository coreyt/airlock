"""
Airlock Fast — C1 Admission Gate (per-client RPM token-bucket + concurrency cap).

Off-by-default. Sheds with a ValueError that surfaces as 429 + Retry-After.
Fails open on internal error — never blocks a request because the limiter errored.

Startup wiring:
    configure_admission(config)   # called from proxy.py after configure_settings
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque

logger = logging.getLogger("airlock.fast.admission")


# ---------------------------------------------------------------------------
# AdmissionConfig — re-exported from settings for a single canonical definition
# ---------------------------------------------------------------------------
# Import here so callers can use `from airlock.fast.admission import AdmissionConfig`
# while settings.py remains the single definition site (no duplicate class).
from airlock.fast.settings import AdmissionConfig  # noqa: E402, F401


# ---------------------------------------------------------------------------
# AdmissionStore — per-client token-bucket request counter
# ---------------------------------------------------------------------------
_BUCKET_WIDTH_SECONDS = 1.0  # 1-second buckets


class AdmissionStore:
    """Per-client rolling request counter backed by 1-second time buckets.

    Mirrors the SpendStore pattern from _state_spend.py but stores raw integer
    request counts (not µ$ micro-dollars). No DualCache dependency — plain dict
    of deques so no litellm import is required at module load time.

    Thread-safe via threading.RLock (same discipline as SpendStore).
    """

    def __init__(self, *, lock: threading.RLock | None = None) -> None:
        self._lock = lock or threading.RLock()
        # {client_id: deque of (bucket_index, count) pairs sorted by bucket_index}
        # We store a simple list of (timestamp_float, count=1) tuples because the
        # record rate is per-request (not per-µ$). A deque with a TTL prune is
        # cheaper than a cache for the short admission window (60s).
        self._requests: dict[str, deque[float]] = {}

    def record_request(self, client_id: str, timestamp: float) -> None:
        """Record a single request at *timestamp* for *client_id*."""
        with self._lock:
            if client_id not in self._requests:
                self._requests[client_id] = deque()
            self._requests[client_id].append(timestamp)

    def recent_count(self, client_id: str, window_s: float, now: float) -> int:
        """Return the number of requests within [now - window_s, now] for *client_id*.

        Prunes entries older than the window as a side-effect (amortised cleanup).
        """
        cutoff = now - window_s
        with self._lock:
            dq = self._requests.get(client_id)
            if not dq:
                return 0
            # Prune left (oldest) entries that are outside the window
            while dq and dq[0] <= cutoff:
                dq.popleft()
            return len(dq)

    def reset(self, client_id: str) -> None:
        """Clear all recorded requests for *client_id* (testing / admin)."""
        with self._lock:
            self._requests.pop(client_id, None)


# ---------------------------------------------------------------------------
# AdmissionGate — the hot-path gate
# ---------------------------------------------------------------------------
class AdmissionGate:
    """Synchronous O(1) admission check.

    - RPM check: rolling 60-second window via AdmissionStore token counts.
    - Concurrency check: non-blocking peek at asyncio.Semaphore._value.
    - Fail-open: any internal exception logs a warning and returns (True, 0.0).
    """

    def __init__(self, config: AdmissionConfig, store: AdmissionStore) -> None:
        self._config = config
        self._store = store
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._sem_lock = threading.Lock()

    def _get_semaphore(self, client_id: str) -> asyncio.Semaphore:
        with self._sem_lock:
            if client_id not in self._semaphores:
                self._semaphores[client_id] = asyncio.Semaphore(
                    self._config.concurrency
                )
            return self._semaphores[client_id]

    def check(self, client_id: str, boost: bool, now: float) -> tuple[bool, float]:
        """O(1) admission check. Returns (allowed, retry_after_s).

        Fails open on any internal exception (logs warning, returns (True, 0.0)).
        Never awaits — this method is synchronous.
        """
        try:
            rpm_cap = int(
                self._config.rpm * (self._config.boost_multiplier if boost else 1.0)
            )
            window_s = 60.0
            count = self._store.recent_count(client_id, window_s, now)
            if count >= rpm_cap:
                # Retry-After: seconds until the next window
                retry_after = max(0.1, window_s - (now % window_s))
                return False, retry_after
            self._store.record_request(client_id, now)
            # Concurrency check (non-blocking peek — no await here)
            sem = self._get_semaphore(client_id)
            if sem._value <= 0:
                return False, 1.0  # retry after ~1s for concurrency shed
            return True, 0.0
        except Exception:
            logger.warning("admission gate error — failing open", exc_info=True)
            return True, 0.0


# ---------------------------------------------------------------------------
# Module-level singleton + configure function
# ---------------------------------------------------------------------------
_admission_gate: AdmissionGate | None = None


def configure_admission(config: dict | None) -> None:
    """Called at startup. Sets _admission_gate if admission.enabled=True.

    When admission is disabled (default), _admission_gate remains None and the
    gate block in guardian.py is a single cheap None-check.
    """
    global _admission_gate
    from airlock.fast.settings import load_airlock_settings

    settings = load_airlock_settings(config or {})
    admission_cfg = settings.admission
    if admission_cfg.enabled:
        _admission_gate = AdmissionGate(admission_cfg, AdmissionStore())
    else:
        _admission_gate = None
