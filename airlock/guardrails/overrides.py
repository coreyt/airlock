"""Backward-compatible re-export shim for the guardrail-skip resolver.

The real implementations live in :mod:`airlock.guardrail_overrides` (a neutral
top-level module with no ``fast``/``guardrails`` imports) so that both layers
can depend on the seam without forming an import cycle. Existing importers of
``airlock.guardrails.overrides`` (``proxy.py``, ``keyword_guard.py``) keep
working unchanged.

The single source of truth for module state (the ``_cfg`` global rebound by
``configure_guardrail_overrides``) lives in :mod:`airlock.guardrail_overrides`.
Attribute access that is not an explicit re-export below — e.g. ``_cfg``, the
config dataclass, or the private helpers — is proxied to that canonical module
via :pep:`562` ``__getattr__`` so reads (and ``from ... import _name``) always
reflect *live* state rather than a stale import-time copy.
"""

from __future__ import annotations

from airlock import guardrail_overrides as _canonical
from airlock.guardrail_overrides import (  # noqa: F401
    configure_guardrail_overrides,
    resolve_guardrail_decision,
)

__all__ = [
    "configure_guardrail_overrides",
    "resolve_guardrail_decision",
]


def __getattr__(name: str):
    """Proxy any non-re-exported attribute to the canonical module (PEP 562).

    Keeps ``airlock.guardrails.overrides._cfg`` (and the private helpers /
    dataclass) pointing at the live single source of truth, so config rebinds
    in :mod:`airlock.guardrail_overrides` are observed through the old path too.
    """
    return getattr(_canonical, name)
