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
