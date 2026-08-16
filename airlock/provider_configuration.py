"""Immutable, secret-safe provider configuration snapshot for the Admin API."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from airlock.capability import capability_record

_MAX_PROVIDERS = 64
_MAX_ALIASES = 200
_MAX_TEXT = 256
_SCHEMA_VERSION = 1
_snapshot_json = json.dumps(
    {
        "schema_version": _SCHEMA_VERSION,
        "source": "startup_config",
        "loaded_at": None,
        "restart_required": True,
        "fingerprint": hashlib.sha256(b"[]").hexdigest(),
        "truncated": {"providers": False, "aliases": False},
        "providers": [],
    },
    sort_keys=True,
    separators=(",", ":"),
)


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:_MAX_TEXT]


def _base_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname[:_MAX_TEXT]


def _credential(value: object, getenv: Callable[[str], str | None]) -> dict:
    if value is None or value == "":
        return {"kind": "none", "configured": False}
    if isinstance(value, str) and value.startswith("os.environ/"):
        name = value.removeprefix("os.environ/")
        present = bool(name and (getenv(name) or "").strip())
        return {"kind": "env_ref", "configured": present}
    # A non-environment reference is deliberately opaque. Do not make an
    # unrecognised environment-looking value observable by echoing its name.
    if isinstance(value, str) and value.startswith(("secret://", "credential://")):
        return {"kind": "credential_ref", "configured": True}
    return {"kind": "redacted_literal", "configured": True}


def _alias_record(entry: dict, getenv: Callable[[str], str | None]) -> dict | None:
    alias = _safe_text(entry.get("model_name"))
    if not alias:
        return None
    params = entry.get("litellm_params") or {}
    if not isinstance(params, dict):
        params = {}
    capability = capability_record(entry)
    provider = _safe_text(capability["airlock_provider"])
    if not provider:
        return None
    endpoints = [
        endpoint[:_MAX_TEXT]
        for endpoint in capability["endpoints"]
        if isinstance(endpoint, str)
    ]
    return {
        "provider": provider,
        "alias": alias,
        "underlying": _safe_text(capability["underlying"]),
        "endpoints": endpoints,
        "region": _safe_text(capability["region"]),
        "deprecated": bool(capability["deprecated"]),
        "api_base_host": _base_host(params.get("api_base") or params.get("base_url")),
        "credential": _credential_for_params(params, getenv),
        "setting_source": "static",
    }


def _credential_for_params(params: dict, getenv: Callable[[str], str | None]) -> dict:
    """Classify the finite supported credential fields without naming them."""
    for field in ("api_key", "vertex_credentials"):
        value = params.get(field)
        if value not in (None, ""):
            return _credential(value, getenv)
    return _credential(None, getenv)


def _fingerprint(providers: list[dict], truncated: dict[str, bool]) -> str:
    # Timestamp and credential state are excluded; this is a stable identity of
    # the safe routing DTO, not a hash oracle over raw YAML, literal secrets,
    # reference names, or credential presence.
    fingerprint_providers = [
        {
            "provider": provider["provider"],
            "aliases": [
                {key: value for key, value in alias.items() if key != "credential"}
                for alias in provider["aliases"]
            ],
        }
        for provider in providers
    ]
    canonical = json.dumps(
        {
            "schema_version": _SCHEMA_VERSION,
            "providers": fingerprint_providers,
            "truncated": truncated,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_provider_configuration_snapshot(
    config: dict | None,
    *,
    getenv: Callable[[str], str | None] = os.getenv,
    loaded_at: str | None = None,
) -> dict:
    """Return a bounded, redacted projection with no raw config references."""
    entries = (config or {}).get("model_list") or []
    records = [
        record
        for entry in entries
        if isinstance(entry, dict)
        if (record := _alias_record(entry, getenv)) is not None
    ]
    records.sort(
        key=lambda item: (item["provider"], item["alias"], item["underlying"] or "")
    )
    provider_names = sorted({record["provider"] for record in records})
    selected_providers = set(provider_names[:_MAX_PROVIDERS])
    scoped = [record for record in records if record["provider"] in selected_providers]
    selected_records = scoped[:_MAX_ALIASES]
    truncated = {
        "providers": len(provider_names) > _MAX_PROVIDERS,
        "aliases": len(scoped) > _MAX_ALIASES,
    }
    providers: list[dict] = []
    for provider in provider_names[:_MAX_PROVIDERS]:
        aliases = [
            {key: value for key, value in record.items() if key != "provider"}
            for record in selected_records
            if record["provider"] == provider
        ]
        if aliases:
            providers.append({"provider": provider, "aliases": aliases})
    return {
        "schema_version": _SCHEMA_VERSION,
        "source": "startup_config",
        "loaded_at": loaded_at
        or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "restart_required": True,
        "fingerprint": _fingerprint(providers, truncated),
        "truncated": truncated,
        "providers": providers,
    }


def configure_provider_configuration(
    config: dict | None,
    *,
    getenv: Callable[[str], str | None] = os.getenv,
    loaded_at: str | None = None,
) -> None:
    """Publish the child-startup snapshot as immutable canonical JSON."""
    global _snapshot_json
    _snapshot_json = json.dumps(
        build_provider_configuration_snapshot(
            config, getenv=getenv, loaded_at=loaded_at
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def provider_configuration_snapshot() -> dict:
    """Return a fresh decoded copy; callers cannot mutate the stored snapshot."""
    return json.loads(_snapshot_json)
