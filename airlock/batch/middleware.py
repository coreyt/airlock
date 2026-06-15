"""BatchGatewayMiddleware — ASGI front controller (design §1/A5, §3.2).

Runs *before* routing (so it is order-independent of LiteLLM's late batch route
registration). It acts only on the batch/file routes and discriminates on the
``custom_llm_provider`` **query parameter**, read from the ASGI scope's
``query_string`` — the request body is NEVER buffered (uploads can be ~2GB).

``aistudio``/``mistral`` -> Airlock gateway; everything else -> ``call_next``
(the inner LiteLLM app) untouched.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import uuid
from typing import Any
from urllib.parse import parse_qs

logger = logging.getLogger("airlock.batch")

# Providers handled by the Airlock gateway.
_GATEWAY_PROVIDERS = {"aistudio", "mistral"}

_BATCH_PREFIXES = ("/v1/batches", "/v1/files", "/airlock/batch")


def _is_batch_request(method: str, path: str) -> bool:
    """True for the batch/file routes (POST creates + GET/cancel variants)."""
    path = path.rstrip("/") or path
    return any(path == p or path.startswith(p + "/") for p in _BATCH_PREFIXES)


def _gateway_provider(query_string: bytes | str) -> str | None:
    """Return the gateway provider from the ``custom_llm_provider`` query param.

    Reads the query string only — never the body. Returns ``None`` for
    non-gateway providers (delegate to LiteLLM native).
    """
    if isinstance(query_string, bytes):
        query_string = query_string.decode("latin-1")
    values = parse_qs(query_string).get("custom_llm_provider")
    provider = values[0] if values else None
    return provider if provider in _GATEWAY_PROVIDERS else None


class BatchGatewayMiddleware:
    """Pure ASGI middleware (no body buffering)."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> Any:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "")
        path = scope.get("path", "")
        if not _is_batch_request(method, path):
            return await self.app(scope, receive, send)

        provider = _gateway_provider(scope.get("query_string", b""))
        if provider is None:
            # Non-gateway provider -> LiteLLM native handler, body untouched.
            return await self.app(scope, receive, send)

        return await dispatch_batch_gateway(scope, receive, send)


