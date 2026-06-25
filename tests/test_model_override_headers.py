"""Tests for airlock/callbacks/model_override_headers.py."""

from __future__ import annotations

from types import SimpleNamespace

from litellm import ModelResponse

from airlock.callbacks.model_override_headers import (
    AirlockModelOverrideHeaders,
)
from airlock.transparency import Mutation, configure_transparency


def _mut(field, op, after=None, count=None, category=None):
    return Mutation(
        field=field,
        op=op,
        before=None,
        after=after,
        stage="pre_call",
        source="test",
        count=count,
        category=category,
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

    async def test_null_metadata_in_request_does_not_crash(self):
        """Regression: data["metadata"]=None must not raise TypeError on stash."""
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "openai"})
        data: dict = {"metadata": None}

        result = await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result is not None
        assert "X-Airlock-Served-By" in result
        assert data["metadata"]["airlock_served"]["provider"] == "openai"

    async def test_missing_metadata_key_does_not_crash(self):
        """Regression: data with no 'metadata' key must stash correctly."""
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "openai"})
        data: dict = {}

        result = await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result is not None
        assert "X-Airlock-Served-By" in result
        assert data["metadata"]["airlock_served"]["provider"] == "openai"


class TestMutationsHeader:
    async def test_allowlisted_value_non_allowlisted_op_and_redact(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "anthropic"})
        ledger = [
            _mut("reasoning_effort", "set", after="high"),
            _mut("system", "inject", after="SECRET INJECTED PROMPT"),
            _mut("ssn", "redact", count=2, category="pii"),
        ]
        data = {"metadata": {"airlock_mutations": ledger}}

        result = await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        header = result["X-Airlock-Mutations"]
        assert "reasoning_effort=high" in header
        assert "system=inject" in header
        assert "SECRET INJECTED PROMPT" not in header
        assert "ssn=redacted(2)" in header

    async def test_empty_ledger_omits_header(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "anthropic"})
        data = {"metadata": {"airlock_mutations": []}}

        result = await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert "X-Airlock-Mutations" not in result

    async def test_mutation_headers_off_suppresses(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "anthropic"})
        data = {"metadata": {"airlock_mutations": [_mut("system", "inject")]}}

        configure_transparency({"transparency": {"mutation_headers": "off"}})
        try:
            result = await hook.async_post_call_response_headers_hook(
                data=data,
                user_api_key_dict=None,
                response=response,
            )
        finally:
            configure_transparency(None)

        assert "X-Airlock-Mutations" not in result

    async def test_header_respects_byte_budget(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={"custom_llm_provider": "anthropic"})
        ledger = [_mut(f"field_with_a_longish_name_{i}", "inject") for i in range(50)]
        data = {"metadata": {"airlock_mutations": ledger}}

        configure_transparency(
            {"transparency": {"mutation_header_budget_bytes": 80}}
        )
        try:
            result = await hook.async_post_call_response_headers_hook(
                data=data,
                user_api_key_dict=None,
                response=response,
            )
        finally:
            configure_transparency(None)

        header = result["X-Airlock-Mutations"]
        assert len(header.encode("utf-8")) <= 80
        assert "more" in header


class TestExplainEnvelope:
    async def test_optin_attaches_serialized_envelope(self):
        hook = AirlockModelOverrideHeaders()
        response = ModelResponse()
        ledger = [_mut("reasoning_effort", "set", after="high")]
        data = {
            "metadata": {"airlock_mutations": ledger},
            "proxy_server_request": {"headers": {"x-airlock-explain": "1"}},
        }

        result = await hook.async_post_call_success_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        dumped = result.model_dump()
        assert dumped["airlock"]["mutations"][0]["field"] == "reasoning_effort"
        assert dumped["airlock"]["mutations"][0]["op"] == "set"
        # Proves the client-visible serialization carries the envelope.
        assert "airlock" in result.model_dump_json()

    async def test_no_optin_body_byte_identical(self):
        hook = AirlockModelOverrideHeaders()
        response = ModelResponse()
        before = response.model_dump_json()
        ledger = [_mut("reasoning_effort", "set", after="high")]
        data = {
            "metadata": {"airlock_mutations": ledger},
            "proxy_server_request": {"headers": {}},
        }

        result = await hook.async_post_call_success_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result.model_dump_json() == before
        assert "airlock" not in result.model_dump()

    async def test_falsy_optin_value_is_no_op(self):
        hook = AirlockModelOverrideHeaders()
        response = ModelResponse()
        before = response.model_dump_json()
        data = {
            "metadata": {"airlock_mutations": [_mut("system", "inject")]},
            "proxy_server_request": {"headers": {"x-airlock-explain": "0"}},
        }

        result = await hook.async_post_call_success_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result.model_dump_json() == before

    async def test_optin_with_empty_ledger_is_no_op(self):
        hook = AirlockModelOverrideHeaders()
        response = ModelResponse()
        before = response.model_dump_json()
        data = {
            "metadata": {"airlock_mutations": []},
            "proxy_server_request": {"headers": {"x-airlock-explain": "1"}},
        }

        result = await hook.async_post_call_success_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result.model_dump_json() == before
