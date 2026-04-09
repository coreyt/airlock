"""Airlock guardrails package."""

from __future__ import annotations

import os


def _env_flag(name: str, default: bool = True) -> bool:
    """Return True/False from an env var, defaulting to ``default`` when unset."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("false", "0", "no", "off", "")


__all__ = ["_env_flag"]