# ---------------------------------------------------------------------------
# Dispatch (live HTTP path; lazy provider SDK)
# ---------------------------------------------------------------------------
async def _send_json(send: Any, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _stream_to_file(receive: Any, dest_path: Any) -> int:
    """Stream the ASGI request body to disk in chunks (no full buffer)."""
    total = 0
    with open(dest_path, "wb") as f:
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if chunk:
                f.write(chunk)
                total += len(chunk)
            more = message.get("more_body", False)
    return total


async def _read_body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        more = message.get("more_body", False)
    return b"".join(chunks)


def _authorized(scope: dict) -> bool:
    """Enforce the proxy master key on the batch ingress (design §3, codex #1).

    The gateway dispatches *before* LiteLLM's route-level auth, so it must check
    the master key itself. When ``AIRLOCK_MASTER_KEY`` is unset we mirror the
    proxy's documented open behavior (``proxy.py`` ``_validate_master_key``) and
    allow the request; when it is set we require ``Authorization: Bearer <key>``
    and compare in constant time.
    """
    master = os.getenv("AIRLOCK_MASTER_KEY", "")
    if not master:
        return True
    header = _header(scope, b"authorization") or ""
    prefix = "Bearer "
    token = header[len(prefix) :] if header.startswith(prefix) else ""
    return hmac.compare_digest(token, master)


async def dispatch_batch_gateway(scope: dict, receive: Any, send: Any) -> None:
    """Route a gateway request to the appropriate handler.

    Errors are returned as OpenAI-style error JSON; the provider SDK is imported
    lazily inside the backend so a missing ``aistudio`` extra yields a clear
    error rather than a boot failure.
    """
    from airlock.batch import runtime  # noqa: PLC0415

    method = scope.get("method", "")
    path = scope.get("path", "").rstrip("/")

    if not _authorized(scope):
        return await _send_json(
            send,
            401,
            {
                "error": {
                    "message": (
                        "Invalid Authorization header. Expected "
                        "'Authorization: Bearer <AIRLOCK_MASTER_KEY>'."
                    ),
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )

    try:
        if method == "POST" and path in ("/v1/files", "/airlock/batch/files"):
            return await _handle_file_upload(scope, receive, send, runtime)
        if method == "POST" and path in ("/v1/batches", "/airlock/batch/batches"):
            return await _handle_create_batch(scope, receive, send, runtime)
        if method == "GET" and path.startswith("/v1/batches/"):
            return await _handle_get_batch(path, send, runtime)
        if method == "POST" and path.endswith("/cancel"):
            return await _handle_cancel(path, send, runtime)
        if (
            method == "GET"
            and path.startswith("/v1/files/")
            and path.endswith("/content")
        ):
            return await _handle_file_content(path, send, runtime)
    except Exception as exc:  # noqa: BLE001  surface as JSON, never 500-crash
        logger.exception("batch gateway dispatch failed")
        return await _send_json(
            send,
            500,
            {"error": {"message": str(exc), "type": "airlock_batch_error"}},
        )

    return await _send_json(
        send, 404, {"error": {"message": "unknown batch route", "type": "not_found"}}
    )


async def _handle_file_upload(scope, receive, send, runtime) -> None:
    file_id = f"file-{uuid.uuid4().hex}"
    size = await _stream_to_file(receive, runtime.upload_path(file_id))
    # NOTE: scan_at_upload is a NO-OP stub in this pack (to-do #2); the file is
    # accepted as-is. The async scan pipeline plugs in here.
    return await _send_json(
        send,
        200,
        {
            "id": file_id,
            "object": "file",
            "bytes": size,
            "purpose": "batch",
            "status": "processed",
        },
    )


async def _handle_create_batch(scope, receive, send, runtime) -> None:
    from airlock.batch.gateway import create_batch  # noqa: PLC0415

    raw = await _read_body(receive)
    body = json.loads(raw) if raw else {}
    model = body.get("model") or ""
    backend = runtime.backend_for_alias(model)
    if backend is None:
        return await _send_json(
            send,
            400,
            {
                "error": {
                    "message": f"model '{model}' is not a configured aistudio batch alias",
                    "type": "invalid_request_error",
                }
            },
        )
    input_file_id = body.get("input_file_id") or ""
    endpoint = body.get("endpoint") or "/v1/chat/completions"
    idem_key = _header(scope, b"idempotency-key")
    # Pass the stored path so create streams it (no full-file buffer; codex #4).
    input_path = str(runtime.upload_path(input_file_id))
    obj = await create_batch(
        runtime.get_store(),
        backend,
        input_file_id=input_file_id,
        model=model,
        endpoint=endpoint,
        params=body.get("metadata") or {},
        input_path=input_path,
        client=_header(scope, b"x-airlock-client"),
        idempotency_key=idem_key,
    )
    return await _send_json(send, 200, obj)


async def _handle_get_batch(path, send, runtime) -> None:
    from airlock.batch.gateway import get_batch  # noqa: PLC0415

    batch_id = path.rsplit("/", 1)[-1]
    store = runtime.get_store()
    row = store.get_by_batch_id(batch_id)
    backend = runtime.backend_for_alias(row["model"]) if row else None
    if row is None or backend is None:
        return await _send_json(
            send, 404, {"error": {"message": "batch not found", "type": "not_found"}}
        )
    obj = await get_batch(store, backend, batch_id)
    # Materialize the staged output file for /v1/files/{id}/content.
    if obj and obj.get("status") == "completed" and obj.get("output_file_id"):
        runtime.write_output(obj["output_file_id"], store.staged_bodies(batch_id))
    return await _send_json(send, 200, obj or {})


async def _handle_cancel(path, send, runtime) -> None:
    batch_id = path.rsplit("/", 2)[-2]
    store = runtime.get_store()
    row = store.get_by_batch_id(batch_id)
    if row is None:
        return await _send_json(
            send, 404, {"error": {"message": "batch not found", "type": "not_found"}}
        )
    backend = runtime.backend_for_alias(row["model"])
    if backend is not None and row.get("job_id"):
        await backend.cancel(row["job_id"])
    store.set_failed(row["idem"], error="cancelled by client", status="CANCELLED")
    from airlock.batch.gateway import to_openai_batch_object  # noqa: PLC0415

    return await _send_json(send, 200, to_openai_batch_object(store.get(row["idem"])))


async def _handle_file_content(path, send, runtime) -> None:
    file_id = path[len("/v1/files/") :].rsplit("/content", 1)[0]
    data = runtime.read_upload(file_id)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/jsonl"),
                (b"content-length", str(len(data)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": data})


def _header(scope: dict, name: bytes) -> str | None:
    for k, v in scope.get("headers", []) or []:
        if k == name:
            return v.decode("latin-1")
    return None


# ---------------------------------------------------------------------------
# Install (import-time bootstrap, next to the other Airlock injectors)
# ---------------------------------------------------------------------------
def install_batch_gateway_on_proxy_app() -> bool:
    """Attach ``BatchGatewayMiddleware`` to the LiteLLM proxy app at import.

    Middleware can be added while ``app.middleware_stack`` is unbuilt (verified
    A5), so this runs from ``model_override_headers`` before the server starts.
    """
    try:
        from fastapi import FastAPI  # noqa: PLC0415
    except ImportError:
        return False

    proxy_server = sys.modules.get("litellm.proxy.proxy_server")
    app = getattr(proxy_server, "app", None)
    if not isinstance(app, FastAPI):
        return False
    if getattr(app.state, "airlock_batch_gateway_installed", False):
        return True
    app.add_middleware(BatchGatewayMiddleware)
    app.state.airlock_batch_gateway_installed = True
    return True
