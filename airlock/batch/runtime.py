"""Runtime wiring for the batch gateway: config, file store, backend registry.

This module is only used by the live HTTP dispatch path (inside the LiteLLM
proxy process). The gateway core (``gateway.py``) stays free of disk/config IO
so it remains fully unit-testable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from airlock.batch.aistudio import AIStudioBackend
from airlock.batch.backend import BatchBackend
from airlock.batch.gateway import load_batch_aliases, load_batch_profile
from airlock.batch.mistral import MistralBackend
from airlock.batch.store import BatchStore

_config_cache: dict[str, Any] | None = None
_store: BatchStore | None = None


def _data_dir() -> Path:
    base = Path(os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs")))
    d = base / "batch_files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_config_path() -> str | None:
    candidates = [
        os.getenv("AIRLOCK_CONFIG", "config.yaml"),
        str(Path(__file__).resolve().parent.parent.parent / "config.yaml"),
        "/etc/airlock/config.yaml",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def get_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    path = _find_config_path()
    if path is None:
        _config_cache = {}
        return _config_cache
    try:
        import yaml  # noqa: PLC0415

        with open(path, encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        _config_cache = {}
    return _config_cache


def get_store() -> BatchStore:
    global _store
    if _store is None:
        _store = BatchStore()
    return _store


def get_batch_profile() -> dict:
    return load_batch_profile(get_config())


def effective_batch_profile() -> dict:
    """Return the active ``batch_profile.default`` sub-dict (design §4.2).

    ``load_batch_profile`` returns the whole ``{default: {...}}`` block; the
    gateway operates on the inner profile. Falls back to an empty dict so callers
    can ``.get(...)`` safely.
    """
    profile = get_batch_profile()
    default = profile.get("default") if isinstance(profile, dict) else None
    return default if isinstance(default, dict) else {}


def _model_entry(config: dict, model: str) -> dict | None:
    """Return the full ``model_list`` entry for an alias (incl. litellm_params)."""
    for entry in (config or {}).get("model_list", []) or []:
        if isinstance(entry, dict) and entry.get("model_name") == model:
            return entry
    return None


def _resolve_env_ref(value):
    """Resolve litellm's ``os.environ/NAME`` reference syntax to its value."""
    if isinstance(value, str) and value.startswith("os.environ/"):
        return os.getenv(value[len("os.environ/") :])
    return value


def backend_for_alias(model: str) -> BatchBackend | None:
    """Resolve a model alias to a configured batch backend.

    Dispatches on the ``airlock_batch`` marker's ``backend`` field: ``aistudio``
    -> ``AIStudioBackend``, ``mistral`` -> ``MistralBackend``, ``vllm`` ->
    ``VLLMBackend`` (gateway-as-executor; per-alias ``api_base``/``api_key`` from
    ``litellm_params``). Returns ``None`` for unknown / unmarked aliases.
    """
    config = get_config()
    marker = load_batch_aliases(config).get(model)
    if not marker:
        return None
    backend = marker.get("backend")
    provider_model = marker.get("provider_model")
    if backend == "aistudio":
        return AIStudioBackend(provider_model=provider_model)
    if backend == "mistral":
        return MistralBackend(provider_model=provider_model)
    if backend == "vllm":
        from airlock.batch.vllm import VLLMBackend  # noqa: PLC0415

        lp = (_model_entry(config, model) or {}).get("litellm_params") or {}
        return VLLMBackend(
            provider_model=provider_model,
            api_base=_resolve_env_ref(lp.get("api_base")),
            api_key=_resolve_env_ref(lp.get("api_key")),
            work_dir=str(_data_dir()),
        )
    return None


# -- file store (streamed; no full in-memory buffer) --------------------
def upload_path(file_id: str) -> Path:
    return _data_dir() / f"{file_id}.jsonl"


def scrubbed_path(file_id: str) -> Path:
    """Path of the scan-scrubbed JSONL (what ``create`` ships once READY)."""
    return _data_dir() / f"{file_id}.scrubbed.jsonl"


def read_upload(file_id: str) -> bytes:
    p = upload_path(file_id)
    return p.read_bytes() if p.exists() else b""


def write_output(output_file_id: str, bodies: list[dict]) -> None:
    import json  # noqa: PLC0415

    p = upload_path(output_file_id)
    with open(p, "w", encoding="utf-8") as f:
        for body in bodies:
            f.write(json.dumps(body) + "\n")
