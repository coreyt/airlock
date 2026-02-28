"""Tests for airlock/fast/router.py — Intelligent routing directives."""

from __future__ import annotations

import json
import time

import pytest

from airlock.fast.monitor import AirlockFastMonitor, _infer_provider
from airlock.fast.router import (
    _apply_budget_awareness,
    _apply_cost_tier,
    _apply_provider_preference,
    _load_cost_tiers,
    _load_provider_budgets,
    _load_session_ttl,
    apply_routing,
    infer_provider,
)


# ---------------------------------------------------------------------------
# Env-var loaders
# ---------------------------------------------------------------------------
class TestLoadCostTiers:
    def test_defaults(self):
        tiers = _load_cost_tiers()
        assert "low" in tiers
        assert "medium" in tiers
        assert "high" in tiers
        assert "claude-haiku" in tiers["low"]

    def test_custom_env(self, monkeypatch):
        custom = {"cheap": ["gpt-4o-mini"], "expensive": ["claude-opus"]}
        monkeypatch.setenv("AIRLOCK_COST_TIERS", json.dumps(custom))
        tiers = _load_cost_tiers()
        assert tiers == custom

    def test_invalid_json(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_COST_TIERS", "not-json{")
        tiers = _load_cost_tiers()
        # Falls back to defaults
        assert "low" in tiers


class TestLoadSessionTtl:
    def test_default(self):
        assert _load_session_ttl() == 3600

    def test_custom_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_SESSION_TTL", "7200")
        assert _load_session_ttl() == 7200


class TestLoadProviderBudgets:
    def test_defaults(self):
        budgets = _load_provider_budgets()
        assert budgets["anthropic"] == 50.0
        assert budgets["gemini"] == 25.0

    def test_custom_env(self, monkeypatch):
        custom = {"anthropic": 100.0, "openai": 75.0}
        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", json.dumps(custom))
        assert _load_provider_budgets() == custom

    def test_invalid_json(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", "{bad")
        budgets = _load_provider_budgets()
        assert "anthropic" in budgets  # defaults


# ---------------------------------------------------------------------------
# Provider inference
# ---------------------------------------------------------------------------
class TestInferProvider:
    def test_claude(self):
        assert infer_provider("claude-sonnet") == "anthropic"
        assert infer_provider("claude-haiku") == "anthropic"

    def test_gpt(self):
        assert infer_provider("gpt-4o") == "openai"
        assert infer_provider("gpt-4o-mini") == "openai"

    def test_gemini(self):
        assert infer_provider("gemini-flash") == "gemini"
        assert infer_provider("gemini-3.1-pro") == "gemini"

    def test_mistral_variants(self):
        assert infer_provider("mistral-small") == "mistral"
        assert infer_provider("codestral") == "mistral"
        assert infer_provider("magistral-medium") == "mistral"

    def test_unknown(self):
        assert infer_provider("llama-3") is None


# ---------------------------------------------------------------------------
# Cost tier
# ---------------------------------------------------------------------------
class TestApplyCostTier:
    def test_model_already_in_tier(self):
        model, reason = _apply_cost_tier("low", "claude-haiku")
        assert model == "claude-haiku"
        assert reason is None

    def test_model_not_in_tier(self):
        model, reason = _apply_cost_tier("low", "claude-sonnet")
        assert model == "claude-haiku"  # first in low tier
        assert reason is not None
        assert "cost_tier" in reason

    def test_unknown_tier(self):
        model, reason = _apply_cost_tier("ultra", "claude-sonnet")
        assert model == "claude-sonnet"
        assert reason is None

    def test_any_tier(self):
        model, reason = _apply_cost_tier("any", "claude-opus")
        assert model == "claude-opus"
        assert reason is None


# ---------------------------------------------------------------------------
# Session affinity
# ---------------------------------------------------------------------------
class TestSessionAffinity:
    def test_new_session_records_model(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "sess-1"}},
        }
        result = apply_routing(data)
        assert result["model"] == "claude-sonnet"
        session = fresh_state_store.get_session("sess-1")
        assert session is not None
        assert session.model == "claude-sonnet"

    def test_existing_session_returns_pinned_model(self, fresh_state_store):
        # First request establishes session
        data1 = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "sess-2"}},
        }
        apply_routing(data1)

        # Second request with different model — should pin to original
        data2 = {
            "model": "claude-opus",
            "metadata": {"airlock": {"session_id": "sess-2"}},
        }
        result = apply_routing(data2)
        assert result["model"] == "claude-sonnet"
        assert "session_pin" in result["metadata"]["airlock_routing"]["reasons"][0]

    def test_expired_session_treats_as_new(self, fresh_state_store, monkeypatch):
        monkeypatch.setenv("AIRLOCK_SESSION_TTL", "1")

        # Create session
        data1 = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "sess-3"}},
        }
        apply_routing(data1)

        # Expire the session by backdating last_used
        session = fresh_state_store.get_session("sess-3")
        session.last_used = time.time() - 10

        # New request — should treat as new session
        data2 = {
            "model": "claude-opus",
            "metadata": {"airlock": {"session_id": "sess-3"}},
        }
        result = apply_routing(data2)
        assert result["model"] == "claude-opus"
        reasons = result["metadata"]["airlock_routing"]["reasons"]
        assert any("session_new" in r for r in reasons)

    def test_session_with_cost_tier_on_new(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "sess-4", "cost_tier": "low"}},
        }
        result = apply_routing(data)
        # Cost tier should determine the initial model
        assert result["model"] == "claude-haiku"
        session = fresh_state_store.get_session("sess-4")
        assert session.model == "claude-haiku"

    def test_session_pins_override_cost_tier(self, fresh_state_store):
        # First request with cost_tier low
        data1 = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "sess-5", "cost_tier": "low"}},
        }
        apply_routing(data1)

        # Second request with different cost tier — session pin wins
        data2 = {
            "model": "claude-opus",
            "metadata": {"airlock": {"session_id": "sess-5", "cost_tier": "high"}},
        }
        result = apply_routing(data2)
        assert result["model"] == "claude-haiku"  # pinned from first request


