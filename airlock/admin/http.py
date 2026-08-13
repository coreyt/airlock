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
import base64
import os
from typing import Any

import airlock.fast.state as _state
from airlock.admin.erase import EraseIncomplete, erase_client
from airlock.admin.policy import LOOPBACK_HOSTS, Principal, admin_enabled, decide
from airlock.callbacks.enterprise_logger import write_admin_action_record
from airlock.fast.settings import get_settings
from airlock.litellm_adapter import install_asgi_middleware, resolve_proxy_app

_PREFIX = "/airlock/admin/"


# --- read views (no audit record) ------------------------------------------
def _view_providers() -> dict:
    out = {}
    providers = _state.store.all_providers()
    limits = _state.store.all_provider_ratelimits()
    budgets = get_settings().provider_budgets
    # The view is an off-hot-path snapshot: it reads the existing stores only
    # and deliberately creates neither telemetry nor an audit action.
    for name in sorted(set(providers) | set(limits) | set(budgets)):
        ps = providers.get(name)
        rl = limits.get(name)
        spend = _state.store.get_provider_spend(name).recent_spend()
        cap = budgets.get(name)
        out[name] = {
            "quarantined": ps.is_quarantined() if ps else False,
            "cooldown_remaining": round(ps.cooldown_remaining(), 1) if ps else 0.0,
            "half_open": ps._half_open_probe if ps else False,
            "last_reason": ps.last_reason if ps else "",
            # Live-only state (rate_limit_events with CC-6 floors) — the
            # separate-process TUI cannot compute this from its replica (#27).
            "impacted_clients": sorted(ps.impacted_clients()) if ps else [],
            "remaining_tokens": rl.remaining_tokens if rl else None,
            "limit_tokens": rl.limit_tokens if rl else None,
            "remaining_requests": rl.remaining_requests if rl else None,
            "limit_requests": rl.limit_requests if rl else None,
            "spend_usd": round(spend, 6),
            "budget_cap_usd": cap,
            "budget_utilization": (round(spend / cap, 6) if cap and cap > 0 else None),
        }
    return {"providers": out}


def _view_clients() -> dict:
    out = {}
    admission_enabled = get_settings().admission.enabled
    for client_id, client in _state.store.all_clients().items():
        if client.priority_score is not None:
            out.setdefault(client_id, {})["priority"] = {
                "score": client.priority_score,
                "boost": client.priority_boost,
                "reasons": list(client.priority_reasons),
                "observed_at": client.priority_observed_at,
                "admission_enabled": admission_enabled,
            }
    for cid, cp in _state.store.all_client_provider_states().items():
        client_id, provider = cid
        if cp.is_quarantined():
            out.setdefault(client_id, {}).setdefault("quarantines", {})[provider] = {
                "quarantined": True,
                "cooldown_remaining": round(cp.cooldown_remaining(), 1),
            }
    return {"source": "live_admin", "clients": out}


def _view_telemetry() -> dict:
    from airlock.telemetry_health import telemetry_snapshot

    return {"source": "process_instrumentation", "exporters": telemetry_snapshot()}


def _view_circuits() -> dict:
    out = {}
    for name, ms in _state.store.all_models().items():
        out[name] = {
            "circuit": ms.circuit.value,
            "consecutive_failures": ms.consecutive_failures,
        }
    return {"circuits": out}


def _view_sessions() -> dict:
    """Return bounded live affinity data without session identifiers."""
    from airlock.fast.router import _load_session_ttl

    return {
        "source": "live_admin",
        "sessions": _state.store.active_session_snapshot(
            ttl_seconds=_load_session_ttl(), limit=100
        ),
    }


def _operational_records(body: dict) -> dict:
    """Serve bounded history from the proxy-owned datastore process only."""
    from airlock.operational_reads import read_records

    days = body.get("days", 31)
    limit = body.get("limit", 5_000)
    if not isinstance(days, int) or not 1 <= days <= 31:
        raise ValueError("days must be an integer from 1 through 31")
    if not isinstance(limit, int) or not 1 <= limit <= 5_000:
        raise ValueError("limit must be an integer from 1 through 5000")
    page = read_records(
        directory=os.getenv("AIRLOCK_LOG_DIR", "./logs"), days=days, limit=limit
    )
    return {
        "records": page.records,
        "source": page.source,
        "degraded_reason": page.degraded_reason,
        "truncated": page.truncated,
        "limit_hit": page.limit_hit,
    }


def _operational_errors(body: dict) -> dict:
    from airlock.advisor.tools import get_recent_errors

    days = _operational_int(body, "days", default=2, maximum=31)
    return get_recent_errors(os.getenv("AIRLOCK_LOG_DIR", "./logs"), days=days)


def _operational_search(body: dict) -> dict:
    from airlock.advisor.tools import search_logs

    days = _operational_int(body, "days", default=7, maximum=31)
    limit = _operational_int(body, "limit", default=20, maximum=50)
    query = body.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 1_000:
        raise ValueError("query must be a non-empty string of at most 1000 characters")
    return search_logs(
        os.getenv("AIRLOCK_LOG_DIR", "./logs"),
        query=query,
        limit=limit,
        days=days,
    )


def _operational_int(body: dict, name: str, *, default: int, maximum: int) -> int:
    value = body.get(name, default)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{name} must be an integer from 1 through {maximum}")
    return value


def _decode_session_client_selector(selector: str) -> str:
    """Decode the opaque URL-safe client selector used by the TUI.

    Client identifiers are header-derived and therefore cannot safely be put
    into a slash-delimited route.  The selector is transport-only; the decoded
    value remains subject to normal StateStore normalization.
    """
    try:
        padded = selector + "=" * (-len(selector) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid session client selector") from None


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
    if method == "GET" and seg == ["sessions"]:
        return ("admin:read", False, lambda p, b, a: _view_sessions())
    if method == "GET" and seg == ["telemetry"]:
        return ("admin:read", False, lambda p, b, a: _view_telemetry())
    if method == "POST" and seg == ["operational", "records"]:
        # History may contain request content; it is a local TUI bridge, not a
        # remotely capability-token-readable snapshot.
        return ("admin:read", True, lambda p, b, a: _operational_records(b))
    if method == "POST" and seg == ["operational", "errors"]:
        return ("admin:read", True, lambda p, b, a: _operational_errors(b))
    if method == "POST" and seg == ["operational", "search"]:
        return ("admin:read", True, lambda p, b, a: _operational_search(b))

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
        and seg[0] == "session-clients"
        and seg[2] == "clear"
    ):
        selector = seg[1]
        return (
            "admin:clear_sessions",
            False,
            lambda p, b, a: _state.store.clear_client_sessions(
                _decode_session_client_selector(selector), actor=a
            ),
        )
    if method == "POST" and len(seg) == 3 and seg[0] == "clients" and seg[2] == "erase":
        client = seg[1]
        return (
            "admin:erase_client",
            True,  # loopback-only (operator) — destructive, like force_quarantine
            lambda p, b, a: erase_client(client, a, confirm=b.get("confirm")),
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
    except EraseIncomplete as exc:
        # A partial erasure is never reported as complete. Audit the attempt,
        # answer 409 with the obligation outstanding; retrying is safe.
        write_admin_action_record(exc.record)
        return 409, dict(exc.record), {}
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

    app = resolve_proxy_app()
    if not isinstance(app, FastAPI):
        return False
    if getattr(app.state, "airlock_admin_installed", False):
        return True
    install_asgi_middleware(app, AdminMiddleware)
    app.state.airlock_admin_installed = True
    return True
