"""
Airlock Advisor — model selection with local-first preference.

Selects the LLM to use for advisor queries.  Priority order:
  1. Explicit ``model_override`` parameter
  2. ``AIRLOCK_ADVISOR_MODEL`` environment variable
  3. First local model in config (has ``api_base``)
  4. First remote model (with ``local_only`` guard)
"""

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

    if model_override is not None:
        for entry in model_list:
            if entry["model_name"] == model_override:
                return (model_override, is_local_model(entry))
        return (model_override, False)

    env_model = os.environ.get("AIRLOCK_ADVISOR_MODEL")
    if env_model:
        for entry in model_list:
            if entry["model_name"] == env_model:
                return (env_model, is_local_model(entry))

    if not model_list:
        raise ValueError("No models configured in model_list")

    for entry in model_list:
        if is_local_model(entry):
            return (entry["model_name"], True)

    if local_only:
        raise ValueError("No local models available and local_only=True")

    first = model_list[0]
    return (first["model_name"], False)
