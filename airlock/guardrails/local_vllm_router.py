"""
Airlock Local-vLLM Router — pre-call guardrail that detects when a client
requests a local vLLM-backed model alias whose underlying model isn't
currently loaded on the (single, shared) vLLM server, and returns a
clean, actionable error instead of letting the upstream 404 propagate.

Why this exists
---------------
On this deployment only one local model is loaded at a time, but every
local alias in ``config.yaml`` points at the same ``api_base``. If a
client asks for ``kimi-dev`` while ``qwen3.6-27b`` is the container
that's actually running, the bare vLLM error ("the model `kimi-dev-72b`
does not exist") is opaque. This guardrail intercepts the request first
and explains what's loaded and how to switch.

Discovery
---------
The set of "local" aliases and their expected ``served-model-name``
values is inferred from ``config.yaml``: any ``model_list`` entry whose
``litellm_params.api_base`` matches ``AIRLOCK_LOCAL_VLLM_BASE_URL`` is
treated as local; the upstream model (with any ``openai/`` provider
prefix stripped) is the expected served-model-name.

Caching
-------
The config file is read once on first use. The list of loaded models is
fetched from ``{api_base}/models`` and cached for
``AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`` (default 5s) so model switches
are picked up quickly without per-request overhead.

Configuration
-------------
- ``AIRLOCK_LOCAL_VLLM_BASE_URL``  — default ``http://192.168.1.45:8000/v1``
- ``AIRLOCK_CONFIG``               — path to airlock config.yaml (re-used)
- ``AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`` — default ``5``
- ``AIRLOCK_LOCAL_VLLM_SWITCH_HINT`` — optional format string appended to
  the error. Supports ``{requested}``, ``{requested_served}``,
  ``{loaded}``, ``{loaded_aliases}``, ``{base_url}``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
import yaml
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

logger = logging.getLogger("airlock.guardrails.local_vllm_router")

_DEFAULT_BASE_URL = "http://192.168.1.45:8000/v1"
_DEFAULT_TTL = 5.0


def _base_url() -> str:
    return os.getenv("AIRLOCK_LOCAL_VLLM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _config_path() -> str:
    return os.getenv("AIRLOCK_CONFIG", "config.yaml")


def _cache_ttl() -> float:
    try:
        return float(os.getenv("AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS", _DEFAULT_TTL))
    except ValueError:
        return _DEFAULT_TTL


def _strip_provider(model: str) -> str:
    # litellm-style "openai/foo" -> "foo"; leave bare names alone.
    return model.split("/", 1)[1] if "/" in model else model


def _load_alias_map(config_path: str, base_url: str) -> dict[str, str]:
    """Return ``{alias: expected_served_name}`` for all local-vLLM entries.

    Returns an empty dict on any read/parse error (the guardrail then
    becomes a no-op rather than breaking the proxy).
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("local_vllm_router: failed to read %s: %s", config_path, exc)
        return {}

    norm_target = base_url.rstrip("/")
    out: dict[str, str] = {}
    for entry in cfg.get("model_list") or []:
        params = entry.get("litellm_params") or {}
        api_base = (params.get("api_base") or "").rstrip("/")
        if api_base != norm_target:
            continue
        alias = entry.get("model_name")
        upstream = params.get("model")
        if not alias or not upstream:
            continue
        out[alias] = _strip_provider(upstream)
    return out


class AirlockLocalVLLMRouter(CustomGuardrail):
    """Pre-call guardrail that fails fast when a local vLLM alias isn't loaded."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            supported_event_hooks=[GuardrailEventHooks.pre_call], **kwargs
        )
        self._alias_map: dict[str, str] | None = None
        self._loaded_cache: tuple[float, set[str]] | None = None
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0))

    def _aliases(self) -> dict[str, str]:
        if self._alias_map is None:
            self._alias_map = _load_alias_map(_config_path(), _base_url())
            logger.info(
                "local_vllm_router discovered aliases: %s", sorted(self._alias_map)
            )
        return self._alias_map

    async def _loaded_models(self) -> set[str]:
        now = time.monotonic()
        if self._loaded_cache and now - self._loaded_cache[0] < _cache_ttl():
            return self._loaded_cache[1]

        url = f"{_base_url()}/models"
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()
            data = resp.json().get("data") or []
            loaded = {m.get("id") for m in data if m.get("id")}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("local_vllm_router: /models query to %s failed: %s", url, exc)
            loaded = set()  # treat as "nothing loaded" → caller will get a clear error

        self._loaded_cache = (now, loaded)
        return loaded

    def _format_switch_hint(
        self,
        requested: str,
        requested_served: str,
        loaded: set[str],
        loaded_aliases: list[str],
    ) -> str:
        template = os.getenv("AIRLOCK_LOCAL_VLLM_SWITCH_HINT", "").strip()
        if not template:
            return (
                "Stop the currently running local vLLM container and start the one "
                f"that serves '{requested_served}' before retrying."
            )
        try:
            return template.format(
                requested=requested,
                requested_served=requested_served,
                loaded=", ".join(sorted(loaded)) or "<none>",
                loaded_aliases=", ".join(loaded_aliases) or "<none>",
                base_url=_base_url(),
            )
        except (KeyError, IndexError) as exc:
            logger.warning("local_vllm_router: malformed switch hint template: %s", exc)
            return "Switch the running vLLM container before retrying."

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,  # noqa: ARG002
        cache: DualCache,  # noqa: ARG002
        data: dict,
        call_type: str,  # noqa: ARG002
    ) -> dict:
        requested = (data or {}).get("model") or ""
        if not requested:
            return data

        aliases = self._aliases()
        expected = aliases.get(requested)
        if expected is None:
            # Not a local vLLM alias — pass through.
            return data

        loaded = await self._loaded_models()
        if expected in loaded:
            return data

        # Build a reverse lookup so we can name what *is* loaded in alias terms.
        loaded_aliases = sorted(a for a, s in aliases.items() if s in loaded)
        hint = self._format_switch_hint(requested, expected, loaded, loaded_aliases)

        currently = ", ".join(sorted(loaded)) if loaded else "<vLLM unreachable or empty>"
        msg = (
            f"Local model '{requested}' (served as '{expected}') is configured but "
            f"not currently loaded on {_base_url()}. "
            f"Currently loaded: {currently}. {hint}"
        )
        logger.warning(
            "local_vllm_router blocked requested=%s expected=%s loaded=%s",
            requested,
            expected,
            sorted(loaded),
        )
        raise ValueError(msg)
