"""Helpers for propagating and extracting Airlock client identity."""

from __future__ import annotations

import os
import urllib.request

from collections.abc import Mapping
from typing import Any

AIRLOCK_CLIENT_HEADER = "X-Airlock-Client"
_HEADER_CANDIDATES = (
    "x-airlock-client",
    "X-Airlock-Client",
    "airlock-client",
)

# Canonical sentinel for a missing client identity. Re-exported by
# ``airlock.fast.state`` so existing imports keep working.
NO_CLIENT_ID = "no_client"


def normalize_client_id(client_id: str | None) -> str:
    """Return a stable client identifier, collapsing missing values to no_client."""
    text = (client_id or "").strip()
    return text or NO_CLIENT_ID


def client_id_from_api_key(user_api_key_dict: Any) -> str:
    """Derive a stable client identifier from the API-key metadata."""
    if user_api_key_dict:
        if hasattr(user_api_key_dict, "api_key"):
            key = user_api_key_dict.api_key or ""
            if len(key) > 8:
                return f"key:{key[-8:]}"
        if isinstance(user_api_key_dict, dict):
            api_key = user_api_key_dict.get("api_key", "")
            if len(api_key) > 8:
                return f"key:{api_key[-8:]}"
    return normalize_client_id(None)


def extract_airlock_client_from_request(
    data: Mapping[str, Any] | None,
    user_api_key_dict: Any = None,
) -> str:
    """Resolve a normalized client id for an inbound request (canonical path).

    Prefers the inbound Airlock client identity (metadata then request/metadata
    headers), falling back to the authenticated API-key derived id. A ``None``
    ``data`` resolves to the API-key id (or ``no_client``), never raising.
    """
    data = data or {}
    metadata = data.get("metadata") or {}
    for value in (
        metadata.get("airlock_client"),
        extract_airlock_client_from_headers(data.get("headers")),
        extract_airlock_client_from_headers(metadata.get("headers")),
    ):
        if value:
            return normalize_client_id(str(value).strip())
    return client_id_from_api_key(user_api_key_dict)


def get_runtime_airlock_client() -> str | None:
    """Return the current process Airlock client identity, if configured."""
    client = os.environ.get("AIRLOCK_CLIENT", "").strip()
    return client or None


def add_airlock_client_header(req: urllib.request.Request) -> urllib.request.Request:
    """Attach ``X-Airlock-Client`` when the current process has one set."""
    client = get_runtime_airlock_client()
    if client:
        req.add_header(AIRLOCK_CLIENT_HEADER, client)
    return req


def extract_airlock_client_from_headers(
    headers: Mapping[str, Any] | None,
) -> str | None:
    """Read Airlock client identity from a headers mapping."""
    if not headers:
        return None
    for key in _HEADER_CANDIDATES:
        value = headers.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text
    return None


def extract_airlock_client_from_kwargs(kwargs: Mapping[str, Any]) -> str | None:
    """Best-effort extraction from callback kwargs / request-like objects."""
    metadata = (
        kwargs.get("litellm_params", {}).get("metadata", {}) or {}
        if isinstance(kwargs.get("litellm_params"), Mapping)
        else {}
    )
    for value in (
        metadata.get("airlock_client"),
        kwargs.get("airlock_client"),
    ):
        if value:
            text = str(value).strip()
            if text:
                return text

    header_sources: list[Mapping[str, Any] | None] = [
        kwargs.get("headers") if isinstance(kwargs.get("headers"), Mapping) else None,
        metadata.get("headers")
        if isinstance(metadata.get("headers"), Mapping)
        else None,
    ]
    for key in ("request", "proxy_server_request", "http_request"):
        obj = kwargs.get(key)
        headers = getattr(obj, "headers", None)
        if isinstance(headers, Mapping):
            header_sources.append(headers)

    for headers in header_sources:
        client = extract_airlock_client_from_headers(headers)
        if client:
            return client
    return None
