"""Airlock model catalog — builds /v1/models response from config + live provider queries.

Two layers of discovery:
  1. Config-based — reads model_list from config.yaml:
       alias names (model_name)       → owned_by: "airlock"
       provider-pinned (litellm_params.model) → owned_by: <provider>
  2. Provider-live — queries each provider's own models API at startup and
     returns every model they advertise, prefixed with provider/...

The live queries run concurrently with a per-provider timeout and are
best-effort: a failure skips that provider without blocking startup.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

logger = logging.getLogger("airlock.models_catalog")

# Stable epoch used for config-derived entries (matches LiteLLM's own practice)
_STATIC_CREATED = 1704067200  # 2024-01-01T00:00:00Z


# ---------------------------------------------------------------------------
# Config helpers (shared with post_cmd.py logic)
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


def _owned_by(model_id: str) -> str:
    """Extract provider name from 'provider/model-id', or 'airlock' for bare names."""
    return model_id.split("/", 1)[0] if "/" in model_id else "airlock"


def _get_api_key(config: dict, provider_prefix: str) -> str | None:
    """Find the first API key configured for a given provider prefix."""
    for entry in config.get("model_list", []):
        params = entry.get("litellm_params") or {}
        model_str = params.get("model", "")
        if not model_str.startswith(f"{provider_prefix}/"):
            continue
        api_key = params.get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("os.environ/"):
            env_var = api_key.split("/", 1)[1]
            return os.environ.get(env_var) or None
        if api_key:
            return api_key
    return None


# ---------------------------------------------------------------------------
# Config-based catalog
# ---------------------------------------------------------------------------

def build_catalog_from_config(
    config: dict | None = None,
    config_path: str | Path | None = None,
) -> list[dict]:
    """Return alias + provider-pinned model entries from config.yaml.

    Args:
        config: Pre-loaded config dict (if already parsed).
        config_path: Path to config.yaml; used when *config* is None.
    """
    if config is None:
        config = _load_config(config_path)

    seen: set[str] = set()
    models: list[dict] = []

    for entry in config.get("model_list") or []:
        if not isinstance(entry, dict):
            continue
        alias: str = entry.get("model_name", "")
        params: dict = entry.get("litellm_params") or {}
        provider_model: str = params.get("model", "")

        # 1. Alias name — e.g. "claude-sonnet"
        if alias and alias not in seen:
            seen.add(alias)
            models.append({
                "id": alias,
                "object": "model",
                "created": _STATIC_CREATED,
                "owned_by": _owned_by(alias),
            })

        # 2. Provider-pinned — e.g. "anthropic/claude-sonnet-4-20250514"
        if provider_model and provider_model not in seen and provider_model != alias:
            seen.add(provider_model)
            models.append({
                "id": provider_model,
                "object": "model",
                "created": _STATIC_CREATED,
                "owned_by": _owned_by(provider_model),
            })

    return models


# ---------------------------------------------------------------------------
# Live provider discovery
# ---------------------------------------------------------------------------

@dataclass
class _ProviderFetcher:
    prefix: str                                  # e.g. "anthropic"
    fn: Callable[[str, float], list[dict]]       # (api_key, timeout) -> model entries


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
        "Content-Type": "application/json",
        auth_header: f"{auth_scheme} {api_key}" if auth_scheme else api_key,
    }
    if auth_header == "Authorization":
        headers["Authorization"] = f"{auth_scheme} {api_key}"
    else:
        headers[auth_header] = f"{auth_scheme} {api_key}" if auth_scheme else api_key
        headers.pop("Authorization", None)

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
        models.append({
            "id": full_id,
            "object": "model",
            "created": item.get("created", _STATIC_CREATED),
            "owned_by": provider_prefix,
        })
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
        models.append({
            "id": full_id,
            "object": "model",
            "created": _STATIC_CREATED,
            "owned_by": "gemini",
        })
    return models


_FETCHERS: list[_ProviderFetcher] = [
    _ProviderFetcher("openai", _fetch_openai_models),
    _ProviderFetcher("anthropic", _fetch_anthropic_models),
    _ProviderFetcher("mistral", _fetch_mistral_models),
    _ProviderFetcher("gemini", _fetch_gemini_models),
]


def fetch_live_provider_models(
    config: dict,
    timeout: float = 10.0,
) -> list[dict]:
    """Query each configured provider's models API concurrently.

    Returns a (possibly empty) list of model entries. Provider failures
    are logged and skipped — this is best-effort discovery.
    """
    results: list[dict] = []
    lock = threading.Lock()

    def _run(fetcher: _ProviderFetcher) -> None:
        api_key = _get_api_key(config, fetcher.prefix)
        if not api_key:
            return
        try:
            entries = fetcher.fn(api_key, timeout)
            with lock:
                results.extend(entries)
            logger.info(
                "models_catalog: discovered %d models from %s",
                len(entries), fetcher.prefix,
            )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, Exception) as exc:
            logger.warning(
                "models_catalog: %s model discovery failed: %s", fetcher.prefix, exc
            )

    threads = [threading.Thread(target=_run, args=(f,), daemon=True) for f in _FETCHERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 1)

    return results


# ---------------------------------------------------------------------------
# Combined catalog
# ---------------------------------------------------------------------------

def build_full_catalog(
    config: dict | None = None,
    config_path: str | Path | None = None,
    fetch_live: bool = True,
    live_timeout: float = 10.0,
) -> list[dict]:
    """Build the complete model catalog.

    Combines:
      - Config-derived alias names and provider-pinned model IDs
      - (Optional) Live models fetched from each provider's /v1/models API

    Provider-live models that share an ID with a config entry are skipped
    (config takes precedence for those IDs).
    """
    if config is None:
        config = _load_config(config_path)

    catalog = build_catalog_from_config(config=config)
    existing_ids = {m["id"] for m in catalog}

    if fetch_live:
        live_models = fetch_live_provider_models(config, timeout=live_timeout)
        for entry in live_models:
            if entry["id"] not in existing_ids:
                catalog.append(entry)
                existing_ids.add(entry["id"])

    return catalog
