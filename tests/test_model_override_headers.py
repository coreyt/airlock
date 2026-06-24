"""Tests for airlock/callbacks/model_override_headers.py."""

from __future__ import annotations

from types import SimpleNamespace

from airlock.callbacks.model_override_headers import (
    AirlockModelOverrideHeaders,
)
from airlock.transparency import configure_transparency


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
                "usage": {
                    "completion_tokens_details": {
                        "reasoning_tokens": 5,
                        "text_tokens": 0,
                    }
                },
            },
        )

        result = await hook.async_post_call_response_headers_hook(
            data={
                "model": "gemini-pro",
                "metadata": {"airlock_gemini": {"mode": "deep_reasoning"}},
            },
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Provider-Mode"] == "gemini"
        assert result["X-Airlock-Reasoning-Mode"] == "deep_reasoning"
        assert result["X-Airlock-Provider-State"] == "thought_only"


class TestServedHeaders:
    async def test_bedrock_served_by_and_region(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(
            _hidden_params={
                "custom_llm_provider": "bedrock",
                "region_name": "us-east-1",
            }
        )

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Served-By"] == "bedrock"
        assert result["X-Airlock-Served-Region"] == "us-east-1"
        assert (
            response._hidden_params["additional_headers"]["X-Airlock-Served-By"]
            == "bedrock"
        )

    async def test_vertex_ai_served_by_and_region(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(
            _hidden_params={
                "custom_llm_provider": "vertex_ai",
                "region_name": "us-central1",
            }
        )

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Served-By"] == "vertex_ai"
        assert result["X-Airlock-Served-Region"] == "us-central1"

    async def test_anthropic_served_by_no_region(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "anthropic"})

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Served-By"] == "anthropic"
        assert "X-Airlock-Served-Region" not in result

    async def test_openai_served_by_no_region(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "openai"})

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Served-By"] == "openai"
        assert "X-Airlock-Served-Region" not in result

    async def test_streaming_provider_from_wrapper_attribute(self):
        hook = AirlockModelOverrideHeaders()
        # Stream wrapper: _hidden_params LACKS custom_llm_provider; it is only on
        # the instance attribute at header-flush time (CC-T6).
        response = SimpleNamespace(
            _hidden_params={"model_id": "abc", "api_base": "https://x"},
            custom_llm_provider="anthropic",
        )

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result["X-Airlock-Served-By"] == "anthropic"

    async def test_unknown_provider_omits_served_headers(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={})

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result is None

    async def test_served_identity_stashed_in_metadata(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(
            _hidden_params={
                "custom_llm_provider": "bedrock",
                "region_name": "us-east-1",
                "model_id": "anthropic.claude-3",
            }
        )
        data = {"metadata": {}}

        await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        served = data["metadata"]["airlock_served"]
        assert served["provider"] == "bedrock"
        assert served["region"] == "us-east-1"
        assert served["model_id"] == "anthropic.claude-3"
        assert served["backend_kind"] == "gateway"
        assert "response_cost" not in served

    async def test_config_optout_suppresses_served_headers(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(
            _hidden_params={
                "custom_llm_provider": "bedrock",
                "region_name": "us-east-1",
            }
        )
        configure_transparency({"transparency": {"served_headers": False}})
        try:
            result = await hook.async_post_call_response_headers_hook(
                data={"metadata": {}},
                user_api_key_dict=None,
                response=response,
            )
        finally:
            configure_transparency(None)

        assert result is None

    async def test_served_headers_additive_to_override_header(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "anthropic"})
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

        assert result["X-Airlock-Model-Override"] == "claude-haiku"
        assert result["X-Airlock-Served-By"] == "anthropic"
