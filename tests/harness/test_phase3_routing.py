"""
S9 — Multi-Provider Routing: model mapping, fallback, cost-based routing.
"""

from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.harness


class TestProviderMapping:
    @pytest.mark.parametrize(
        "model,expected_provider",
        [
            ("claude-sonnet", "anthropic"),
            ("claude-haiku", "anthropic"),
            ("gpt-4o", "openai"),
            ("gpt-4o-mini", "openai"),
            ("gemini-flash", "gemini"),
            ("perplexity-sonar", "perplexity"),
            ("sonar-pro", "perplexity"),
            ("tavily-search", "tavily"),
        ],
    )
    def test_model_provider_mapping(self, model, expected_provider):
        from airlock.fast.router import infer_provider

        provider = infer_provider(model)
        assert provider == expected_provider

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-opus", "anthropic"),
            ("gpt-4o-mini", "openai"),
            ("gemini-pro", "gemini"),
            ("perplexity-sonar-deep-research", "perplexity"),
            ("unknown-model", None),
        ],
    )
    def test_infer_provider_correctness(self, model, expected):
        from airlock.fast.router import infer_provider

        assert infer_provider(model) == expected


class TestFallback:
    def test_fallback_on_open_circuit(self, fresh_state_store, monkeypatch):
        import time
        from airlock.fast.circuit_breaker import check_model

        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-sonnet": ["gpt-4o", "gemini-flash"]}),
        )
        model_state = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model_state.record_failure(now)

        result = check_model("claude-sonnet")
        assert not result.allowed or result.failover_model is not None


class TestCostBasedRouting:
    def test_cost_tiers_populated(self):
        from airlock.fast.router import _load_cost_tiers

        tiers = _load_cost_tiers()
        assert "low" in tiers
        assert "medium" in tiers
        assert "high" in tiers
        for tier_models in tiers.values():
            assert isinstance(tier_models, list)
            assert len(tier_models) > 0

    def test_provider_budget_config(self, monkeypatch):
        from airlock.fast.router import _load_provider_budgets

        budgets = _load_provider_budgets()
        assert isinstance(budgets, dict)


class TestLiveRouting:
    @pytest.mark.live
    async def test_provider_routing_anthropic(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku",
                "messages": [{"role": "user", "content": "Say hi"}],
                "max_tokens": 5,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert (
            "claude" in body.get("model", "").lower()
            or "anthropic" in body.get("model", "").lower()
        )

    @pytest.mark.live
    @pytest.mark.parametrize("model", ["claude-haiku", "gpt-4o-mini", "gemini-flash"])
    async def test_provider_routing_multi(self, http_client, model):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Say hi"}],
                "max_tokens": 5,
            },
        )
        assert resp.status_code == 200
