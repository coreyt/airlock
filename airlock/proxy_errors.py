"""Typed Airlock rate-limit error + a FastAPI exception handler (workstream B).

When Airlock's circuit breaker blocks a request pre-flight it raises
:class:`AirlockProviderBlocked` (a ``RateLimitError`` subclass) so the client
receives an HTTP 429 with a ``Retry-After`` header and an enriched but
OpenAI-compatible body — distinguishable from a passthrough provider 429 without
string-parsing. A local Fast Guardian threat backoff uses the separately typed
:class:`AirlockThreatBackoff` contract. Handlers are registered on the LiteLLM
proxy app via
:func:`install_airlock_error_handlers_on_proxy_app`, mirroring the other
``install_*_on_proxy_app`` hooks in ``model_override_headers``.
"""

from __future__ import annotations

import math
import re
from typing import Any

from litellm import RateLimitError
from litellm.proxy._types import ProxyException

from airlock.litellm_adapter import resolve_proxy_app
from airlock.transparency import model_suggestion_header

# Redact key-like tokens (provider keys, bearer tokens, long secret-ish strings)
# before any upstream reason text is echoed back to the client.
_SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{6,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{6,}"
    r"|[A-Za-z0-9_\-]{32,}"
)


def sanitize_reason(reason: str | None, limit: int = 300) -> str:
    """Defensively redact key-like tokens and bound the length of reason text.

    Applied at the boundary where untrusted upstream text enters an
    Airlock-emitted error, so neither the top-level ``error.message`` nor the
    ``error.airlock.reason`` field can leak a secret or bloat the response.
    """
    text = _SECRET_RE.sub("[REDACTED]", str(reason or ""))
    return text[:limit]


class AirlockProviderBlocked(RateLimitError):
    """An Airlock circuit-breaker block (not a passthrough provider 429).

    Subclasses ``RateLimitError`` so existing ``except RateLimitError`` paths keep
    working, while carrying the structured fields the handler needs.
    """

    def __init__(
        self,
        message: str,
        *,
        llm_provider: str,
        model: str,
        cooldown_seconds: float,
        scope: str,
        reason: str,
        client_id: str,
    ) -> None:
        super().__init__(message=message, llm_provider=llm_provider, model=model)
        self.cooldown_seconds = float(cooldown_seconds)
        self.scope = scope
        self.reason = reason
        self.client_id = client_id


class AirlockModelNotFound(Exception):
    """A refused near-match, rendered as an OpenAI-shaped 404."""

    def __init__(
        self,
        requested_model: str,
        suggestions: list[dict[str, str | float]],
        *,
        reason: str = "dropped_qualifier",
    ) -> None:
        self.requested_model = requested_model
        self.suggestions = suggestions
        self.reason = reason
        models = [str(item["model"]) for item in suggestions if item.get("model")]
        message = (
            f"Model '{requested_model}' is not available. "
            f"Try '{models[0]}'. Alternatives: {', '.join(models[1:]) or 'none'}."
        )
        super().__init__(message)


class AirlockInvalidReasoningEffort(Exception):
    """A known-invalid OpenAI reasoning effort rejected before provider dispatch."""

    def __init__(self, requested: str, model: str, supported: frozenset[str]) -> None:
        self.requested = requested
        self.model = model
        self.supported = tuple(sorted(supported))
        options = ", ".join(self.supported)
        super().__init__(
            f"reasoning_effort {requested!r} is not supported by {model!r}; "
            f"use one of: {options}."
        )


class AirlockEndpointNotSupported(ProxyException):
    """A configured alias cannot be used on the requested API endpoint."""

    def __init__(self, model: str, endpoint: str) -> None:
        self.model = model
        self.endpoint = endpoint
        self._airlock_error_code = "model_endpoint_not_supported"
        super().__init__(
            message=f"Model {model!r} is not configured for the {endpoint!r} endpoint.",
            type="invalid_request_error",
            param="model",
            code=400,
            openai_code=self._airlock_error_code,
        )

    def to_dict(self) -> dict:
        body = super().to_dict()
        body["code"] = self._airlock_error_code
        body["airlock"] = {"endpoint": self.endpoint, "model": self.model}
        return body


class AirlockGatewayRoutingOverride(ProxyException):
    """A client tried to override an operator-owned gateway routing policy."""

    def __init__(self, option: str) -> None:
        self.option = option
        self._airlock_error_code = "gateway_routing_override_not_allowed"
        super().__init__(
            message=(
                f"OpenRouter routing option {option!r} is operator-controlled and "
                "cannot be set by a client request."
            ),
            type="invalid_request_error",
            param=option,
            code=400,
            openai_code=self._airlock_error_code,
        )

    def to_dict(self) -> dict:
        body = super().to_dict()
        body["code"] = self._airlock_error_code
        return body


class AirlockDeepSeekToolNotSupported(ProxyException):
    """A DeepSeek request contains a non-function tool LiteLLM would drop."""

    def __init__(self, option: str = "tools") -> None:
        self.option = option
        self._airlock_error_code = "deepseek_non_function_tool_not_supported"
        super().__init__(
            message="DeepSeek supports OpenAI function tools only.",
            type="invalid_request_error",
            param=option,
            code=400,
            openai_code=self._airlock_error_code,
        )

    def to_dict(self) -> dict:
        body = super().to_dict()
        body["code"] = self._airlock_error_code
        return body


