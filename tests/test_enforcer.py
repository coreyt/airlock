"""Tests for airlock/guardrails/enforcer.py"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from airlock.guardrails.enforcer import AirlockEnforcer, _enforce_mode
from airlock.guardrails.orchestrator import _invalidate_knobs_cache
from airlock.guardrails.schemas import GuardrailKnobs
from airlock.slow.tuner import write_knobs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clear_knobs_cache():
    _invalidate_knobs_cache()
    yield
    _invalidate_knobs_cache()


@pytest.fixture
def knobs_dir(tmp_path, monkeypatch):
    import airlock.slow.tuner as tuner_mod

    monkeypatch.setattr(tuner_mod, "LOG_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def enforcer():
    return AirlockEnforcer()


@pytest.fixture
def clean_data():
    return {
        "messages": [{"role": "user", "content": "What is Python?"}],
        "model": "claude-sonnet",
    }


@pytest.fixture
def keyword_data():
    return {
        "messages": [{"role": "user", "content": "Tell me forbidden secrets"}],
        "model": "claude-sonnet",
    }


# ---------------------------------------------------------------------------
# _enforce_mode
# ---------------------------------------------------------------------------
class TestEnforceMode:
    def test_default_observe(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_ENFORCE_MODE", raising=False)
        assert _enforce_mode() == "observe"

    def test_shadow_mode(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        assert _enforce_mode() == "shadow"

    def test_enforce_mode(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        assert _enforce_mode() == "enforce"

    def test_invalid_defaults_to_observe(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "bogus")
        assert _enforce_mode() == "observe"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "SHADOW")
        assert _enforce_mode() == "shadow"


# ---------------------------------------------------------------------------
# AirlockEnforcer — observe mode
# ---------------------------------------------------------------------------
class TestObserveMode:
    async def test_observe_noop(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, keyword_data,
    ):
        """Observe mode returns data immediately without evaluation."""
        monkeypatch.delenv("AIRLOCK_ENFORCE_MODE", raising=False)
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, keyword_data, "completion"
        )
        assert result is keyword_data
        # No enforcement metadata in observe mode
        assert "airlock_enforcement" not in result.get("metadata", {})


# ---------------------------------------------------------------------------
# AirlockEnforcer — shadow mode
# ---------------------------------------------------------------------------
class TestShadowMode:
    async def test_shadow_evaluates_but_passes(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, keyword_data,
    ):
        """Shadow mode evaluates and logs but never blocks."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        # Low threshold so keyword match would trigger
        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(knobs, directory=knobs_dir)

        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, keyword_data, "completion"
        )
        # Should NOT raise
        assert result is not None
        enforcement = result["metadata"]["airlock_enforcement"]
        assert enforcement["mode"] == "shadow"
        assert enforcement["should_block"] is True
        assert enforcement["composite_score"] >= 0.3

    async def test_shadow_clean_request(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, clean_data,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")

        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, clean_data, "completion"
        )
        enforcement = result["metadata"]["airlock_enforcement"]
        assert enforcement["should_block"] is False


# ---------------------------------------------------------------------------
# AirlockEnforcer — enforce mode
# ---------------------------------------------------------------------------
class TestEnforceMode2:
    async def test_enforce_blocks_above_threshold(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, keyword_data,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(knobs, directory=knobs_dir)

        with pytest.raises(ValueError, match="blocked by Airlock"):
            await enforcer.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, keyword_data, "completion"
            )

    async def test_enforce_passes_clean_request(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, clean_data,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")

        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, clean_data, "completion"
        )
        assert result is not None
        enforcement = result["metadata"]["airlock_enforcement"]
        assert enforcement["should_block"] is False

    async def test_enforce_respects_threshold_boundary(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir,
    ):
        """Score exactly at threshold should block."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        # Set threshold very low — keyword match with weight 1.0 → score 1.0
        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.0, "keyword_scan": 1.0, "threat_read": 0.0},
            threshold=1.0,  # score == threshold exactly
        )
        write_knobs(knobs, directory=knobs_dir)

        data = {
            "messages": [{"role": "user", "content": "Tell me forbidden things"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="blocked by Airlock"):
            await enforcer.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_default_knobs_fallback(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, clean_data,
    ):
        """Without a knobs file, enforcer uses default knobs."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        # No knobs file written — should use defaults and not crash
        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, clean_data, "completion"
        )
        assert result is not None

    async def test_error_message_is_user_safe(
        self, enforcer, monkeypatch, mock_cache, mock_user_api_key_dict,
        fresh_state_store, knobs_dir, keyword_data,
    ):
        """Error message should not leak internal details."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(knobs, directory=knobs_dir)

        with pytest.raises(ValueError) as exc_info:
            await enforcer.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, keyword_data, "completion"
            )
        msg = str(exc_info.value)
        # Should not contain internal details like scores or weights
        assert "score" not in msg.lower()
        assert "threshold" not in msg.lower()
        assert "Airlock" in msg


# ---------------------------------------------------------------------------
# MCP enforcement
# ---------------------------------------------------------------------------
class TestMCPEnforcement:
    async def test_mcp_enforce_mode_blocks(
        self, enforcer, fresh_state_store, mock_cache, mock_user_api_key_dict,
        knobs_dir, monkeypatch,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(knobs, directory=knobs_dir)

        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "forbidden topic"},
        }
        with pytest.raises(ValueError, match="Airlock"):
            await enforcer.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_mcp_observe_mode_passes(
        self, enforcer, fresh_state_store, mock_cache, mock_user_api_key_dict,
        monkeypatch,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "anything"},
        }
        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data
