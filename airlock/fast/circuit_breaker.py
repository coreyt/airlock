"""
Airlock Fast — Circuit breaker for model/provider failover.

Detects when an upstream model becomes unavailable (consecutive failures
exceed a threshold) and transparently re-routes to a healthy fallback.

The failover map is configurable via the AIRLOCK_FAILOVER_MAP environment
variable (JSON object) or falls back to sensible defaults derived from
the models already declared in config.yaml.

Env vars:
    AIRLOCK_FAILOVER_MAP — JSON mapping of model → fallback list, e.g.
        {"claude-sonnet": ["claude-haiku", "gpt-4o"]}
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from .state import store

logger = logging.getLogger("airlock.fast.circuit_breaker")


# ---------------------------------------------------------------------------
# Default failover map (mirrors models declared in config.yaml)
# ---------------------------------------------------------------------------
_DEFAULT_FAILOVER_MAP: dict[str, list[str]] = {
    "claude-sonnet": ["claude-haiku", "gpt-4o"],
    "claude-haiku": ["claude-sonnet", "gpt-4o-mini"],
    "claude-opus": ["claude-sonnet", "gpt-4o"],
    "gpt-4o": ["claude-sonnet", "gpt-4o-mini"],
    "gpt-4o-mini": ["claude-haiku", "gpt-4o"],
}


def _load_failover_map() -> dict[str, list[str]]:
    raw = os.getenv("AIRLOCK_FAILOVER_MAP")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("invalid AIRLOCK_FAILOVER_MAP JSON, using defaults")
    return _DEFAULT_FAILOVER_MAP


@dataclass
class FailoverResult:
    """Outcome of a circuit-breaker check."""
    original_model: str
    allowed: bool                   # True if original model is healthy
    failover_model: str | None      # suggested replacement (if any)
    circuit_state: str              # current state label
    reason: str


def check_model(model_name: str) -> FailoverResult:
    """Check if *model_name* is available; suggest a failover if not."""
    model_state = store.get_model(model_name)
    failover_map = _load_failover_map()

    if model_state.should_allow_request():
        return FailoverResult(
            original_model=model_name,
            allowed=True,
            failover_model=None,
            circuit_state=model_state.circuit.value,
            reason="model_healthy",
        )

    # Circuit is open — look for a healthy fallback
    for fallback in failover_map.get(model_name, []):
        fallback_state = store.get_model(fallback)
        if fallback_state.should_allow_request():
            logger.warning(
                "circuit_open model=%s failover=%s consecutive_failures=%d",
                model_name,
                fallback,
                model_state.consecutive_failures,
            )
            return FailoverResult(
                original_model=model_name,
                allowed=False,
                failover_model=fallback,
                circuit_state=model_state.circuit.value,
                reason=f"circuit_open(failures={model_state.consecutive_failures})",
            )

    # No healthy fallback
    logger.error(
        "circuit_open model=%s no_healthy_fallback available", model_name
    )
    return FailoverResult(
        original_model=model_name,
        allowed=False,
        failover_model=None,
        circuit_state=model_state.circuit.value,
        reason="all_models_unavailable",
    )