class AirlockAdmissionShed(RateLimitError):
    """A local admission-gate rejection with an honest retry time."""

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message=message, llm_provider="airlock", model="admission")
        self.retry_after = float(retry_after)


class AirlockThreatBackoff(ProxyException, RateLimitError):
    """A local Fast Guardian threat backoff, not a provider rate limit.

    Retains only the remaining duration needed to tell the client when to retry;
    client identity, threat signals, and triggering request details stay inside
    the Guardian/logging boundary.
    """

    def __init__(self, *, retry_after: float) -> None:
        retry_after_seconds_value = max(1, math.ceil(float(retry_after)))
        # RateLimitError preserves existing rate-limit catches and telemetry.
        # Calling it explicitly matters because ProxyException is first in the
        # MRO so LiteLLM's guardrail pipeline preserves our stable body schema.
        RateLimitError.__init__(
            self,
            message="Too many requests. Please retry later.",
            llm_provider="airlock",
            model="threat_backoff",
            headers={"Retry-After": str(retry_after_seconds_value)},
        )
        # Do not call ProxyException.__init__: with this intentional multiple
        # inheritance its cooperative ``super()`` would re-enter
        # RateLimitError. Mirror its small public protocol after RateLimitError
        # has initialized the OpenAI exception base.
        self.message = "Too many requests. Please retry later."
        self.type = "airlock_threat_backoff"
        self.param = None
        self.openai_code = "threat_backoff"
        self.code = "429"
        self.headers = {"Retry-After": str(retry_after_seconds_value)}
        self.provider_specific_fields = {
            "airlock": {
                "source": "threat_backoff",
                "retry_after": retry_after_seconds_value,
            }
        }
        self.retry_after = float(retry_after)

    def to_dict(self) -> dict:
        """Use LiteLLM's direct guardrail path without nesting Airlock fields."""
        return {
            "message": self.message,
            "type": self.type,
            "param": self.param,
            "code": self.openai_code,
            "airlock": {
                "source": "threat_backoff",
                "retry_after": retry_after_seconds(self.retry_after),
            },
        }


def retry_after_seconds(cooldown_seconds: float) -> int:
    """Whole-second ``Retry-After`` value, at least 1."""
    return max(1, math.ceil(cooldown_seconds))


def block_response_payload(exc: AirlockProviderBlocked) -> tuple[dict, dict]:
    """Build the (body, headers) for an Airlock block. OpenAI-shaped, enriched."""
    retry_after = retry_after_seconds(exc.cooldown_seconds)
    body = {
        "error": {
            # Defensive: the message is Airlock-built, but sanitize again in case a
            # caller embedded raw upstream text in it.
            "message": sanitize_reason(str(getattr(exc, "message", "") or exc), 500),
            "type": "airlock_circuit_breaker",
            "code": "provider_blocked",
            "param": None,
            "airlock": {
                "scope": exc.scope,
                "provider": exc.llm_provider,
                "cooldown_seconds": round(exc.cooldown_seconds, 1),
                "retry_after": retry_after,
                "reason": sanitize_reason(exc.reason),
                "source": "circuit_breaker",
            },
        }
    }
    headers = {
        "Retry-After": str(retry_after),
        "X-Airlock-Provider-State": "quarantined",
        "X-Airlock-Block-Scope": exc.scope,
    }
    return body, headers


def model_not_found_response_payload(exc: AirlockModelNotFound) -> tuple[dict, dict]:
    """Build an OpenAI-compatible 404 for a refused near-match."""
    body = {
        "error": {
            "message": sanitize_reason(str(exc), 500),
            "type": "invalid_request_error",
            "code": "model_not_found",
            "param": "model",
            "airlock": {"suggestions": exc.suggestions},
        }
    }
    header = model_suggestion_header(
        exc.requested_model, exc.suggestions, reason=exc.reason
    )
    return body, {"X-Airlock-Model-Suggestion": header} if header else {}


def admission_shed_response_payload(exc: AirlockAdmissionShed) -> tuple[dict, dict]:
    """Build the OpenAI-compatible response for local admission shedding."""
    retry_after = retry_after_seconds(exc.retry_after)
    body = {
        "error": {
            "message": sanitize_reason(str(exc), 500),
            "type": "airlock_admission",
            "code": "admission_shed",
            "param": None,
            "airlock": {
                "source": "admission",
                "retry_after": retry_after,
            },
        }
    }
    headers = {
        "Retry-After": str(retry_after),
        "X-Airlock-Admission": f"shed; retry_after={retry_after}",
    }
    return body, headers


def threat_backoff_response_payload(
    exc: AirlockThreatBackoff,
) -> tuple[dict, dict]:
    """Build the non-disclosing response for a local threat backoff."""
    retry_after = retry_after_seconds(exc.retry_after)
    body = {
        "error": {
            "message": "Too many requests. Please retry later.",
            "type": "airlock_threat_backoff",
            "code": "threat_backoff",
            "param": None,
            "airlock": {
                "source": "threat_backoff",
                "retry_after": retry_after,
            },
        }
    }
    return body, {"Retry-After": str(retry_after)}


