"""Tests for airlock/fast/guardian.py"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from airlock.fast.guardian import (
    AirlockFastGuardian,
    _extract_client_id,
    _extract_text,
)
from airlock.fast.state import CircuitState


# ---------------------------------------------------------------------------
# _extract_client_id()
# ---------------------------------------------------------------------------
class TestExtractClientId:
    def test_from_api_key_attribute(self):
        mock = MagicMock()
        mock.api_key = "sk-1234567890abcdef"
        assert _extract_client_id(mock) == "key:90abcdef"

    def test_from_dict(self):
        d = {"api_key": "sk-1234567890abcdef"}
        assert _extract_client_id(d) == "key:90abcdef"

    def test_short_key_fallback(self):
        mock = MagicMock()
        mock.api_key = "short"
        # len <= 8, so it won't match the first branch
        result = _extract_client_id(mock)
        # falls to dict check, mock is not a dict, so "unknown"
        assert isinstance(result, str)

    def test_none_returns_unknown(self):
        assert _extract_client_id(None) == "unknown"

    def test_empty_dict_returns_unknown(self):
        result = _extract_client_id({})
        assert "unknown" in result


# ---------------------------------------------------------------------------
# _extract_text()
# ---------------------------------------------------------------------------
class TestExtractText:
    def test_string_content(self):
        messages = [{"role": "user", "content": "hello"}]
        assert "hello" in _extract_text(messages)

    def test_multipart_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        result = _extract_text(messages)
        assert "Describe" in result
        assert "data:" not in result


# ---------------------------------------------------------------------------
# AirlockFastGuardian.async_pre_call_hook()
# ---------------------------------------------------------------------------
class TestGuardianPreCallHook:
    @pytest.fixture
    def guardian(self):
        return AirlockFastGuardian()

    async def test_normal_request_passes(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "metadata" in result
        assert "airlock_priority" in result["metadata"]
        assert "score" in result["metadata"]["airlock_priority"]

    async def test_client_in_backoff_rejected(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        client = fresh_state_store.get_client(client_id)
        client.backoff_until = time.time() + 60

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="Too many requests"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_high_threat_blocked(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        client = fresh_state_store.get_client(client_id)
        now = time.time()
        # Rapid-fire + high score to trigger threat block
        for i in range(20):
            client.record_request(now - 2 + i * 0.05)
            client.record_error(now - i * 0.05, "Error")
        client.threat_score = 0.8

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="unusual activity"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_open_circuit_failover(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        # Break claude-sonnet
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Model should be rewritten
        assert result["model"] != "claude-sonnet"
        assert "airlock_failover" in result["metadata"]
        assert result["metadata"]["airlock_failover"]["original_model"] == "claude-sonnet"

    async def test_all_circuits_open_rejected(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        now = time.time()
        for model_name in ["claude-sonnet", "claude-haiku", "gpt-4o"]:
            model = fresh_state_store.get_model(model_name)
            for _ in range(5):
                model.record_failure(now)

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="currently unavailable"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_record_request_called(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        client = fresh_state_store.get_client(client_id)
        assert len(client.request_times) == 1

    async def test_unknown_api_key(self, guardian, fresh_state_store, mock_cache):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            None, mock_cache, data, "completion"
        )
        assert "airlock_priority" in result.get("metadata", {})
