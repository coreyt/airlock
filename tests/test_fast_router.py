"""Tests for airlock/fast/router.py — Intelligent routing directives."""

from __future__ import annotations

import json
import time

import pytest

import airlock.fast.settings as settings_mod
from airlock.fast.monitor import AirlockFastMonitor, _infer_provider
from airlock.fast.router import (
    _apply_budget_awareness,
    _apply_cost_tier,
    _apply_provider_preference,
    _extract_text,
    _load_cost_tiers,
    _load_session_ttl,
    _load_smart_thresholds,
    apply_routing,
    classify_complexity,
    infer_provider,
    set_router_config,
)
from airlock.fast.settings import configure_settings


@pytest.fixture(autouse=True)
def _reset_router_config():
    """Every test starts with an empty router config cache and unconfigured
    AirlockSettings (so env overrides / configure_settings are honoured per-test)."""
    set_router_config(None)
    settings_mod._configured = None
    yield
    set_router_config(None)
    settings_mod._configured = None


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

    def test_from_config(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config(
            {
                "cost_tiers": {
                    "low": ["gpt-5-mini"],
                    "medium": ["gpt-5"],
                    "high": ["gpt-5-pro"],
                }
            }
        )
        tiers = _load_cost_tiers()
        assert tiers["low"] == ["gpt-5-mini"]
        assert tiers["high"] == ["gpt-5-pro"]

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_COST_TIERS", json.dumps({"low": ["env-only"]}))
        set_router_config({"cost_tiers": {"low": ["config-only"]}})
        tiers = _load_cost_tiers()
        assert tiers["low"] == ["env-only"]

    def test_invalid_config_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config({"cost_tiers": {"low": "not-a-list"}})
        tiers = _load_cost_tiers()
        assert "claude-haiku" in tiers["low"]  # built-in defaults

    def test_config_cost_tier_swaps_model(self, monkeypatch):
        """End-to-end: cost_tiers from config drives _apply_cost_tier swaps."""
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config(
            {"cost_tiers": {"low": ["gpt-5-mini"], "high": ["gpt-5-pro"]}}
        )
        model, reason = _apply_cost_tier("low", "gpt-5")
        assert model == "gpt-5-mini"
        assert reason is not None


class TestCostTierAliasEquivalence:
    """An alias sharing a body with an in-tier model must NOT be force-swapped.

    Regression guard: `_apply_cost_tier` is a plain membership test that swaps to
    `tier_models[0]` on a miss. Provider-prefixed twins and family aliases share a
    `litellm_params.model` with the alias named in the tier, so without body-aware
    matching they were silently rerouted to a *different* model.
    """

    def _config(self):
        return {
            "cost_tiers": {"low": ["cheap"], "high": ["spendy"]},
            "model_list": [
                {"model_name": "cheap", "litellm_params": {"model": "openai/cheap-1"}},
                {
                    "model_name": "openai/cheap",
                    "litellm_params": {"model": "openai/cheap-1"},
                },
                {
                    "model_name": "cheap-family",
                    "litellm_params": {"model": "openai/cheap-1"},
                },
                {
                    "model_name": "spendy",
                    "litellm_params": {"model": "openai/spendy-1"},
                },
            ],
        }

    def test_prefixed_twin_is_not_swapped(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config(self._config())
        model, reason = _apply_cost_tier("low", "openai/cheap")
        assert model == "openai/cheap", "prefixed twin was force-swapped"
        assert reason is None

    def test_family_alias_sharing_body_is_not_swapped(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config(self._config())
        model, reason = _apply_cost_tier("low", "cheap-family")
        assert model == "cheap-family"
        assert reason is None

    def test_genuinely_out_of_tier_still_swaps(self, monkeypatch):
        """The tier-restriction feature itself must keep working."""
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config(self._config())
        model, reason = _apply_cost_tier("low", "spendy")
        assert model == "cheap"
        assert reason is not None

    def test_unknown_model_still_swaps(self, monkeypatch):
        """No body known → cannot prove in-tier → swap, as before."""
        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        set_router_config(self._config())
        model, reason = _apply_cost_tier("low", "never-heard-of-it")
        assert model == "cheap"
        assert reason is not None


class TestLoadSessionTtl:
    def test_default(self):
        assert _load_session_ttl() == 3600

    def test_custom_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_SESSION_TTL", "7200")
        assert _load_session_ttl() == 7200


class TestProviderBudgetSource:
    """Budget source is now get_settings().provider_budgets (SET-unify).

    The router no longer owns a hidden default map; with no config and no env there
    are simply no budgets.
    """

    def test_no_config_no_budgets(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_PROVIDER_BUDGETS", raising=False)
        from airlock.fast.settings import get_settings

        assert get_settings().provider_budgets == {}

    def test_custom_env(self, monkeypatch):
        custom = {"anthropic": 100.0, "openai": 75.0}
        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", json.dumps(custom))
        from airlock.fast.settings import get_settings

        assert get_settings().provider_budgets == custom

    def test_from_router_settings_config(self):
        configure_settings(
            {
                "router_settings": {
                    "provider_budget_config": {
                        "anthropic": {"budget_limit": 40.0, "time_period": "1d"},
                    }
                }
            }
        )
        from airlock.fast.settings import get_settings

        assert get_settings().provider_budgets == {"anthropic": 40.0}


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

    def test_null_or_empty_model(self):
        """Batch/file routes carry no top-level model — must not crash."""
        assert infer_provider(None) is None
        assert infer_provider("") is None
        assert infer_provider("magistral-medium") == "mistral"

    def test_unknown(self):
        assert infer_provider("llama-3") is None


class TestInferProviderCatalog:
    """After set_router_config(root config), every Appendix-A alias maps to its
    served-by token (no enhanced / aistudio / vertex literals)."""

    @pytest.fixture
    def root_config(self):
        import pathlib

        import yaml

        path = pathlib.Path(__file__).resolve().parent.parent / "config.yaml"
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @pytest.fixture
    def configured(self, root_config):
        set_router_config(root_config)
        return root_config

    def test_aistudio_prefix_infers_gemini(self, configured):
        assert infer_provider("aistudio/gemini-3.5-flash") == "gemini"

    def test_vertex_prefix_infers_vertex_ai(self, configured):
        assert infer_provider("vertex/gemini-3.5-flash") == "vertex_ai"

    def test_enhanced_prefixed_alias_infers_gemini(self, configured):
        assert infer_provider("aistudio/gemini-coding") == "gemini"

    def test_enhanced_bare_alias_infers_gemini(self, configured):
        assert infer_provider("gemini-coding") == "gemini"

    def test_vllm_prefix_infers_openai(self, configured):
        assert infer_provider("vllm/qwen3.6-27b") == "openai"

    def test_anthropic_prefix(self, configured):
        assert infer_provider("anthropic/claude-opus") == "anthropic"

    def test_mistral_prefix(self, configured):
        assert infer_provider("mistral/mistral-large") == "mistral"

    def test_perplexity_prefix(self, configured):
        assert infer_provider("perplexity/sonar") == "perplexity"

    def test_tavily_prefix(self, configured):
        assert infer_provider("tavily/web-search") == "tavily"

    def test_no_alias_reports_enhanced_or_prefix_literal(self, configured, root_config):
        """No catalog alias may resolve to enhanced/aistudio/vertex literals."""
        from airlock.capability import airlock_provider_for

        for entry in root_config["model_list"]:
            alias = entry["model_name"]
            provider = infer_provider(alias)
            assert provider not in {"enhanced", "aistudio", "vertex"}, (
                f"{alias} -> {provider}"
            )
            # catalog map must match the shared classifier
            assert provider == airlock_provider_for(entry), alias


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
    def test_under_budget_no_change(self, fresh_state_store, monkeypatch):
        monkeypatch.setenv(
            "AIRLOCK_PROVIDER_BUDGETS",
            json.dumps({"anthropic": 50.0, "openai": 50.0}),
        )
        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model == "claude-sonnet"
        assert reason is None

    def test_over_threshold_swaps(self, fresh_state_store, monkeypatch):
        # Budgets are config-driven now (SET-unify) — set them explicitly.
        monkeypatch.setenv(
            "AIRLOCK_PROVIDER_BUDGETS",
            json.dumps({"anthropic": 50.0, "openai": 50.0}),
        )
        # Spend 46 of 50 on anthropic (>90%)
        spend = fresh_state_store.get_provider_spend("anthropic")
        spend.record_spend(time.time(), 46.0)

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model != "claude-sonnet"
        assert reason is not None
        assert "budget" in reason

    def test_all_providers_near_budget_stays(self, fresh_state_store, monkeypatch):
        # Set budgets explicitly so the test controls the scenario and never depends on
        # the production-tuned default budgets (which change for operational reasons).
        budgets = {"anthropic": 50.0, "openai": 50.0, "gemini": 25.0}
        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", json.dumps(budgets))
        now = time.time()
        for prov, limit in budgets.items():
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

    def test_no_provider_budget_config_no_swap(self, fresh_state_store, monkeypatch):
        """Behavior-change #1: with NO provider_budget_config there is no hidden
        default budget, so even a huge spend never triggers a proactive swap."""
        monkeypatch.delenv("AIRLOCK_PROVIDER_BUDGETS", raising=False)
        configure_settings({"router_settings": {}})  # no provider_budget_config
        spend = fresh_state_store.get_provider_spend("anthropic")
        spend.record_spend(time.time(), 10_000.0)

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model == "claude-sonnet"
        assert reason is None

    def test_zero_budget_means_no_swap(self, fresh_state_store, monkeypatch):
        """AC-0 (router layer): a 0-configured budget => no proactive swap, even when
        spend is enormous (falsy short-circuit, identical to absent)."""
        monkeypatch.delenv("AIRLOCK_PROVIDER_BUDGETS", raising=False)
        configure_settings(
            {
                "router_settings": {
                    "provider_budget_config": {
                        "anthropic": {"budget_limit": 0, "time_period": "1d"},
                    }
                }
            }
        )
        spend = fresh_state_store.get_provider_spend("anthropic")
        spend.record_spend(time.time(), 10_000.0)

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model == "claude-sonnet"
        assert reason is None

    def test_default_swap_point_is_warn_ratio_080(self, fresh_state_store, monkeypatch):
        """Behavior-change #2: the proactive swap point is now the configured
        budget_warn_ratio (default 0.8), NOT the old hardcoded 0.9. A spend at 84%
        of budget — between 0.8 and 0.9 — now swaps (it would not have at 0.9)."""
        monkeypatch.setenv(
            "AIRLOCK_PROVIDER_BUDGETS",
            json.dumps({"anthropic": 50.0, "openai": 50.0}),
        )
        spend = fresh_state_store.get_provider_spend("anthropic")
        spend.record_spend(time.time(), 42.0)  # 42/50 = 0.84: >=0.8, <0.9

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model != "claude-sonnet"
        assert reason is not None
        assert "budget" in reason

    def test_swap_point_honors_configured_ratio(self, fresh_state_store, monkeypatch):
        """R3 single source: the router swap reads get_settings().budget_warn_ratio.
        With ratio 0.5, a 60%-of-budget spend swaps (it would not at the 0.8 default)."""
        monkeypatch.delenv("AIRLOCK_PROVIDER_BUDGETS", raising=False)
        configure_settings(
            {
                "router_settings": {
                    "provider_budget_config": {
                        "anthropic": {"budget_limit": 50.0, "time_period": "1d"},
                        "openai": {"budget_limit": 50.0, "time_period": "1d"},
                    }
                },
                "airlock_settings": {"budget_warn_ratio": 0.5},
            }
        )
        spend = fresh_state_store.get_provider_spend("anthropic")
        spend.record_spend(time.time(), 30.0)  # 30/50 = 0.6: >=0.5, <0.8

        model, reason = _apply_budget_awareness(
            "claude-sonnet", ["gpt-4o", "gemini-pro"]
        )
        assert model != "claude-sonnet"
        assert reason is not None
        assert "budget" in reason

    def test_no_legacy_warn_threshold_constant(self):
        """R3: the duplicate hardcoded router threshold is gone — single source only."""
        import airlock.fast.router as router_mod

        assert not hasattr(router_mod, "_BUDGET_WARN_THRESHOLD")


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
            "metadata": {
                "airlock": {
                    "cost_tier": "low",
                    "prefer_provider": "gemini",
                }
            },
        }
        result = apply_routing(data)
        # Cost tier narrows to low models, then provider preference picks gemini
        assert result["model"] == "gemini-flash"
        routing = result["metadata"]["airlock_routing"]
        assert routing["changed"] is True
        assert len(routing["reasons"]) == 2

    def test_unknown_model_with_cost_tier(self, fresh_state_store):
        """Unknown model names get tier-swapped like any other model."""
        data = {
            "model": "some-unknown-model",
            "metadata": {"airlock": {"cost_tier": "low"}},
        }
        result = apply_routing(data)
        # Not in the low tier, so gets swapped to the first low model
        assert result["model"] in _load_cost_tiers()["low"]


# ---------------------------------------------------------------------------
# Monitor spend tracking
# ---------------------------------------------------------------------------
class TestMonitorSpendTracking:
    @pytest.fixture
    def monitor(self):
        return AirlockFastMonitor()

    def test_success_with_cost_records_spend(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        kwargs = {**mock_logger_kwargs, "response_cost": 0.05}
        start, end = mock_start_end_times
        monitor.log_success_event(kwargs, mock_response_obj, start, end)

        spend = fresh_state_store.get_provider_spend("anthropic")
        assert spend.recent_spend() == pytest.approx(0.05)

    def test_success_without_cost_no_spend(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        spend = fresh_state_store.get_provider_spend("anthropic")
        assert spend.recent_spend() == 0.0

    def test_unknown_provider_no_spend(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
        mock_response_obj,
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


# ---------------------------------------------------------------------------
# Complexity classifier
# ---------------------------------------------------------------------------
class TestComplexityClassifier:
    def test_simple_greeting(self):
        result = classify_complexity("Hello!")
        assert result.complexity == "simple"
        assert result.tier == "low"
        assert result.score < 0.30

    def test_simple_factual_question(self):
        result = classify_complexity("What is the capital of France?")
        assert result.complexity == "simple"
        assert result.tier == "low"

    def test_simple_single_word(self):
        result = classify_complexity("Hi")
        assert result.complexity == "simple"
        assert result.tier == "low"

    def test_moderate_explanation(self):
        result = classify_complexity(
            "Explain how HTTP cookies work and why they are important "
            "for maintaining user sessions in web applications. Compare "
            "session cookies vs persistent cookies and analyze the security "
            "trade-offs involved in each approach."
        )
        assert result.complexity in ("moderate", "complex")
        assert result.tier in ("medium", "high")

    def test_moderate_short_code_task(self):
        result = classify_complexity(
            "Write a Python function that checks if a number is prime."
        )
        # Should be at least moderate due to "implement"-like intent
        assert result.complexity in ("simple", "moderate")

    def test_complex_architecture_design(self):
        result = classify_complexity(
            "Design a microservices architecture for an e-commerce platform. "
            "Compare monolithic vs microservices trade-offs. First, analyze "
            "the current system. Then, evaluate database options. "
            "Finally, implement the service mesh. Consider:\n"
            "1. Service discovery\n"
            "2. Load balancing\n"
            "3. Circuit breaking\n"
            "4. Distributed tracing\n"
            "```python\nclass ServiceMesh:\n    def discover(self): ...\n```\n"
            "Explain why each component is critical and diagnose potential "
            "failure modes. Optimize for high availability and synthesize "
            "a deployment strategy."
        )
        assert result.complexity == "complex"
        assert result.tier == "high"
        assert result.score >= 0.60

    def test_complex_multi_step_code_review(self):
        prompt = (
            "Please analyze this code and refactor it:\n"
            "```python\n"
            "def process(data):\n"
            "    result = []\n"
            "    for item in data:\n"
            "        if item > 0:\n"
            "            result.append(item * 2)\n"
            "    return result\n"
            "```\n"
            "First, identify the performance issues. Then, debug any edge "
            "cases. Finally, optimize the implementation and compare the "
            "trade-offs between readability and performance."
        )
        result = classify_complexity(prompt)
        assert result.complexity in ("moderate", "complex")
        assert result.score >= 0.30

    def test_empty_text_returns_moderate(self):
        result = classify_complexity("")
        assert result.complexity == "moderate"
        assert result.tier == "medium"
        assert result.score == 0.45

    def test_whitespace_returns_moderate(self):
        result = classify_complexity("   \n\t  ")
        assert result.complexity == "moderate"
        assert result.tier == "medium"

    def test_score_normalized_0_to_1(self):
        for text in ["Hi", "Explain quantum computing", "a" * 1000]:
            result = classify_complexity(text)
            assert 0.0 <= result.score <= 1.0

    def test_features_dict_has_all_keys(self):
        result = classify_complexity("Hello world")
        expected_keys = {
            "token_count",
            "code_blocks",
            "reasoning",
            "multi_step",
            "vocab_rich",
            "sentence_len",
        }
        assert set(result.features.keys()) == expected_keys

    def test_code_blocks_boost_score(self):
        text_no_code = "Write a sorting function in Python"
        text_with_code = (
            "Write a sorting function in Python\n```python\ndef sort(arr): pass\n```"
        )
        score_no_code = classify_complexity(text_no_code).score
        score_with_code = classify_complexity(text_with_code).score
        assert score_with_code > score_no_code

    def test_reasoning_keywords_boost_score(self):
        text_plain = "Tell me about databases"
        text_reasoning = "Analyze and compare databases, evaluate trade-offs"
        score_plain = classify_complexity(text_plain).score
        score_reasoning = classify_complexity(text_reasoning).score
        assert score_reasoning > score_plain


class TestSmartThresholds:
    def test_default_thresholds(self):
        assert _load_smart_thresholds() == (0.30, 0.60)

    def test_custom_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_SMART_THRESHOLDS", "[0.20, 0.50]")
        assert _load_smart_thresholds() == (0.20, 0.50)

    def test_invalid_json_falls_back(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_SMART_THRESHOLDS", "not-json")
        assert _load_smart_thresholds() == (0.30, 0.60)


class TestExtractText:
    def test_simple_string_content(self):
        data = {"messages": [{"role": "user", "content": "Hello"}]}
        assert _extract_text(data) == "Hello"

    def test_multimodal_content_blocks(self):
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Look at this"},
                        {"type": "image_url", "image_url": {"url": "..."}},
                    ],
                }
            ]
        }
        assert _extract_text(data) == "Look at this"

    def test_skips_non_user_messages(self):
        data = {
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        }
        assert _extract_text(data) == "Hello"

    def test_empty_messages(self):
        assert _extract_text({"messages": []}) == ""
        assert _extract_text({}) == ""


# ---------------------------------------------------------------------------
# Smart routing integration
# ---------------------------------------------------------------------------
class TestSmartRouting:
    def test_simple_prompt_routes_to_low_tier(self, fresh_state_store):
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hi there!"}],
        }
        result = apply_routing(data)
        low_models = _load_cost_tiers()["low"]
        assert result["model"] in low_models
        routing = result["metadata"]["airlock_routing"]
        assert routing["original_model"] == "smart"
        assert routing["changed"] is True
        assert "smart_classify" in routing
        assert routing["smart_classify"]["complexity"] == "simple"

    def test_complex_prompt_routes_to_high_tier(self, fresh_state_store):
        prompt = (
            "Design a distributed system architecture. First, analyze the "
            "trade-offs between consistency and availability. Then, evaluate "
            "database options and compare their performance characteristics.\n"
            "1. Implement service discovery\n"
            "2. Design the API gateway\n"
            "3. Optimize caching strategy\n"
            "```python\nclass ServiceMesh:\n    pass\n```\n"
            "Finally, diagnose potential failure modes and synthesize a "
            "comprehensive deployment strategy."
        )
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": prompt}],
        }
        result = apply_routing(data)
        high_models = _load_cost_tiers()["high"]
        assert result["model"] in high_models
        routing = result["metadata"]["airlock_routing"]
        assert routing["smart_classify"]["complexity"] == "complex"

    def test_smart_with_session_first_classifies_then_pins(self, fresh_state_store):
        data1 = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello!"}],
            "metadata": {"airlock": {"session_id": "smart-sess-1"}},
        }
        result1 = apply_routing(data1)
        first_model = result1["model"]

        # Second request — different prompt but session pins
        data2 = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Design a complex system"}],
            "metadata": {"airlock": {"session_id": "smart-sess-1"}},
        }
        result2 = apply_routing(data2)
        assert result2["model"] == first_model  # pinned

    def test_smart_with_prefer_provider(self, fresh_state_store):
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hi!"}],
            "metadata": {"airlock": {"prefer_provider": "gemini"}},
        }
        result = apply_routing(data)
        # Simple prompt → low tier, then prefer gemini within low
        assert result["model"] == "gemini-flash"

    def test_regular_model_bypasses_classifier(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"cost_tier": "low"}},
        }
        result = apply_routing(data)
        # Normal routing — no smart_classify in metadata
        routing = result["metadata"]["airlock_routing"]
        assert "smart_classify" not in routing


