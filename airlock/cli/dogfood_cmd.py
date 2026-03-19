"""``airlock dogfood`` — print env export lines for routing Claude Code through Airlock."""

from __future__ import annotations

import os
import sys
import urllib.request

from airlock.client_identity import add_airlock_client_header


def _probe_health(host: str, port: str) -> bool:
    # 0.0.0.0 is a bind address, not connectable — probe via loopback
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{probe_host}:{port}/health?client=cli-dogfood"
    req = add_airlock_client_header(urllib.request.Request(url))
    master_key = os.environ.get("AIRLOCK_MASTER_KEY")
    if master_key:
        req.add_header("Authorization", f"Bearer {master_key}")
    try:
        urllib.request.urlopen(req, timeout=3)  # noqa: S310
        return True
    except Exception:
        return False


def _quote_value(value: str, shell: str) -> str:
    """Quote a value for the target shell."""
    if shell == "fish":
        return f"'{value}'"
    return f"'{value}'"


def run(args) -> None:
    """Print export lines for routing Claude Code through Airlock."""
    host = args.host or os.environ.get("AIRLOCK_HOST", "localhost")
    port = args.port or os.environ.get("AIRLOCK_PORT", "4000")
    master_key = args.master_key or os.environ.get("AIRLOCK_MASTER_KEY", "")
    shell = args.shell or "bash"

    # Health check — warn to stderr if proxy is down
    if not _probe_health(host, port):
        print(
            f"Warning: Airlock proxy is not reachable at {host}:{port}",
            file=sys.stderr,
        )

    base_url = f"http://{host}:{port}"

    if shell == "fish":
        print(f"set -gx ANTHROPIC_BASE_URL {_quote_value(base_url, shell)}")
        if master_key:
            print(f"set -gx ANTHROPIC_AUTH_TOKEN {_quote_value(master_key, shell)}")
    else:
        print(f"export ANTHROPIC_BASE_URL={_quote_value(base_url, shell)}")
        if master_key:
            print(f"export ANTHROPIC_AUTH_TOKEN={_quote_value(master_key, shell)}")
