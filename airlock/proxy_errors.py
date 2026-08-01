"""Typed Airlock rate-limit error + a FastAPI exception handler (workstream B).

When Airlock's circuit breaker blocks a request pre-flight it raises
:class:`AirlockProviderBlocked` (a ``RateLimitError`` subclass) so the client
receives an HTTP 429 with a ``Retry-After`` header and an enriched but
OpenAI-compatible body — distinguishable from a passthrough provider 429 without
string-parsing. The handler is registered on the LiteLLM proxy app via
:func:`install_airlock_error_handlers_on_proxy_app`, mirroring the other
``install_*_on_proxy_app`` hooks in ``model_override_headers``.
"""

from __future__ import annotations

import math
import re
from typing import Any

from litellm import RateLimitError

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
        self, requested_model: str, suggestions: list[dict[str, str | float]]
    ) -> None:
        self.requested_model = requested_model
        self.suggestions = suggestions
        models = [str(item["model"]) for item in suggestions if item.get("model")]
        message = (
            f"Model '{requested_model}' is not available. "
            f"Try '{models[0]}'. Alternatives: {', '.join(models[1:]) or 'none'}."
        )
        super().__init__(message)


class AirlockAdmissionShed(RateLimitError):
    """A local admission-gate rejection with an honest retry time."""

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message=message, llm_provider="airlock", model="admission")
        self.retry_after = float(retry_after)


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
    header = model_suggestion_header(exc.requested_model, exc.suggestions)
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


async def airlock_admission_shed_handler(request: Any, exc: Exception):
    """FastAPI exception handler → local admission 429 with Retry-After."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockAdmissionShed)
    body, headers = admission_shed_response_payload(exc)
    return JSONResponse(status_code=429, content=body, headers=headers)


def install_airlock_error_handlers_on_proxy_app() -> bool:
    """Register the AirlockProviderBlocked handler on the LiteLLM proxy app.

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
    if not getattr(app.state, "airlock_admission_shed_handler_installed", False):
        app.add_exception_handler(AirlockAdmissionShed, airlock_admission_shed_handler)
        app.state.airlock_admission_shed_handler_installed = True
    app.state.airlock_error_handlers_installed = True
    return True
