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


def extract_airlock_client_from_headers(headers: Mapping[str, Any] | None) -> str | None:
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
        metadata.get("headers") if isinstance(metadata.get("headers"), Mapping) else None,
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
