"""
Airlock Health — circuit breaker state endpoint.

Exposes ``/health/circuits`` so operators can see which models have
open or half-open circuits.  Installed onto the LiteLLM FastAPI app
by the model-override-headers callback at startup.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from airlock.fast.state import CircuitState, StateStore


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


def install_circuit_health_on_proxy_app() -> bool:
    """Install the circuit health endpoint on the LiteLLM proxy app."""
    try:
        from fastapi import FastAPI
    except ImportError:
        return False

    proxy_server = sys.modules.get("litellm.proxy.proxy_server")
    app = getattr(proxy_server, "app", None)
    if not isinstance(app, FastAPI):
        return False
    install_circuit_health_endpoint(app)
    return True
