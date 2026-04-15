"""Airlock provider model discovery — queries provider APIs at startup to log
available models.

Queries run concurrently with a per-provider timeout and are best-effort:
a failure skips that provider without blocking startup.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

logger = logging.getLogger("airlock.models_catalog")

_STATIC_CREATED = (
    1704067200  # 2024-01-01T00:00:00Z — fallback for providers that omit it
)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config(config_path: str | Path | None = None) -> dict:
    if config_path is None:
        config_path = os.getenv("AIRLOCK_CONFIG", "config.yaml")
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("models_catalog: failed to load config: %s", exc)
        return {}


def _resolve_secret(value: object) -> str | None:
    """Resolve a literal secret or an ``os.environ/NAME`` reference to its value."""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("os.environ/"):
        env_var = value.split("/", 1)[1]
        return os.environ.get(env_var) or None
    return value


def _get_api_key(config: dict, provider_prefix: str) -> str | None:
    """Find the first API key configured for a given provider prefix."""
    for entry in config.get("model_list", []):
        params = entry.get("litellm_params") or {}
        model_str = params.get("model", "")
        if not model_str.startswith(f"{provider_prefix}/"):
            continue
        resolved = _resolve_secret(params.get("api_key", ""))
        if resolved:
            return resolved
    return None


# ---------------------------------------------------------------------------
# Live provider discovery
# ---------------------------------------------------------------------------


@dataclass
class _ProviderFetcher:
    prefix: str  # e.g. "anthropic"
    fn: Callable[[str, float], list[dict]]  # (api_key, timeout) -> model entries
    api_key_override: str | None = None  # pre-resolved key for custom providers


def _fetch_openai_compatible(
    base_url: str,
    provider_prefix: str,
    api_key: str,
    timeout: float,
    auth_header: str = "Authorization",
    auth_scheme: str = "Bearer",
    extra_headers: dict | None = None,
) -> list[dict]:
    """Fetch models from any OpenAI-compatible /v1/models endpoint."""
    headers: dict[str, str] = {
        auth_header: f"{auth_scheme} {api_key}" if auth_scheme else api_key,
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(base_url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
        data = json.loads(resp.read())

    models = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        if not model_id:
            continue
        full_id = f"{provider_prefix}/{model_id}"
        models.append(
            {
                "id": full_id,
                "object": "model",
                "created": item.get("created", _STATIC_CREATED),
                "owned_by": provider_prefix,
            }
        )
    return models


def _fetch_openai_models(api_key: str, timeout: float) -> list[dict]:
    return _fetch_openai_compatible(
        "https://api.openai.com/v1/models",
        provider_prefix="openai",
        api_key=api_key,
        timeout=timeout,
    )


def _fetch_anthropic_models(api_key: str, timeout: float) -> list[dict]:
    return _fetch_openai_compatible(
        "https://api.anthropic.com/v1/models",
        provider_prefix="anthropic",
        api_key=api_key,
        timeout=timeout,
        auth_header="x-api-key",
        auth_scheme="",
        extra_headers={"anthropic-version": "2023-06-01"},
    )


def _fetch_mistral_models(api_key: str, timeout: float) -> list[dict]:
    return _fetch_openai_compatible(
        "https://api.mistral.ai/v1/models",
        provider_prefix="mistral",
        api_key=api_key,
        timeout=timeout,
    )


def _fetch_gemini_models(api_key: str, timeout: float) -> list[dict]:
    """Gemini uses a different models API shape (name field, not id)."""
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    req = urllib.request.Request(url, headers={"x-goog-api-key": api_key})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
        data = json.loads(resp.read())

    models = []
    for item in data.get("models", []):
        # name is "models/gemini-2.5-flash"
        name = item.get("name", "")
        if not name:
            continue
        bare = name.split("/", 1)[-1]  # "gemini-2.5-flash"
        full_id = f"gemini/{bare}"
        models.append(
            {
                "id": full_id,
                "object": "model",
                "created": _STATIC_CREATED,
                "owned_by": "gemini",
            }
        )
    return models


def _fetch_perplexity_models(api_key: str, timeout: float) -> list[dict]:
    return _fetch_openai_compatible(
        "https://api.perplexity.ai/v1/models",
        provider_prefix="perplexity",
        api_key=api_key,
        timeout=timeout,
    )


_FETCHERS: list[_ProviderFetcher] = [
    _ProviderFetcher("openai", _fetch_openai_models),
    _ProviderFetcher("anthropic", _fetch_anthropic_models),
    _ProviderFetcher("mistral", _fetch_mistral_models),
    _ProviderFetcher("gemini", _fetch_gemini_models),
    _ProviderFetcher("perplexity", _fetch_perplexity_models),
]


def _custom_fetchers_from_config(config: dict) -> list[_ProviderFetcher]:
    """Build fetchers for any ``providers:`` entries in config.yaml.

    Each entry describes an OpenAI-compatible ``/v1/models`` endpoint —
    e.g. a self-hosted LiteLLM proxy or vLLM gateway. Example::

        providers:
          - name: litellm-lan
            base_url: http://192.168.1.45:4000/v1/models
            api_key: os.environ/LITELLM_API_KEY
            auth_header: Authorization   # optional
            auth_scheme: Bearer          # optional
            extra_headers: {}            # optional
    """
    fetchers: list[_ProviderFetcher] = []
    for entry in config.get("providers", []) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        base_url = entry.get("base_url")
        if not name or not base_url:
            logger.warning(
                "models_catalog: skipping providers entry missing name/base_url"
            )
            continue
        api_key = _resolve_secret(entry.get("api_key", ""))
        if not api_key:
            logger.warning(
                "models_catalog: providers entry %r has no resolvable api_key, skipping",
                name,
            )
            continue
        auth_header = entry.get("auth_header", "Authorization")
        auth_scheme = entry.get("auth_scheme", "Bearer")
        extra_headers = entry.get("extra_headers")

        def _make_fn(
            url: str,
            prefix: str,
            hdr: str,
            scheme: str,
            extra: dict | None,
        ) -> Callable[[str, float], list[dict]]:
            def _fn(key: str, timeout: float) -> list[dict]:
                return _fetch_openai_compatible(
                    url,
                    provider_prefix=prefix,
                    api_key=key,
                    timeout=timeout,
                    auth_header=hdr,
                    auth_scheme=scheme,
                    extra_headers=extra,
                )

            return _fn

        fetchers.append(
            _ProviderFetcher(
                prefix=name,
                fn=_make_fn(base_url, name, auth_header, auth_scheme, extra_headers),
                api_key_override=api_key,
            )
        )
    return fetchers


def fetch_live_provider_models(
    config: dict,
    timeout: float = 10.0,
) -> list[dict]:
    """Query each configured provider's models API concurrently.

    Returns a (possibly empty) list of model entries. Provider failures
    are logged and skipped — this is best-effort discovery.

    Custom ``providers:`` entries in ``config`` replace any built-in
    fetcher sharing the same prefix, giving users an explicit escape
    hatch to point at a self-hosted proxy instead of upstream vendors.
    """
    custom = _custom_fetchers_from_config(config)
    custom_prefixes = {f.prefix for f in custom}
    overridden = custom_prefixes & {f.prefix for f in _FETCHERS}
    if overridden:
        logger.info(
            "models_catalog: custom providers override built-ins: %s",
            ", ".join(sorted(overridden)),
        )
    active: list[_ProviderFetcher] = [
        f for f in _FETCHERS if f.prefix not in custom_prefixes
    ] + custom

    results: list[dict] = []
    lock = threading.Lock()

    def _run(fetcher: _ProviderFetcher) -> None:
        api_key = fetcher.api_key_override or _get_api_key(config, fetcher.prefix)
        if not api_key:
            return
        try:
            entries = fetcher.fn(api_key, timeout)
            with lock:
                results.extend(entries)
            logger.info(
                "models_catalog: discovered %d models from %s",
                len(entries),
                fetcher.prefix,
            )
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            Exception,
        ) as exc:
            logger.warning(
                "models_catalog: %s model discovery failed: %s", fetcher.prefix, exc
            )

    threads = [threading.Thread(target=_run, args=(f,), daemon=True) for f in active]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 1)

    return results