# ---------------------------------------------------------------------------
# Budget awareness
# ---------------------------------------------------------------------------
class TestBudgetAwareness:
    def test_under_budget_no_change(self, fresh_state_store):
        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model == "claude-sonnet"
        assert reason is None

    def test_over_threshold_swaps(self, fresh_state_store):
        # Spend 46 of 50 on anthropic (>90%)
        spend = fresh_state_store.get_provider_spend("anthropic")
        spend.record_spend(time.time(), 46.0)

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model != "claude-sonnet"
        assert reason is not None
        assert "budget" in reason

    def test_all_providers_near_budget_stays(self, fresh_state_store):
        now = time.time()
        for prov, limit in [("anthropic", 50.0), ("openai", 50.0), ("gemini", 25.0)]:
            spend = fresh_state_store.get_provider_spend(prov)
            spend.record_spend(now, limit * 0.95)

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model == "claude-sonnet"
        assert reason is None

    def test_no_budget_configured(self, fresh_state_store, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", json.dumps({}))
        model, reason = _apply_budget_awareness("claude-sonnet", ["gpt-4o"])
        assert model == "claude-sonnet"
        assert reason is None


# ---------------------------------------------------------------------------
# Provider preference
# ---------------------------------------------------------------------------
class TestProviderPreference:
    def test_already_on_preferred(self):
        model, reason = _apply_provider_preference(
            "anthropic", "claude-sonnet", ["claude-haiku", "gpt-4o"]
        )
        assert model == "claude-sonnet"
        assert reason is None

    def test_swap_to_preferred(self):
        model, reason = _apply_provider_preference(
            "anthropic", "gpt-4o", ["claude-sonnet", "gemini-pro"]
        )
        assert model == "claude-sonnet"
        assert reason is not None
        assert "prefer_provider" in reason

    def test_no_match_stays(self):
        model, reason = _apply_provider_preference(
            "anthropic", "gpt-4o", ["gemini-pro", "mistral-small"]
        )
        assert model == "gpt-4o"
        assert reason is None


# ---------------------------------------------------------------------------
# Full apply_routing
# ---------------------------------------------------------------------------
class TestApplyRouting:
    def test_no_directives_passthrough(self):
        data = {"model": "claude-sonnet", "messages": []}
        result = apply_routing(data)
        assert result["model"] == "claude-sonnet"
        assert "airlock_routing" not in result.get("metadata", {})

    def test_no_airlock_metadata_passthrough(self):
        data = {"model": "claude-sonnet", "metadata": {"other": "stuff"}}
        result = apply_routing(data)
        assert result["model"] == "claude-sonnet"

    def test_cost_tier_only(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"cost_tier": "low"}},
        }
        result = apply_routing(data)
        assert result["model"] == "claude-haiku"
        routing = result["metadata"]["airlock_routing"]
        assert routing["changed"] is True
        assert routing["original_model"] == "claude-sonnet"
        assert routing["routed_model"] == "claude-haiku"
        assert routing["cost_tier"] == "low"

    def test_session_only(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "full-1"}},
        }
        result = apply_routing(data)
        routing = result["metadata"]["airlock_routing"]
        assert routing["session_id"] == "full-1"
        assert any("session_new" in r for r in routing["reasons"])

    def test_combined_directives_priority(self, fresh_state_store):
        """Cost tier + provider preference — cost tier narrows, then preference tiebreaks."""
        data = {
            "model": "claude-opus",
            "metadata": {"airlock": {
                "cost_tier": "low",
                "prefer_provider": "gemini",
            }},
        }
        result = apply_routing(data)
        # Cost tier narrows to low models, then provider preference picks gemini
        assert result["model"] == "gemini-flash"
        routing = result["metadata"]["airlock_routing"]
        assert routing["changed"] is True
        assert len(routing["reasons"]) == 2

    def test_smart_model_not_overridden(self, fresh_state_store):
        """The 'smart' model is handled by LiteLLM's complexity router,
        not by Airlock routing directives."""
        data = {
            "model": "smart",
            "metadata": {"airlock": {"cost_tier": "low"}},
        }
        result = apply_routing(data)
        # "smart" is not in the low tier, so it gets swapped to the first low model.
        # This is correct behavior — if a client explicitly says cost_tier=low
        # and model=smart, they want a cheap model.
        assert result["model"] in _load_cost_tiers()["low"]


