"""Loopback admin client for the TUI (Pack 0.5.0-ADM-tui).

The TUI runs as a separate process and reaches the proxy's admin API over
loopback, where the perimeter grants operator access by network position
(Path A) — no credentials needed. When the proxy serves native TLS the cert is
typically self-signed, so verification is skipped for the loopback connection
only (umbrella R10).
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request


def _scheme_and_context() -> tuple[str, ssl.SSLContext | None]:
    if os.getenv("AIRLOCK_SSL_CERTFILE") and os.getenv("AIRLOCK_SSL_KEYFILE"):
        ctx = ssl.create_default_context()
        # Loopback self-signed cert: skip verification for 127.0.0.1 only.
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
    scheme, ctx = _scheme_and_context()
    url = f"{scheme}://{host}:{port}{path}"
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read() or b"{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            payload = {"error": f"HTTP {exc.code}"}
        return exc.code, payload
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"error": str(exc)}


def clear_provider_quarantine(
    host: str, port: str, provider: str, mode: str = "probe"
) -> tuple[int, dict]:
    return admin_post(
        host,
        port,
        f"/airlock/admin/providers/{provider}/clear-quarantine",
        {"mode": mode},
    )
