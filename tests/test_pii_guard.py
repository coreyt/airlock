"""Tests for airlock/guardrails/pii_guard.py"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from airlock.guardrails.pii_guard import (
    AirlockPIIGuard,
    _configured_entities,
    _hydrate_tool_calls,
    _hydrate_value_recursive,
    _hydration_enabled,
    _scrub_messages,
    _scrub_text,
    _scrub_text_with_mapping,
)


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


# ---------------------------------------------------------------------------
# MCP tool call PII tests
# ---------------------------------------------------------------------------
class TestMCPPIIScrubbing:
    async def test_mcp_arguments_scrubbed(
        self, mock_cache, mock_user_api_key_dict, reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        guard = AirlockPIIGuard()
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {
                "query": "Find records for user@example.com",
                "limit": "10",
            },
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        # Email in arguments should be redacted
        assert "user@example.com" not in result["mcp_arguments"]["query"]
        # Non-PII argument should be untouched
        assert result["mcp_arguments"]["limit"] == "10"

    async def test_mcp_nested_arguments_scrubbed(
        self, mock_cache, mock_user_api_key_dict, reset_presidio_singletons, presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        guard = AirlockPIIGuard()
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {
                "config": {"email": "user@example.com"},
                "tags": ["safe", "Contact: user@example.com"],
            },
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        # Nested dict PII should be redacted
        assert "user@example.com" not in result["mcp_arguments"]["config"]["email"]
        # Nested list PII should be redacted
        assert "user@example.com" not in result["mcp_arguments"]["tags"][1]
        # Non-PII values untouched
        assert result["mcp_arguments"]["tags"][0] == "safe"

    async def test_mcp_no_arguments_passes(
        self, mock_cache, mock_user_api_key_dict,
    ):
        guard = AirlockPIIGuard()
        data = {"mcp_tool_name": "list_tools"}
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data


# ---------------------------------------------------------------------------
# _scrub_text_with_mapping()
# ---------------------------------------------------------------------------
class TestScrubTextWithMapping:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    def test_single_email_numbered(self):
        mapping, counters = {}, {}
        result = _scrub_text_with_mapping("Email me at alice@corp.com", mapping, counters)
        assert "alice@corp.com" not in result
        assert "<EMAIL_ADDRESS_1>" in result
        assert mapping["<EMAIL_ADDRESS_1>"] == "alice@corp.com"

    def test_two_same_type_get_distinct_numbers(self):
        mapping, counters = {}, {}
        result = _scrub_text_with_mapping(
            "From alice@corp.com to bob@corp.com", mapping, counters,
        )
        assert "<EMAIL_ADDRESS_1>" in result
        assert "<EMAIL_ADDRESS_2>" in result
        assert len(mapping) == 2
        assert set(mapping.values()) == {"alice@corp.com", "bob@corp.com"}

    def test_mixed_entity_types(self):
        mapping, counters = {}, {}
        result = _scrub_text_with_mapping(
            "Email alice@corp.com card 4111111111111111", mapping, counters,
        )
        assert "<EMAIL_ADDRESS_1>" in result
        assert "alice@corp.com" not in result
        assert "4111111111111111" not in result
        # Credit card should get its own counter
        cc_keys = [k for k in mapping if "CREDIT_CARD" in k]
        email_keys = [k for k in mapping if "EMAIL_ADDRESS" in k]
        assert len(cc_keys) == 1
        assert len(email_keys) == 1

    def test_same_value_deduplicates(self):
        mapping, counters = {}, {}
        result = _scrub_text_with_mapping(
            "From alice@corp.com and again alice@corp.com", mapping, counters,
        )
        assert result.count("<EMAIL_ADDRESS_1>") == 2
        assert len(mapping) == 1
        assert mapping["<EMAIL_ADDRESS_1>"] == "alice@corp.com"

    def test_mapping_accumulates_across_calls(self):
        mapping, counters = {}, {}
        _scrub_text_with_mapping("Email alice@corp.com", mapping, counters)
        _scrub_text_with_mapping("Email bob@corp.com", mapping, counters)
        assert len(mapping) == 2
        assert mapping["<EMAIL_ADDRESS_1>"] == "alice@corp.com"
        assert mapping["<EMAIL_ADDRESS_2>"] == "bob@corp.com"

    def test_safe_text_no_mapping(self):
        mapping, counters = {}, {}
        result = _scrub_text_with_mapping("What is the capital of France?", mapping, counters)
        assert result == "What is the capital of France?"
        assert mapping == {}


# ---------------------------------------------------------------------------
# Mapping storage in request metadata
# ---------------------------------------------------------------------------
class TestMappingStorage:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    async def test_mapping_attached_after_redaction(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {
            "messages": [{"role": "user", "content": "Email alice@corp.com"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        pii_map = result.get("metadata", {}).get("airlock_pii_map")
        assert pii_map is not None
        assert isinstance(pii_map, dict)
        assert "<EMAIL_ADDRESS_1>" in pii_map
        assert pii_map["<EMAIL_ADDRESS_1>"] == "alice@corp.com"

    async def test_no_mapping_when_no_pii(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "airlock_pii_map" not in result.get("metadata", {})

    async def test_mapping_correct_for_multiple_entities(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {
            "messages": [
                {"role": "user", "content": "Email alice@corp.com card 4111111111111111"},
            ],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        pii_map = result["metadata"]["airlock_pii_map"]
        assert len(pii_map) == 2
        assert "alice@corp.com" in pii_map.values()
        assert "4111111111111111" in pii_map.values()

    async def test_preserves_existing_metadata(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {
            "messages": [{"role": "user", "content": "Email alice@corp.com"}],
            "model": "claude-sonnet",
            "metadata": {"airlock_other": True},
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result["metadata"]["airlock_other"] is True
        assert "airlock_pii_map" in result["metadata"]

    async def test_mapping_available_in_mcp_path(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "Find alice@corp.com"},
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        pii_map = result.get("metadata", {}).get("airlock_pii_map")
        assert pii_map is not None
        assert "alice@corp.com" in pii_map.values()


# ---------------------------------------------------------------------------
# _hydrate_value_recursive()
# ---------------------------------------------------------------------------
class TestHydrateValueRecursive:
    def test_embedded_placeholder_in_string(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        val, count = _hydrate_value_recursive("from:<EMAIL_ADDRESS_1> newer_than:7d", mapping)
        assert val == "from:alice@corp.com newer_than:7d"
        assert count == 1

    def test_nested_dict(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        val, count = _hydrate_value_recursive({"config": {"recipient": "<EMAIL_ADDRESS_1>"}}, mapping)
        assert val == {"config": {"recipient": "alice@corp.com"}}
        assert count == 1

    def test_nested_list(self):
        mapping = {
            "<EMAIL_ADDRESS_1>": "alice@corp.com",
            "<EMAIL_ADDRESS_2>": "bob@corp.com",
        }
        val, count = _hydrate_value_recursive(
            {"attendees": [{"email": "<EMAIL_ADDRESS_1>"}, {"email": "<EMAIL_ADDRESS_2>"}]},
            mapping,
        )
        assert val["attendees"][0]["email"] == "alice@corp.com"
        assert val["attendees"][1]["email"] == "bob@corp.com"
        assert count == 2

    def test_mixed_placeholders_and_literals(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        val, count = _hydrate_value_recursive(
            {"query": "<EMAIL_ADDRESS_1>", "limit": 10, "label": "inbox"},
            mapping,
        )
        assert val["query"] == "alice@corp.com"
        assert val["limit"] == 10
        assert val["label"] == "inbox"
        assert count == 1

    def test_empty_string(self):
        val, count = _hydrate_value_recursive("", {"<EMAIL_ADDRESS_1>": "alice@corp.com"})
        assert val == ""
        assert count == 0

    def test_empty_dict(self):
        val, count = _hydrate_value_recursive({}, {"<EMAIL_ADDRESS_1>": "alice@corp.com"})
        assert val == {}
        assert count == 0

    def test_depth_limit(self):
        # Build a structure nested 22 levels deep with a placeholder at the bottom
        value = "<EMAIL_ADDRESS_1>"
        for _ in range(22):
            value = {"nested": value}
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        result, count = _hydrate_value_recursive(value, mapping)
        # Walk down to the leaf — should still be the placeholder (depth > 20)
        node = result
        for _ in range(22):
            node = node["nested"]
        assert node == "<EMAIL_ADDRESS_1>"
        assert count == 0

    def test_no_matching_placeholder(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        val, count = _hydrate_value_recursive("<EMAIL_ADDRESS_99>", mapping)
        assert val == "<EMAIL_ADDRESS_99>"
        assert count == 0


# ---------------------------------------------------------------------------
# _hydrate_tool_calls()
# ---------------------------------------------------------------------------
class TestHydrateToolCalls:
    def test_single_placeholder_hydrated(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        tc = _make_tool_call("gmail_search", {"from_address": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        count = _hydrate_tool_calls(response, mapping)
        assert count == 1
        assert json.loads(tc.function.arguments)["from_address"] == "alice@corp.com"

    def test_multiple_placeholders(self):
        mapping = {
            "<EMAIL_ADDRESS_1>": "alice@corp.com",
            "<EMAIL_ADDRESS_2>": "bob@corp.com",
        }
        tc = _make_tool_call("send_email", {
            "to": "<EMAIL_ADDRESS_1>",
            "cc": "<EMAIL_ADDRESS_2>",
        })
        response = _make_response(tool_calls=[tc])
        count = _hydrate_tool_calls(response, mapping)
        assert count == 2
        args = json.loads(tc.function.arguments)
        assert args["to"] == "alice@corp.com"
        assert args["cc"] == "bob@corp.com"

    def test_prose_not_hydrated(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        response = _make_response(content="Contact <EMAIL_ADDRESS_1> for help")
        count = _hydrate_tool_calls(response, mapping)
        assert count == 0
        assert response.choices[0].message.content == "Contact <EMAIL_ADDRESS_1> for help"

    def test_no_tool_calls_passes_through(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        response = _make_response(content="Hello")
        count = _hydrate_tool_calls(response, mapping)
        assert count == 0

    def test_multiple_tool_calls(self):
        mapping = {
            "<EMAIL_ADDRESS_1>": "alice@corp.com",
            "<EMAIL_ADDRESS_2>": "bob@corp.com",
        }
        tc1 = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        tc2 = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_2>"})
        response = _make_response(tool_calls=[tc1, tc2])
        count = _hydrate_tool_calls(response, mapping)
        assert count == 2
        assert json.loads(tc1.function.arguments)["from"] == "alice@corp.com"
        assert json.loads(tc2.function.arguments)["from"] == "bob@corp.com"

    def test_no_placeholders_unchanged(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        tc = _make_tool_call("gmail_search", {"query": "inbox", "limit": 10})
        original_args = tc.function.arguments
        response = _make_response(tool_calls=[tc])
        count = _hydrate_tool_calls(response, mapping)
        assert count == 0
        assert tc.function.arguments == original_args

    def test_malformed_json_skipped(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        tc = SimpleNamespace(
            function=SimpleNamespace(name="bad_tool", arguments="not valid json {"),
        )
        response = _make_response(tool_calls=[tc])
        count = _hydrate_tool_calls(response, mapping)
        assert count == 0
        assert tc.function.arguments == "not valid json {"

    def test_empty_arguments_skipped(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        tc = SimpleNamespace(
            function=SimpleNamespace(name="no_args", arguments=""),
        )
        response = _make_response(tool_calls=[tc])
        count = _hydrate_tool_calls(response, mapping)
        assert count == 0

    def test_none_response(self):
        assert _hydrate_tool_calls(None, {"<EMAIL_ADDRESS_1>": "x"}) == 0

    def test_response_without_choices(self):
        assert _hydrate_tool_calls(SimpleNamespace(), {"<EMAIL_ADDRESS_1>": "x"}) == 0


# ---------------------------------------------------------------------------
# async_post_call_success_hook round-trip
# ---------------------------------------------------------------------------
class TestPostCallSuccessHook:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    async def test_round_trip_single_email(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        # Pre-call: redact
        data = {
            "messages": [{"role": "user", "content": "search gmail for alice@corp.com"}],
            "model": "claude-sonnet",
        }
        data = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "alice@corp.com" not in str(data["messages"])
        pii_map = data["metadata"]["airlock_pii_map"]

        # Simulate model returning a tool call with the placeholder
        placeholder = next(iter(pii_map))
        tc = _make_tool_call("gmail_search", {"from_address": placeholder})
        response = _make_response(tool_calls=[tc])

        # Post-call: hydrate
        result = await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        args = json.loads(result.choices[0].message.tool_calls[0].function.arguments)
        assert args["from_address"] == "alice@corp.com"

    async def test_no_mapping_passes_through(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {"metadata": {}}
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        result = await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        # Placeholder left as-is
        assert json.loads(result.choices[0].message.tool_calls[0].function.arguments)["from"] == "<EMAIL_ADDRESS_1>"

    async def test_empty_mapping_passes_through(self, mock_cache, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {"metadata": {"airlock_pii_map": {}}}
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        result = await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        assert json.loads(result.choices[0].message.tool_calls[0].function.arguments)["from"] == "<EMAIL_ADDRESS_1>"


# ---------------------------------------------------------------------------
# Hydration config toggle
# ---------------------------------------------------------------------------
class TestHydrationConfig:
    def test_default_enabled(self):
        assert _hydration_enabled() is True

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_HYDRATION", "off")
        assert _hydration_enabled() is False

    def test_case_insensitive_off(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_HYDRATION", "OFF")
        assert _hydration_enabled() is False

    def test_tools_explicit(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_HYDRATION", "tools")
        assert _hydration_enabled() is True

    async def test_hook_skips_when_disabled(self, monkeypatch, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_PII_HYDRATION", "off")
        guard = AirlockPIIGuard()
        data = {"metadata": {"airlock_pii_map": {"<EMAIL_ADDRESS_1>": "alice@corp.com"}}}
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        result = await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        # Should NOT hydrate
        assert json.loads(result.choices[0].message.tool_calls[0].function.arguments)["from"] == "<EMAIL_ADDRESS_1>"


# ---------------------------------------------------------------------------
# Phase 3: Failure modes and edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    async def test_sequential_requests_have_independent_mappings(
        self, mock_cache, mock_user_api_key_dict,
        presidio_available, reset_presidio_singletons,
    ):
        """I6: separate pre-call hooks produce independent mappings."""
        if not presidio_available:
            pytest.skip("Presidio not available")
        guard = AirlockPIIGuard()

        data_a = {
            "messages": [{"role": "user", "content": "Email alice@corp.com"}],
            "model": "claude-sonnet",
        }
        data_b = {
            "messages": [{"role": "user", "content": "Email bob@other.org"}],
            "model": "claude-sonnet",
        }

        result_a = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data_a, "completion"
        )
        result_b = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data_b, "completion"
        )

        map_a = result_a["metadata"]["airlock_pii_map"]
        map_b = result_b["metadata"]["airlock_pii_map"]

        assert map_a is not map_b

    async def test_presidio_unavailable_full_round_trip(self, mock_user_api_key_dict,
                                                         reset_presidio_singletons):
        """I7: when Presidio is not installed, pre-call raises ImportError,
        no mapping is stored, and post-call passes response through unchanged."""
        import airlock.guardrails.pii_guard as pii_mod

        pii_mod._analyzer = None
        pii_mod._anonymizer = None

        guard = AirlockPIIGuard()
        data = {
            "messages": [{"role": "user", "content": "Email alice@corp.com"}],
            "model": "claude-sonnet",
        }

        with patch.dict(
            "sys.modules",
            {"presidio_analyzer": None, "presidio_anonymizer": None},
        ):
            with pytest.raises(ImportError):
                await guard.async_pre_call_hook(
                    mock_user_api_key_dict, MagicMock(), data, "completion"
                )

        # Pre-call crashed, so data is unmodified — no mapping, content unchanged
        assert "airlock_pii_map" not in data.get("metadata", {})
        assert data["messages"][0]["content"] == "Email alice@corp.com"

        # Post-call with no mapping passes through unchanged
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        result = await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        assert json.loads(result.choices[0].message.tool_calls[0].function.arguments)["from"] == "<EMAIL_ADDRESS_1>"


class TestPrivacyBoundary:
    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    async def test_pre_call_logs_no_raw_values(self, mock_cache, mock_user_api_key_dict, caplog):
        """F2: log output from redaction contains entity types and counts
        but never the raw original PII values."""
        guard = AirlockPIIGuard()
        data = {
            "messages": [{"role": "user", "content": "Email alice@corp.com"}],
            "model": "claude-sonnet",
        }
        with caplog.at_level("INFO", logger="airlock.guardrails.pii"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert len(caplog.records) > 0
        assert "pii_redacted" in caplog.text
        assert "alice@corp.com" not in caplog.text

    async def test_post_call_logs_no_raw_values(self, mock_cache, mock_user_api_key_dict, caplog):
        """F3: log output from hydration contains count but never the
        restored original PII values."""
        guard = AirlockPIIGuard()
        data = {"metadata": {"airlock_pii_map": {"<EMAIL_ADDRESS_1>": "alice@corp.com"}}}
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        with caplog.at_level("INFO", logger="airlock.guardrails.pii"):
            await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        assert len(caplog.records) > 0
        assert "pii_hydrated" in caplog.text
        assert "alice@corp.com" not in caplog.text


# ---------------------------------------------------------------------------
# Hydration validation and edge cases
# ---------------------------------------------------------------------------
class TestHydrationEnabledValidation:
    def test_invalid_value_falls_back_to_enabled(self, monkeypatch, caplog):
        monkeypatch.setenv("AIRLOCK_PII_HYDRATION", "ooff")
        with caplog.at_level("WARNING", logger="airlock.guardrails.pii"):
            result = _hydration_enabled()
        assert result is True
        assert "Invalid AIRLOCK_PII_HYDRATION" in caplog.text

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PII_HYDRATION", "  off  ")
        assert _hydration_enabled() is False


class TestHydrateMultiplePlaceholdersInOneString:
    def test_two_placeholders_in_single_string(self):
        mapping = {
            "<EMAIL_ADDRESS_1>": "alice@corp.com",
            "<EMAIL_ADDRESS_2>": "bob@corp.com",
        }
        val, count = _hydrate_value_recursive(
            "from:<EMAIL_ADDRESS_1> to:<EMAIL_ADDRESS_2>", mapping,
        )
        assert val == "from:alice@corp.com to:bob@corp.com"
        assert count == 2


class TestHydratePassthroughTypes:
    """Non-string/dict/list values should pass through unchanged."""

    def test_integer(self):
        val, count = _hydrate_value_recursive(42, {"<X>": "y"})
        assert val == 42
        assert count == 0

    def test_float(self):
        val, count = _hydrate_value_recursive(3.14, {"<X>": "y"})
        assert val == 3.14
        assert count == 0

    def test_boolean(self):
        val, count = _hydrate_value_recursive(True, {"<X>": "y"})
        assert val is True
        assert count == 0

    def test_none(self):
        val, count = _hydrate_value_recursive(None, {"<X>": "y"})
        assert val is None
        assert count == 0


class TestHydrateToolCallsMultipleChoices:
    def test_hydrates_across_choices(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        tc1 = _make_tool_call("tool_a", {"email": "<EMAIL_ADDRESS_1>"})
        tc2 = _make_tool_call("tool_b", {"addr": "<EMAIL_ADDRESS_1>"})
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc1])),
                SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc2])),
            ],
        )
        count = _hydrate_tool_calls(response, mapping)
        assert count == 2
        assert json.loads(tc1.function.arguments)["email"] == "alice@corp.com"
        assert json.loads(tc2.function.arguments)["addr"] == "alice@corp.com"


class TestHydrateNoOpPreservesArguments:
    """When no placeholders match, function.arguments must not be rewritten."""

    def test_arguments_string_identity_preserved(self):
        mapping = {"<EMAIL_ADDRESS_1>": "alice@corp.com"}
        tc = _make_tool_call("tool", {"query": "no placeholders here"})
        original_id = id(tc.function.arguments)
        response = _make_response(tool_calls=[tc])
        _hydrate_tool_calls(response, mapping)
        # The string object itself should be untouched — no json.dumps round-trip
        assert id(tc.function.arguments) == original_id


class TestPostCallNoMetadata:
    async def test_data_without_metadata_key(self, mock_user_api_key_dict):
        guard = AirlockPIIGuard()
        data = {}  # no "metadata" key at all
        tc = _make_tool_call("gmail_search", {"from": "<EMAIL_ADDRESS_1>"})
        response = _make_response(tool_calls=[tc])
        result = await guard.async_post_call_success_hook(data, mock_user_api_key_dict, response)
        assert json.loads(result.choices[0].message.tool_calls[0].function.arguments)["from"] == "<EMAIL_ADDRESS_1>"


class TestScrubMessagesDefaultMapping:
    """_scrub_messages called without mapping/counters still scrubs correctly."""

    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    def test_scrub_without_mapping_args(self):
        messages = [{"role": "user", "content": "Email alice@corp.com"}]
        result = _scrub_messages(messages)
        assert "alice@corp.com" not in result[0]["content"]
        # Numbered placeholder should still be used
        assert "<EMAIL_ADDRESS_1>" in result[0]["content"]


class TestScrubValueRecursiveDepthGuard:
    """Scrub-side depth guard at 20 levels."""

    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    def test_deep_nesting_stops_at_depth_20(self):
        from airlock.guardrails.pii_guard import _scrub_value_recursive

        value = "alice@corp.com"
        for _ in range(22):
            value = {"nested": value}

        mapping, counters = {}, {}
        result = _scrub_value_recursive(value, mapping, counters)

        # Walk to the leaf
        node = result
        for _ in range(22):
            node = node["nested"]
        # Should still be the raw email — depth guard prevented scrubbing
        assert node == "alice@corp.com"
        assert mapping == {}


class TestMappingPlaceholderConsistency:
    """Placeholders stored in the mapping exactly match what's in the scrubbed text."""

    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    def test_placeholder_in_text_matches_mapping_key(self):
        mapping, counters = {}, {}
        result = _scrub_text_with_mapping("Email alice@corp.com", mapping, counters)
        for placeholder in mapping:
            assert placeholder in result

    async def test_round_trip_mapping_keys_match_scrubbed_output(
        self, mock_cache, mock_user_api_key_dict,
    ):
        guard = AirlockPIIGuard()
        data = {
            "messages": [
                {"role": "user", "content": "Email alice@corp.com and bob@other.org"},
            ],
            "model": "claude-sonnet",
        }
        data = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        pii_map = data["metadata"]["airlock_pii_map"]
        content = data["messages"][0]["content"]
        # Every placeholder key must appear in the scrubbed content
        for placeholder in pii_map:
            assert placeholder in content
        # No original values should remain
        for original in pii_map.values():
            assert original not in content


