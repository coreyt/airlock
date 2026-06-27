"""Airlock — shared provider-capability helpers.

Single source of truth for the *served-by* provider token of a ``model_list``
entry. Used by ``router.set_router_config`` (catalog-based provider inference)
and the collision-safe alias resolver (``fast/model_alias.py``). Pure, no I/O.

This module must NOT import ``airlock.fast.router`` (would create an import
cycle — router imports from here).
"""

from __future__ import annotations

# Provider-token aliases. Maps a *display* / alias prefix or a beta provider
# token to its canonical served-by token. ``aistudio`` and ``vertex`` are only
# ever alias prefixes (the underlying litellm string uses ``gemini`` /
# ``vertex_ai``); ``vertex_ai_beta`` is the litellm beta token for Vertex.
_PROVIDER_TOKEN_ALIASES = {
    "aistudio": "gemini",
    "vertex": "vertex_ai",
    "vertex_ai_beta": "vertex_ai",
}


def normalize_provider_token(token: str) -> str:
    """Normalize a provider/alias prefix token to its canonical served-by token.

    ``aistudio`` -> ``gemini``, ``vertex`` -> ``vertex_ai``,
    ``vertex_ai_beta`` -> ``vertex_ai``; native tokens pass through unchanged.
    """
    return _PROVIDER_TOKEN_ALIASES.get(token, token)


def airlock_provider_for(entry: dict) -> str | None:
    """Return the served-by provider token for a ``model_list`` entry.

    Normal case: the prefix of ``litellm_params.model`` (``anthropic/...`` ->
    ``anthropic``), normalized (``vertex_ai_beta`` -> ``vertex_ai``). For an
    ``enhanced/<profile>`` wrapper, resolve through
    ``litellm_params.enhanced_profile.target_model`` so the served-by token is
    the wrapped provider (``gemini``), never ``enhanced``. Returns ``None`` when
    no model string is present.
    """
    params = entry.get("litellm_params") or {}
    model = params.get("model") or ""
    if not model:
        return None

    token = model.split("/", 1)[0] if "/" in model else model

    if token == "enhanced":
        profile = params.get("enhanced_profile") or {}
        target = profile.get("target_model") or ""
        if not target:
            return None
        token = target.split("/", 1)[0] if "/" in target else target

    return normalize_provider_token(token)


def endpoints_for(entry: dict) -> list[str]:
    """Return the supported endpoints for a ``model_list`` entry.

    Always includes ``"chat"``. Appends ``"batch"`` iff the entry carries a
    truthy ``airlock_batch`` marker OR it is a ``vertex_ai/`` model with a
    *regional* ``vertex_location`` (set and not ``global``). A bare ``vertex_ai/``
    prefix is NOT sufficient — vertex batch is region-gated, and the current
    entries use ``vertex_location: global`` (sync/chat-only). This is the
    anti-overclaim rule; do not weaken it.
    """
    endpoints = ["chat"]

    if entry.get("airlock_batch"):
        endpoints.append("batch")
        return endpoints

    params = entry.get("litellm_params") or {}
    model = params.get("model") or ""
    if model.startswith("vertex_ai/"):
        location = params.get("vertex_location")
        if location and location.lower() != "global":
            endpoints.append("batch")

    return endpoints


def capability_record(entry: dict) -> dict:
    """Compute the published ``model_info`` capability record for an entry.

    Returns exactly ``airlock_provider``, ``endpoints``, ``underlying``,
    ``region``, and ``deprecated``. ``region`` is the ``vertex_location`` (or
    ``None``); ``deprecated`` marks the legacy suffix twins. Pure and total —
    entries with no/empty ``litellm_params.model`` yield a safe record rather
    than raising.
    """
    params = entry.get("litellm_params") or {}
    model_name = entry.get("model_name") or ""
    return {
        "airlock_provider": airlock_provider_for(entry),
        "endpoints": endpoints_for(entry),
        "underlying": params.get("model"),
        "region": params.get("vertex_location"),
        "deprecated": model_name.endswith(("-aistudio", "-vertex", "-batch")),
    }