async def airlock_provider_blocked_handler(request: Any, exc: Exception):
    """FastAPI exception handler → 429 with Retry-After + enriched body."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockProviderBlocked)
    body, headers = block_response_payload(exc)
    return JSONResponse(status_code=429, content=body, headers=headers)


async def airlock_model_not_found_handler(request: Any, exc: Exception):
    """FastAPI exception handler → an enriched model-not-found response."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockModelNotFound)
    body, headers = model_not_found_response_payload(exc)
    return JSONResponse(status_code=404, content=body, headers=headers)


def invalid_reasoning_effort_response_payload(
    exc: AirlockInvalidReasoningEffort,
) -> tuple[dict, dict]:
    """Build the stable OpenAI-shaped P-2 validation response."""
    body = {
        "error": {
            "message": sanitize_reason(str(exc), 500),
            "type": "invalid_request_error",
            "code": "invalid_reasoning_effort",
            "param": "reasoning_effort",
            "airlock": {
                "requested": exc.requested,
                "model": exc.model,
                "supported": list(exc.supported),
            },
        }
    }
    return body, {}


def endpoint_not_supported_response_payload(
    exc: AirlockEndpointNotSupported,
) -> tuple[dict, dict]:
    """Build an OpenAI-compatible 400 for an unsupported model endpoint."""
    return (
        {
            "error": {
                "message": sanitize_reason(str(exc), 500),
                "type": "invalid_request_error",
                "code": "model_endpoint_not_supported",
                "param": "model",
                "airlock": {"endpoint": exc.endpoint, "model": exc.model},
            }
        },
        {},
    )


async def airlock_invalid_reasoning_effort_handler(request: Any, exc: Exception):
    """FastAPI exception handler → an OpenAI-compatible 400."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockInvalidReasoningEffort)
    body, headers = invalid_reasoning_effort_response_payload(exc)
    return JSONResponse(status_code=400, content=body, headers=headers)


async def airlock_endpoint_not_supported_handler(request: Any, exc: Exception):
    """FastAPI exception handler → an OpenAI-compatible 400."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockEndpointNotSupported)
    body, headers = endpoint_not_supported_response_payload(exc)
    return JSONResponse(status_code=400, content=body, headers=headers)


async def airlock_admission_shed_handler(request: Any, exc: Exception):
    """FastAPI exception handler → local admission 429 with Retry-After."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockAdmissionShed)
    body, headers = admission_shed_response_payload(exc)
    return JSONResponse(status_code=429, content=body, headers=headers)


async def airlock_threat_backoff_handler(request: Any, exc: Exception):
    """FastAPI exception handler → a local-threat 429 with Retry-After."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockThreatBackoff)
    body, headers = threat_backoff_response_payload(exc)
    return JSONResponse(status_code=429, content=body, headers=headers)


def install_airlock_error_handlers_on_proxy_app() -> bool:
    """Register Airlock-owned typed error handlers on the LiteLLM proxy app.

    Registered for the subclass specifically (not the base ``RateLimitError``) so
    passthrough provider 429s keep LiteLLM's own handling — the perimeter only
    shapes Airlock's own breaker blocks. Idempotent via ``app.state``.
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        return False

    app = resolve_proxy_app()
    if not isinstance(app, FastAPI):
        return False
    if not getattr(app.state, "airlock_provider_blocked_handler_installed", False):
        app.add_exception_handler(
            AirlockProviderBlocked, airlock_provider_blocked_handler
        )
        app.state.airlock_provider_blocked_handler_installed = True
    if not getattr(app.state, "airlock_model_not_found_handler_installed", False):
        app.add_exception_handler(AirlockModelNotFound, airlock_model_not_found_handler)
        app.state.airlock_model_not_found_handler_installed = True
    if not getattr(
        app.state, "airlock_invalid_reasoning_effort_handler_installed", False
    ):
        app.add_exception_handler(
            AirlockInvalidReasoningEffort, airlock_invalid_reasoning_effort_handler
        )
        app.state.airlock_invalid_reasoning_effort_handler_installed = True
    if not getattr(
        app.state, "airlock_endpoint_not_supported_handler_installed", False
    ):
        app.add_exception_handler(
            AirlockEndpointNotSupported, airlock_endpoint_not_supported_handler
        )
        app.state.airlock_endpoint_not_supported_handler_installed = True
    if not getattr(app.state, "airlock_admission_shed_handler_installed", False):
        app.add_exception_handler(AirlockAdmissionShed, airlock_admission_shed_handler)
        app.state.airlock_admission_shed_handler_installed = True
    if not getattr(app.state, "airlock_threat_backoff_handler_installed", False):
        app.add_exception_handler(AirlockThreatBackoff, airlock_threat_backoff_handler)
        app.state.airlock_threat_backoff_handler_installed = True
    app.state.airlock_error_handlers_installed = True
    return True
