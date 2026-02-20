"""Tests for airlock/guardrails/orchestrator.py"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import patch

import pytest

from airlock.guardrails.orchestrator import (
    AirlockOrchestrator,
    _get_knobs,
    _invalidate_knobs_cache,
    evaluate,
)
from airlock.guardrails.schemas import (
    GuardrailKnobs,
    GuardrailSignal,
    default_knobs,
)
from airlock.slow.tuner import write_knobs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clear_knobs_cache():
    """Ensure knobs cache is clear before each test."""
    _invalidate_knobs_cache()
    yield
    _invalidate_knobs_cache()


@pytest.fixture
def knobs_dir(tmp_path, monkeypatch):
    """Point tuner LOG_DIR at tmp_path for loading."""
    import airlock.slow.tuner as tuner_mod

    monkeypatch.setattr(tuner_mod, "LOG_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------
class TestEvaluate:
    def test_weighted_average(self):
        signals = [
            GuardrailSignal("pii_scan", True, 0.8, {}, 1.0),
            GuardrailSignal("keyword_scan", False, 0.0, {}, 1.0),
            GuardrailSignal("threat_read", False, 0.2, {}, 1.0),
        ]
        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.5, "keyword_scan": 0.3, "threat_read": 0.2},
            threshold=0.5,
        )
        score = evaluate(signals, knobs)
        # (0.8*0.5 + 0.0*0.3 + 0.2*0.2) / (0.5+0.3+0.2) = 0.44
        assert score == pytest.approx(0.44, abs=0.01)

    def test_all_zero_weights(self):
        signals = [
            GuardrailSignal("pii_scan", True, 1.0, {}, 1.0),
        ]
        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.0},
            threshold=0.5,
        )
        score = evaluate(signals, knobs)
        assert score == 0.0

    def test_unknown_guardrail_ignored(self):
        signals = [
            GuardrailSignal("unknown_guard", True, 1.0, {}, 1.0),
        ]
        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.5},
            threshold=0.5,
        )
        score = evaluate(signals, knobs)
        assert score == 0.0

    def test_single_signal(self):
        signals = [
            GuardrailSignal("keyword_scan", True, 1.0, {}, 1.0),
        ]
        knobs = GuardrailKnobs(
            version="test",
            weights={"keyword_scan": 1.0},
            threshold=0.5,
        )
        score = evaluate(signals, knobs)
        assert score == 1.0


# ---------------------------------------------------------------------------
# _get_knobs() caching
# ---------------------------------------------------------------------------
class TestGetKnobs:
    def test_default_knobs_fallback(self, knobs_dir):
        """No knobs file → default knobs."""
        knobs = _get_knobs()
        assert knobs.version == "default"
        assert "pii_scan" in knobs.weights

    def test_loads_from_file(self, knobs_dir):
        custom = GuardrailKnobs(
            version="custom-v1",
            weights={"pii_scan": 0.6, "keyword_scan": 0.3, "threat_read": 0.1},
            threshold=0.7,
        )
        write_knobs(custom, directory=knobs_dir)

        knobs = _get_knobs()
        assert knobs.version == "custom-v1"
        assert knobs.threshold == 0.7

    def test_ttl_caching(self, knobs_dir):
        """Second call within TTL returns cached value."""
        _get_knobs()  # Populates cache with default

        # Write new knobs — but cache should still return the old one
        custom = GuardrailKnobs(
            version="new-version",
            weights={"pii_scan": 0.5, "keyword_scan": 0.3, "threat_read": 0.2},
            threshold=0.8,
        )
        write_knobs(custom, directory=knobs_dir)

        knobs = _get_knobs()
        # Should still be the default (cached)
        assert knobs.version == "default"

        # Invalidate and reload
        _invalidate_knobs_cache()
        knobs = _get_knobs()
        assert knobs.version == "new-version"


# ---------------------------------------------------------------------------
# AirlockOrchestrator
# ---------------------------------------------------------------------------
class TestAirlockOrchestrator:
    @pytest.fixture
    def orchestrator(self):
        return AirlockOrchestrator()

    async def test_attaches_metadata_with_composite_score(
        self, orchestrator, fresh_state_store, mock_user_api_key_dict, knobs_dir
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello world"}],
            "model": "claude-sonnet",
        }
        await orchestrator.async_moderation_hook(
            data, mock_user_api_key_dict, "completion"
        )

        obs = data["metadata"]["airlock_observation"]
        assert obs["composite_score"] is not None
        assert obs["would_block"] is not None
        assert obs["orchestrator_version"] is not None

    async def test_would_block_above_threshold(
        self, orchestrator, monkeypatch, fresh_state_store, mock_user_api_key_dict,
        knobs_dir,
    ):
        """Keyword match with high weight → would_block=True."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        # Write knobs with low threshold
        custom = GuardrailKnobs(
            version="test-block",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(custom, directory=knobs_dir)

        data = {
            "messages": [{"role": "user", "content": "Tell me forbidden stuff"}],
            "model": "claude-sonnet",
        }
        await orchestrator.async_moderation_hook(
            data, mock_user_api_key_dict, "completion"
        )

        obs = data["metadata"]["airlock_observation"]
        assert obs["would_block"] is True
        assert obs["composite_score"] >= 0.3

    async def test_clean_request_not_blocked(
        self, orchestrator, fresh_state_store, mock_user_api_key_dict, knobs_dir
    ):
        data = {
            "messages": [{"role": "user", "content": "What is Python?"}],
            "model": "claude-sonnet",
        }
        await orchestrator.async_moderation_hook(
            data, mock_user_api_key_dict, "completion"
        )

        obs = data["metadata"]["airlock_observation"]
        assert obs["would_block"] is False
        assert obs["composite_score"] < 0.5

    async def test_never_raises(
        self, orchestrator, fresh_state_store, mock_user_api_key_dict, knobs_dir
    ):
        """Orchestrator must never raise — even on internal errors."""
        data = {
            "messages": "not-a-list",
            "model": "claude-sonnet",
        }
        await orchestrator.async_moderation_hook(
            data, mock_user_api_key_dict, "completion"
        )

    async def test_uses_knobs_version_in_observation(
        self, orchestrator, fresh_state_store, mock_user_api_key_dict, knobs_dir
    ):
        custom = GuardrailKnobs(
            version="2024-01-15T10:00:00Z",
            weights={"pii_scan": 0.4, "keyword_scan": 0.4, "threat_read": 0.2},
            threshold=0.5,
        )
        write_knobs(custom, directory=knobs_dir)

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        await orchestrator.async_moderation_hook(
            data, mock_user_api_key_dict, "completion"
        )

        obs = data["metadata"]["airlock_observation"]
        assert obs["orchestrator_version"] == "2024-01-15T10:00:00Z"
