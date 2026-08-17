"""Authenticated public-body contract for OpenAI embedding requests."""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute, request_response
from starlette.responses import JSONResponse

from airlock.embedding import (
    AirlockInvalidEmbeddingOption,
    validate_embedding_client_body,
)
from airlock.litellm_adapter import resolve_proxy_app

_EMBEDDING_PATHS = {"/v1/embeddings", "/embeddings"}


def _is_embedding_path(path: str) -> bool:
    """Return whether *path* is any LiteLLM OpenAI-compatible embedding route."""
    normalized = path.rstrip("/") or "/"
    return (
        normalized in _EMBEDDING_PATHS
        or (normalized.startswith("/engines/") and normalized.endswith("/embeddings"))
        or (
            normalized.startswith("/openai/deployments/")
            and normalized.endswith("/embeddings")
        )
    )


def _invalid_option_response(exc: AirlockInvalidEmbeddingOption) -> JSONResponse:
    """Return the established OpenAI-shaped validation response."""
    return JSONResponse(status_code=400, content={"error": exc.to_dict()})


def _install_authenticated_embedding_validator(route: APIRoute) -> None:
    """Insert validation after LiteLLM's auth dependency, before its endpoint.

    LiteLLM authenticates embeddings as a FastAPI dependency and reads/caches the
    request body there. Wrapping the endpoint dependent therefore reuses that
    authenticated cached body without introducing an outer pre-auth body read.
    This protects the normal authentication/resource boundary while still
    distinguishing client fields from LiteLLM's later proxy-generated fields.
    """
    if getattr(route, "_airlock_embedding_validator_installed", False):
        return
    original = route.dependant.call

    async def _validated_endpoint(**kwargs: Any) -> Any:
        request = kwargs["request"]
        try:
            body = await request.json()
        except ValueError:
            # Preserve LiteLLM's ordinary malformed-body response behavior.
            return await original(**kwargs)
        try:
            validate_embedding_client_body(body)
        except AirlockInvalidEmbeddingOption as exc:
            return _invalid_option_response(exc)
        return await original(**kwargs)

    route.dependant.call = _validated_endpoint
    # Route handlers close over the dependant. Rebuild it after replacing the
    # call so both pre- and post-start bootstrap paths use the wrapped endpoint.
    route.app = request_response(route.get_route_handler())
    route._airlock_embedding_validator_installed = True


def install_embedding_request_boundary_on_proxy_app() -> bool:
    """Install the idempotent authenticated embedding body boundary."""
    app = resolve_proxy_app()
    if app is None or getattr(app, "state", None) is None:
        return False
    if getattr(app.state, "airlock_embedding_boundary_installed", False):
        return True
    for route in app.routes:
        if (
            isinstance(route, APIRoute)
            and "POST" in route.methods
            and _is_embedding_path(route.path)
        ):
            _install_authenticated_embedding_validator(route)
    app.state.airlock_embedding_boundary_installed = True
    return True
