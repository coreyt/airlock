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
import sys
from typing import Any

from litellm import RateLimitError


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


def retry_after_seconds(cooldown_seconds: float) -> int:
    """Whole-second ``Retry-After`` value, at least 1."""
    return max(1, math.ceil(cooldown_seconds))


def _sanitize_reason(reason: str | None) -> str:
    """Bound the reason text so upstream detail can't bloat or leak via the body."""
    return str(reason or "")[:300]


def block_response_payload(exc: AirlockProviderBlocked) -> tuple[dict, dict]:
    """Build the (body, headers) for an Airlock block. OpenAI-shaped, enriched."""
    retry_after = retry_after_seconds(exc.cooldown_seconds)
    body = {
        "error": {
            "message": str(getattr(exc, "message", "") or exc),
            "type": "airlock_circuit_breaker",
            "code": "provider_blocked",
            "param": None,
            "airlock": {
                "scope": exc.scope,
                "provider": exc.llm_provider,
                "cooldown_seconds": round(exc.cooldown_seconds, 1),
                "retry_after": retry_after,
                "reason": _sanitize_reason(exc.reason),
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


async def airlock_provider_blocked_handler(request: Any, exc: Exception):
    """FastAPI exception handler → 429 with Retry-After + enriched body."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, AirlockProviderBlocked)
    body, headers = block_response_payload(exc)
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

    proxy_server = sys.modules.get("litellm.proxy.proxy_server")
    app = getattr(proxy_server, "app", None)
    if not isinstance(app, FastAPI):
        return False
    if getattr(app.state, "airlock_error_handlers_installed", False):
        return True
    app.add_exception_handler(AirlockProviderBlocked, airlock_provider_blocked_handler)
    app.state.airlock_error_handlers_installed = True
    return True