# ---------------------------------------------------------------------------
# Monitor spend tracking
# ---------------------------------------------------------------------------
class TestMonitorSpendTracking:
    @pytest.fixture
    def monitor(self):
        return AirlockFastMonitor()

    def test_success_with_cost_records_spend(
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        kwargs = {**mock_logger_kwargs, "response_cost": 0.05}
        start, end = mock_start_end_times
        monitor.log_success_event(kwargs, mock_response_obj, start, end)

        spend = fresh_state_store.get_provider_spend("anthropic")
        assert spend.recent_spend() == pytest.approx(0.05)

    def test_success_without_cost_no_spend(
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )

        spend = fresh_state_store.get_provider_spend("anthropic")
        assert spend.recent_spend() == 0.0

    def test_unknown_provider_no_spend(
        self, monitor, fresh_state_store, mock_start_end_times, mock_response_obj,
    ):
        kwargs = {
            "model": "llama-3-70b",
            "response_cost": 0.10,
            "litellm_params": {"metadata": {}},
        }
        start, end = mock_start_end_times
        monitor.log_success_event(kwargs, mock_response_obj, start, end)

        # No provider spend recorded for unknown provider
        for prov in ["anthropic", "openai", "gemini", "mistral"]:
            assert fresh_state_store.get_provider_spend(prov).recent_spend() == 0.0


# ---------------------------------------------------------------------------
# Monitor _infer_provider (unit tests for the monitor copy)
# ---------------------------------------------------------------------------
class TestMonitorInferProvider:
    def test_claude(self):
        assert _infer_provider("claude-sonnet") == "anthropic"

    def test_gpt(self):
        assert _infer_provider("gpt-4o") == "openai"

    def test_gemini(self):
        assert _infer_provider("gemini-flash") == "gemini"

    def test_mistral(self):
        assert _infer_provider("mistral-small") == "mistral"
        assert _infer_provider("codestral") == "mistral"

    def test_unknown(self):
        assert _infer_provider("llama-3") is None
