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
import re
import uuid
from typing import Any
from urllib.parse import parse_qs

from airlock.litellm_adapter import install_asgi_middleware, resolve_proxy_app

logger = logging.getLogger("airlock.batch")

# Providers handled by the Airlock gateway.
_GATEWAY_PROVIDERS = {"aistudio", "mistral", "vllm"}

# Every id the gateway issues is ``file-<uuid4 hex>``; reject anything else
# before it reaches a filesystem path (defense in depth vs. path traversal).
_FILE_ID_RE = re.compile(r"^file-[0-9a-f]{32}$")

# A caller-supplied ``Idempotency-Key`` becomes the batch ``idem``, which the
# vLLM executor uses as a filename. Constrain it to a path-safe token (no
# separators) so it can never escape the work dir.
_IDEM_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")

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


def _is_gateway_owned_file_content(method: str, path: str) -> bool:
    """True for ``GET /v1/files/{id}/content`` where ``{id}`` is a file the
    gateway itself staged.

    Lets a stock OpenAI SDK ``files.content(output_file_id)`` fetch gateway
    output **without** the ``custom_llm_provider`` query param. The id must match
    the gateway's issued shape (``file-<32 hex>``) AND a gateway file must exist
    on disk for that id — so a LiteLLM-native file (never in the gateway store)
    falls through untouched, and a traversal/garbage id is rejected before any
    filesystem use.
    """
    if method != "GET":
        return False
    p = path.rstrip("/")
    if not (p.startswith("/v1/files/") and p.endswith("/content")):
        return False
    file_id = p[len("/v1/files/") : -len("/content")]
    if not _FILE_ID_RE.match(file_id):
        return False
    from airlock.batch import runtime  # noqa: PLC0415  lazy

    return runtime.upload_path(file_id).exists()


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
            # No provider param. Still intercept a content fetch for a file the
            # gateway staged, so a stock OpenAI SDK works without the param;
            # everything else -> LiteLLM native handler, body untouched.
            if _is_gateway_owned_file_content(method, path):
                return await dispatch_batch_gateway(scope, receive, send)
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
        if method == "GET" and path.startswith("/v1/files/"):
            return await _handle_file_status(path, send, runtime)
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
    from airlock.batch import worker  # noqa: PLC0415
    from airlock.batch.store import FILE_READY  # noqa: PLC0415

    file_id = f"file-{uuid.uuid4().hex}"
    raw_path = runtime.upload_path(file_id)
    size = await _stream_to_file(receive, raw_path)

    store = runtime.get_store()
    profile = runtime.effective_batch_profile()
    if profile.get("scan_at_upload", True):
        # Accept now, scan async (design A1): the provider job is gated on READY.
        store.record_file_upload(file_id, byte_count=size)
        worker.schedule_scan(
            store,
            file_id,
            str(raw_path),
            str(runtime.scrubbed_path(file_id)),
            profile,
        )
        status = "pending"
    else:
        # Scanning disabled -> legacy posture: ready immediately, ship raw upload.
        store.record_file_upload(
            file_id, byte_count=size, status=FILE_READY, scan_enabled=False
        )
        status = "processed"

    return await _send_json(
        send,
        200,
        {
            "id": file_id,
            "object": "file",
            "bytes": size,
            "purpose": "batch",
            "status": status,
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
                    "message": f"model '{model}' is not a configured batch gateway alias",
                    "type": "invalid_request_error",
                }
            },
        )
    input_file_id = body.get("input_file_id") or ""
    endpoint = body.get("endpoint") or "/v1/chat/completions"
    idem_key = _header(scope, b"idempotency-key")
    if idem_key is not None and not _IDEM_KEY_RE.match(idem_key):
        return await _send_json(
            send,
            400,
            {
                "error": {
                    "message": (
                        "Idempotency-Key must match [A-Za-z0-9._:-]{1,200} "
                        "(no path separators)"
                    ),
                    "type": "invalid_request_error",
                    "code": "invalid_idempotency_key",
                }
            },
        )

    # Gate on the content scan (to-do #2): create only ships a READY file, and
    # only the scrubbed bytes. A rejected/failed/still-scanning file never starts
    # a provider job — this is the async-scan failure surface (design §7.1).
    input_path, err = await _resolve_scanned_input(runtime, input_file_id)
    if err is not None:
        return await _send_json(send, err[0], err[1])

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


