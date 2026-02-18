"""SessionStart hook — probe proxy health and inject context."""

from __future__ import annotations

import os

from airlock.hooks._common import probe_health, respond_json


def main() -> None:
    host = os.environ.get("AIRLOCK_HOST", "localhost")
    port = os.environ.get("AIRLOCK_PORT", "4000")

    if probe_health(host, port):
        status = f"Airlock proxy is running at {host}:{port}."
    else:
        status = (
            f"Airlock proxy is NOT reachable at {host}:{port}. "
            "API calls routed through the proxy will FAIL. "
            "To recover: run `airlock start` to restart the proxy, "
            "or run `unset ANTHROPIC_BASE_URL` to bypass it."
        )

    respond_json({"additionalContext": status})


if __name__ == "__main__":
    main()
