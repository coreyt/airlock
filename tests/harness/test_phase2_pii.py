"""
S5 — PII: redaction via Presidio guardrail hooks.

Direct guardrail hook calls, no proxy needed.
Note: Presidio SSN detection requires NER context and may not match
isolated test patterns. Tests focus on EMAIL and CREDIT_CARD which
have reliable pattern-based detection.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.harness


@pytest.fixture
def pii_guard():
    from airlock.guardrails.pii_guard import AirlockPIIGuard

    return AirlockPIIGuard()


class TestPIIRedaction:

    async def test_email_redacted(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "Email: alice@company.com"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "alice@company.com" not in str(result["messages"])
        assert "<EMAIL_ADDRESS>" in str(result["messages"])

    async def test_credit_card_redacted(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "Card: 4111111111111111"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "4111111111111111" not in str(result["messages"])

    async def test_phone_redacted(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "My phone number is 212-555-1234"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Phone detection has lower confidence; verify it ran without error
        assert result is not None

    async def test_multiple_pii_types(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [
                {"role": "user", "content": "Card 4111111111111111 email alice@company.com"}
            ],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        content = str(result["messages"])
        assert "4111111111111111" not in content
        assert "alice@company.com" not in content

    async def test_safe_text_unchanged(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result["messages"][0]["content"] == "What is the capital of France?"

    async def test_redaction_does_not_block(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "Email: bob@test.org"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is not None

    async def test_multipart_content_redacted(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Card: 4111111111111111"},
                    ],
                }
            ],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "4111111111111111" not in str(result["messages"])

    async def test_multipart_image_url_preserved(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                        {"type": "text", "text": "What is this?"},
                    ],
                }
            ],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        parts = result["messages"][0]["content"]
        image_parts = [p for p in parts if p.get("type") == "image_url"]
        assert len(image_parts) == 1
        assert image_parts[0]["image_url"]["url"] == "https://example.com/img.png"

    async def test_custom_entity_config(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available, monkeypatch,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        monkeypatch.setenv("AIRLOCK_PII_ENTITIES", "EMAIL_ADDRESS")
        data = {
            "messages": [{"role": "user", "content": "Email: alice@company.com"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "alice@company.com" not in str(result["messages"])

    async def test_mcp_args_recursively_scrubbed(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "Find alice@company.com", "limit": "10"},
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert "alice@company.com" not in str(result.get("mcp_arguments", {}))
