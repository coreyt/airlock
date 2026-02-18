"""Shared utilities for Airlock Claude Code hooks."""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def read_hook_input() -> dict:
    """Parse JSON from stdin (Claude Code hook protocol)."""
    return json.loads(sys.stdin.read())


def block(message: str) -> None:
    """Reject the action — exit 2 with message to stderr."""
    print(message, file=sys.stderr)
    raise SystemExit(2)


def proceed() -> None:
    """Allow the action — exit 0."""
    raise SystemExit(0)


def respond_json(data: dict) -> None:
    """Return JSON payload to Claude Code on stdout, then exit 0."""
    json.dump(data, sys.stdout)
    raise SystemExit(0)


def probe_health(host: str, port: str, timeout: int = 3) -> bool:
    """Check if the Airlock proxy is reachable."""
    url = f"http://{host}:{port}/health"
    try:
        urllib.request.urlopen(url, timeout=timeout)  # noqa: S310
        return True
    except Exception:
        return False


def get_blocked_keywords() -> list[str]:
    """Parse AIRLOCK_BLOCKED_KEYWORDS env var into lowercase keyword list."""
    raw = os.getenv("AIRLOCK_BLOCKED_KEYWORDS", "")
    return [kw.strip().lower() for kw in raw.split(",") if kw.strip()]