def _ledger(data):
    return data.get("metadata", {}).get("airlock_mutations", [])


# ---------------------------------------------------------------------------
# OBS-ledger — model-rewrite records
# ---------------------------------------------------------------------------
class TestRoutingLedger:
    def test_cost_tier_records_model_rewrite(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"cost_tier": "low"}},
        }
        apply_routing(data)
        muts = [m for m in _ledger(data) if m.field == "model"]
        assert len(muts) == 1
        m = muts[0]
        assert m.op == "rewrite"
        assert m.before == "claude-sonnet"
        assert m.after == "claude-haiku"
        assert m.stage == "pre_call"
        assert m.source == "router.cost_tier"
        # CC-T1 back-compat
        assert data["metadata"]["airlock_routing"]["routed_model"] == "claude-haiku"

    def test_smart_records_smart_default_then_route(self, fresh_state_store):
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hi there!"}],
        }
        apply_routing(data)
        muts = [m for m in _ledger(data) if m.field == "model"]
        # 1) smart placeholder substitution, then 2) directive routing
        assert muts[0].op == "rewrite"
        assert muts[0].before == "smart"
        assert muts[0].after == "claude-sonnet"
        assert muts[0].source == "router.smart"
        assert any(m.source == "router.cost_tier" for m in muts)
        # the routing record's before is the placeholder, not "smart" (no dup)
        routed = next(m for m in muts if m.source == "router.cost_tier")
        assert routed.before == "claude-sonnet"

    def test_no_directives_records_nothing(self):
        data = {"model": "claude-sonnet", "messages": []}
        apply_routing(data)
        assert _ledger(data) == []

    def test_session_new_same_model_no_model_rewrite(self, fresh_state_store):
        data = {
            "model": "claude-sonnet",
            "metadata": {"airlock": {"session_id": "led-1"}},
        }
        apply_routing(data)
        assert [m for m in _ledger(data) if m.field == "model"] == []
