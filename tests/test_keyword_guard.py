"""Tests for airlock/guardrails/keyword_guard.py"""

from __future__ import annotations

import pytest

from airlock.guardrails.keyword_guard import (
    AirlockKeywordGuard,
    _blocked_keywords,
)
from airlock.guardrails.extract import extract_text_from_messages as _extract_text


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

    def test_cache_identity_same_env(self, monkeypatch):
        """Repeated calls with the same env string return the same list object."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "foo,bar")
        first = _blocked_keywords()
        second = _blocked_keywords()
        assert first == ["foo", "bar"]
        assert first is second

    def test_cache_invalidates_on_env_change(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "foo,bar")
        assert _blocked_keywords() == ["foo", "bar"]
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "foo,bar,baz")
        assert _blocked_keywords() == ["foo", "bar", "baz"]


# ---------------------------------------------------------------------------
# _extract_text()
# ---------------------------------------------------------------------------
class TestExtractText:
    def test_string_content(self):
        messages = [{"role": "user", "content": "Hello world"}]
        assert "Hello world" in _extract_text(messages)

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
        assert "First part" in result
        assert "Second part" in result

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
        assert "Describe this" in result
        assert "image" not in result
        assert "base64" not in result

    def test_multiple_messages_joined(self):
        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Tell me about project-x"},
        ]
        result = _extract_text(messages)
        assert "Be helpful" in result
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


# ---------------------------------------------------------------------------
# MCP tool call tests
# ---------------------------------------------------------------------------
class TestMCPKeywordBlocking:
    async def test_keyword_in_tool_name_blocked(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        guard = AirlockKeywordGuard()
        data = {
            "mcp_tool_name": "get_secret_data",
            "mcp_arguments": {"key": "safe-value"},
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_keyword_in_mcp_arguments_blocked(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "project manhattan")
        guard = AirlockKeywordGuard()
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "Tell me about Project Manhattan"},
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_safe_mcp_call_passes(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        guard = AirlockKeywordGuard()
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/tmp/safe.txt"},
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data


# ---------------------------------------------------------------------------
# Unicode normalization bypass protection (P2 Fix #8)
# ---------------------------------------------------------------------------
class TestUnicodeNormalization:
    async def test_fullwidth_chars_blocked(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        """Fullwidth 'secret' (U+FF53 etc.) should still be blocked."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        guard = AirlockKeywordGuard()
        # Fullwidth Latin: ｓｅｃｒｅｔ
        fullwidth_secret = "\uff53\uff45\uff43\uff52\uff45\uff54"
        data = {
            "messages": [{"role": "user", "content": f"Tell me the {fullwidth_secret}"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_zero_width_chars_stripped(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        """Zero-width characters inserted into a keyword should not bypass."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        guard = AirlockKeywordGuard()
        # Insert zero-width spaces and joiners
        zwsp = "\u200b"
        text = f"f{zwsp}o{zwsp}r{zwsp}b{zwsp}i{zwsp}d{zwsp}d{zwsp}e{zwsp}n"
        data = {
            "messages": [{"role": "user", "content": text}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_non_breaking_space_normalized(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        """Non-breaking spaces should be treated as regular spaces."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "project x")
        guard = AirlockKeywordGuard()
        nbsp = "\u00a0"
        data = {
            "messages": [{"role": "user", "content": f"Tell me about project{nbsp}x"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_normal_text_still_passes(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        """Normal text without blocked keywords should still pass."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "What is Python?"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data


# ---------------------------------------------------------------------------
# AIRLOCK_KW_ENABLED env flag
# ---------------------------------------------------------------------------
class TestKeywordEnabledFlag:
    async def test_async_pre_call_hook_skipped_when_disabled(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "false")
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me the secret plan"}],
            "model": "claude-sonnet",
        }
        # Must NOT raise — guardrail is disabled.
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data

    async def test_async_pre_call_hook_enabled_by_default(
        self, monkeypatch, mock_cache, mock_user_api_key_dict
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        monkeypatch.delenv("AIRLOCK_KW_ENABLED", raising=False)
        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me the secret plan"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
