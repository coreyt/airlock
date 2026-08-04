"""Pluggable semantic-detection providers.

See :mod:`airlock.guardrails.providers.base` for the interface every provider
implements and the rules it must honor.
"""

from __future__ import annotations

from .base import (
    CLEAN,
    DETECTED,
    UNAVAILABLE,
    Availability,
    InjectionProvider,
    PreflightResult,
    ProviderVerdict,
    Transport,
)
from .registry import available_provider_names, build_providers, register_builder

__all__ = [
    "Availability",
    "CLEAN",
    "DETECTED",
    "InjectionProvider",
    "PreflightResult",
    "ProviderVerdict",
    "Transport",
    "UNAVAILABLE",
    "available_provider_names",
    "build_providers",
    "register_builder",
]