async def _resolve_scanned_input(
    runtime, input_file_id: str
) -> tuple[str, tuple[int, dict] | None]:
    """Resolve the input path to ship, gating on scan state (to-do #2).

    Returns ``(input_path, None)`` to proceed, or ``("", (status, error_json))``
    to short-circuit create. Files with no scan record (scanning disabled, or a
    legacy/raw id) fall through to the raw upload — preserving prior behavior.
    """
    from airlock.batch import worker  # noqa: PLC0415
    from airlock.batch.store import (  # noqa: PLC0415
        FILE_FAILED,
        FILE_READY,
        FILE_REJECTED,
    )

    if not _FILE_ID_RE.match(input_file_id):
        return "", (
            400,
            {
                "error": {
                    "message": f"invalid input_file_id: {input_file_id!r}",
                    "type": "invalid_request_error",
                    "code": "invalid_file_id",
                }
            },
        )

    store = runtime.get_store()
    if store.get_file(input_file_id) is None:
        return str(runtime.upload_path(input_file_id)), None

    wait = float(os.getenv("AIRLOCK_BATCH_SCAN_WAIT_SECONDS", "30"))
    row = await worker.await_file_ready(store, input_file_id, timeout=wait)
    status = (row or {}).get("status")

    if status == FILE_READY:
        scrubbed = runtime.scrubbed_path(input_file_id)
        if scrubbed.exists():
            return str(scrubbed), None
        if (row or {}).get("scan_enabled"):
            # Scanned-clean but the scrubbed artifact is gone (external deletion /
            # disk fault). We must NOT fall back to the raw upload — that would
            # ship unredacted content and silently break terminal redaction (A2).
            logger.error(
                "batch input %s is READY but its scrubbed file is missing; "
                "refusing to ship the raw upload",
                input_file_id,
            )
            return "", (
                400,
                {
                    "error": {
                        "message": (
                            "scanned input is no longer available; re-upload the "
                            "file before creating the batch"
                        ),
                        "type": "airlock_batch_error",
                        "code": "scrubbed_input_missing",
                    }
                },
            )
        # scan_enabled is false (scanning disabled) -> raw upload is intended.
        return str(runtime.upload_path(input_file_id)), None
    if status == FILE_REJECTED:
        return "", (
            400,
            {
                "error": {
                    "message": (
                        "input file rejected by content scan: "
                        f"{(row or {}).get('reason') or 'blocked content'}"
                    ),
                    "type": "invalid_request_error",
                    "code": "content_scan_rejected",
                }
            },
        )
    if status == FILE_FAILED:
        return "", (
            400,
            {
                "error": {
                    "message": f"content scan failed: {(row or {}).get('reason')}",
                    "type": "airlock_batch_error",
                    "code": "content_scan_failed",
                }
            },
        )
    # Still SCANNING after the wait -> tell the client to retry (not an error).
    return "", (
        409,
        {
            "error": {
                "message": (
                    "input file is still being scanned; retry after it reports "
                    "status 'processed' via GET /v1/files/{id}"
                ),
                "type": "invalid_request_error",
                "code": "file_not_ready",
            }
        },
    )


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


def _file_status_object(file_id: str, row: dict) -> dict:
    """Shape a ``batch_files`` row as an OpenAI file object (status poll).

    Maps the scan state machine onto OpenAI's file ``status`` enum so a client
    can observe a rejection that happened *after* upload returned 200 (§7.1):
    ``UPLOADED``/``SCANNING`` -> ``pending``; ``READY`` -> ``processed``;
    ``REJECTED``/``FAILED`` -> ``error`` (with ``status_details``).
    """
    from airlock.batch.store import (  # noqa: PLC0415
        FILE_FAILED,
        FILE_READY,
        FILE_REJECTED,
    )

    state = row.get("status")
    if state == FILE_READY:
        status = "processed"
    elif state in (FILE_REJECTED, FILE_FAILED):
        status = "error"
    else:
        status = "pending"
    obj = {
        "id": file_id,
        "object": "file",
        "bytes": row.get("byte_count") or 0,
        "purpose": "batch",
        "status": status,
    }
    if status == "error" and row.get("reason"):
        obj["status_details"] = row["reason"]
    return obj


async def _handle_file_status(path, send, runtime) -> None:
    file_id = path[len("/v1/files/") :]
    row = runtime.get_store().get_file(file_id)
    if row is None:
        return await _send_json(
            send, 404, {"error": {"message": "file not found", "type": "not_found"}}
        )
    return await _send_json(send, 200, _file_status_object(file_id, row))


async def _handle_file_content(path, send, runtime) -> None:
    file_id = path[len("/v1/files/") :].rsplit("/content", 1)[0]
    if not _FILE_ID_RE.match(file_id):
        return await _send_json(
            send, 404, {"error": {"message": "file not found", "type": "not_found"}}
        )
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
    """Attach ``BatchGatewayMiddleware`` to the LiteLLM proxy app.

    This runs from ``model_override_headers`` (imported as a LiteLLM callback).
    That import can happen **either** before the app starts **or** during the
    startup lifespan — and Starlette forbids ``add_middleware`` once the app has
    started (``middleware_stack`` is built), raising
    ``RuntimeError("Cannot add middleware after an application has started")``.
    To be safe under both orderings:

    - **Not started yet** (``middleware_stack is None``): use the normal
      ``add_middleware`` path; Starlette builds the stack (with our middleware)
      on startup.
    - **Already started** (``middleware_stack`` built): wrap the built stack so
      the gateway sits at the outermost ASGI layer. ``BatchGatewayMiddleware``
      is a pure pass-through ASGI app, so wrapping is transparent for every
      non-gateway request.
    """
    try:
        from fastapi import FastAPI  # noqa: PLC0415
    except ImportError:
        return False

    app = resolve_proxy_app()
    if not isinstance(app, FastAPI):
        return False
    if getattr(app.state, "airlock_batch_gateway_installed", False):
        return True
    install_asgi_middleware(app, BatchGatewayMiddleware)
    app.state.airlock_batch_gateway_installed = True
    _schedule_vllm_reconcile()
    return True


def _schedule_vllm_reconcile() -> None:
    """Best-effort: re-spawn executors for in-flight vLLM batches after a restart.

    In-proxy executor tasks die with the process, so a restart must re-spawn
    them. Runs as a background task if an event loop is already running; if
    install happens pre-loop (no running loop) it is skipped — resume then waits
    for the next client poll/create on that batch.
    """
    try:
        from airlock.batch import runtime  # noqa: PLC0415
        from airlock.batch.vllm import reconcile_vllm_batches  # noqa: PLC0415

        import asyncio  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        loop.create_task(
            reconcile_vllm_batches(runtime.get_store()), name="vllm-reconcile"
        )
    except RuntimeError:
        logger.debug("no running loop at install; vLLM reconcile deferred")
    except Exception:  # noqa: BLE001  never break install
        logger.debug("vLLM reconcile scheduling failed", exc_info=True)
