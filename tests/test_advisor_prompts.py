"""Tests for advisor system prompts and tool description formatting."""

from __future__ import annotations

import json

from airlock.advisor.prompts import (
    build_system_prompt,
    build_tool_descriptions,
    format_tool_result,
)


class TestSystemPrompt:
    def test_system_prompt_contains_circuit_breaker(self):
        prompt = build_system_prompt()
        lower = prompt.lower()
        assert "circuit breaker" in lower or (
            "closed" in prompt and "open" in prompt
        )

    def test_system_prompt_contains_guardrail_chain(self):
        prompt = build_system_prompt()
        lower = prompt.lower()
        assert "guardrail" in lower
        assert "pii" in lower

    def test_system_prompt_contains_action_format(self):
        prompt = build_system_prompt()
        assert "ACTION" in prompt

    def test_system_prompt_contains_threat_detector(self):
        prompt = build_system_prompt()
        assert "threat" in prompt.lower()


class TestBuildToolDescriptions:
    def _make_registry(self):
        return {
            "get_state_snapshot": (
                lambda: None,
                {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "description": "Get real-time state snapshot",
                },
            ),
            "get_recent_errors": (
                lambda: None,
                {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Number of days",
                            "default": 2,
                        },
                    },
                    "required": [],
                    "description": "Get recent error records",
                },
            ),
        }

    def test_build_tool_descriptions_format(self):
        registry = self._make_registry()
        result = build_tool_descriptions(registry)
        assert isinstance(result, list)
        for entry in result:
            assert entry["type"] == "function"
            assert "name" in entry["function"]
            assert "parameters" in entry["function"]

    def test_build_tool_descriptions_matches_registry(self):
        registry = self._make_registry()
        result = build_tool_descriptions(registry)
        names = {entry["function"]["name"] for entry in result}
        assert names == set(registry.keys())

    def test_build_tool_descriptions_valid_schema(self):
        registry = self._make_registry()
        result = build_tool_descriptions(registry)
        for entry in result:
            assert entry["function"]["parameters"]["type"] == "object"


class TestFormatToolResult:
    def test_format_tool_result_json(self):
        data = {"status": "ok", "count": 42}
        result = format_tool_result("test_tool", data)
        parsed = json.loads(result)
        assert parsed == data

    def test_format_tool_result_truncates(self):
        big_data = {"key": "x" * 60000}
        result = format_tool_result("test_tool", big_data)
        assert len(result) <= 50000 + 200  # truncation + note
        assert "truncated" in result

    def test_format_tool_result_small_not_truncated(self):
        small_data = {"key": "hello"}
        result = format_tool_result("test_tool", small_data)
        assert "truncated" not in result
        parsed = json.loads(result)
        assert parsed == small_data
