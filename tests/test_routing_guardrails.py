"""Tests for Pack 0.5.0-RES-routing (A2 fallback suppression + A3 budget warn)."""

from __future__ import annotations

import time

from airlock.fast.guardian import (
    _estimate_prompt_tokens,
    _fallback_max_prompt_tokens,
    _maybe_suppress_fallbacks,
    _suppress_fallbacks,
)


class TestFallbackSuppression:
    def test_estimate_prompt_tokens(self):
        data = {"messages": [{"role": "user", "content": "x" * 400}]}
        assert _estimate_prompt_tokens(data) == 100  # 400 chars / 4
        # multimodal content list
        data2 = {
            "messages": [
                {"content": [{"type": "text", "text": "y" * 40}, {"type": "image"}]}
            ]
        }
        assert _estimate_prompt_tokens(data2) == 10

    def test_max_prompt_tokens_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS", "1234")
        assert _fallback_max_prompt_tokens() == 1234
        monkeypatch.setenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS", "bad")
        assert _fallback_max_prompt_tokens() == 60000  # default on garbage

    def test_suppress_sets_flags(self):
        data = {}
        _suppress_fallbacks(data, "large_prompt")
        assert data["disable_fallbacks"] is True
        assert data["num_retries"] == 0
        assert data["metadata"]["airlock_fallback_suppressed"] == "large_prompt"

    def test_large_prompt_suppresses(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS", "10")
        data = {"messages": [{"content": "z" * 400}]}  # ~100 tokens > 10
        _maybe_suppress_fallbacks(data, "gpt-5.4", time.time())
        assert data.get("disable_fallbacks") is True
        assert data["metadata"]["airlock_fallback_suppressed"] == "large_prompt"

    def test_quarantined_provider_suppresses(self, fresh_state_store):
        now = time.time()
        fresh_state_store.get_provider("openai").quarantine_until = now + 100
        data = {"messages": [{"content": "small"}]}
        _maybe_suppress_fallbacks(data, "gpt-5.4", now)
        assert data.get("disable_fallbacks") is True
        assert data["metadata"]["airlock_fallback_suppressed"] == "provider_quarantined"

    def test_normal_request_untouched(self, fresh_state_store):
        data = {"messages": [{"content": "hello"}]}
        _maybe_suppress_fallbacks(data, "gpt-5.4", time.time())
        assert "disable_fallbacks" not in data  # no suppression


class TestBudgetWarn:
    def test_near_limit_warns_and_flags(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", '{"openai": 100.0}')
        monkeypatch.setenv("AIRLOCK_BUDGET_WARN_RATIO", "0.8")
        mon._budget_warned.clear()
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 85.0)  # 85% of 100 -> over 0.8
        kwargs = {"litellm_params": {"metadata": {}}}
        assert mon._maybe_warn_budget("openai", spend, kwargs) is True
        hdrs = kwargs["litellm_params"]["metadata"]["airlock_response_headers"]
        assert hdrs["X-Airlock-Budget-State"] == "near_limit"

    def test_under_limit_no_warn(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", '{"openai": 100.0}')
        mon._budget_warned.clear()
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 10.0)
        assert mon._maybe_warn_budget("openai", spend, {}) is False

    def test_no_budget_configured_no_warn(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", "{}")
        spend = fresh_state_store.get_provider_spend("zzz")
        spend.record_spend(time.time(), 999.0)
        assert mon._maybe_warn_budget("zzz", spend, {}) is False
