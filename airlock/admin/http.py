"""Admin HTTP surface: ``/airlock/admin/*`` perimeter middleware + routes.

The perimeter is a thin ASGI wrapper that extracts a principal from the request
and calls the pure :func:`handle_admin_request` (which the tests exercise
directly). It mounts ahead of LiteLLM's routes and *behind* the batch gateway
(see ``model_override_headers``), gates only ``/airlock/admin/*``, and **never
raises** — every outcome is a JSON response, so it can't be mis-shaped by the
rate-limit error handler.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import airlock.fast.state as _state
from airlock.admin.policy import LOOPBACK_HOSTS, Principal, admin_enabled, decide
from airlock.callbacks.enterprise_logger import write_admin_action_record

_PREFIX = "/airlock/admin/"


# --- read views (no audit record) ------------------------------------------
def _view_providers() -> dict:
    out = {}
    for name, ps in _state.store.all_providers().items():
        out[name] = {
            "quarantined": ps.is_quarantined(),
            "cooldown_remaining": round(ps.cooldown_remaining(), 1),
            "half_open": ps._half_open_probe,
            "last_reason": ps.last_reason,
        }
    return {"providers": out}


def _view_clients() -> dict:
    out = {}
    for cid, cp in _state.store.all_client_provider_states().items():
        client_id, provider = cid
        if cp.is_quarantined():
            out.setdefault(client_id, {})[provider] = {
                "quarantined": True,
                "cooldown_remaining": round(cp.cooldown_remaining(), 1),
            }
    return {"clients": out}


def _view_circuits() -> dict:
    out = {}
    for name, ms in _state.store.all_models().items():
        out[name] = {
            "circuit": ms.circuit.value,
            "consecutive_failures": ms.consecutive_failures,
        }
    return {"circuits": out}


# --- route table: (method, segments-template) -> (scope, loopback_only, fn) --
def _match_route(method: str, path: str):
    """Return (op_scope, loopback_only, handler) or None.

    handler(params: list[str], body: dict, actor: str) -> dict
    """
    if not (path == _PREFIX.rstrip("/") or path.startswith(_PREFIX)):
        return None
    tail = path[len("/airlock/admin/") :] if path.startswith(_PREFIX) else ""
    seg = [s for s in tail.split("/") if s]

    if method == "GET" and seg == ["providers"]:
        return ("admin:read", False, lambda p, b, a: _view_providers())
    if method == "GET" and seg == ["clients"]:
        return ("admin:read", False, lambda p, b, a: _view_clients())
    if method == "GET" and seg == ["circuits"]:
        return ("admin:read", False, lambda p, b, a: _view_circuits())

    if (
        method == "POST"
        and len(seg) == 3
        and seg[0] == "providers"
        and seg[2] == "clear-quarantine"
    ):
        prov = seg[1]
        return (
            "admin:clear_quarantine",
            False,
            lambda p, b, a: _state.store.clear_provider_quarantine(
                prov, mode=b.get("mode", "probe"), actor=a
            ),
        )
    if (
        method == "POST"
        and len(seg) == 3
        and seg[0] == "providers"
        and seg[2] == "quarantine"
    ):
        prov = seg[1]
        return (
            "admin:force_quarantine",
            True,  # loopback-only (operator)
            lambda p, b, a: _state.store.quarantine_provider(
                prov, actor=a, cooldown=b.get("cooldown_seconds")
            ),
        )
    if (
        method == "POST"
        and len(seg) == 5
        and seg[0] == "clients"
        and seg[2] == "providers"
        and seg[4] == "clear-quarantine"
    ):
        client, prov = seg[1], seg[3]
        return (
            "admin:clear_quarantine",
            False,
            lambda p, b, a: _state.store.clear_client_provider_quarantine(
                client, prov, mode=b.get("mode", "probe"), actor=a
            ),
        )
    if (
        method == "POST"
        and len(seg) == 3
        and seg[0] == "clients"
        and seg[2] == "clear-backoff"
    ):
        client = seg[1]
        return (
            "admin:clear_backoff",
            False,
            lambda p, b, a: _state.store.clear_client_backoff(client, actor=a),
        )
    if (
        method == "POST"
        and len(seg) == 3
        and seg[0] == "models"
        and seg[2] == "reset-circuit"
    ):
        model = seg[1]
        return (
            "admin:reset_circuit",
            False,
            lambda p, b, a: _state.store.reset_model_circuit(model, actor=a),
        )
    return None


def handle_admin_request(
    method: str, path: str, body: bytes, principal: Principal
) -> tuple[int, dict, dict]:
    """Pure request handler → (status, json_body, extra_headers). Never raises."""
    if not admin_enabled():
        return 404, {"error": "not found"}, {}
    route = _match_route(method, path)
    if route is None:
        return 404, {"error": "unknown admin route"}, {}
    op_scope, loopback_only, handler = route

    d = decide(principal, op_scope, loopback_only=loopback_only)
    if not d.allowed:
        return d.status, {"error": d.reason}, {}

    parsed: dict[str, Any] = {}
    if body:
        try:
            loaded = json.loads(body)
            if isinstance(loaded, dict):
                parsed = loaded
        except (json.JSONDecodeError, TypeError):
            return 400, {"error": "invalid JSON body"}, {}

    try:
        result = handler([], parsed, d.actor)
        # Mutating ops return an admin_action record → audit + replicate.
        if isinstance(result, dict) and result.get("record_type") == "admin_action":
            write_admin_action_record(result)
    except ValueError as exc:
        return 400, {"error": str(exc)}, {}
    except Exception:  # noqa: BLE001 — the perimeter must never raise (CC-10)
        return 500, {"error": "internal error"}, {}
    return 200, result, {}


# --- ASGI plumbing ----------------------------------------------------------
# Admin bodies are tiny JSON; cap the pre-auth read so an unauthenticated caller
# can't exhaust memory by streaming a large body to an admin endpoint.
_MAX_ADMIN_BODY = 64 * 1024


async def _read_body(receive, max_bytes: int = _MAX_ADMIN_BODY) -> bytes | None:
    """Read the request body, or return None if it exceeds ``max_bytes``."""
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if len(body) > max_bytes:
            return None
        if not msg.get("more_body", False):
            break
    return body


async def _send_json(send, status: int, payload: dict, extra: dict) -> None:
    data = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(data)).encode()),
    ]
    for key, value in extra.items():
        headers.append((key.encode(), value.encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


class AdminMiddleware:
    """ASGI middleware that serves ``/airlock/admin/*`` and passes everything else
    through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != _PREFIX.rstrip("/") and not path.startswith(_PREFIX):
            await self.app(scope, receive, send)
            return

        client = scope.get("client") or ("", 0)
        loopback = (client[0] if client else "") in LOOPBACK_HOSTS
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        bearer = auth[7:] if auth.startswith("Bearer ") else None
        actor = headers.get("x-airlock-client") or (
            "loopback" if loopback else "remote"
        )
        principal = Principal(loopback=loopback, bearer=bearer, actor=actor)

        method = scope.get("method", "GET")
        if method in ("POST", "PUT"):
            body = await _read_body(receive)
            if body is None:
                await _send_json(send, 413, {"error": "request body too large"}, {})
                return
        else:
            body = b""
        try:
            status, payload, extra = handle_admin_request(method, path, body, principal)
        except Exception:  # noqa: BLE001 — defense in depth; never 500 from a raise
            status, payload, extra = 500, {"error": "internal error"}, {}
        await _send_json(send, status, payload, extra)


def install_admin_on_proxy_app() -> bool:
    """Attach the admin perimeter to the LiteLLM proxy app.

    Mirrors ``install_batch_gateway_on_proxy_app``'s pre-start/post-start dual
    path. MUST be called *before* the batch gateway install so the gateway stays
    the outermost layer (see the umbrella note §3). Idempotent.
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        return False

    proxy_server = sys.modules.get("litellm.proxy.proxy_server")
    app = getattr(proxy_server, "app", None)
    if not isinstance(app, FastAPI):
        return False
    if getattr(app.state, "airlock_admin_installed", False):
        return True
    if app.middleware_stack is None:
        app.add_middleware(AdminMiddleware)
    else:
        app.middleware_stack = AdminMiddleware(app.middleware_stack)
    app.state.airlock_admin_installed = True
    return True
