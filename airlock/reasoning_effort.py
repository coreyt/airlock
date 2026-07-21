"""Normalize ``reasoning_effort`` per target provider before the request leaves
the proxy, so client intent survives litellm's ``drop_params``.

The headline case: a client sends ``reasoning_effort="none"`` for an OpenAI model.
OpenAI's enum is ``{minimal, low, medium, high}`` — ``"none"`` is invalid, so
``drop_params: true`` silently strips it and the model falls back to its
**default** (often high) reasoning, the opposite of what "none" meant. We
translate an "off"-intent value to the target provider's real floor here, in the
guardian pre-call hook, which runs *before* litellm validates/drops params.

Note: dropping is not the same as "off" — that yields the model default. Only an
explicit translation to each provider's lowest valid setting honours intent:
  * OpenAI/Azure → ``"minimal"`` (no true "off" for reasoning models)
  * Gemini       → ``"disable"`` (thinking budget 0)
  * Anthropic    → omit the param (no extended thinking; Anthropic has no enum,
                   litellm maps the value to a thinking budget)
Unknown providers and genuinely-unknown values are left for ``drop_params``.

0.5.8 (P-2 / P-6c, design §5 + §13) — WARN-ONLY strict validation
----------------------------------------------------------------
The translation above is itself a guess, and for newer families it guesses
*wrong*: on gpt-5.6+ ``none`` is VALID and ``minimal`` is REJECTED, so rewriting
``none`` → ``minimal`` gets the param dropped and the model falls back to its
default (high) reasoning — a client asking for no reasoning gets the maximum and
pays for it.

The fix is to stop rewriting and reject invalid values outright, but that turns
silent behaviour into 400s for a client population that can only be *measured*,
not enumerated from the code. So this release ships **warn-only**: it computes
what strict validation would decide and reports it as ``effort_would_reject``
(WARNING log + mutation-ledger entry), while sending byte-identical values
upstream. Enforcement follows once the measurement window closes (design §13.3).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from airlock.transparency import record_mutation

logger = logging.getLogger("airlock.reasoning_effort")

# Tokens a caller uses to mean "turn reasoning off / as low as possible".
_OFF_INTENT = {"none", "off", "disable", "disabled", "false", "no", "0"}
_OPENAI_VALID = {"minimal", "low", "medium", "high"}
_GEMINI_VALID = {"disable", "low", "medium", "high"}
_OPENAI_PROVIDERS = {"openai", "azure", "azure_ai"}

# Levels every reasoning model accepts; the litellm map carries no per-level flag
# for these, so they are the floor of any computed support set.
_ALWAYS_SUPPORTED = frozenset({"low", "medium", "high"})

# Optional levels, each gated by its own litellm capability flag. Absent-or-None
# means "not supported" — never assume a level exists because a flag is missing.
#
# DELIBERATE DECISION (design §5.2, §11 Q2): `max` is treated as UNSUPPORTED.
# litellm 1.89.0 exposes `supports_max_reasoning_effort` but leaves it None for
# every model in the map, including the 5.6 family. With no funded key to verify
# `max` against the live API, we trust the map rather than allow-list a level we
# cannot confirm. REVISIT when upstream sets the flag (or when a key is
# available to test): if `max` turns out to be real, it becomes a false positive
# in the warn-only counts and must be corrected BEFORE enforcement ships.
_FLAGGED_LEVELS = {
    "none": "supports_none_reasoning_effort",
    "minimal": "supports_minimal_reasoning_effort",
    "xhigh": "supports_xhigh_reasoning_effort",
    "max": "supports_max_reasoning_effort",
}


def _enabled() -> bool:
    return os.getenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


# ---------------------------------------------------------------------------
# Alias -> provider body resolution (design §5.2 step 1)
# ---------------------------------------------------------------------------
# `data["model"]` is an Airlock alias, which may be semantic (`gpt-5`) with no
# textual relationship to the body it routes to. Prefix-stripping alone cannot
# find it — the mapping lives in config's `model_list`. Cached: this runs in the
# per-request pre-call hook and must not re-read YAML per request.
_alias_bodies: dict[str, str] | None = None


def reset_model_map_cache() -> None:
    """Drop the cached alias->body map (config reload / tests)."""
    global _alias_bodies
    _alias_bodies = None


def _load_model_list() -> list:
    """Read ``model_list`` from config the way ``model_alias`` does. [] on any error."""
    path = Path(os.getenv("AIRLOCK_CONFIG", "config.yaml"))
    if not path.is_file():
        return []
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.debug("Failed to load config for reasoning_effort validation")
        return []
    if not isinstance(cfg, dict):
        return []
    model_list = cfg.get("model_list")
    return model_list if isinstance(model_list, list) else []


def _alias_body_map() -> dict[str, str]:
    global _alias_bodies
    if _alias_bodies is None:
        table: dict[str, str] = {}
        for entry in _load_model_list():
            if not isinstance(entry, dict):
                continue
            alias = entry.get("model_name")
            body = (entry.get("litellm_params") or {}).get("model")
            if alias and body:
                table.setdefault(str(alias).lower(), str(body))
        _alias_bodies = table
    return _alias_bodies


def _supported_efforts(model: str | None) -> frozenset[str] | None:
    """The ``reasoning_effort`` levels the target model actually accepts.

    Derived from the litellm model map's per-level capability flags, never
    hardcoded — a new model family must not require a code change to be
    validated correctly.

    Returns None when the model is unknown or unresolvable, which means "cannot
    validate": change nothing and say nothing. Self-hosted and custom endpoints
    legitimately are not in the map, and rejecting on ignorance would break them.
    """
    if not model:
        return None
    body = _alias_body_map().get(str(model).lower(), str(model))

    # Try the full body first ("openai/gpt-5.6-sol"), then the bare name, since
    # the map is keyed inconsistently across families.
    candidates = [body]
    if "/" in body:
        candidates.append(body.split("/", 1)[1])

    info: Any = None
    for candidate in candidates:
        try:
            import litellm

            info = litellm.get_model_info(candidate)
            break
        except Exception:  # noqa: BLE001 — unknown model ⇒ cannot validate.
            continue
    if not isinstance(info, dict):
        return None

    levels = set(_ALWAYS_SUPPORTED)
    for level, flag in _FLAGGED_LEVELS.items():
        if info.get(flag):
            levels.add(level)
    return frozenset(levels)


def _warn_would_reject(
    data: dict[str, Any],
    *,
    requested: Any,
    emitted: str | None,
    model: str | None,
    provider: str | None,
    client_id: str | None,
) -> None:
    """WARN-ONLY (design §13): report what strict validation *would* reject.

    Changes nothing about the outbound request. Emits a WARNING with a stable
    ``event`` discriminator and records an advisory ledger entry so the event
    reaches ``X-Airlock-Mutations`` and the unified RequestEvent stream — the
    measurement is only useful if it is countable with the normal tooling.

    Only computed for the OpenAI family: litellm's per-level flags encode the
    OpenAI enum, so a Gemini model reports every flag as None and would yield
    {low,medium,high}, falsely flagging its legitimate ``disable``. Where no
    level set is genuinely knowable, we stay silent rather than guess.
    """
    # Evaluate what the CLIENT ASKED FOR, not what we emit after translation.
    #
    # This is the whole point of the window: enforcement (§5.1) validates the
    # client's value, so `none` on gpt-5.4 gets a 400 even though today's
    # translation quietly turns it into a supported `minimal`. Measuring the
    # emitted value would report that cohort as fine and hide the single
    # largest group the breaking change actually hits.
    asked = str(requested).strip().lower() if requested is not None else None
    if asked is None or provider not in _OPENAI_PROVIDERS:
        return
    supported = _supported_efforts(model)
    if supported is None or asked in supported:
        return

    levels = ",".join(sorted(supported))
    logger.warning(
        "event=effort_would_reject requested=%s translated_to=%s model=%s "
        "supported=%s%s",
        requested,
        emitted,
        model,
        levels,
        f" client_id={client_id}" if client_id else "",
    )
    record_mutation(
        data.setdefault("metadata", {}),
        field="reasoning_effort_would_reject",
        op="inject",
        before=requested,
        after=emitted,
        stage="pre_call",
        source="reasoning_effort.validate",
        reason=f"{emitted!r} unsupported by {model}; supported: {levels}",
    )


def normalize_reasoning_effort(
    data: dict[str, Any], provider: str | None, client_id: str | None = None
) -> dict[str, Any]:
    """In-place: map an off-intent / provider-invalid ``reasoning_effort`` to the
    target provider's floor (or drop it where the provider has no enum). Returns
    ``data`` for chaining. A no-op unless ``reasoning_effort`` is present.

    Additionally (0.5.8, WARN-ONLY): computes what strict model-aware validation
    *would* decide and reports mismatches as ``effort_would_reject``. The value
    sent upstream is byte-identical to what this function has always emitted —
    enforcement is a later release, gated on the measurement window (design §13).
    """
    if not _enabled():
        return data
    raw = data.get("reasoning_effort")
    if raw is None:
        return data
    val = str(raw).strip().lower()
    model = data.get("model")

    if provider in _OPENAI_PROVIDERS:
        if val not in _OPENAI_VALID and val in _OFF_INTENT:
            data["reasoning_effort"] = "minimal"
            record_mutation(
                data.setdefault("metadata", {}),
                field="reasoning_effort",
                op="set",
                before=raw,
                after="minimal",
                stage="pre_call",
                source="reasoning_effort.normalize",
            )
    elif provider == "gemini":
        if val not in _GEMINI_VALID and (val in _OFF_INTENT or val == "minimal"):
            data["reasoning_effort"] = "disable"
            record_mutation(
                data.setdefault("metadata", {}),
                field="reasoning_effort",
                op="set",
                before=raw,
                after="disable",
                stage="pre_call",
                source="reasoning_effort.normalize",
            )
    elif provider == "anthropic":
        # Anthropic has no reasoning_effort enum; "off" intent → no extended thinking.
        if val in _OFF_INTENT:
            data.pop("reasoning_effort", None)
            record_mutation(
                data.setdefault("metadata", {}),
                field="reasoning_effort",
                op="drop",
                before=raw,
                after=None,
                stage="pre_call",
                source="reasoning_effort.normalize",
            )
    # Unknown providers / unknown values: leave for drop_params.

    # WARN-ONLY: judge the value we are ACTUALLY about to send. Runs after every
    # branch above so it sees the post-translation value and never alters it.
    emitted = data.get("reasoning_effort")
    _warn_would_reject(
        data,
        requested=raw,
        emitted=str(emitted).strip().lower() if emitted is not None else None,
        model=model,
        provider=provider,
        client_id=client_id,
    )
    return data
