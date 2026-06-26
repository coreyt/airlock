"""Tests for the bucketed integer-µ$ spend seam (0.5.1 STORE-seam).

Covers R5 (no >1000-call/day undercount), integer-µ$ accumulation, explicit
per-key TTL ≥ window, rolling-window exclusion, versioned/atomic/prune/idempotent/
age-bounded checkpoint+restore, a no-network cross-process round-trip, and that the
breaker cb_state.json checkpoint still round-trips on the (now child-located) path
with its 5-min gate intact for the breaker but NOT for spend.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from airlock.fast.state import (
    SPEND_STATE_VERSION,
    CircuitState,
    SpendStore,
    StateStore,
    checkpoint_spend,
    checkpoint_state,
    restore_spend,
    restore_state,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# R5 — no undercount above 1000 billed calls/day
# ---------------------------------------------------------------------------
class TestR5Accuracy:
    def test_over_1000_calls_no_undercount(self):
        store = StateStore()
        spend = store.get_provider_spend("openai")
        now = time.time()
        n = 2500  # well past the old deque(maxlen=1000) cap
        per_call = 0.01
        for _ in range(n):
            spend.record_spend(now, per_call)
        # True sum is 25.00; the old deque would have kept only 1000 records (10.00).
        assert spend.recent_spend() == pytest.approx(n * per_call)
        assert spend.recent_spend() > 1000 * per_call  # would-have-undercounted

    def test_accuracy_independent_of_call_volume(self):
        store = StateStore()
        spend = store.get_provider_spend("anthropic")
        now = time.time()
        for _ in range(5000):
            spend.record_spend(now, 0.002)
        assert spend.recent_spend() == pytest.approx(5000 * 0.002)


# ---------------------------------------------------------------------------
# Integer µ$ accumulation
# ---------------------------------------------------------------------------
class TestIntegerMicroDollars:
    def test_subcent_costs_accumulate_exactly(self):
        store = StateStore()
        spend = store.get_provider_spend("openai")
        now = time.time()
        for _ in range(1000):
            spend.record_spend(now, 0.000123)  # 123 µ$ each
        assert spend.recent_spend() == pytest.approx(1000 * 0.000123, abs=1e-9)

    def test_recent_spend_returns_float_usd(self):
        store = StateStore()
        spend = store.get_provider_spend("openai")
        spend.record_spend(time.time(), 0.05)
        val = spend.recent_spend()
        assert isinstance(val, float)
        assert val == 0.05


# ---------------------------------------------------------------------------
# FIX-3 — explicit per-key TTL ≥ window (no reliance on the 600s default)
# ---------------------------------------------------------------------------
class TestTTL:
    def test_spend_key_ttl_at_least_window(self):
        window = 86400.0
        ss = SpendStore(window_seconds=window, bucket_width_seconds=3600.0)
        now = time.time()
        ss.record_spend("openai", now, 1.0)
        ttl_dict = ss._cache.in_memory_cache.ttl_dict
        assert ttl_dict, "expected at least one spend key with an explicit TTL"
        for expiry in ttl_dict.values():
            # Expiry is an absolute epoch; remaining life must clear the window
            # (and must NOT be the ~600s InMemoryCache default).
            assert expiry - now >= window


# ---------------------------------------------------------------------------
# Rolling window exclusion
# ---------------------------------------------------------------------------
class TestRollingWindow:
    def test_out_of_window_excluded(self):
        store = StateStore()
        spend = store.get_provider_spend("openai")
        now = time.time()
        spend.record_spend(now, 1.0)  # in window
        spend.record_spend(now - 90000.0, 5.0)  # > 24h + bucket ago → excluded
        assert spend.recent_spend() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Checkpoint semantics (FIX-7)
# ---------------------------------------------------------------------------
class TestCheckpointSemantics:
    def test_versioned_schema(self, tmp_path):
        store = StateStore()
        store.get_provider_spend("openai").record_spend(time.time(), 1.5)
        path = tmp_path / "spend_state.json"
        checkpoint_spend(store, str(path))
        data = json.loads(path.read_text())
        assert data["version"] == SPEND_STATE_VERSION
        assert "providers" in data
        assert "openai" in data["providers"]

    def test_atomic_write_leaves_no_temp_file(self, tmp_path):
        store = StateStore()
        store.get_provider_spend("openai").record_spend(time.time(), 1.0)
        path = tmp_path / "spend_state.json"
        checkpoint_spend(store, str(path))
        assert path.exists()
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "spend_state.json"]
        assert leftovers == [], f"temp files left behind: {leftovers}"

    def test_prune_before_checkpoint_drops_out_of_window(self, tmp_path):
        store = StateStore()
        spend = store.get_provider_spend("openai")
        now = time.time()
        spend.record_spend(now, 1.0)
        spend.record_spend(now - 90000.0, 9.0)  # out of window
        path = tmp_path / "spend_state.json"
        checkpoint_spend(store, str(path))
        data = json.loads(path.read_text())
        # The persisted total for openai must be the in-window value only.
        total_micro = sum(int(v) for v in data["providers"]["openai"].values())
        assert total_micro == 1_000_000

    def test_restore_idempotent_no_double_count(self, tmp_path):
        store = StateStore()
        store.get_provider_spend("openai").record_spend(time.time(), 3.0)
        path = tmp_path / "spend_state.json"
        checkpoint_spend(store, str(path))

        target = StateStore()
        restore_spend(target, str(path))
        restore_spend(target, str(path))  # twice — must not double-count
        assert target.get_provider_spend("openai").recent_spend() == pytest.approx(3.0)

    def test_restore_age_bounded(self, tmp_path):
        # A hand-written checkpoint with one in-window and one ancient bucket.
        now = time.time()
        bucket_width = 3600.0
        in_bucket = int(now // bucket_width)
        old_bucket = int((now - 200000.0) // bucket_width)  # > 24h ago
        path = tmp_path / "spend_state.json"
        path.write_text(
            json.dumps(
                {
                    "version": SPEND_STATE_VERSION,
                    "timestamp": now,
                    "bucket_width_seconds": bucket_width,
                    "window_seconds": 86400.0,
                    "providers": {
                        "openai": {str(in_bucket): 2_000_000, str(old_bucket): 9_000_000}
                    },
                }
            )
        )
        target = StateStore()
        restore_spend(target, str(path))
        # Only the in-window bucket (2.0) rehydrates; the ancient one does not.
        assert target.get_provider_spend("openai").recent_spend() == pytest.approx(2.0)

    def test_restore_rejects_version_mismatch(self, tmp_path):
        path = tmp_path / "spend_state.json"
        path.write_text(
            json.dumps(
                {
                    "version": SPEND_STATE_VERSION + 999,
                    "timestamp": time.time(),
                    "bucket_width_seconds": 3600.0,
                    "window_seconds": 86400.0,
                    "providers": {"openai": {"1": 5_000_000}},
                }
            )
        )
        target = StateStore()
        restore_spend(target, str(path))
        assert target.get_provider_spend("openai").recent_spend() == 0.0

    def test_restore_registers_provider_for_advisor(self, tmp_path):
        store = StateStore()
        store.get_provider_spend("openai").record_spend(time.time(), 4.0)
        path = tmp_path / "spend_state.json"
        checkpoint_spend(store, str(path))
        target = StateStore()
        restore_spend(target, str(path))
        # advisor/tools.py iterates store._provider_spend directly.
        assert "openai" in target._provider_spend


# ---------------------------------------------------------------------------
# No-network subprocess round-trip (cross-process durability proof)
# ---------------------------------------------------------------------------
class TestSubprocessRoundTrip:
    def test_checkpoint_in_A_restored_in_fresh_process_B(self, tmp_path):
        # Process A: record in-window + out-of-window spend, then checkpoint.
        store = StateStore()
        spend = store.get_provider_spend("openai")
        now = time.time()
        spend.record_spend(now, 7.0)  # in window
        spend.record_spend(now - 90000.0, 11.0)  # out of window → pruned on checkpoint
        path = tmp_path / "spend_state.json"
        checkpoint_spend(store, str(path))

        # Process B: a fresh interpreter restores and reports recent_spend.
        script = (
            "import json,sys;"
            "from airlock.fast.state import StateStore, restore_spend;"
            "s=StateStore();"
            f"restore_spend(s, {str(path)!r});"
            "print(json.dumps({'openai': s.get_provider_spend('openai').recent_spend()}))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["openai"] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# Breaker still round-trips on the (now child-located) shared path
# ---------------------------------------------------------------------------
class TestBreakerStillRoundTrips:
    def test_breaker_checkpoint_restore_round_trip(self, tmp_path):
        store = StateStore()
        model = store.get_model("gpt-4o")
        for _ in range(5):
            model.record_failure(time.time())
        assert model.circuit == CircuitState.OPEN

        path = tmp_path / "cb_state.json"
        checkpoint_state(store, str(path))

        target = StateStore()
        restore_state(target, str(path))
        assert target.get_model("gpt-4o").circuit == CircuitState.OPEN

    def test_breaker_5min_gate_still_applies(self, tmp_path):
        path = tmp_path / "cb_state.json"
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.time() - 400,  # > 5 min → stale
                    "models": {"old": {"circuit": "open", "consecutive_failures": 5}},
                }
            )
        )
        target = StateStore()
        restore_state(target, str(path))
        assert len(target.all_models()) == 0

    def test_spend_restore_not_gated_by_5min(self, tmp_path):
        # A spend checkpoint stamped > 5 min ago must STILL restore (age-bounded by
        # bucket age, not the breaker freshness gate).
        now = time.time()
        bucket_width = 3600.0
        bucket = int(now // bucket_width)
        path = tmp_path / "spend_state.json"
        path.write_text(
            json.dumps(
                {
                    "version": SPEND_STATE_VERSION,
                    "timestamp": now - 600,  # > 5 min old
                    "bucket_width_seconds": bucket_width,
                    "window_seconds": 86400.0,
                    "providers": {"openai": {str(bucket): 6_000_000}},
                }
            )
        )
        target = StateStore()
        restore_spend(target, str(path))
        assert target.get_provider_spend("openai").recent_spend() == pytest.approx(6.0)
