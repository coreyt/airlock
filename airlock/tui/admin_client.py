"""Loopback admin client for the TUI (Pack 0.5.0-ADM-tui).

The TUI runs as a separate process and reaches the proxy's admin API over
loopback, where the perimeter grants operator access by network position
(Path A) — no credentials needed. When the proxy serves native TLS the cert is
typically self-signed, so verification is skipped for the loopback connection
only (umbrella R10).
"""

from __future__ import annotations

import json
import base64
import os
import ssl
import urllib.error
import urllib.request

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _scheme_and_context(host: str) -> tuple[str, ssl.SSLContext | None]:
    if os.getenv("AIRLOCK_SSL_CERTFILE") and os.getenv("AIRLOCK_SSL_KEYFILE"):
        ctx = ssl.create_default_context()
        if host in _LOOPBACK_HOSTS:
            # Loopback self-signed cert: skip verification for loopback ONLY (R10).
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return "https", ctx
    return "http", None


def admin_post(
    host: str,
    port: str,
    path: str,
    body: dict | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[int, dict]:
    """POST to a loopback admin endpoint. Returns (status, payload). status 0 on
    a transport error (with an ``error`` payload). Never raises."""
    scheme, ctx = _scheme_and_context(host)
    url = f"{scheme}://{host}:{port}{path}"
    try:
        data = json.dumps(body or {}).encode()
    except (TypeError, ValueError):
        return 0, {"error": "unserializable body"}
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status, raw = resp.status, (resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            return exc.code, {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return 0, {"error": str(exc)}
    # Parse the body outside the transport try so a non-JSON 2xx keeps its status.
    try:
        payload = json.loads(raw)
    except ValueError:
        return status, {"error": "non-JSON response"}
    return status, payload if isinstance(payload, dict) else {"data": payload}


def admin_get(
    host: str, port: str, path: str, *, timeout: float = 2.0
) -> tuple[int, dict]:
    """Read a loopback admin snapshot. Never raises or emits an audit action."""
    scheme, ctx = _scheme_and_context(host)
    try:
        req = urllib.request.Request(f"{scheme}://{host}:{port}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status, raw = resp.status, (resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            return exc.code, {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return 0, {"error": str(exc)}
    try:
        payload = json.loads(raw)
    except ValueError:
        return status, {"error": "non-JSON response"}
    return status, payload if isinstance(payload, dict) else {"data": payload}


def provider_snapshot(host: str, port: str) -> dict | None:
    """Return a provider snapshot, degrading silently when admin is unavailable."""
    status, payload = admin_get(host, port, "/airlock/admin/providers")
    return (
        payload
        if status == 200 and isinstance(payload.get("providers"), dict)
        else None
    )


def session_snapshot(host: str, port: str) -> dict | None:
    """Return the bounded live affinity view, or None when admin is unavailable."""
    status, payload = admin_get(host, port, "/airlock/admin/sessions")
    return (
        payload if status == 200 and isinstance(payload.get("sessions"), list) else None
    )


def client_snapshot(host: str, port: str) -> dict | None:
    status, payload = admin_get(host, port, "/airlock/admin/clients")
    return (
        payload if status == 200 and isinstance(payload.get("clients"), dict) else None
    )


def telemetry_snapshot(host: str, port: str) -> dict | None:
    status, payload = admin_get(host, port, "/airlock/admin/telemetry")
    return (
        payload
        if status == 200 and isinstance(payload.get("exporters"), dict)
        else None
    )


def operational_records(host: str, port: str, *, days: int, limit: int) -> dict | None:
    """Read history from the proxy-owned FathomDB process, when selected."""
    status, payload = admin_post(
        host,
        port,
        "/airlock/admin/operational/records",
        {"days": days, "limit": limit},
    )
    return (
        payload if status == 200 and isinstance(payload.get("records"), list) else None
    )


def operational_view(host: str, port: str, kind: str, body: dict) -> dict | None:
    """Call a loopback-only, proxy-owned operational read view."""
    status, payload = admin_post(host, port, f"/airlock/admin/operational/{kind}", body)
    return payload if status == 200 else None


def clear_client_sessions(host: str, port: str, client_id: str) -> tuple[int, dict]:
    # Client IDs come from a header and can contain a slash. Use an opaque
    # URL-safe selector so a path parser can never split the selected identity.
    selector = base64.urlsafe_b64encode(client_id.encode("utf-8")).decode("ascii")
    selector = selector.rstrip("=")
    return admin_post(host, port, f"/airlock/admin/session-clients/{selector}/clear")


def clear_provider_quarantine(
    host: str, port: str, provider: str, mode: str = "probe"
) -> tuple[int, dict]:
    return admin_post(
        host,
        port,
        f"/airlock/admin/providers/{provider}/clear-quarantine",
        {"mode": mode},
    )
