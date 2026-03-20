"""Tests for airlock/callbacks/model_override_headers.py."""

from __future__ import annotations

from types import SimpleNamespace

from airlock.callbacks.model_override_headers import (
    AirlockModelOverrideHeaders,
)


class TestModelOverrideHeaders:
    async def test_returns_override_header_and_updates_hidden_params(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={})
        data = {
            "metadata": {
                "airlock_response_headers": {
                    "X-Airlock-Model-Override": "claude-haiku",
                }
            }
        }

        result = await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result == {"X-Airlock-Model-Override": "claude-haiku"}
        assert response._hidden_params["additional_headers"] == {
            "X-Airlock-Model-Override": "claude-haiku",
        }

    async def test_returns_none_when_no_override_present(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={})

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result is None
        assert response._hidden_params == {}

    async def test_builds_gemini_headers_from_request_and_response(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(
            _hidden_params={},
            model_dump=lambda: {
                "choices": [{"message": {"content": None}, "finish_reason": "length"}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 5, "text_tokens": 0}},
            },
        )

        result = await hook.async_post_call_response_headers_hook(
            data={"model": "gemini-pro", "metadata": {"airlock_gemini": {"mode": "deep_reasoning"}}},
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Provider-Mode"] == "gemini"
        assert result["X-Airlock-Reasoning-Mode"] == "deep_reasoning"
        assert result["X-Airlock-Provider-State"] == "thought_only"
