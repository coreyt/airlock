"""Tests for airlock/guardrails/keyword_guard.py"""

from __future__ import annotations

import pytest

from airlock.guardrails.keyword_guard import (
    AirlockKeywordGuard,
    _blocked_keywords,
    _extract_text,
)


# ---------------------------------------------------------------------------
# _blocked_keywords()
# ---------------------------------------------------------------------------
class TestBlockedKeywords:
    def test_no_env_var_returns_empty(self):
        assert _blocked_keywords() == []

    def test_single_keyword(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "project-x")
        assert _blocked_keywords() == ["project-x"]

    def test_multiple_keywords(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "alpha,bravo,charlie")
        assert _blocked_keywords() == ["alpha", "bravo", "charlie"]

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", " alpha , bravo ")
        assert _blocked_keywords() == ["alpha", "bravo"]

    def test_empty_parts_filtered(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "alpha,,bravo,")
        assert _blocked_keywords() == ["alpha", "bravo"]

    def test_lowercased(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "Project-X,SECRET")
        assert _blocked_keywords() == ["project-x", "secret"]


# ---------------------------------------------------------------------------
# _extract_text()
# ---------------------------------------------------------------------------
class TestExtractText:
    def test_string_content(self):
        messages = [{"role": "user", "content": "Hello world"}]
        assert "hello world" in _extract_text(messages)

    def test_multipart_text(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "First part"},
                    {"type": "text", "text": "Second part"},
                ],
            }
        ]
        result = _extract_text(messages)
        assert "first part" in result
        assert "second part" in result

    def test_image_parts_ignored(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        result = _extract_text(messages)
        assert "describe this" in result
        assert "image" not in result
        assert "base64" not in result

    def test_multiple_messages_joined(self):
        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Tell me about project-x"},
        ]
        result = _extract_text(messages)
        assert "be helpful" in result
        assert "project-x" in result

    def test_empty_messages(self):
        assert _extract_text([]) == ""

    def test_missing_content(self):
        messages = [{"role": "system"}]
        assert _extract_text(messages) == ""


# ---------------------------------------------------------------------------
# AirlockKeywordGuard.async_pre_call_hook()
# ---------------------------------------------------------------------------
class TestAsyncPreCallHook:
    async def test_no_keywords_configured_passes(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me anything"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data

    async def test_keyword_match_raises(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "project-x")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me about project-x"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_case_insensitive_match(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me the SECRET plan"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_substring_match(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "topsecretinfo"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_error_message_does_not_echo_keyword(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified-codename")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "classified-codename details"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content") as exc_info:
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert "classified-codename" not in str(exc_info.value)

    async def test_multipart_text_scanned(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "This has forbidden content"},
                        {"type": "image_url", "image_url": {"url": "https://example.com"}},
                    ],
                }
            ],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_safe_text_passes(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden,secret")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data

    async def test_no_messages_passes(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        guard = AirlockKeywordGuard()
        data = {"model": "claude-sonnet"}
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data

    async def test_multiple_keywords_any_triggers(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "alpha,bravo,charlie")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me about bravo team"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
