"""``airlock status`` — check if the proxy is running."""

from __future__ import annotations

import os
import sys
import urllib.request

from airlock.client_identity import add_airlock_client_header


def _health_request(url: str) -> urllib.request.Request:
    """Build a health-check request, adding auth if master key is set."""
    req = add_airlock_client_header(urllib.request.Request(url))
    master_key = os.environ.get("AIRLOCK_MASTER_KEY")
    if master_key:
        req.add_header("Authorization", f"Bearer {master_key}")
    return req


def run(args) -> None:
    """Probe the proxy health endpoint and report status."""
    host = args.host or os.environ.get("AIRLOCK_HOST", "localhost")
    port = args.port or os.environ.get("AIRLOCK_PORT", "4000")
    # 0.0.0.0 is a bind address, not connectable — probe via loopback
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{probe_host}:{port}/health/liveliness"

    try:
        urllib.request.urlopen(_health_request(url), timeout=5)  # noqa: S310
    except Exception:
        print(f"Airlock is not reachable at {host}:{port}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Airlock is running at {host}:{port}")
    raise SystemExit(0)
