"""``airlock status`` — check if the proxy is running."""

from __future__ import annotations

import os
import sys
import urllib.request


def run(args) -> None:
    """Probe the proxy health endpoint and report status."""
    host = args.host or os.environ.get("AIRLOCK_HOST", "localhost")
    port = args.port or os.environ.get("AIRLOCK_PORT", "4000")
    url = f"http://{host}:{port}/health"

    try:
        urllib.request.urlopen(url, timeout=5)  # noqa: S310
    except Exception:
        print(f"Airlock is not reachable at {host}:{port}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Airlock is running at {host}:{port}")
    raise SystemExit(0)
