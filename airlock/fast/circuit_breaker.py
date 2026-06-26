"""
Airlock Fast — Circuit breaker for model/provider failover.

Detects when an upstream model becomes unavailable (consecutive failures
exceed a threshold) and transparently re-routes to a healthy fallback.

The failover map derives from ``router_settings.fallbacks`` in config.yaml (via
``get_settings()``), with an optional ``AIRLOCK_FAILOVER_MAP`` environment override.
There is no hidden default: an unconfigured deployment has no failover targets.
Failover targets are constrained to aliases present in the loaded ``model_list``
catalog (``router.known_model_aliases``) so a typo or override can't reroute to a
non-existent alias; when the catalog is empty/unconfigured, filtering is disabled.

Env vars:
    AIRLOCK_FAILOVER_MAP — JSON mapping of model → fallback list, e.g.
        {"claude-sonnet": ["claude-haiku", "gpt-5-mini"]}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .router import infer_provider, known_model_aliases
from .settings import get_settings
from .state import store

logger = logging.getLogger("airlock.fast.circuit_breaker")


def _load_failover_map() -> dict[str, list[str]]:
    """Failover map, derived from ``router_settings.fallbacks`` (SET-unify).

    Thin accessor over :func:`get_settings`: the env override
    (``AIRLOCK_FAILOVER_MAP``) and the list-of-dicts -> dict conversion are handled
    there. There is no hidden default — an unconfigured deployment has no failover
    targets. Kept as a function because ``airlock/tui/screens/overview.py`` imports it.
    """
    return get_settings().failover_map


@dataclass
class FailoverResult:
    """Outcome of a circuit-breaker check."""

    original_model: str
    allowed: bool  # True if original model is healthy
    failover_model: str | None  # suggested replacement (if any)
    circuit_state: str  # current state label
    reason: str


def check_model(model_name: str) -> FailoverResult:
    """Check if *model_name* is available; suggest a failover if not."""
    return check_model_with_filters(model_name)


def check_model_with_filters(
    model_name: str,
    *,
    blocked_providers: set[str] | None = None,
    blocked_models: set[str] | None = None,
) -> FailoverResult:
    """Check if *model_name* is available; suggest a filtered failover if not."""
    model_state = store.get_model(model_name)
    failover_map = _load_failover_map()
    blocked_providers = blocked_providers or set()
    blocked_models = blocked_models or set()
    current_provider = infer_provider(model_name)

    if (
        model_name not in blocked_models
        and (current_provider is None or current_provider not in blocked_providers)
        and store.should_allow_request(model_name)
    ):
        return FailoverResult(
            original_model=model_name,
            allowed=True,
            failover_model=None,
            circuit_state=model_state.circuit.value,
            reason="model_healthy",
        )

    if model_name in blocked_models or (
        current_provider is not None and current_provider in blocked_providers
    ):
        reason = f"provider_quarantined({current_provider})"
    else:
        reason = f"circuit_open(failures={model_state.consecutive_failures})"

    # Circuit is open — look for a healthy fallback. Constrain candidates to aliases
    # that actually exist in the loaded model_list catalog so a typo'd config fallback
    # or AIRLOCK_FAILOVER_MAP override can't reroute to a non-existent LiteLLM alias
    # (unknown models are healthy-by-default in the state store). Safe fallback: when
    # the catalog is empty/unconfigured we cannot validate, so we do NOT filter.
    catalog = known_model_aliases()
    for fallback in failover_map.get(model_name, []):
        if fallback in blocked_models:
            continue
        if catalog and fallback not in catalog:
            logger.warning(
                "circuit_open model=%s skipping failover=%s not in model_list catalog",
                model_name,
                fallback,
            )
            continue
        provider = infer_provider(fallback)
        if provider and provider in blocked_providers:
            continue
        if store.should_allow_request(fallback):
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
                reason=reason,
            )

    # No healthy fallback
    logger.error("circuit_open model=%s no_healthy_fallback available", model_name)
    return FailoverResult(
        original_model=model_name,
        allowed=False,
        failover_model=None,
        circuit_state=model_state.circuit.value,
        reason="all_models_unavailable",
    )
