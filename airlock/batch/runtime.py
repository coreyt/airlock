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
from airlock.batch.gateway import load_batch_aliases, load_batch_profile
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


def backend_for_alias(model: str) -> AIStudioBackend | None:
    """Resolve a model alias to a configured batch backend (aistudio only)."""
    marker = load_batch_aliases(get_config()).get(model)
    if not marker:
        return None
    if marker.get("backend") != "aistudio":
        return None
    return AIStudioBackend(provider_model=marker.get("provider_model"))


# -- file store (streamed; no full in-memory buffer) --------------------
def upload_path(file_id: str) -> Path:
    return _data_dir() / f"{file_id}.jsonl"


def read_upload(file_id: str) -> bytes:
    p = upload_path(file_id)
    return p.read_bytes() if p.exists() else b""


def write_output(output_file_id: str, bodies: list[dict]) -> None:
    import json  # noqa: PLC0415

    p = upload_path(output_file_id)
    with open(p, "w", encoding="utf-8") as f:
        for body in bodies:
            f.write(json.dumps(body) + "\n")
