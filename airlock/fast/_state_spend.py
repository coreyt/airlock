"""
Airlock Fast — Provider spend tracking sub-module.

Contains SpendStore and ProviderSpend.
Intentionally standalone (no imports from other airlock.fast sub-modules)
so _state_core.py can import from here without a circular dependency.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Provider spend tracking
# ---------------------------------------------------------------------------
DEFAULT_SPEND_WINDOW_SECONDS = 86400.0  # rolling spend window (24h)
DEFAULT_SPEND_BUCKET_SECONDS = 3600.0  # bucket width (1h) — width <= 1h
_SPEND_CACHE_MAX_ITEMS = 100_000  # FIX-3: adequate max size, not the 200 default


class SpendStore:
    """Airlock-owned, DualCache-backed rolling spend accumulator (the seam).

    Spend is kept as time-bucketed integer **micro-dollars** (µ$) so accuracy is
    independent of call volume (fixes R5 — no ``deque(maxlen=1000)`` cap) and maps
    cleanly onto an integer ``increment_cache`` / Redis ``INCR`` for a future
    multi-process backend. Swapping the in-memory ``DualCache`` for Redis later is a
    config flip behind this one class, not a rewrite.

    FIX-3: every key carries an **explicit TTL strictly greater than the window** so
    spend never expires early off the ~600s DualCache/InMemoryCache default, and the
    caller's lock is held around the compound read-modify-write.

    Trailing-edge semantic: because whole buckets are summed/pruned, ``recent_spend``
    (and ``export_buckets``) are accurate to within **one bucket width** at the
    trailing edge and round **UP** — the bucket that straddles ``now - window`` is
    counted in full even though part of it predates the window. This is the SAFE
    direction for budget protection: budget warns/swaps fire slightly EARLY, never
    late, so real spend can never silently exceed a budget by more than one bucket.
    A smaller ``bucket_width_seconds`` tightens the worst-case overage.
    """

    def __init__(
        self,
        *,
        window_seconds: float = DEFAULT_SPEND_WINDOW_SECONDS,
        bucket_width_seconds: float = DEFAULT_SPEND_BUCKET_SECONDS,
        lock: threading.RLock | None = None,
        cache=None,
    ) -> None:
        self._window = float(window_seconds)
        self._bucket_width = float(bucket_width_seconds)
        # Explicit TTL > window (FIX-3): never rely on the ~600s default.
        self._ttl = self._window + self._bucket_width
        self._lock = lock or threading.RLock()
        # The DualCache is built lazily so importing state.py does not force a
        # litellm import in processes (e.g. the TUI) that never record spend.
        self._cache = cache
        # Index of live bucket indices per provider. DualCache cannot enumerate
        # keys; a Redis backend would use a sorted set here — same seam.
        self._buckets: dict[str, set[int]] = {}

    @property
    def cache(self):
        if self._cache is None:
            from litellm.caching.dual_cache import DualCache
            from litellm.caching.in_memory_cache import InMemoryCache

            self._cache = DualCache(
                InMemoryCache(
                    max_size_in_memory=_SPEND_CACHE_MAX_ITEMS,
                    default_ttl=int(self._ttl),
                )
            )
        return self._cache

    def _bucket_index(self, timestamp: float) -> int:
        return int(timestamp // self._bucket_width)

    def _key(self, provider: str, bucket: int) -> str:
        return f"airlock_spend:{provider}:{bucket}"

    def record_spend(self, provider: str, timestamp: float, cost_usd: float) -> None:
        micro = int(round(cost_usd * 1_000_000))
        if micro == 0:
            return
        bucket = self._bucket_index(timestamp)
        with self._lock:
            self.cache.increment_cache(
                self._key(provider, bucket), micro, ttl=self._ttl
            )
            self._buckets.setdefault(provider, set()).add(bucket)

    def recent_spend(
        self,
        provider: str,
        window_seconds: float | None = None,
        now: float | None = None,
    ) -> float:
        """Sum in-window buckets and return **float USD** (µ$ / 1e6).

        Conservative trailing edge: the bucket containing ``now - window`` is
        included in full, so the result is accurate to within one bucket width and
        rounds UP. Counting that boundary bucket means budget warns/swaps fire
        slightly early rather than late — the safe direction (real spend can never
        exceed budget by more than one bucket without warning).
        """
        window = self._window if window_seconds is None else float(window_seconds)
        now = time.time() if now is None else now
        # floor() => buckets with index >= min_bucket are kept, which INCLUDES the
        # bucket straddling (now - window). Conservative by design (see docstring).
        min_bucket = int((now - window) // self._bucket_width)
        total_micro = 0
        with self._lock:
            buckets = self._buckets.get(provider)
            if not buckets:
                return 0.0
            live: set[int] = set()
            for bucket in buckets:
                if bucket < min_bucket:
                    continue  # out of window; drop index (TTL expires the key)
                val = self.cache.get_cache(self._key(provider, bucket))
                if val:
                    total_micro += int(val)
                live.add(bucket)
            self._buckets[provider] = live
        return total_micro / 1_000_000

    def export_buckets(self, now: float | None = None) -> dict[str, dict[str, int]]:
        """Prune out-of-window buckets, then snapshot ``{provider: {bucket: µ$}}``."""
        now = time.time() if now is None else now
        min_bucket = int((now - self._window) // self._bucket_width)
        out: dict[str, dict[str, int]] = {}
        with self._lock:
            for provider, buckets in list(self._buckets.items()):
                kept: dict[str, int] = {}
                live: set[int] = set()
                for bucket in buckets:
                    if bucket < min_bucket:
                        continue
                    val = self.cache.get_cache(self._key(provider, bucket))
                    if val:
                        kept[str(bucket)] = int(val)
                        live.add(bucket)
                self._buckets[provider] = live
                if kept:
                    out[provider] = kept
        return out

    def import_buckets(self, providers: dict, now: float | None = None) -> list[str]:
        """Rehydrate in-window buckets idempotently (absolute set, not add).

        Age-bounded: buckets older than the window are skipped. Replace-not-append
        ``set_cache`` means restoring twice cannot double-count. Returns the provider
        names touched so callers can register them for the advisor.
        """
        now = time.time() if now is None else now
        min_bucket = int((now - self._window) // self._bucket_width)
        touched: list[str] = []
        with self._lock:
            for provider, buckets in (providers or {}).items():
                seen = False
                for bucket_str, micro in (buckets or {}).items():
                    try:
                        bucket = int(bucket_str)
                        micro_int = int(micro)
                    except (TypeError, ValueError):
                        continue
                    if bucket < min_bucket:
                        continue  # age-bounded: do not rehydrate stale buckets
                    self.cache.set_cache(
                        self._key(provider, bucket), micro_int, ttl=self._ttl
                    )
                    self._buckets.setdefault(provider, set()).add(bucket)
                    seen = True
                if seen:
                    touched.append(provider)
        return touched


@dataclass
class ProviderSpend:
    """Handle to a provider's rolling spend, backed by the shared ``SpendStore`` seam.

    The public API (``record_spend`` / ``recent_spend``) is preserved; internals
    delegate to the time-bucketed integer-µ$ accumulator. ``recent_spend`` still
    returns **float USD** so the advisor, router, and monitor call sites are unchanged.
    """

    provider: str
    _store: SpendStore | None = None

    def _spend_store(self) -> SpendStore:
        if self._store is None:
            self._store = SpendStore()
        return self._store

    def record_spend(self, timestamp: float, cost_usd: float) -> None:
        self._spend_store().record_spend(self.provider, timestamp, cost_usd)

    def recent_spend(
        self, window_seconds: float = DEFAULT_SPEND_WINDOW_SECONDS
    ) -> float:
        return self._spend_store().recent_spend(self.provider, window_seconds)
