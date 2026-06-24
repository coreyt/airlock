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
"""

from __future__ import annotations

import os
from typing import Any

# Tokens a caller uses to mean "turn reasoning off / as low as possible".
_OFF_INTENT = {"none", "off", "disable", "disabled", "false", "no", "0"}
_OPENAI_VALID = {"minimal", "low", "medium", "high"}
_GEMINI_VALID = {"disable", "low", "medium", "high"}
_OPENAI_PROVIDERS = {"openai", "azure", "azure_ai"}


def _enabled() -> bool:
    return os.getenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def normalize_reasoning_effort(
    data: dict[str, Any], provider: str | None
) -> dict[str, Any]:
    """In-place: map an off-intent / provider-invalid ``reasoning_effort`` to the
    target provider's floor (or drop it where the provider has no enum). Returns
    ``data`` for chaining. A no-op unless ``reasoning_effort`` is present."""
    if not _enabled():
        return data
    raw = data.get("reasoning_effort")
    if raw is None:
        return data
    val = str(raw).strip().lower()

    if provider in _OPENAI_PROVIDERS:
        if val in _OPENAI_VALID:
            return data
        if val in _OFF_INTENT:
            data["reasoning_effort"] = "minimal"
    elif provider == "gemini":
        if val in _GEMINI_VALID:
            return data
        if val in _OFF_INTENT or val == "minimal":
            data["reasoning_effort"] = "disable"
    elif provider == "anthropic":
        # Anthropic has no reasoning_effort enum; "off" intent → no extended thinking.
        if val in _OFF_INTENT:
            data.pop("reasoning_effort", None)
    # Unknown providers / unknown values: leave for drop_params.
    return data
