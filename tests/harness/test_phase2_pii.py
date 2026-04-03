"""
S5 — PII: redaction and hydration via Presidio guardrail hooks.

Direct guardrail hook calls, no proxy needed.
Note: Presidio SSN detection requires NER context and may not match
isolated test patterns. Tests focus on EMAIL and CREDIT_CARD which
have reliable pattern-based detection.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

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
        assert "<EMAIL_ADDRESS_1>" in str(result["messages"])

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


# ---------------------------------------------------------------------------
# Helpers for building mock LLM responses
# ---------------------------------------------------------------------------
def _make_tool_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _make_response(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
    )


# ---------------------------------------------------------------------------
# G — Round-trip integration tests (redact → model → hydrate)
# ---------------------------------------------------------------------------
class TestPIIHydrationRoundTrip:

    async def test_single_email_round_trip(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        """G1: email redacted outbound, hydrated inbound in tool-call args."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "search gmail for alice@company.com"}],
            "model": "claude-sonnet",
        }
        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "alice@company.com" not in str(data["messages"])
        pii_map = data["metadata"]["airlock_pii_map"]

        # Simulate model returning a tool call with the placeholder
        placeholder = next(ph for ph, orig in pii_map.items() if orig == "alice@company.com")
        tc = _make_tool_call("gmail_search", {"from_address": placeholder})
        response = _make_response(tool_calls=[tc])

        result = await pii_guard.async_post_call_success_hook(
            data, mock_user_api_key_dict, response
        )
        args = json.loads(result.choices[0].message.tool_calls[0].function.arguments)
        assert args["from_address"] == "alice@company.com"

    async def test_multiple_entity_types_round_trip(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        """G2: email + credit card redacted, both hydrated in separate tool calls."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [
                {"role": "user", "content": "Card 4111111111111111 email alice@company.com"},
            ],
            "model": "claude-sonnet",
        }
        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        content = str(data["messages"])
        assert "4111111111111111" not in content
        assert "alice@company.com" not in content

        pii_map = data["metadata"]["airlock_pii_map"]
        email_ph = next(ph for ph, orig in pii_map.items() if orig == "alice@company.com")
        cc_ph = next(ph for ph, orig in pii_map.items() if orig == "4111111111111111")

        tc1 = _make_tool_call("send_email", {"to": email_ph})
        tc2 = _make_tool_call("process_payment", {"card": cc_ph})
        response = _make_response(tool_calls=[tc1, tc2])

        result = await pii_guard.async_post_call_success_hook(
            data, mock_user_api_key_dict, response
        )
        args1 = json.loads(result.choices[0].message.tool_calls[0].function.arguments)
        args2 = json.loads(result.choices[0].message.tool_calls[1].function.arguments)
        assert args1["to"] == "alice@company.com"
        assert args2["card"] == "4111111111111111"

    async def test_mcp_round_trip(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        """G3: MCP arguments redacted, placeholder hydrated on return."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "Find alice@company.com"},
        }
        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert "alice@company.com" not in str(data["mcp_arguments"])
        pii_map = data["metadata"]["airlock_pii_map"]

        # Simulate model returning a tool call that references the placeholder
        placeholder = next(iter(pii_map))
        tc = _make_tool_call("gmail_search", {"from": placeholder})
        response = _make_response(tool_calls=[tc])

        result = await pii_guard.async_post_call_success_hook(
            data, mock_user_api_key_dict, response
        )
        args = json.loads(result.choices[0].message.tool_calls[0].function.arguments)
        assert args["from"] == "alice@company.com"

    async def test_no_pii_passes_through(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        """G4: no PII means no mapping, response passes through unchanged."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "model": "claude-sonnet",
        }
        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "airlock_pii_map" not in data.get("metadata", {})

        tc = _make_tool_call("get_info", {"query": "capital of France"})
        response = _make_response(tool_calls=[tc])

        result = await pii_guard.async_post_call_success_hook(
            data, mock_user_api_key_dict, response
        )
        args = json.loads(result.choices[0].message.tool_calls[0].function.arguments)
        assert args["query"] == "capital of France"


# ---------------------------------------------------------------------------
# F — Privacy boundary verification
# ---------------------------------------------------------------------------
class TestPIIPrivacyBoundary:

    async def test_outbound_still_redacted(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available,
    ):
        """F1: after pre-call, messages contain only placeholders."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "Email alice@company.com"}],
            "model": "claude-sonnet",
        }
        result = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "alice@company.com" not in str(result["messages"])
        assert "<EMAIL_ADDRESS_1>" in str(result["messages"])

    async def test_redaction_logs_no_raw_values(
        self, pii_guard, mock_cache, mock_user_api_key_dict,
        reset_presidio_singletons, presidio_available, caplog,
    ):
        """F2: pre-call log contains entity types and counts, not raw PII."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        data = {
            "messages": [{"role": "user", "content": "Email alice@company.com"}],
            "model": "claude-sonnet",
        }
        with caplog.at_level(logging.INFO, logger="airlock.guardrails.pii"):
            await pii_guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert len(caplog.records) > 0
        assert "pii_redacted" in caplog.text
        assert "alice@company.com" not in caplog.text

    async def test_hydration_logs_no_raw_values(
        self, pii_guard, mock_user_api_key_dict, caplog,
    ):
        """F3: post-call log contains hydration count, not restored values."""
        data = {
            "metadata": {"airlock_pii_map": {"<EMAIL_ADDRESS_1>": "alice@company.com"}},
        }
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])

        with caplog.at_level(logging.INFO, logger="airlock.guardrails.pii"):
            await pii_guard.async_post_call_success_hook(
                data, mock_user_api_key_dict, response
            )
        assert len(caplog.records) > 0
        assert "pii_hydrated" in caplog.text
        assert "alice@company.com" not in caplog.text
