"""
Airlock Health — probe endpoints and circuit breaker state.

Exposes ``/health/circuits`` so operators can see which models have
open or half-open circuits, plus the canonical probe surface
(``/livez``, ``/readyz``, ``/healthz``, ``/health/live``, ``/health/ready``)
and a replacement for ``GET /health``.

Installed onto the LiteLLM FastAPI app by the model-override-headers callback
at startup.

``GET /health`` is **replaced, not extended**. LiteLLM's implementation fires a
live completion to every configured model when ``background_health_checks`` is
off, which makes the most-probed path in the ecosystem the most expensive one.
Deep per-model results remain available from the background health-check loop
via the cached ``/health/latest``; nothing here ever calls a model. See
``dev/notes/design-health-endpoints.md``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from airlock.fast.state import CircuitState, StateStore
from airlock.health_checks import (
    aggregate_report,
    liveness_report,
    readiness_report,
    render,
)
from airlock.litellm_adapter import resolve_proxy_app

logger = logging.getLogger("airlock.health")


class HealthRouteInstallError(RuntimeError):
    """Raised when ``GET /health`` could not be replaced.

    Deliberately fatal. If LiteLLM restructures the route and the removal
    silently no-ops, the expensive endpoint keeps serving while the changelog
    and documentation claim it is cheap — a hazard that is invisible precisely
    because everything looks fine.
    """


def get_circuit_health(state_store: StateStore) -> dict[str, Any]:
    """Build a JSON-serializable summary of all circuit breaker states."""
    models = state_store.all_models()
    circuits: dict[str, dict[str, Any]] = {}
    has_degraded = False

    for name, model_state in sorted(models.items()):
        state_val = model_state.circuit.value
        if model_state.circuit != CircuitState.CLOSED:
            has_degraded = True
        circuits[name] = {
            "state": state_val,
            "consecutive_failures": model_state.consecutive_failures,
            "last_state_change": model_state.last_state_change,
        }

    return {
        "status": "degraded" if has_degraded else "ok",
        "timestamp": time.time(),
        "circuits": circuits,
    }


def install_circuit_health_endpoint(
    app: Any,
    state_store: StateStore | None = None,
) -> None:
    """Register ``GET /health/circuits`` on the given FastAPI app."""
    from fastapi.responses import JSONResponse

    if getattr(app.state, "airlock_circuit_health_installed", False):
        return

    if state_store is None:
        from airlock.fast.state import store

        state_store = store

    # Capture in closure
    _store = state_store

    @app.get(
        "/health/circuits",
        include_in_schema=True,
        tags=["Airlock"],
        summary="Circuit breaker state for all models",
    )
    async def circuit_health() -> JSONResponse:
        data = get_circuit_health(_store)
        return JSONResponse(content=data)

    app.state.airlock_circuit_health_installed = True


# ---------------------------------------------------------------------------
# Canonical probe endpoints
# ---------------------------------------------------------------------------
def _router_model_count() -> int | None:
    """Configured model count from the LiteLLM router, or None if unknown.

    Read-only: inspects the already-loaded model list and never triggers a call.
    """
    try:
        from litellm.proxy.proxy_server import llm_model_list

        if llm_model_list is None:
            return None
        return len(llm_model_list)
    except Exception:  # noqa: BLE001 - a probe must not fail on introspection
        return None


def _database_state() -> tuple[bool, bool | None]:
    """Return ``(configured, reachable)`` for the optional proxy database."""
    try:
        from litellm.proxy.proxy_server import prisma_client

        if prisma_client is None:
            return False, None
        # Presence of a connected client is the cached signal; querying here
        # would put I/O on a probe path.
        return True, True
    except Exception:  # noqa: BLE001
        return False, None


def _build_report(kind: str) -> Any:
    if kind == "live":
        return liveness_report()
    db_configured, db_reachable = _database_state()
    builder = readiness_report if kind == "ready" else aggregate_report
    return builder(
        configured_models=_router_model_count(),
        db_configured=db_configured,
        db_reachable=db_reachable,
    )


def install_probe_endpoints(app: Any) -> None:
    """Register the canonical probe surface on the given FastAPI app.

    Additive and unauthenticated. Legacy ``/health/liveliness``,
    ``/health/liveness`` and ``/health/readiness`` are left untouched — several
    in-repo consumers and user-authored manifests depend on their exact bodies.
    """
    from fastapi import Response
    from fastapi.responses import JSONResponse

    if getattr(app.state, "airlock_probe_endpoints_installed", False):
        return

    def _respond(kind: str) -> JSONResponse:
        payload, status_code, media_type = render(_build_report(kind))
        return JSONResponse(
            content=payload, status_code=status_code, media_type=media_type
        )

    # (path, kind, summary). HEAD is registered explicitly because FastAPI does
    # not synthesize it from GET, and several uptime checkers default to HEAD.
    routes: tuple[tuple[str, str, str], ...] = (
        ("/livez", "live", "Liveness probe (Kubernetes convention)"),
        ("/readyz", "ready", "Readiness probe (Kubernetes convention)"),
        ("/healthz", "aggregate", "Aggregate health (no model calls)"),
        ("/health/live", "live", "Liveness probe (MicroProfile convention)"),
        ("/health/ready", "ready", "Readiness probe (MicroProfile convention)"),
    )

    for path, kind, summary in routes:

        def _make(kind: str = kind):
            async def _handler() -> JSONResponse:
                return _respond(kind)

            return _handler

        app.get(path, include_in_schema=True, tags=["Airlock"], summary=summary)(
            _make()
        )

        def _make_head(kind: str = kind):
            async def _head_handler() -> Response:
                _, status_code, media_type = render(_build_report(kind))
                return Response(status_code=status_code, media_type=media_type)

            return _head_handler

        app.head(path, include_in_schema=False)(_make_head())

    app.state.airlock_probe_endpoints_installed = True


def replace_health_endpoint(app: Any) -> None:
    """Replace LiteLLM's expensive ``GET /health`` with the cheap aggregate.

    FastAPI resolves routes in registration order, so simply registering another
    ``/health`` would never match — the inherited route must be removed first.

    Raises :class:`HealthRouteInstallError` when no ``GET /health`` route is
    found, rather than silently doing nothing. A no-op would leave live model
    calls being fired by the most-probed path in the ecosystem while every
    document in the repository states otherwise.
    """
    from fastapi.responses import JSONResponse

    if getattr(app.state, "airlock_health_replaced", False):
        return

    existing = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/health"
        and "GET" in (getattr(route, "methods", None) or set())
    ]
    if not existing:
        raise HealthRouteInstallError(
            "no GET /health route found to replace; LiteLLM may have "
            "restructured its health endpoints. Refusing to continue: the "
            "inherited endpoint fires live completions to every configured "
            "model."
        )

    for route in existing:
        app.router.routes.remove(route)
    logger.info("health_route_replaced removed=%d", len(existing))

    @app.get(
        "/health",
        include_in_schema=True,
        tags=["Airlock"],
        summary="Aggregate health (no model calls)",
    )
    async def airlock_health() -> JSONResponse:
        payload, status_code, media_type = render(_build_report("aggregate"))
        return JSONResponse(
            content=payload, status_code=status_code, media_type=media_type
        )

    app.state.airlock_health_replaced = True


def install_health_surface(app: Any) -> None:
    """Install the probe endpoints and replace ``GET /health``."""
    install_probe_endpoints(app)
    replace_health_endpoint(app)


def install_circuit_health_on_proxy_app() -> bool:
    """Install the circuit health endpoint on the LiteLLM proxy app."""
    try:
        from fastapi import FastAPI
    except ImportError:
        return False

    app = resolve_proxy_app()
    if not isinstance(app, FastAPI):
        return False
    install_circuit_health_endpoint(app)
    install_health_surface(app)
    return True
