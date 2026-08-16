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
import stat
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class AdminConnectionError(ValueError):
    """A local remote-admin client credential or TLS setup is unsafe."""


@dataclass(frozen=True)
class AdminConnection:
    """Immutable remote Admin transport; token is intentionally non-repr."""

    host: str
    port: str
    token: str = field(repr=False)
    ssl_context: ssl.SSLContext = field(repr=False, compare=False)

    @classmethod
    def from_files(
        cls, host: str, port: str, token_file: str | Path, ca_file: str | Path
    ) -> "AdminConnection":
        path = Path(token_file)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise AdminConnectionError(
                "remote Admin token files require no-follow support"
            )
        try:
            descriptor = os.open(path, os.O_RDONLY | nofollow)
        except OSError as exc:
            raise AdminConnectionError(
                "remote Admin token file must be a readable regular file"
            ) from exc
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as token_handle:
                info = os.fstat(token_handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise AdminConnectionError(
                        "remote Admin token file must be a regular file"
                    )
                if info.st_mode & 0o077:
                    raise AdminConnectionError(
                        "remote Admin token file permissions must be 0600"
                    )
                if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                    raise AdminConnectionError(
                        "remote Admin token file must be owned by this user"
                    )
                if info.st_size > 8192:
                    raise AdminConnectionError("remote Admin token file is too large")
                token = token_handle.read().strip()
        except (OSError, UnicodeError) as exc:
            raise AdminConnectionError("remote Admin token file is unreadable") from exc
        if not token:
            raise AdminConnectionError("remote Admin token file is empty")
        if not all(
            char.isascii() and (char.isalnum() or char in "-_.") for char in token
        ):
            raise AdminConnectionError("remote Admin token file is invalid")
        try:
            context = ssl.create_default_context(cafile=os.fspath(ca_file))
        except (OSError, ssl.SSLError) as exc:
            raise AdminConnectionError(
                "remote Admin CA file is invalid or unreadable"
            ) from exc
        # Be explicit: a localhost target is still a remote container path.
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return cls(host=str(host), port=str(port), token=token, ssl_context=context)


def _scheme_and_context(host: str) -> tuple[str, ssl.SSLContext | None]:
    if os.getenv("AIRLOCK_SSL_CERTFILE") and os.getenv("AIRLOCK_SSL_KEYFILE"):
        ctx = ssl.create_default_context()
        if host in _LOOPBACK_HOSTS:
            # Loopback self-signed cert: skip verification for loopback ONLY (R10).
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return "https", ctx
    return "http", None


def _transport(
    host: str, port: str, connection: AdminConnection | None
) -> tuple[str, str, str, ssl.SSLContext | None, dict[str, str]]:
    if connection is not None:
        return (
            "https",
            connection.host,
            connection.port,
            connection.ssl_context,
            {"Authorization": f"Bearer {connection.token}"},
        )
    scheme, context = _scheme_and_context(host)
    return scheme, host, port, context, {}


def admin_post(
    host: str,
    port: str,
    path: str,
    body: dict | None = None,
    *,
    timeout: float = 5.0,
    connection: AdminConnection | None = None,
) -> tuple[int, dict]:
    """POST to a loopback admin endpoint. Returns (status, payload). status 0 on
    a transport error (with an ``error`` payload). Never raises."""
    scheme, host, port, ctx, auth_headers = _transport(host, port, connection)
    url = f"{scheme}://{host}:{port}{path}"
    try:
        data = json.dumps(body or {}).encode()
    except (TypeError, ValueError):
        return 0, {"error": "unserializable body"}
    try:
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", **auth_headers},
        )
    except ValueError:
        return 0, {"error": "request unavailable"}
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
    host: str,
    port: str,
    path: str,
    *,
    timeout: float = 2.0,
    connection: AdminConnection | None = None,
) -> tuple[int, dict]:
    """Read a loopback admin snapshot. Never raises or emits an audit action."""
    scheme, host, port, ctx, auth_headers = _transport(host, port, connection)
    try:
        req = urllib.request.Request(
            f"{scheme}://{host}:{port}{path}", method="GET", headers=auth_headers
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status, raw = resp.status, (resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            return exc.code, {"error": f"HTTP {exc.code}"}
    except ValueError:
        return 0, {"error": "request unavailable"}
    except (urllib.error.URLError, OSError) as exc:
        return 0, {"error": str(exc)}
    try:
        payload = json.loads(raw)
    except ValueError:
        return status, {"error": "non-JSON response"}
    return status, payload if isinstance(payload, dict) else {"data": payload}


def provider_snapshot(
    host: str, port: str, *, connection: AdminConnection | None = None
) -> dict | None:
    """Return a provider snapshot, degrading silently when admin is unavailable."""
    status, payload = admin_get(
        host, port, "/airlock/admin/providers", connection=connection
    )
    return (
        payload
        if status == 200 and isinstance(payload.get("providers"), dict)
        else None
    )


def provider_configuration_snapshot(
    host: str, port: str, *, connection: AdminConnection | None = None
) -> tuple[int, dict]:
    """Fetch the child-startup configuration view; never read local files."""
    return admin_get(
        host, port, "/airlock/admin/config/providers", connection=connection
    )


def session_snapshot(
    host: str, port: str, *, connection: AdminConnection | None = None
) -> dict | None:
    """Return the bounded live affinity view, or None when admin is unavailable."""
    status, payload = admin_get(
        host, port, "/airlock/admin/sessions", connection=connection
    )
    return (
        payload if status == 200 and isinstance(payload.get("sessions"), list) else None
    )


def client_snapshot(
    host: str, port: str, *, connection: AdminConnection | None = None
) -> dict | None:
    status, payload = admin_get(
        host, port, "/airlock/admin/clients", connection=connection
    )
    return (
        payload if status == 200 and isinstance(payload.get("clients"), dict) else None
    )


def telemetry_snapshot(
    host: str, port: str, *, connection: AdminConnection | None = None
) -> dict | None:
    status, payload = admin_get(
        host, port, "/airlock/admin/telemetry", connection=connection
    )
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


def clear_client_sessions(
    host: str, port: str, client_id: str, *, connection: AdminConnection | None = None
) -> tuple[int, dict]:
    # Client IDs come from a header and can contain a slash. Use an opaque
    # URL-safe selector so a path parser can never split the selected identity.
    selector = base64.urlsafe_b64encode(client_id.encode("utf-8")).decode("ascii")
    selector = selector.rstrip("=")
    return admin_post(
        host,
        port,
        f"/airlock/admin/session-clients/{selector}/clear",
        connection=connection,
    )


def clear_provider_quarantine(
    host: str,
    port: str,
    provider: str,
    mode: str = "probe",
    *,
    connection: AdminConnection | None = None,
) -> tuple[int, dict]:
    return admin_post(
        host,
        port,
        f"/airlock/admin/providers/{provider}/clear-quarantine",
        {"mode": mode},
        connection=connection,
    )
