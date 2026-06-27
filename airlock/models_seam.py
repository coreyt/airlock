"""Additive ASGI seam for ``GET /v1/models`` (and ``/models``).

Folds an ``airlock`` sub-object (``capability.capability_record(entry)``) into
each model in the standard OpenAI list response. **Purely additive** — never
removes or renames a standard field (``id``/``object``/``created``/``owned_by``);
a client that ignores ``airlock`` sees an unchanged response.

The seam is a pure ASGI response-transform middleware, mirroring the
batch-gateway install discipline (``batch/middleware.py:546-583``, dual
pre-start/post-start + idempotency flag) so import-order cannot break it. On any
parse/transform error — or on a non-200, non-JSON, or unexpected-shape response —
the original bytes are forwarded unchanged. Robustness over cleverness.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from airlock.capability import capability_record
from airlock.litellm_adapter import install_asgi_middleware, resolve_proxy_app

try:  # FastAPI is always present at runtime; tolerate its absence defensively.
    from fastapi import FastAPI
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]

logger = logging.getLogger("airlock.models_seam")

_MODELS_PATHS = ("/v1/models", "/models")


# ---------------------------------------------------------------------------
# Capability map (built once at install time from config — pure data)
# ---------------------------------------------------------------------------
def _build_capability_map() -> dict[str, dict]:
    """Build ``{model_name: capability_record(entry)}`` from the config.

    Loads config the same way ``model_alias.load_from_config`` does
    (``AIRLOCK_CONFIG`` env → default ``config.yaml``). A missing/invalid config
    tolerantly yields an empty map (warn) so the seam degrades to a transparent
    pass-through rather than crashing import.
    """
    config_path = os.getenv("AIRLOCK_CONFIG", "config.yaml")
    path = Path(config_path)
    if not path.is_file():
        logger.warning("Config file not found at %s — models seam map empty", path)
        return {}

    try:
        with open(path) as f:
            cfg = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load config for models seam: %s", exc)
        return {}

    if not isinstance(cfg, dict):
        logger.warning("Config is not a dict — models seam map empty")
        return {}

    model_list = cfg.get("model_list")
    if not isinstance(model_list, list):
        # A truthy non-list (e.g. ``model_list: true``) must never be iterated.
        if model_list:
            logger.warning("Config model_list is not a list — models seam map empty")
        return {}

    result: dict[str, dict] = {}
    for entry in model_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("model_name")
        if not name:
            continue
        try:
            result[name] = capability_record(entry)
        except Exception:  # noqa: BLE001 — one bad entry must not break startup.
            logger.warning(
                "Skipping model %r in models seam map: capability_record failed",
                name,
                exc_info=True,
            )
    return result


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
class ModelsCapabilityMiddleware:
    """Pure ASGI middleware that augments the ``/v1/models`` list response."""

    def __init__(self, app: Any, capability_map: dict[str, dict]):
        self.app = app
        self.capability_map = capability_map

    async def __call__(self, scope: dict, receive: Any, send: Any) -> Any:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "GET"
            or scope.get("path") not in _MODELS_PATHS
        ):
            return await self.app(scope, receive, send)

        start_message: dict = {}
        body_chunks: list[bytes] = []

        async def buffering_send(message: dict) -> None:
            mtype = message.get("type")
            if mtype == "http.response.start":
                nonlocal start_message
                start_message = message
            elif mtype == "http.response.body":
                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return  # keep buffering until the final chunk
                await self._emit(send, start_message, b"".join(body_chunks))
            else:
                await send(message)

        return await self.app(scope, receive, buffering_send)

    async def _emit(self, send: Any, start_message: dict, body: bytes) -> None:
        try:
            new_body = self._maybe_transform(start_message, body)
        except Exception:  # noqa: BLE001 — never corrupt a response.
            logger.debug("models seam transform failed; passing through", exc_info=True)
            new_body = None

        if new_body is None:
            await send(start_message)
            await send({"type": "http.response.body", "body": body})
            return

        await send(
            {
                "type": "http.response.start",
                "status": start_message.get("status", 200),
                "headers": _fix_headers(
                    start_message.get("headers", []), len(new_body)
                ),
            }
        )
        await send({"type": "http.response.body", "body": new_body})

    def _maybe_transform(self, start_message: dict, body: bytes) -> bytes | None:
        """Return augmented bytes, or ``None`` to forward the original unchanged."""
        if start_message.get("status") != 200:
            return None
        if not _is_json(start_message.get("headers", [])):
            return None

        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            return None
        data = parsed.get("data")
        if not isinstance(data, list):
            return None

        changed = False
        for item in data:
            if not isinstance(item, dict):
                continue
            record = self.capability_map.get(item.get("id"))
            if record is not None:
                item["airlock"] = record
                changed = True

        if not changed:
            return None
        return json.dumps(parsed).encode()


def _is_json(headers: list) -> bool:
    for key, value in headers:
        if key.lower() == b"content-type":
            return b"application/json" in value.lower()
    return False


def _fix_headers(headers: list, length: int) -> list:
    """Drop content-length/transfer-encoding, append the corrected length."""
    fixed = [
        (key, value)
        for key, value in headers
        if key.lower() not in (b"content-length", b"transfer-encoding")
    ]
    fixed.append((b"content-length", str(length).encode()))
    return fixed


# ---------------------------------------------------------------------------
# Install (mirror batch/middleware.py:546-583 — dual pre/post-start + flag)
# ---------------------------------------------------------------------------
def _get_proxy_app() -> Any:
    return resolve_proxy_app()


def install_models_capability_seam_on_proxy_app() -> bool:
    """Attach ``ModelsCapabilityMiddleware`` to the LiteLLM proxy app.

    Mirrors ``install_batch_gateway_on_proxy_app``: runs from the
    ``model_override_headers`` callback import, which may land before the app
    starts (``middleware_stack is None`` → ``add_middleware``) or after
    (``middleware_stack`` built → wrap it, since ``add_middleware`` would raise).
    Idempotent via ``app.state.airlock_models_seam_installed``.
    """
    app = _get_proxy_app()
    if FastAPI is None or not isinstance(app, FastAPI):
        return False
    if getattr(app.state, "airlock_models_seam_installed", False):
        return True

    capability_map = _build_capability_map()
    install_asgi_middleware(
        app, ModelsCapabilityMiddleware, capability_map=capability_map
    )
    app.state.airlock_models_seam_installed = True
    return True
