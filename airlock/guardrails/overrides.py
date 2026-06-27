"""Backward-compatible re-export shim for the guardrail-skip resolver.

The real implementations live in :mod:`airlock.guardrail_overrides` (a neutral
top-level module with no ``fast``/``guardrails`` imports) so that both layers
can depend on the seam without forming an import cycle. Existing importers of
``airlock.guardrails.overrides`` (``proxy.py``, ``keyword_guard.py``) keep
working unchanged.

The single source of truth for ``_cfg`` lives in :mod:`airlock.guardrail_overrides`;
``configure_guardrail_overrides`` / ``resolve_guardrail_decision`` both operate on
that module global. ``_cfg`` and the config dataclass are re-exported here for
tests that introspect/restore module state.
"""

from __future__ import annotations

from airlock.guardrail_overrides import (  # noqa: F401
    _authenticated_client_id,
    _cfg,
    _DECISION_KEY,
    _GuardrailOverrideConfig,
    _header_value,
    configure_guardrail_overrides,
    resolve_guardrail_decision,
)

__all__ = [
    "configure_guardrail_overrides",
    "resolve_guardrail_decision",
]
