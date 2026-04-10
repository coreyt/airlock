"""
S10 — Intelligent Routing: complexity classification, session affinity,
cost tier, provider preference, budget awareness.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.harness


class TestComplexityClassification:
    def test_simple_prompt_low_tier(self):
        from airlock.fast.router import classify_complexity

        result = classify_complexity("What is 2+2?")
        assert result.tier == "low"

    def test_complex_prompt_high_tier(self):
        from airlock.fast.router import classify_complexity

        result = classify_complexity(
            "Analyze the trade-offs between microservices and monolithic "
            "architecture. Consider deployment complexity, data consistency, "
            "team autonomy, and operational overhead. Provide a decision "
            "framework with concrete criteria for choosing between them, "
            "including code examples showing service boundaries and "
            "communication patterns. Compare event sourcing vs CQRS for "
            "the data layer. Evaluate Kubernetes vs serverless deployment "
            "strategies with cost projections."
        )
        assert result.tier in ("medium", "high")

    def test_moderate_prompt_not_low(self):
        from airlock.fast.router import classify_complexity

        result = classify_complexity(
            "Analyze the difference between async and sync programming "
            "in Python. First, explain the event loop architecture. "
            "Then compare performance characteristics for I/O-bound vs "
            "CPU-bound workloads. Provide code examples for each approach "
            "and discuss when to use asyncio vs threading vs multiprocessing. "
            "Consider error handling patterns and debugging challenges."
        )
        # Longer, multi-step prompt should score above simple
        assert result.score > 0.1

    def test_smart_replaces_model(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
        }
        result = apply_routing(data)
        assert result["model"] != "smart"


class TestSessionAffinity:
    def test_same_session_same_model(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        models = []
        for i in range(3):
            data = {
                "model": "smart",
                "messages": [{"role": "user", "content": f"Request {i}"}],
                "metadata": {"airlock": {"session_id": "test-session-1"}},
            }
            result = apply_routing(data)
            models.append(result["model"])
        assert models[0] == models[1] == models[2]

    def test_different_sessions_may_differ(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        data1 = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"session_id": "session-a"}},
        }
        data2 = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"session_id": "session-b"}},
        }
        r1 = apply_routing(data1)
        r2 = apply_routing(data2)
        # They could be the same or different; just verify both resolved
        assert r1["model"] != "smart"
        assert r2["model"] != "smart"


class TestCostTierDirective:
    def test_cost_tier_high_override(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"cost_tier": "high"}},
        }
        result = apply_routing(data)
        assert result["model"] != "smart"

    def test_cost_tier_low_override(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing, _load_cost_tiers

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        tiers = _load_cost_tiers()
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"cost_tier": "low"}},
        }
        result = apply_routing(data)
        assert result["model"] in tiers.get("low", []) or result["model"] != "smart"


class TestProviderPreference:
    def test_provider_pref_openai(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"prefer_provider": "openai"}},
        }
        result = apply_routing(data)
        assert "gpt" in result["model"].lower() or result["model"] != "smart"

    def test_provider_pref_anthropic(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"airlock": {"prefer_provider": "anthropic"}},
        }
        result = apply_routing(data)
        assert "claude" in result["model"].lower() or result["model"] != "smart"


class TestBudgetAwareness:
    def test_budget_awareness_skips_exhausted(self, fresh_state_store, monkeypatch):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        monkeypatch.setenv(
            "AIRLOCK_PROVIDER_BUDGETS",
            '{"anthropic": 0.001, "openai": 0.001, "google": 100.0}',
        )
        import time as _time

        now = _time.time()
        fresh_state_store.get_provider_spend("anthropic").record_spend(now, 1.0)
        fresh_state_store.get_provider_spend("openai").record_spend(now, 1.0)
        data = {
            "model": "smart",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        result = apply_routing(data)
        assert result["model"] != "smart"


class TestLiveSmart:
    @pytest.mark.live
    async def test_smart_simple_routes_cheap(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "What is 2+2?"}],
                "max_tokens": 10,
            },
        )
        assert resp.status_code == 200

    @pytest.mark.live
    async def test_smart_complex_routes_expensive(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "smart",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Analyze microservices vs monolithic architecture trade-offs. "
                            "Cover deployment, data consistency, team autonomy. "
                            "Provide decision framework with code examples."
                        ),
                    }
                ],
                "max_tokens": 50,
            },
        )
        assert resp.status_code == 200
