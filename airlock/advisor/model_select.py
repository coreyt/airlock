"""Advisor model selection — pick the best LLM, preferring local models."""

from __future__ import annotations

import os


def is_local_model(entry: dict) -> bool:
    """Return True if a model entry points at a local/self-hosted endpoint."""
    params = entry.get("litellm_params") or {}
    return bool(params.get("api_base"))


def select_advisor_model(
    config: dict,
    *,
    local_only: bool = False,
    model_override: str | None = None,
) -> tuple[str, bool]:
    """Pick the best model for the advisor, preferring local.

    Returns (model_name, is_local).
    Raises ValueError if no suitable model found.
    """
    model_list = config.get("model_list") or []

    # 1. Explicit model_override parameter takes top priority.
    if model_override is not None:
        for entry in model_list:
            if entry["model_name"] == model_override:
                return (model_override, is_local_model(entry))
        # Not found in list — admin knows what they want.
        return (model_override, False)

    # 2. AIRLOCK_ADVISOR_MODEL env var.
    env_model = os.environ.get("AIRLOCK_ADVISOR_MODEL")
    if env_model:
        for entry in model_list:
            if entry["model_name"] == env_model:
                return (env_model, is_local_model(entry))
        # Env override not found in config — fall through to normal selection.

    # 3. Empty list check.
    if not model_list:
        raise ValueError("No models configured in model_list")

    # 4. Prefer local models.
    for entry in model_list:
        if is_local_model(entry):
            return (entry["model_name"], True)

    # 5. local_only with no local models.
    if local_only:
        raise ValueError("No local models available and local_only=True")

    # 6. First remote model.
    first = model_list[0]
    return (first["model_name"], False)
