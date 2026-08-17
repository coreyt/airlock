"""Configuration loading that reproduces the pinned LiteLLM include contract.

Airlock uses this narrow resolver only to materialize the one canonical mapping
passed to LiteLLM and every Slice-40 consumer. In pinned LiteLLM, an included
``include`` list extends the active root include list, so descendants are
processed from the root directory after already-queued entries.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml


def _load_mapping(path: Path) -> dict:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not load LiteLLM config {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"LiteLLM config {path} must contain a mapping")
    return loaded


def resolve_litellm_direct_config(config_path: str | Path) -> dict:
    """Load a config with the pinned LiteLLM include semantics."""
    path = Path(config_path).resolve()
    config = copy.deepcopy(_load_mapping(path))
    if "include" in config:
        if not isinstance(config["include"], list):
            raise ValueError("LiteLLM config include must be a list of paths")
        # Do not snapshot this list. LiteLLM extends it when a child carries an
        # `include` list, which makes that child reachable after existing items.
        for item in config["include"]:
            include_path = Path(item)
            if not include_path.is_absolute():
                include_path = path.parent / include_path
            if not include_path.exists():
                raise FileNotFoundError(f"Included file not found: {include_path}")
            included = _load_mapping(include_path.resolve())
            for key, value in included.items():
                if isinstance(value, list) and key in config:
                    config[key].extend(copy.deepcopy(value))
                else:
                    config[key] = copy.deepcopy(value)
        del config["include"]
    return config
