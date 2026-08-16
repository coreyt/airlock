"""Tests for airlock/proxy_errors.py (workstream B / Pack 0.5.0-RES-errors)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from litellm import RateLimitError

from airlock.proxy_errors import (
    AirlockEndpointNotSupported,
    AirlockGatewayRoutingOverride,
    AirlockInvalidReasoningEffort,
    AirlockProviderBlocked,
    AirlockThreatBackoff,
    airlock_invalid_reasoning_effort_handler,
    airlock_provider_blocked_handler,
    airlock_threat_backoff_handler,
    block_response_payload,
    invalid_reasoning_effort_response_payload,
    endpoint_not_supported_response_payload,
    install_airlock_error_handlers_on_proxy_app,
    retry_after_seconds,
    threat_backoff_response_payload,
)


def test_endpoint_not_supported_payload_is_openai_shaped() -> None:
    body, headers = endpoint_not_supported_response_payload(
        AirlockEndpointNotSupported("gpt-4o-mini", "embeddings")
    )
    assert headers == {}
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "model_endpoint_not_supported"
    assert body["error"]["param"] == "model"


def test_openrouter_routing_override_is_openai_shaped() -> None:
    exc = AirlockGatewayRoutingOverride("extra_body.route")
    body = exc.to_dict()
    assert body["code"] == "gateway_routing_override_not_allowed"
    assert body["param"] == "extra_body.route"


def test_deepseek_non_function_tool_is_openai_shaped() -> None:
    from airlock.proxy_errors import AirlockDeepSeekToolNotSupported

    body = AirlockDeepSeekToolNotSupported().to_dict()
    assert body["code"] == "deepseek_non_function_tool_not_supported"
    assert body["param"] == "tools"


async def test_deepseek_tool_rejection_survives_litellm_proxy_translation(
    mock_user_api_key_dict,
) -> None:
    from litellm.proxy._types import ProxyException
    from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing
    from litellm.proxy.proxy_server import openai_exception_handler
    from starlette.requests import Request

    from airlock.proxy_errors import AirlockDeepSeekToolNotSupported

    proxy_logging = type(
        "ProxyLoggingStub",
        (),
        {
            "post_call_failure_hook": AsyncMock(return_value=None),
            "post_call_response_headers_hook": AsyncMock(return_value={}),
        },
    )()
    with pytest.raises(ProxyException) as raised:
        await ProxyBaseLLMRequestProcessing(data={})._handle_llm_api_exception(
            e=AirlockDeepSeekToolNotSupported(),
            user_api_key_dict=mock_user_api_key_dict,
            proxy_logging_obj=proxy_logging,
            version="test",
        )
    exc = raised.value
    assert (exc.code, exc.type, exc.param, exc.openai_code) == (
        "400",
        "invalid_request_error",
        "tools",
        "deepseek_non_function_tool_not_supported",
    )
    response = await openai_exception_handler(
        Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [],
            }
        ),
        exc,
    )
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == {
        "message": "DeepSeek supports OpenAI function tools only.",
        "type": "invalid_request_error",
        "param": "tools",
        "code": "deepseek_non_function_tool_not_supported",
    }


async def test_embedding_rejection_survives_litellm_proxy_exception_translation(
    mock_user_api_key_dict,
) -> None:
    """LiteLLM catches guardrail exceptions inside `/v1/embeddings` routes."""
    from litellm.proxy._types import ProxyException
    from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing
    from litellm.proxy.proxy_server import openai_exception_handler
    from starlette.requests import Request

    proxy_logging = type(
        "ProxyLoggingStub",
        (),
        {
            "post_call_failure_hook": AsyncMock(return_value=None),
            "post_call_response_headers_hook": AsyncMock(return_value={}),
        },
    )()
    processor = ProxyBaseLLMRequestProcessing(data={})
    with pytest.raises(ProxyException) as raised:
        await processor._handle_llm_api_exception(
            e=AirlockEndpointNotSupported("gpt-4o-mini", "embeddings"),
            user_api_key_dict=mock_user_api_key_dict,
            proxy_logging_obj=proxy_logging,
            version="test",
        )

    exc = raised.value
    assert exc.code == "400"
    assert exc.type == "invalid_request_error"
    assert exc.param == "model"
    assert exc.openai_code == "model_endpoint_not_supported"
    response = await openai_exception_handler(
        Request(
            {"type": "http", "method": "POST", "path": "/v1/embeddings", "headers": []}
        ),
        exc,
    )
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == {
        "message": "Model 'gpt-4o-mini' is not configured for the 'embeddings' endpoint.",
        "type": "invalid_request_error",
        "param": "model",
        "code": "model_endpoint_not_supported",
        "airlock": {"endpoint": "embeddings", "model": "gpt-4o-mini"},
    }


async def test_openrouter_override_rejection_survives_litellm_proxy_translation(
    mock_user_api_key_dict,
) -> None:
    """The pre-dispatch provider guard must reach clients as an OpenAI 400."""
    from litellm.proxy._types import ProxyException
    from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing
    from litellm.proxy.proxy_server import openai_exception_handler
    from starlette.requests import Request

    proxy_logging = type(
        "ProxyLoggingStub",
        (),
        {
            "post_call_failure_hook": AsyncMock(return_value=None),
            "post_call_response_headers_hook": AsyncMock(return_value={}),
        },
    )()
    with pytest.raises(ProxyException) as raised:
        await ProxyBaseLLMRequestProcessing(data={})._handle_llm_api_exception(
            e=AirlockGatewayRoutingOverride("extra_body.route"),
            user_api_key_dict=mock_user_api_key_dict,
            proxy_logging_obj=proxy_logging,
            version="test",
        )

    exc = raised.value
    assert exc.code == "400"
    assert exc.type == "invalid_request_error"
    assert exc.param == "extra_body.route"
    assert exc.openai_code == "gateway_routing_override_not_allowed"
    response = await openai_exception_handler(
        Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [],
            }
        ),
        exc,
    )
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == {
        "message": (
            "OpenRouter routing option 'extra_body.route' is operator-controlled "
            "and cannot be set by a client request."
        ),
        "type": "invalid_request_error",
        "param": "extra_body.route",
        "code": "gateway_routing_override_not_allowed",
    }


class TestAirlockProviderBlocked:
    def test_is_a_rate_limit_error(self):
        exc = AirlockProviderBlocked(
            "blocked",
            llm_provider="openai",
            model="gpt-5.4",
            cooldown_seconds=42.0,
            scope="client_provider",
            reason="quota",
            client_id="key:abc",
        )
        assert isinstance(exc, RateLimitError)
        assert exc.cooldown_seconds == 42.0
        assert exc.scope == "client_provider"
        assert exc.reason == "quota"
        assert exc.client_id == "key:abc"


class TestAirlockThreatBackoff:
    def test_is_a_rate_limit_error_without_client_or_heuristic_fields(self):
        exc = AirlockThreatBackoff(retry_after=2.1)
        assert isinstance(exc, RateLimitError)
        assert exc.retry_after == 2.1
        assert not hasattr(exc, "client_id")
        assert not hasattr(exc, "reason")

    async def test_guardrail_translation_preserves_429_contract(
        self, mock_user_api_key_dict
    ):
        """LiteLLM can translate guardrail errors before FastAPI handlers run."""
        from litellm.proxy._types import ProxyException
        from litellm.proxy.common_request_processing import (
            ProxyBaseLLMRequestProcessing,
        )
        from litellm.proxy.proxy_server import openai_exception_handler
        from starlette.requests import Request

        proxy_logging = type(
            "ProxyLoggingStub",
            (),
            {
                "post_call_failure_hook": AsyncMock(return_value=None),
                "post_call_response_headers_hook": AsyncMock(return_value={}),
            },
        )()
        with pytest.raises(ProxyException) as raised:
            await ProxyBaseLLMRequestProcessing(data={})._handle_llm_api_exception(
                e=AirlockThreatBackoff(retry_after=2.1),
                user_api_key_dict=mock_user_api_key_dict,
                proxy_logging_obj=proxy_logging,
                version="test",
            )
        response = await openai_exception_handler(
            Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "headers": [],
                }
            ),
            raised.value,
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "3"
        assert json.loads(response.body)["error"] == {
            "message": "Too many requests. Please retry later.",
            "type": "airlock_threat_backoff",
            "param": None,
            "code": "threat_backoff",
            "airlock": {"source": "threat_backoff", "retry_after": 3},
        }


class TestRetryAfter:
    def test_ceils_and_floors_at_one(self):
        assert retry_after_seconds(0.0) == 1
        assert retry_after_seconds(0.1) == 1
        assert retry_after_seconds(29.2) == 30
        assert retry_after_seconds(208.0) == 208


class TestBlockResponsePayload:
    def _exc(self, **kw):
        base = dict(
            llm_provider="openai",
            model="gpt-5.4",
            cooldown_seconds=29.4,
            scope="provider",
            reason="exceeded your current quota",
            client_id="key:abc",
        )
        base.update(kw)
        return AirlockProviderBlocked("Airlock blocked", **base)

    def test_body_is_openai_shaped_and_enriched(self):
        body, headers = block_response_payload(self._exc())
        assert body["error"]["type"] == "airlock_circuit_breaker"
        assert body["error"]["code"] == "provider_blocked"
        assert body["error"]["param"] is None
        air = body["error"]["airlock"]
        assert air["scope"] == "provider"
        assert air["provider"] == "openai"
        assert air["retry_after"] == 30  # ceil(29.4)
        assert air["source"] == "circuit_breaker"

    def test_headers(self):
        _, headers = block_response_payload(self._exc())
        assert headers["Retry-After"] == "30"
        assert headers["X-Airlock-Provider-State"] == "quarantined"
        assert headers["X-Airlock-Block-Scope"] == "provider"

    def test_reason_is_bounded(self):
        # Use spaced words (no 32+ char token) so truncation, not redaction, applies.
        body, _ = block_response_payload(self._exc(reason="word " * 200))
        assert len(body["error"]["airlock"]["reason"]) == 300


class TestThreatBackoffResponsePayload:
    def test_body_is_openai_shaped_and_does_not_disclose_protection_details(self):
        body, headers = threat_backoff_response_payload(
            AirlockThreatBackoff(retry_after=2.1)
        )
        assert body == {
            "error": {
                "message": "Too many requests. Please retry later.",
                "type": "airlock_threat_backoff",
                "code": "threat_backoff",
                "param": None,
                "airlock": {"source": "threat_backoff", "retry_after": 3},
            }
        }
        assert headers == {"Retry-After": "3"}
        assert "provider" not in str(body).lower()
        assert "client" not in str(body).lower()

    @pytest.mark.parametrize("retry_after", [0.0, -1.0, 0.1, 2.0])
    def test_retry_after_is_whole_seconds_and_minimum_one(self, retry_after):
        body, headers = threat_backoff_response_payload(
            AirlockThreatBackoff(retry_after=retry_after)
        )
        expected = retry_after_seconds(retry_after)
        assert body["error"]["airlock"]["retry_after"] == expected
        assert headers["Retry-After"] == str(expected)


class TestHandler:
    async def test_handler_returns_429(self):
        exc = AirlockProviderBlocked(
            "blocked",
            llm_provider="anthropic",
            model="claude-sonnet",
            cooldown_seconds=88.0,
            scope="model",
            reason="r",
            client_id="key:z",
        )
        resp = await airlock_provider_blocked_handler(None, exc)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "88"
        assert resp.headers["X-Airlock-Block-Scope"] == "model"
        payload = json.loads(bytes(resp.body))
        assert payload["error"]["type"] == "airlock_circuit_breaker"

    async def test_threat_backoff_handler_returns_429(self):
        response = await airlock_threat_backoff_handler(
            None, AirlockThreatBackoff(retry_after=0.1)
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert json.loads(response.body)["error"]["code"] == "threat_backoff"


class TestInvalidReasoningEffort:
    def test_payload_is_openai_shaped_400(self):
        exc = AirlockInvalidReasoningEffort(
            "none", "openai/gpt-5.4", frozenset({"minimal", "low", "medium", "high"})
        )
        body, headers = invalid_reasoning_effort_response_payload(exc)
        assert headers == {}
        assert body["error"]["code"] == "invalid_reasoning_effort"
        assert body["error"]["param"] == "reasoning_effort"
        assert body["error"]["airlock"]["requested"] == "none"

    async def test_handler_returns_400(self):
        exc = AirlockInvalidReasoningEffort(
            "none", "openai/gpt-5.4", frozenset({"minimal", "low", "medium", "high"})
        )
        response = await airlock_invalid_reasoning_effort_handler(None, exc)
        assert response.status_code == 400
        assert json.loads(response.body)["error"]["code"] == "invalid_reasoning_effort"


class TestInstall:
    def test_returns_false_without_proxy_app(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", object())
        assert install_airlock_error_handlers_on_proxy_app() is False

    def test_idempotent_on_fastapi_app(self, monkeypatch):
        import sys

        from fastapi import FastAPI

        app = FastAPI()
        stub = type("M", (), {"app": app})()
        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", stub)
        assert install_airlock_error_handlers_on_proxy_app() is True
        assert getattr(app.state, "airlock_error_handlers_installed") is True
        # second call is a no-op success
        assert install_airlock_error_handlers_on_proxy_app() is True
        assert AirlockProviderBlocked in app.exception_handlers
        assert AirlockThreatBackoff in app.exception_handlers
        assert AirlockInvalidReasoningEffort in app.exception_handlers


class TestGuardianRaisesTyped:
    def test_raise_provider_protection_raises_typed(self):
        from airlock.fast.guardian import _raise_provider_protection

        with pytest.raises(AirlockProviderBlocked) as ei:
            _raise_provider_protection(
                {"metadata": {}},
                "key:abc",
                "openai",
                "gpt-5.4",
                "quota",
                30.0,
                scope="client_provider",
            )
        exc = ei.value
        assert isinstance(exc, RateLimitError)
        assert exc.scope == "client_provider"
        assert exc.cooldown_seconds == 30.0
        assert exc.client_id == "key:abc"


class TestSanitizeReason:
    def test_redacts_key_like_tokens(self):
        from airlock.proxy_errors import sanitize_reason

        out = sanitize_reason("quota for sk-ABCD1234efgh5678 exhausted")
        assert "sk-ABCD1234efgh5678" not in out
        assert "[REDACTED]" in out

    def test_redacts_bearer_and_long_secrets(self):
        from airlock.proxy_errors import sanitize_reason

        assert "[REDACTED]" in sanitize_reason("Bearer abcdef123456789")
        assert "[REDACTED]" in sanitize_reason("token " + "a" * 40)

    def test_truncates(self):
        from airlock.proxy_errors import sanitize_reason

        # Spaced words so truncation (not redaction) is what bounds the length.
        assert len(sanitize_reason("word " * 200)) == 300

    def test_message_in_body_is_sanitized(self):
        """The BLOCK: raw reason embedded in the message must not leak."""
        from airlock.fast.guardian import _raise_provider_protection

        with pytest.raises(AirlockProviderBlocked) as ei:
            _raise_provider_protection(
                {"metadata": {}},
                "key:abc",
                "openai",
                "gpt-5.4",
                "upstream said sk-LEAK1234567890abcdef bad",
                30.0,
                scope="provider",
            )
        body, _ = block_response_payload(ei.value)
        assert "sk-LEAK1234567890abcdef" not in body["error"]["message"]
        assert "sk-LEAK1234567890abcdef" not in body["error"]["airlock"]["reason"]
