"""Tests for Pack 0.5.0-RES-routing (A2 fallback suppression + A3 budget warn)."""

from __future__ import annotations

import time

import pytest

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
        data2 = {
            "messages": [
                {"content": [{"type": "text", "text": "y" * 40}, {"type": "image"}]}
            ]
        }
        assert _estimate_prompt_tokens(data2) == 10

    def test_estimate_tolerates_odd_shapes(self):
        assert _estimate_prompt_tokens({}) == 0
        assert _estimate_prompt_tokens({"messages": None}) == 0
        assert (
            _estimate_prompt_tokens({"messages": ["not-a-dict", {"content": None}]}) == 0
        )

    def test_max_prompt_tokens_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS", "1234")
        assert _fallback_max_prompt_tokens() == 1234
        monkeypatch.setenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS", "bad")
        assert _fallback_max_prompt_tokens() == 60000  # default on garbage

    def test_suppress_sets_all_pinned_lock_fields(self):
        data = {}
        _suppress_fallbacks(data, "large_prompt")
        assert data["disable_fallbacks"] is True
        assert data["num_retries"] == 0
        assert data["max_retries"] == 0  # full mirror of the pinned lock
        assert data["metadata"]["airlock_fallback_suppressed"] == "large_prompt"

    def test_large_prompt_suppresses(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS", "10")
        data = {"messages": [{"content": "z" * 400}]}  # ~100 tokens > 10
        _maybe_suppress_fallbacks(data)
        assert data.get("disable_fallbacks") is True
        assert data["metadata"]["airlock_fallback_suppressed"] == "large_prompt"

    def test_normal_request_untouched_cc3(self):
        data = {"messages": [{"content": "hello"}]}
        _maybe_suppress_fallbacks(data)
        assert "disable_fallbacks" not in data  # no behaviour change without size


@pytest.fixture(autouse=True)
def _reset_budget_state():
    import airlock.fast.monitor as mon

    mon._budget_warned.clear()
    saved = mon._configured_budgets
    mon._configured_budgets = {}
    yield
    mon._budget_warned.clear()
    mon._configured_budgets = saved


class TestBudgetWarn:
    def test_no_budget_configured_no_warn_cc3(self, monkeypatch, fresh_state_store):
        """CC-3: no env var AND no provider_budget_config -> never warns, even at
        high spend. The router's internal routing defaults must not leak here."""
        import airlock.fast.monitor as mon

        monkeypatch.delenv("AIRLOCK_PROVIDER_BUDGETS", raising=False)
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 9999.0)
        assert mon._maybe_warn_budget("openai", spend, {}) is False

    def test_env_budget_near_limit_warns_and_flags(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", '{"openai": 100.0}')
        monkeypatch.setenv("AIRLOCK_BUDGET_WARN_RATIO", "0.8")
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 85.0)  # 85% > 80%
        kwargs = {"litellm_params": {"metadata": {}}}
        assert mon._maybe_warn_budget("openai", spend, kwargs) is True
        hdrs = kwargs["litellm_params"]["metadata"]["airlock_response_headers"]
        assert hdrs["X-Airlock-Budget-State"] == "near_limit"

    def test_configured_budget_from_config(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.delenv("AIRLOCK_PROVIDER_BUDGETS", raising=False)
        mon.configure_budgets(
            {"provider_budget_config": {"openai": {"budget_limit": 50.0}}}
        )
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 45.0)  # 90% of 50
        assert mon._maybe_warn_budget("openai", spend, {}) is True

    def test_under_limit_no_warn(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", '{"openai": 100.0}')
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 10.0)
        assert mon._maybe_warn_budget("openai", spend, {}) is False

    def test_warns_once_remembered(self, monkeypatch, fresh_state_store):
        import airlock.fast.monitor as mon

        monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", '{"openai": 100.0}')
        spend = fresh_state_store.get_provider_spend("openai")
        spend.record_spend(time.time(), 85.0)
        assert mon._maybe_warn_budget("openai", spend, {}) is True
        assert "openai" in mon._budget_warned  # remembered (anti-spam)

    def test_configure_budgets_ignores_malformed(self):
        import airlock.fast.monitor as mon

        mon.configure_budgets({"provider_budget_config": {"x": {"nope": 1}}})
        assert mon._configured_budgets == {}
        mon.configure_budgets(None)
        assert mon._configured_budgets == {}
