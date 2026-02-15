"""Tests for airlock/fast/circuit_breaker.py"""

from __future__ import annotations

import json
import time

import pytest

from airlock.fast.circuit_breaker import (
    FailoverResult,
    _load_failover_map,
    check_model,
)
from airlock.fast.state import CircuitState


# ---------------------------------------------------------------------------
# _load_failover_map()
# ---------------------------------------------------------------------------
class TestLoadFailoverMap:
    def test_default_map(self):
        fmap = _load_failover_map()
        assert "claude-sonnet" in fmap
        assert isinstance(fmap["claude-sonnet"], list)

    def test_custom_map_from_env(self, monkeypatch):
        custom = {"my-model": ["fallback-a", "fallback-b"]}
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", json.dumps(custom))
        fmap = _load_failover_map()
        assert fmap == custom

    def test_invalid_json_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", "not-valid-json{{{")
        fmap = _load_failover_map()
        assert "claude-sonnet" in fmap  # defaults


# ---------------------------------------------------------------------------
# check_model()
# ---------------------------------------------------------------------------
class TestCheckModel:
    def test_healthy_model_allowed(self, fresh_state_store):
        result = check_model("claude-sonnet")
        assert result.allowed is True
        assert result.failover_model is None
        assert result.circuit_state == "closed"
        assert result.reason == "model_healthy"

    def test_open_circuit_with_healthy_fallback(self, fresh_state_store):
        # Break the primary model
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)
        assert model.circuit == CircuitState.OPEN

        result = check_model("claude-sonnet")
        assert result.allowed is False
        assert result.failover_model is not None
        assert result.circuit_state == "open"
        assert "circuit_open" in result.reason

    def test_all_models_open_no_fallback(self, fresh_state_store):
        now = time.time()
        # Break primary and all fallbacks
        for model_name in ["claude-sonnet", "claude-haiku", "gpt-4o"]:
            model = fresh_state_store.get_model(model_name)
            for _ in range(5):
                model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.allowed is False
        assert result.failover_model is None
        assert result.reason == "all_models_unavailable"

    def test_half_open_allows_probe(self, fresh_state_store):
        model = fresh_state_store.get_model("claude-sonnet")
        model.circuit = CircuitState.HALF_OPEN
        model.last_state_change = time.time()

        result = check_model("claude-sonnet")
        assert result.allowed is True
        assert result.circuit_state == "half_open"

    def test_unknown_model_healthy_by_default(self, fresh_state_store):
        result = check_model("some-new-model")
        assert result.allowed is True
        assert result.circuit_state == "closed"

    def test_custom_failover_map_used(self, fresh_state_store, monkeypatch):
        custom = {"model-a": ["model-b"]}
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", json.dumps(custom))

        # Break model-a
        model = fresh_state_store.get_model("model-a")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("model-a")
        assert result.failover_model == "model-b"
