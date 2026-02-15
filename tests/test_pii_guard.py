"""Tests for airlock/guardrails/pii_guard.py"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from airlock.guardrails.pii_guard import (
    AirlockPIIGuard,
    _configured_entities,
    _scrub_messages,
    _scrub_text,
)


# ---------------------------------------------------------------------------
# _configured_entities()
# ---------------------------------------------------------------------------
class TestConfiguredEntities:
    def test_defaults(self):
        entities = _configured_entities()
        assert set(entities) == {"CREDIT_CARD", "US_SSN", "EMAIL_ADDRESS", "PHONE_NUMBER"}

    def test_custom_entities(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_ENTITIES", "PERSON,LOCATION")
        assert _configured_entities() == ["PERSON", "LOCATION"]

    def test_whitespace_trimming(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_ENTITIES", " PERSON , LOCATION ")
        assert _configured_entities() == ["PERSON", "LOCATION"]

    def test_empty_parts_filtered(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_ENTITIES", "PERSON,,LOCATION,")
        assert _configured_entities() == ["PERSON", "LOCATION"]

    def test_single_entity(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_ENTITIES", "US_SSN")
        assert _configured_entities() == ["US_SSN"]


# ---------------------------------------------------------------------------
# _scrub_text() — requires Presidio
# ---------------------------------------------------------------------------
class TestScrubText:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    def test_email_redacted(self):
        result = _scrub_text("Email me at john.doe@example.com")
        assert "john.doe@example.com" not in result

    def test_credit_card_redacted(self):
        result = _scrub_text("Card number 4111111111111111")
        assert "4111111111111111" not in result

    def test_phone_redacted(self):
        result = _scrub_text("Call me at 555-123-4567")
        assert "555-123-4567" not in result

    def test_safe_text_unchanged(self):
        text = "What is the capital of France?"
        assert _scrub_text(text) == text

    def test_code_snippet_unchanged(self):
        text = "def hello():\n    print('Hello, World!')"
        assert _scrub_text(text) == text


# ---------------------------------------------------------------------------
# _scrub_messages() — requires Presidio
# ---------------------------------------------------------------------------
class TestScrubMessages:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    def test_string_content_scrubbed(self):
        messages = [{"role": "user", "content": "Email me at alice@corp.com"}]
        result = _scrub_messages(messages)
        assert "alice@corp.com" not in result[0]["content"]

    def test_multipart_text_scrubbed_image_preserved(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Contact alice@corp.com please"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                ],
            }
        ]
        result = _scrub_messages(messages)
        assert "alice@corp.com" not in result[0]["content"][0]["text"]
        assert result[0]["content"][1]["type"] == "image_url"
        assert result[0]["content"][1]["image_url"]["url"] == "https://example.com/img.png"

    def test_empty_messages(self):
        assert _scrub_messages([]) == []

    def test_missing_content_field(self):
        messages = [{"role": "system"}]
        result = _scrub_messages(messages)
        assert result == [{"role": "system"}]

    def test_multiple_messages_only_pii_scrubbed(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "My email is test@example.com"},
        ]
        result = _scrub_messages(messages)
        assert result[0]["content"] == "You are helpful."
        assert "test@example.com" not in result[1]["content"]

    def test_original_messages_not_mutated(self):
        messages = [{"role": "user", "content": "Contact alice@corp.com"}]
        original_content = messages[0]["content"]
        _scrub_messages(messages)
        assert messages[0]["content"] == original_content


# ---------------------------------------------------------------------------
# Graceful degradation when Presidio not installed
# ---------------------------------------------------------------------------
class TestGracefulDegradation:
    def test_module_loads_without_presidio(self):
        """The pii_guard module imports successfully even without Presidio."""
        import airlock.guardrails.pii_guard as mod

        assert hasattr(mod, "AirlockPIIGuard")
        assert hasattr(mod, "_scrub_text")

    def test_get_presidio_raises_when_unavailable(self, reset_presidio_singletons):
        """_get_presidio raises ImportError when Presidio can't be imported."""
        import airlock.guardrails.pii_guard as mod

        with patch.dict(
            "sys.modules",
            {"presidio_analyzer": None, "presidio_anonymizer": None},
        ):
            mod._analyzer = None
            mod._anonymizer = None
            with pytest.raises(ImportError):
                mod._get_presidio()


# ---------------------------------------------------------------------------
# AirlockPIIGuard.async_pre_call_hook()
# ---------------------------------------------------------------------------
class TestAsyncPreCallHook:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    async def test_hook_scrubs_pii_and_returns_data(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {
            "messages": [{"role": "user", "content": "Contact alice@corp.com"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "alice@corp.com" not in str(result["messages"])
        assert result["model"] == "claude-sonnet"

    async def test_hook_no_messages(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {"model": "claude-sonnet"}
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result == data

    async def test_hook_empty_messages(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {"messages": [], "model": "claude-sonnet"}
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result["messages"] == []
