"""Tests for airlock/fast/circuit_breaker.py"""

from __future__ import annotations

import json
import time

import pytest

import airlock.fast.settings as settings_mod
from airlock.fast.circuit_breaker import (
    _load_failover_map,
    check_model,
)
from airlock.fast.router import set_router_config
from airlock.fast.settings import configure_settings
from airlock.fast.state import CircuitState


@pytest.fixture(autouse=True)
def _reset_settings():
    """Failover now derives from get_settings().failover_map (SET-unify); start each
    test unconfigured so env / configure_settings is honoured deterministically. The
    router catalog (alias->provider map) is also cleared so the default state is an
    EMPTY catalog (failover-target filtering disabled — the safe fallback)."""
    settings_mod._configured = None
    set_router_config(None)
    yield
    settings_mod._configured = None
    set_router_config(None)


# Representative fallbacks block (real model_name aliases, mirrors config.yaml). Used
# by the circuit-open scenarios now that there is no hidden default failover map.
_FALLBACK_CONFIG = {
    "router_settings": {
        "fallbacks": [
            {"claude-sonnet": ["claude-haiku", "gpt-5-mini"]},
            {"claude-haiku": ["gemini-flash", "gpt-5-nano"]},
        ]
    }
}

# A loaded model_list catalog (alias->provider via litellm_params.model prefix). When
# present, failover targets are constrained to these known aliases.
_CATALOG = {
    "model_list": [
        {
            "model_name": "claude-sonnet",
            "litellm_params": {"model": "anthropic/claude-sonnet-4"},
        },
        {
            "model_name": "claude-haiku",
            "litellm_params": {"model": "anthropic/claude-haiku"},
        },
        {"model_name": "gpt-5-mini", "litellm_params": {"model": "openai/gpt-5-mini"}},
        {
            "model_name": "gemini-flash",
            "litellm_params": {"model": "gemini/gemini-flash"},
        },
    ]
}


# ---------------------------------------------------------------------------
# _load_failover_map()  (now a thin shim over get_settings().failover_map)
# ---------------------------------------------------------------------------
class TestLoadFailoverMap:
    def test_empty_when_no_config(self, monkeypatch):
        # SET-unify removed the hidden default map: no config => empty.
        monkeypatch.delenv("AIRLOCK_FAILOVER_MAP", raising=False)
        assert _load_failover_map() == {}

    def test_from_config_fallbacks(self):
        configure_settings(_FALLBACK_CONFIG)
        fmap = _load_failover_map()
        assert fmap["claude-sonnet"] == ["claude-haiku", "gpt-5-mini"]

    def test_custom_map_from_env(self, monkeypatch):
        custom = {"my-model": ["fallback-a", "fallback-b"]}
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", json.dumps(custom))
        fmap = _load_failover_map()
        assert fmap == custom

    def test_invalid_json_falls_back_to_empty(self, monkeypatch):
        # Invalid env + no config => empty (no hidden default to fall back to).
        monkeypatch.delenv("AIRLOCK_FAILOVER_MAP", raising=False)
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", "not-valid-json{{{")
        assert _load_failover_map() == {}


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
        configure_settings(_FALLBACK_CONFIG)
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

    def test_failover_lands_on_real_config_model_not_stale_gpt4o(
        self, fresh_state_store
    ):
        """R2: failover derives from router_settings.fallbacks and lands on a real
        model_name present in the config, never the removed stale 'gpt-4o'."""
        configure_settings(_FALLBACK_CONFIG)
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.allowed is False
        assert result.failover_model == "claude-haiku"
        assert result.failover_model != "gpt-4o"

    def test_all_models_open_no_fallback(self, fresh_state_store):
        configure_settings(_FALLBACK_CONFIG)
        now = time.time()
        # Break primary and all configured fallbacks
        for model_name in ["claude-sonnet", "claude-haiku", "gpt-5-mini"]:
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

    def test_unknown_failover_target_skipped_when_catalog_known(
        self, fresh_state_store, monkeypatch
    ):
        """With a loaded model_list catalog, an env-override failover target that is
        not a known alias (typo/mistake) is skipped; selection lands on a real
        model_name that is actually served."""
        set_router_config(_CATALOG)
        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-sonnet": ["typo-model", "claude-haiku"]}),
        )

        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.failover_model == "claude-haiku"  # typo-model skipped

    def test_all_unknown_targets_filtered_when_catalog_known(
        self, fresh_state_store, monkeypatch
    ):
        """If every failover target is unknown to the catalog, none is selected."""
        set_router_config(_CATALOG)
        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-sonnet": ["model-x", "model-y"]}),
        )

        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.failover_model is None
        assert result.reason == "all_models_unavailable"

    def test_config_fallback_unknown_target_skipped_when_catalog_known(
        self, fresh_state_store, monkeypatch
    ):
        """Filtering covers the config-fallbacks-derived map too, not just the env
        override: a typo'd config fallback is skipped, real one is kept."""
        monkeypatch.delenv("AIRLOCK_FAILOVER_MAP", raising=False)
        set_router_config(_CATALOG)
        configure_settings(
            {
                "router_settings": {
                    "fallbacks": [
                        {"claude-sonnet": ["typo-model", "gpt-5-mini"]},
                    ]
                }
            }
        )

        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.failover_model == "gpt-5-mini"

    def test_unknown_target_allowed_when_catalog_empty(
        self, fresh_state_store, monkeypatch
    ):
        """SAFE FALLBACK: with an empty/unconfigured catalog, target filtering is
        disabled — arbitrary failover targets pass through (preserves prior behavior
        and avoids suppressing ALL failover when the catalog simply is not loaded)."""
        set_router_config(None)  # empty catalog
        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", json.dumps({"model-a": ["model-b"]}))

        model = fresh_state_store.get_model("model-a")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("model-a")
        assert result.failover_model == "model-b"

    def test_no_config_no_failover(self, fresh_state_store, monkeypatch):
        """Behavior-change: with no fallbacks configured there is no hidden default
        map, so a broken model has no failover target."""
        monkeypatch.delenv("AIRLOCK_FAILOVER_MAP", raising=False)
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        result = check_model("claude-sonnet")
        assert result.allowed is False
        assert result.failover_model is None
        assert result.reason == "all_models_unavailable"
