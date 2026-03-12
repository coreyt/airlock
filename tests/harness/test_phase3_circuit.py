"""
S12 — Circuit Breaker & Failover.
"""

from __future__ import annotations

import json
import time

import pytest


pytestmark = pytest.mark.harness


class TestCircuitBreakerStates:

    def test_initial_state_closed(self, fresh_state_store):
        from airlock.fast.state import CircuitState

        model = fresh_state_store.get_model("claude-sonnet")
        assert model.circuit == CircuitState.CLOSED

    def test_consecutive_failures_open(self, fresh_state_store):
        from airlock.fast.state import CircuitState

        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)
        assert model.circuit == CircuitState.OPEN

    def test_half_open_after_recovery(self, fresh_state_store):
        from airlock.fast.state import CircuitState

        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)
        assert model.circuit == CircuitState.OPEN
        # Advance past recovery window to trigger HALF_OPEN on next request check
        model.last_state_change = now - 120  # 2 minutes ago
        assert model.should_allow_request()  # Triggers HALF_OPEN
        assert model.circuit == CircuitState.HALF_OPEN

    def test_probe_success_closes(self, fresh_state_store):
        from airlock.fast.state import CircuitState

        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)
        model.last_state_change = now - 120
        model.should_allow_request()  # Triggers HALF_OPEN
        assert model.circuit == CircuitState.HALF_OPEN
        future = time.time() + 1  # After last_state_change
        for _ in range(model.SUCCESS_THRESHOLD):
            model.record_success(future, latency_ms=100.0)
        assert model.circuit == CircuitState.CLOSED


class TestFailover:

    def test_failover_first_healthy(self, fresh_state_store, monkeypatch):
        from airlock.fast.circuit_breaker import check_model

        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-sonnet": ["gpt-4o", "gemini-flash"]}),
        )
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        if not result.allowed:
            assert result.failover_model in ("gpt-4o", "gemini-flash")

    def test_failover_chain_exhausted(self, fresh_state_store, monkeypatch):
        from airlock.fast.circuit_breaker import check_model

        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-sonnet": ["gpt-4o"]}),
        )
        now = time.time()
        for name in ["claude-sonnet", "gpt-4o"]:
            model = fresh_state_store.get_model(name)
            for _ in range(5):
                model.record_failure(now)

        result = check_model("claude-sonnet")
        if not result.allowed:
            assert result.failover_model is None or result.failover_model == "gpt-4o"

    def test_failover_metadata_recorded(self, fresh_state_store, monkeypatch):
        from airlock.fast.circuit_breaker import check_model

        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-sonnet": ["gpt-4o"]}),
        )
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.original_model == "claude-sonnet"
        assert result.circuit_state is not None
        assert result.reason is not None

    def test_failover_map_env_override(self, monkeypatch):
        from airlock.fast.circuit_breaker import _load_failover_map

        custom = {"model-a": ["model-b", "model-c"]}
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", json.dumps(custom))
        result = _load_failover_map()
        assert result == custom

    def test_failover_map_default_entries(self):
        from airlock.fast.circuit_breaker import _load_failover_map

        fmap = _load_failover_map()
        assert isinstance(fmap, dict)
