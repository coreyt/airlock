"""Typed AirlockSettings loader — the single in-place reader for every ``fast/``
setting, with uniform ``env > config > default`` precedence and malformed-input
fallback.

Structure mirrors ``transparency.TransparencyConfig`` / ``load_transparency_config``;
the precedence ladder mirrors ``router._load_cost_tiers``. Budget blocks are parsed
with LiteLLM's own ``BudgetConfig`` + ``duration_in_seconds`` primitives.

As of pack 0.5.1-SET-unify the router (budget-aware swap), monitor (near-limit warn)
and circuit-breaker (failover) consumers read through this loader via
:func:`get_settings`. SET-unify also removed the hidden value-carrying provider-budget
and failover defaults (operator-confirmed behavior change): with no config and no env
override those maps are empty, which means no swap / no warn / no failover — identical
to the ``0 => no enforcement`` contract. The non-budget defaults (cost tiers, session
TTL, smart thresholds, warn ratio) are still reproduced verbatim. Config keys are read
in place — this loader never moves a key.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from litellm.litellm_core_utils.duration_parser import duration_in_seconds
from litellm.types.utils import BudgetConfig

logger = logging.getLogger("airlock.fast.settings")

# ---------------------------------------------------------------------------
# Defaults.
#   cost tiers:       router._DEFAULT_COST_TIERS
#   session ttl:      router._DEFAULT_SESSION_TTL
#   smart thresholds: router._DEFAULT_SMART_THRESHOLDS
#   warn ratio:       monitor._DEFAULT_BUDGET_WARN_RATIO
#
# Provider budgets and the failover map have NO hidden value-carrying default
# (operator-confirmed behavior change, SET-unify): with no config and no env
# override they are empty, which means no proactive swap / no warn / no failover
# (the falsy short-circuit, consistent with the `0 => no enforcement` contract).
# ---------------------------------------------------------------------------
_DEFAULT_PROVIDER_BUDGETS: dict[str, float] = {}

_DEFAULT_COST_TIERS: dict[str, list[str]] = {
    "low": [
        "claude-haiku",
        "gemini-flash",
        "gemini-flash-lite",
        "gpt-5-nano",
        "mistral-small",
    ],
    "medium": [
        "claude-sonnet",
        "gemini-pro",
        "gpt-5-mini",
        "mistral-medium",
        "codestral",
    ],
    "high": [
        "claude-opus",
        "gpt-5",
        "gpt-5-pro",
        "mistral-large",
        "magistral-medium",
    ],
}

_DEFAULT_FAILOVER_MAP: dict[str, list[str]] = {}

_DEFAULT_SESSION_TTL = 3600  # 1 hour
_DEFAULT_SMART_THRESHOLDS = (0.30, 0.60)
_DEFAULT_BUDGET_WARN_RATIO = 0.8
_DEFAULT_BUDGET_WINDOW = 86400.0  # "1d" in seconds


@dataclass(frozen=True)
class AirlockSettings:
    """Frozen snapshot of every ``fast/`` knob, read once at startup.

    ``provider_budgets`` maps provider -> USD daily cap; a value of ``0`` is a real
    configured value (``0 => no enforcement / unlimited / no swap``) and is
    preserved, never coerced to a default. ``budget_windows`` maps provider ->
    enforcement window in seconds (parsed from ``time_period``; default 86400).
    """

    provider_budgets: dict[str, float] = field(default_factory=dict)
    budget_windows: dict[str, float] = field(default_factory=dict)
    failover_map: dict[str, list[str]] = field(default_factory=dict)
    cost_tiers: dict[str, list[str]] = field(
        default_factory=lambda: dict(_DEFAULT_COST_TIERS)
    )
    session_ttl: int = _DEFAULT_SESSION_TTL
    smart_thresholds: tuple[float, float] = _DEFAULT_SMART_THRESHOLDS
    budget_warn_ratio: float = _DEFAULT_BUDGET_WARN_RATIO


# ---------------------------------------------------------------------------
# Per-field loaders (uniform env > config > default; malformed -> next level)
# ---------------------------------------------------------------------------
def _parse_budget_block(
    block: object,
) -> tuple[dict[str, float], dict[str, float]] | None:
    """Parse ``router_settings.provider_budget_config`` via LiteLLM ``BudgetConfig``.

    Returns ``(budgets, windows)`` or ``None`` if the block itself is not a mapping
    (caller falls back to defaults). Malformed *per-provider* entries are skipped
    (treated as no budget) with a warning. ``budget_limit: 0`` is preserved as
    ``0.0``.
    """
    if not isinstance(block, dict):
        return None
    budgets: dict[str, float] = {}
    windows: dict[str, float] = {}
    for prov, entry in block.items():
        if not isinstance(entry, dict):
            logger.warning(
                "provider_budget_config[%s] is not a mapping, skipping", prov
            )
            continue
        try:
            parsed = BudgetConfig(**entry)
        except Exception:  # noqa: BLE001 — tolerate any malformed budget block
            logger.warning("Invalid provider_budget_config[%s], skipping", prov)
            continue
        if parsed.max_budget is None:
            continue
        budgets[str(prov)] = float(parsed.max_budget)
        period = parsed.budget_duration or "1d"
        try:
            windows[str(prov)] = float(duration_in_seconds(period))
        except Exception:  # noqa: BLE001 — bad duration -> default window
            windows[str(prov)] = _DEFAULT_BUDGET_WINDOW
    return budgets, windows


def _load_provider_budgets(
    router_settings: dict, env: Mapping[str, str]
) -> tuple[dict[str, float], dict[str, float]]:
    raw = env.get("AIRLOCK_PROVIDER_BUDGETS")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                budgets = {str(k): float(v) for k, v in parsed.items()}
                windows = {k: _DEFAULT_BUDGET_WINDOW for k in budgets}
                return budgets, windows
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        logger.warning(
            "Invalid AIRLOCK_PROVIDER_BUDGETS, falling back to config/defaults"
        )

    block = router_settings.get("provider_budget_config")
    if block is not None:
        result = _parse_budget_block(block)
        if result is not None:
            return result
        logger.warning("Invalid provider_budget_config, using default budgets")

    budgets = dict(_DEFAULT_PROVIDER_BUDGETS)
    windows = {k: _DEFAULT_BUDGET_WINDOW for k in budgets}
    return budgets, windows


def _convert_fallbacks(fallbacks: object) -> dict[str, list[str]] | None:
    """Convert the ``fallbacks`` list-of-single-key-dicts into ``dict``."""
    if not isinstance(fallbacks, list):
        return None
    result: dict[str, list[str]] = {}
    for item in fallbacks:
        if isinstance(item, dict) and len(item) == 1:
            ((key, value),) = item.items()
            if isinstance(value, list):
                result[str(key)] = [str(v) for v in value]
    return result or None


def _load_failover_map(
    router_settings: dict, env: Mapping[str, str]
) -> dict[str, list[str]]:
    raw = env.get("AIRLOCK_FAILOVER_MAP")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and all(
                isinstance(vals, list) for vals in parsed.values()
            ):
                return {str(k): [str(v) for v in vals] for k, vals in parsed.items()}
        except (json.JSONDecodeError, TypeError):
            pass
        logger.warning(
            "Invalid AIRLOCK_FAILOVER_MAP shape, falling back to config/defaults"
        )

    converted = _convert_fallbacks(router_settings.get("fallbacks"))
    if converted is not None:
        return converted

    return dict(_DEFAULT_FAILOVER_MAP)


def _validate_cost_tiers(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, dict):
        return None
    validated: dict[str, list[str]] = {}
    for tier, models in value.items():
        if not isinstance(models, list):
            return None
        validated[str(tier)] = [str(m) for m in models]
    return validated


def _load_cost_tiers(config: dict, env: Mapping[str, str]) -> dict[str, list[str]]:
    raw = env.get("AIRLOCK_COST_TIERS")
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Invalid AIRLOCK_COST_TIERS JSON, falling back to config/defaults"
            )
        else:
            validated = _validate_cost_tiers(parsed)
            if validated is not None:
                return validated
            logger.warning("AIRLOCK_COST_TIERS has wrong shape, falling back")

    cfg_tiers = config.get("cost_tiers")
    if cfg_tiers is not None:
        validated = _validate_cost_tiers(cfg_tiers)
        if validated is not None:
            return validated
        logger.warning("Invalid cost_tiers in config, using defaults")

    return dict(_DEFAULT_COST_TIERS)


def _load_session_ttl(airlock_settings: dict, env: Mapping[str, str]) -> int:
    raw = env.get("AIRLOCK_SESSION_TTL")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "Invalid AIRLOCK_SESSION_TTL, falling back to config/defaults"
            )

    val = airlock_settings.get("session_ttl")
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            logger.warning("Invalid airlock_settings.session_ttl, using default")

    return _DEFAULT_SESSION_TTL


def _coerce_thresholds(value: object) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (float(value[0]), float(value[1]))
    raise ValueError("smart_thresholds must be a 2-element sequence")


def _load_smart_thresholds(
    airlock_settings: dict, env: Mapping[str, str]
) -> tuple[float, float]:
    raw = env.get("AIRLOCK_SMART_THRESHOLDS")
    if raw:
        try:
            return _coerce_thresholds(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning(
                "Invalid AIRLOCK_SMART_THRESHOLDS, falling back to config/defaults"
            )

    val = airlock_settings.get("smart_thresholds")
    if val is not None:
        try:
            return _coerce_thresholds(val)
        except (TypeError, ValueError):
            logger.warning("Invalid airlock_settings.smart_thresholds, using default")

    return _DEFAULT_SMART_THRESHOLDS


def _load_budget_warn_ratio(airlock_settings: dict, env: Mapping[str, str]) -> float:
    raw = env.get("AIRLOCK_BUDGET_WARN_RATIO")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "Invalid AIRLOCK_BUDGET_WARN_RATIO, falling back to config/defaults"
            )

    val = airlock_settings.get("budget_warn_ratio")
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            logger.warning("Invalid airlock_settings.budget_warn_ratio, using default")

    return _DEFAULT_BUDGET_WARN_RATIO


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def load_airlock_settings(
    config: dict | None, env: Mapping[str, str] | None = None
) -> AirlockSettings:
    """Build :class:`AirlockSettings` with uniform ``env > config > default``.

    ``env`` defaults to ``os.environ``; it is injectable for testing. Reads keys in
    place: ``router_settings.provider_budget_config`` (budgets/windows),
    ``router_settings.fallbacks`` (failover map), top-level ``cost_tiers``, and the
    ``airlock_settings`` block (session_ttl / smart_thresholds / budget_warn_ratio).
    """
    if env is None:
        env = os.environ
    config = config or {}
    router_settings = _as_dict(config.get("router_settings"))
    airlock_settings = _as_dict(config.get("airlock_settings"))

    provider_budgets, budget_windows = _load_provider_budgets(router_settings, env)

    return AirlockSettings(
        provider_budgets=provider_budgets,
        budget_windows=budget_windows,
        failover_map=_load_failover_map(router_settings, env),
        cost_tiers=_load_cost_tiers(config, env),
        session_ttl=_load_session_ttl(airlock_settings, env),
        smart_thresholds=_load_smart_thresholds(airlock_settings, env),
        budget_warn_ratio=_load_budget_warn_ratio(airlock_settings, env),
    )


# ---------------------------------------------------------------------------
# Module-level singleton seam (mirrors transparency.configure_transparency)
# ---------------------------------------------------------------------------
_configured: AirlockSettings | None = None


def configure_settings(config: dict | None) -> None:
    """Set the module-global settings from ``load_airlock_settings`` (read at startup)."""
    global _configured
    _configured = load_airlock_settings(config)


def get_settings() -> AirlockSettings:
    """Return the configured settings, or safe defaults if never configured.

    When unconfigured we build via ``load_airlock_settings({})`` (not a bare
    ``AirlockSettings()``) so every field gets its real default — including the
    non-budget defaults (cost tiers, session TTL, smart thresholds, warn ratio).
    Provider budgets and the failover map have no hidden default, so they are empty
    unless config or an env override supplies them.
    """
    if _configured is None:
        return load_airlock_settings({})
    return _configured
