"""Tests for the admin clear/arm mutators + admin_action ingest (Pack 0.5.0-ADM-state)."""

from __future__ import annotations

import time

from airlock.fast.state import CircuitState, StateStore


class TestClearMutators:
    def test_clear_provider_probe_half_open_and_cascade(self):
        store = StateStore()
        now = time.time()
        # arm provider + two client buckets
        store.get_provider("openai").quarantine(now, "r", "RL")
        for c in ("c1", "c2"):
            store.get_client_provider(c, "openai").quarantine_until = now + 100
        rec = store.clear_provider_quarantine("openai", mode="probe", actor="op", now=now)
        ps = store.get_provider("openai")
        assert ps._half_open_probe is True
        assert ps.cleared_at == now
        assert rec["op"] == "clear_provider_quarantine"
        assert rec["record_type"] == "admin_action"
        assert rec["cascaded_clients"] == 2
        # cascade cleared the client buckets too (R12: the pinned victim)
        assert store.get_client_provider("c1", "openai")._half_open_probe is True

    def test_clear_provider_force_hard_clears(self):
        store = StateStore()
        now = time.time()
        store.get_provider("openai").quarantine_until = now + 100
        store.clear_provider_quarantine("openai", mode="force", now=now)
        ps = store.get_provider("openai")
        assert ps.quarantine_until == 0.0
        assert ps._half_open_probe is False

    def test_clear_provider_unblocks_pinned_client_victim(self):
        """R12: a single client's quarantine (what a pinned request checks first)
        is cleared by a provider clear."""
        store = StateStore()
        now = time.time()
        cp = store.get_client_provider("key:victim", "openai")
        cp.quarantine_until = now + 200
        assert cp.is_quarantined(now) is True
        store.clear_provider_quarantine("openai", mode="force", now=now)
        assert store.get_client_provider("key:victim", "openai").is_quarantined(now) is False

    def test_clear_then_in_window_429_does_not_rearm(self):
        """CC-6: cleared_at floor means a post-clear 429 starts a fresh window."""
        store = StateStore()
        now = time.time()
        # pre-clear history that would otherwise re-arm at threshold 1
        cp = store.get_client_provider("c", "openai")
        for _ in range(3):
            cp.rate_limit_times.append(now)
        store.clear_client_provider_quarantine("c", "openai", mode="force", now=now)
        # the cleared floor hides the 3 pre-clear events
        assert cp.recent_rate_limit_count() == 0

    def test_clear_client_backoff(self):
        store = StateStore()
        store.get_client("c").backoff_until = time.time() + 100
        rec = store.clear_client_backoff("c")
        assert store.get_client("c").is_in_backoff() is False
        assert rec["op"] == "clear_client_backoff"

    def test_reset_model_circuit_half_open(self):
        store = StateStore()
        ms = store.get_model("gpt-5.4")
        ms.circuit = CircuitState.OPEN
        ms.consecutive_failures = 9
        rec = store.reset_model_circuit("gpt-5.4")
        assert ms.circuit == CircuitState.HALF_OPEN
        assert ms.consecutive_failures == 0
        assert rec["op"] == "reset_model_circuit"

    def test_quarantine_provider_manual_arm(self):
        store = StateStore()
        now = time.time()
        rec = store.quarantine_provider("openai", now=now, cooldown=120)
        assert store.get_provider("openai").is_quarantined(now) is True
        assert rec["op"] == "quarantine_provider"
        assert rec["cooldown_seconds"] == 120


class TestAdminActionIngest:
    def test_ingest_admin_action_converges_replica(self):
        # live store performs a clear and emits the record...
        live = StateStore()
        now = time.time()
        live.get_provider("openai").quarantine_until = now + 100
        rec = live.clear_provider_quarantine("openai", mode="probe", now=now)
        # ...the replica (separate store, as the TUI process) ingests it
        replica = StateStore()
        replica.get_provider("openai").quarantine_until = now + 100  # replica thought it was quarantined
        replica.ingest_jsonl_record(rec)
        assert replica.get_provider("openai").is_quarantined() is False

    def test_admin_action_record_not_dropped_by_model_check(self):
        replica = StateStore()
        rec = {
            "record_type": "admin_action",
            "op": "clear_client_backoff",
            "client_id": "c",
            "actor": "op",
        }
        replica.get_client("c").backoff_until = time.time() + 100
        replica.ingest_jsonl_record(rec)  # no "model" key — must NOT be dropped
        assert replica.get_client("c").is_in_backoff() is False


class TestAdmStateFix1:
    """From the ADM-state PASS_WITH_NOTES review."""

    def test_unknown_mode_raises(self):
        import pytest

        store = StateStore()
        with pytest.raises(ValueError):
            store.clear_provider_quarantine("openai", mode="Force")
        with pytest.raises(ValueError):
            store.clear_client_provider_quarantine("c", "openai", mode="nope")

    def test_probe_unquarantines_immediately(self):
        store = StateStore()
        now = time.time()
        store.get_provider("openai").quarantine_until = now + 100
        store.clear_provider_quarantine("openai", mode="probe", now=now)
        ps = store.get_provider("openai")
        assert ps.quarantine_until == now
        assert ps.is_quarantined(now) is False  # now < now is False

    def test_ingest_uses_record_timestamp(self):
        live = StateStore()
        t0 = 10_000.0
        rec = live.clear_client_provider_quarantine("c", "openai", mode="force", now=t0)
        replica = StateStore()
        replica.ingest_jsonl_record(rec)
        # cleared_at on the replica reflects the original event time, not replay time
        assert replica.get_client_provider("c", "openai").cleared_at == t0

    def test_ingest_roundtrip_client_provider(self):
        live = StateStore()
        now = time.time()
        rec = live.clear_client_provider_quarantine("c", "openai", mode="force", now=now)
        replica = StateStore()
        replica.get_client_provider("c", "openai").quarantine_until = now + 100
        replica.ingest_jsonl_record(rec)
        assert replica.get_client_provider("c", "openai").is_quarantined() is False

    def test_ingest_roundtrip_reset_model_circuit(self):
        live = StateStore()
        rec = live.reset_model_circuit("gpt-5.4")
        replica = StateStore()
        replica.get_model("gpt-5.4").circuit = CircuitState.OPEN
        replica.ingest_jsonl_record(rec)
        assert replica.get_model("gpt-5.4").circuit == CircuitState.HALF_OPEN

    def test_ingest_roundtrip_quarantine_provider(self):
        live = StateStore()
        now = time.time()
        rec = live.quarantine_provider("openai", now=now, cooldown=120)
        replica = StateStore()
        replica.ingest_jsonl_record(rec)
        assert replica.get_provider("openai").is_quarantined() is True
