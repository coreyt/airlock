"""Tests for airlock/guardrails/observer.py"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from airlock.guardrails.extract import extract_text_from_messages as extract_text
from airlock.guardrails.observer import (
    AirlockObserver,
    collect_signals,
    scan_keywords,
    scan_pii,
    read_threat,
)


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------
class TestExtractText:
    def test_string_content(self):
        messages = [{"role": "user", "content": "Hello world"}]
        assert extract_text(messages) == "Hello world"

    def test_multipart_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part one"},
                    {"type": "image_url", "url": "http://example.com/img.png"},
                    {"type": "text", "text": "Part two"},
                ],
            }
        ]
        result = extract_text(messages)
        assert "Part one" in result
        assert "Part two" in result

    def test_empty_messages(self):
        assert extract_text([]) == ""

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User question"},
        ]
        result = extract_text(messages)
        assert "System prompt" in result
        assert "User question" in result


# ---------------------------------------------------------------------------
# scan_pii
# ---------------------------------------------------------------------------
class TestScanPii:
    def test_detects_email(self):
        signal = scan_pii("Contact alice@example.com for details")
        assert signal.detected is True
        assert signal.score > 0
        assert "EMAIL_ADDRESS" in signal.details["entities"]

    def test_detects_ssn(self):
        signal = scan_pii("SSN: 123-45-6789")
        assert signal.detected is True
        assert "US_SSN" in signal.details["entities"]

    def test_detects_phone(self):
        signal = scan_pii("Call me at (555) 123-4567")
        assert signal.detected is True
        assert "PHONE_NUMBER" in signal.details["entities"]

    def test_clean_text(self):
        signal = scan_pii("What is the capital of France?")
        assert signal.detected is False
        assert signal.score == 0.0
        assert signal.details["total_count"] == 0

    def test_score_scales_with_count(self):
        text = " ".join(f"user{i}@example.com" for i in range(6))
        signal = scan_pii(text)
        assert signal.score == 1.0  # 5+ entities → capped at 1.0

    def test_has_duration(self):
        signal = scan_pii("no pii here")
        assert signal.duration_ms >= 0


# ---------------------------------------------------------------------------
# scan_keywords
# ---------------------------------------------------------------------------
class TestScanKeywords:
    def test_detects_keyword(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden,secret project")
        signal = scan_keywords("Tell me about the forbidden zone")
        assert signal.detected is True
        assert signal.score == 1.0
        assert "forbidden" in signal.details["matched_keywords"]

    def test_no_keywords_set(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_BLOCKED_KEYWORDS", raising=False)
        signal = scan_keywords("anything goes here")
        assert signal.detected is False
        assert signal.score == 0.0

    def test_clean_text(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        signal = scan_keywords("What is the capital of France?")
        assert signal.detected is False
        assert signal.details["match_count"] == 0

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        signal = scan_keywords("This is FORBIDDEN content")
        assert signal.detected is True


# ---------------------------------------------------------------------------
# read_threat
# ---------------------------------------------------------------------------
class TestReadThreat:
    def test_reads_score_from_state(self, fresh_state_store, mock_user_api_key_dict):
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.threat_score = 0.3

        signal = read_threat(mock_user_api_key_dict)
        assert signal.score == 0.3
        assert signal.detected is False

    def test_high_threat_detected(self, fresh_state_store, mock_user_api_key_dict):
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.threat_score = 0.9

        signal = read_threat(mock_user_api_key_dict)
        assert signal.detected is True
        assert signal.score == 0.9

    def test_unknown_client(self, fresh_state_store):
        signal = read_threat(None)
        assert signal.score == 0.0
        assert signal.detected is False


# ---------------------------------------------------------------------------
# collect_signals
# ---------------------------------------------------------------------------
class TestCollectSignals:
    def test_returns_three_signals(self, fresh_state_store, mock_user_api_key_dict):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        signals = collect_signals(data, mock_user_api_key_dict)
        assert len(signals) == 3
        names = {s.guardrail_name for s in signals}
        assert names == {"pii_scan", "keyword_scan", "threat_read"}


# ---------------------------------------------------------------------------
# AirlockObserver
# ---------------------------------------------------------------------------
class TestAirlockObserver:
    @pytest.fixture
    def observer(self):
        return AirlockObserver()

    async def test_attaches_metadata(
        self, observer, fresh_state_store, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello world"}],
            "model": "claude-sonnet",
        }
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        obs = data["metadata"]["airlock_observation"]
        assert obs["model"] == "claude-sonnet"
        assert len(obs["signals"]) == 3

    async def test_detects_pii_in_metadata(
        self, observer, fresh_state_store, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Email: alice@example.com"}],
            "model": "claude-sonnet",
        }
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        obs = data["metadata"]["airlock_observation"]
        pii_signal = next(s for s in obs["signals"] if s["guardrail_name"] == "pii_scan")
        assert pii_signal["detected"] is True

    async def test_detects_keywords_in_metadata(
        self, observer, monkeypatch, fresh_state_store, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        data = {
            "messages": [{"role": "user", "content": "Tell me forbidden things"}],
            "model": "claude-sonnet",
        }
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        obs = data["metadata"]["airlock_observation"]
        kw_signal = next(s for s in obs["signals"] if s["guardrail_name"] == "keyword_scan")
        assert kw_signal["detected"] is True

    async def test_never_raises(
        self, observer, fresh_state_store, mock_user_api_key_dict
    ):
        """Observer must never raise — even on internal errors."""
        data = {
            "messages": "not-a-list",  # deliberately broken
            "model": "claude-sonnet",
        }
        # Should not raise
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

    async def test_includes_threat_score(
        self, observer, fresh_state_store, mock_user_api_key_dict
    ):
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.threat_score = 0.5

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        obs = data["metadata"]["airlock_observation"]
        threat_signal = next(s for s in obs["signals"] if s["guardrail_name"] == "threat_read")
        assert threat_signal["score"] == 0.5

    async def test_handles_empty_messages(
        self, observer, fresh_state_store, mock_user_api_key_dict
    ):
        data = {"messages": [], "model": "claude-sonnet"}
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        obs = data["metadata"]["airlock_observation"]
        assert len(obs["signals"]) == 3
        pii_signal = next(s for s in obs["signals"] if s["guardrail_name"] == "pii_scan")
        assert pii_signal["detected"] is False


# ---------------------------------------------------------------------------
# MCP observation
# ---------------------------------------------------------------------------
class TestMCPObservation:
    @pytest.fixture
    def observer(self):
        return AirlockObserver()

    async def test_mcp_call_observed(
        self, observer, fresh_state_store, mock_user_api_key_dict, monkeypatch
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "find secret docs"},
            "model": "unknown",
        }
        await observer.async_moderation_hook(
            data, mock_user_api_key_dict, "call_mcp_tool"
        )

        obs = data["metadata"]["airlock_observation"]
        assert len(obs["signals"]) == 3
        kw_signal = next(
            s for s in obs["signals"] if s["guardrail_name"] == "keyword_scan"
        )
        assert kw_signal["detected"] is True

    async def test_mcp_pii_in_arguments(
        self, observer, fresh_state_store, mock_user_api_key_dict
    ):
        data = {
            "mcp_tool_name": "send_email",
            "mcp_arguments": {"to": "user@example.com", "body": "Hello"},
            "model": "unknown",
        }
        await observer.async_moderation_hook(
            data, mock_user_api_key_dict, "call_mcp_tool"
        )

        obs = data["metadata"]["airlock_observation"]
        pii_signal = next(
            s for s in obs["signals"] if s["guardrail_name"] == "pii_scan"
        )
        assert pii_signal["detected"] is True


# ---------------------------------------------------------------------------
# AIRLOCK_PII_ENABLED / AIRLOCK_KW_ENABLED env flags
# ---------------------------------------------------------------------------
class TestObserverEnabledFlags:
    def test_scan_pii_returns_neutral_when_disabled(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_ENABLED", "false")
        signal = scan_pii("Contact alice@example.com or call 123-45-6789")
        assert signal.detected is False
        assert signal.score == 0.0
        assert signal.details["total_count"] == 0
        assert signal.details["entities"] == {}

    def test_scan_pii_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_PII_ENABLED", raising=False)
        signal = scan_pii("Contact alice@example.com")
        assert signal.detected is True

    def test_scan_keywords_returns_neutral_when_disabled(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "false")
        signal = scan_keywords("the secret plan")
        assert signal.detected is False
        assert signal.score == 0.0
        assert signal.details["match_count"] == 0
        assert signal.details["matched_keywords"] == []

    def test_scan_keywords_enabled_by_default(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        monkeypatch.delenv("AIRLOCK_KW_ENABLED", raising=False)
        signal = scan_keywords("the secret plan")
        assert signal.detected is True
