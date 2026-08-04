"""Provider registry — maps provider names to their environment builders.

Adding a backend (Azure Prompt Shields, a self-hosted detector, an LLM judge)
means writing a module that satisfies
:class:`~airlock.guardrails.providers.base.InjectionProvider` and adding one
entry to :data:`_BUILDERS`. Nothing else in Airlock changes.

``AIRLOCK_INJECTION_PROVIDERS`` selects which builders run, as a comma-separated
list. When unset, every known builder is attempted and each decides for itself
whether its own configuration is present — so a deployment enables a provider
purely through that provider's own settings.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from .base import InjectionProvider
from .model_armor import PROVIDER_NAME as MODEL_ARMOR
from .model_armor import build_from_env as _build_model_armor

logger = logging.getLogger("airlock.guardrails.providers.registry")

#: name → builder returning a configured provider, or None when not enabled.
#: Builders receive the same environment mapping the registry was called with
#: (``None`` meaning ``os.environ``) so a caller can configure providers
#: programmatically without mutating the process environment.
_BUILDERS: dict[str, Callable[[dict[str, str] | None], InjectionProvider | None]] = {
    MODEL_ARMOR: _build_model_armor,
}


def available_provider_names() -> tuple[str, ...]:
    """Every provider name Airlock knows how to build."""
    return tuple(_BUILDERS)


def register_builder(
    name: str, builder: Callable[[dict[str, str] | None], InjectionProvider | None]
) -> None:
    """Add or replace a provider builder (used by tests and extensions)."""
    _BUILDERS[name] = builder


def _requested(env: dict[str, str] | None = None) -> list[str]:
    source = os.environ if env is None else env
    raw = source.get("AIRLOCK_INJECTION_PROVIDERS", "").strip()
    if not raw:
        return list(_BUILDERS)
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_providers(env: dict[str, str] | None = None) -> list[InjectionProvider]:
    """Construct every requested and configured provider.

    A builder that raises is logged and skipped rather than taking down proxy
    startup — but the resulting classifier then reports itself unavailable, so
    the misconfiguration stays visible instead of masquerading as clean traffic.
    """
    providers: list[InjectionProvider] = []
    for name in _requested(env):
        builder = _BUILDERS.get(name)
        if builder is None:
            logger.warning(
                "unknown_injection_provider name=%s known=%s",
                name,
                ",".join(_BUILDERS),
            )
            continue
        try:
            provider = builder(env)
        except Exception as exc:  # noqa: BLE001 - startup must not crash
            logger.error("injection_provider_build_failed name=%s error=%s", name, exc)
            continue
        if provider is not None:
            providers.append(provider)
            logger.info("injection_provider_configured name=%s", name)
    return providers