class TestLogEntityTypeExtraction:
    """The entity type extraction in the log line handles multi-underscore names."""

    @pytest.fixture(autouse=True)
    def _require_presidio(self, presidio_available, reset_presidio_singletons):
        if not presidio_available:
            pytest.skip("Presidio not available")

    async def test_entity_types_in_log_are_correct(
        self, mock_cache, mock_user_api_key_dict, caplog,
    ):
        guard = AirlockPIIGuard()
        data = {
            "messages": [
                {"role": "user", "content": "Email alice@corp.com card 4111111111111111"},
            ],
            "model": "claude-sonnet",
        }
        with caplog.at_level("INFO", logger="airlock.guardrails.pii"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert len(caplog.records) > 0
        log_text = caplog.text
        assert "EMAIL_ADDRESS" in log_text
        assert "CREDIT_CARD" in log_text


# ---------------------------------------------------------------------------
# Streaming + PII guard limitation warning (P1 Fix #6)
# ---------------------------------------------------------------------------
class TestStreamingPiiWarning:
    async def test_streaming_request_logs_warning(
        self, mock_cache, mock_user_api_key_dict, caplog, reset_presidio_singletons,
        presidio_available,
    ):
        """When streaming is enabled and PII mapping exists, a warning is logged."""
        if not presidio_available:
            pytest.skip("Presidio not available")
        guard = AirlockPIIGuard()
        data = {
            "messages": [
                {"role": "user", "content": "My email is alice@example.com"},
            ],
            "model": "claude-sonnet",
            "stream": True,
        }
        with caplog.at_level("WARNING", logger="airlock.guardrails.pii"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert any("stream" in r.message.lower() for r in caplog.records)

    async def test_non_streaming_request_no_warning(
        self, mock_cache, mock_user_api_key_dict, caplog, reset_presidio_singletons,
        presidio_available,
    ):
        """Non-streaming requests should not log the streaming warning."""
        if not presidio_available:
            pytest.skip("Presidio not available")
        guard = AirlockPIIGuard()
        data = {
            "messages": [
                {"role": "user", "content": "My email is alice@example.com"},
            ],
            "model": "claude-sonnet",
            "stream": False,
        }
        with caplog.at_level("WARNING", logger="airlock.guardrails.pii"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert not any("stream" in r.message.lower() for r in caplog.records)
