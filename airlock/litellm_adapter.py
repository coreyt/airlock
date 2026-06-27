"""Anti-Corruption Layer for LiteLLM internals (Pack 0.5.3-ACL).

This is the ONE module that reaches into LiteLLM's undocumented / version-coupled
surfaces. **This is the single file to re-verify on a LiteLLM upgrade.**

Pinned to LiteLLM 1.89.0 behavior under the wide ``litellm[proxy]>=1.83.4,<2``
range. The surfaces owned here:

* ``response._hidden_params`` — the per-response private dict carrying the served
  backend identity (``custom_llm_provider``, ``api_base``, ``region_name``,
  ``model_id``/``litellm_model_name``/``received_model_id``, ``response_cost``) and
  the nested ``additional_headers`` (provider rate-limit headers + Airlock's own
  response headers).
* ``response.custom_llm_provider`` — the wrapper attribute. On **streams** the
  provider is carried here (not yet in ``_hidden_params`` at header-flush time);
  the streaming wrapper hardcodes ``custom_llm_provider="vertex_ai_beta"`` for the
  Vertex-Gemini handler even for native AI-Studio gemini. Alias normalization /
  AI-Studio-vs-Vertex disambiguation by ``api_base`` host stays in
  ``transparency._normalize_served_provider`` (provider routing/dispatch is NOT in
  scope for this ACL — only reads of internals).
* ``response_cost`` — read from ``_hidden_params``. (The bare
  ``kwargs.get("response_cost")`` reads in the ``CustomLogger`` hooks are the
  *documented* callback-kwargs surface and intentionally stay in the loggers /
  monitor as ``cost_fallback`` sources — not owned here.)
* the LiteLLM proxy ASGI app — resolved via
  ``sys.modules.get("litellm.proxy.proxy_server").app`` — and the
  ``middleware_stack is None`` (pre-start) vs built-stack (post-start) install
  branch. This ACL owns the install *mechanism*; install *order* stays in callers.

Pure reads of internals only — no provider-dispatch registry, no behavior change.
"""

from __future__ import annotations

import sys
from typing import Any


# ---------------------------------------------------------------------------
# Response-internal accessors (``_hidden_params`` contract)
# ---------------------------------------------------------------------------
def hidden_params(response: Any) -> dict | None:
    """Return ``response._hidden_params`` (the private dict) or ``None``."""
    return getattr(response, "_hidden_params", None)


def served_provider(response: Any) -> str | None:
    """The provider that served the request.

    Reads ``_hidden_params['custom_llm_provider']`` and, on streams where that is
    not yet populated, falls back to the wrapper attribute
    ``response.custom_llm_provider``.
    """
    hp = hidden_params(response) or {}
    return hp.get("custom_llm_provider") or getattr(
        response, "custom_llm_provider", None
    )


def response_cost(response: Any, fallback: float | None = None) -> float | None:
    """``_hidden_params['response_cost']``, falling back to ``fallback`` when None."""
    hp = hidden_params(response) or {}
    cost = hp.get("response_cost")
    if cost is None:
        return fallback
    return cost


def additional_headers(response: Any) -> dict | None:
    """The nested ``_hidden_params['additional_headers']`` dict, or ``None``."""
    hp = hidden_params(response)
    if not isinstance(hp, dict):
        return None
    return hp.get("additional_headers")


def merge_additional_headers(response: Any, headers: dict) -> bool:
    """Merge ``headers`` into ``_hidden_params['additional_headers']`` (write seam).

    No-op (returns False) when ``_hidden_params`` is not a dict.
    """
    hp = hidden_params(response)
    if not isinstance(hp, dict):
        return False
    hp.setdefault("additional_headers", {}).update(headers)
    return True


# ---------------------------------------------------------------------------
# Proxy-app resolution + middleware install mechanism
# ---------------------------------------------------------------------------
def resolve_proxy_app() -> Any:
    """The LiteLLM proxy ASGI app, or ``None`` if the proxy module is unresolved."""
    proxy_server = sys.modules.get("litellm.proxy.proxy_server")
    return getattr(proxy_server, "app", None)


def install_asgi_middleware(
    app: Any, middleware_cls: Any, *args: Any, **kwargs: Any
) -> None:
    """Install ``middleware_cls`` on ``app``, handling pre/post-start ordering.

    Encodes the Starlette constraint once: ``add_middleware`` may only be used
    before the app has started (``middleware_stack is None``); once the stack is
    built, ``add_middleware`` raises, so the built stack must be wrapped instead.
    Install *order* is the caller's concern.
    """
    if app.middleware_stack is None:
        # Pre-start: the normal path; the stack is built (with us) on startup.
        app.add_middleware(middleware_cls, *args, **kwargs)
    else:
        # Post-start: add_middleware would raise — wrap the built stack instead.
        app.middleware_stack = middleware_cls(app.middleware_stack, *args, **kwargs)
