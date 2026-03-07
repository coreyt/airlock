"""Shared utilities for Airlock Claude Code hooks."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from pathlib import Path

from dotenv import load_dotenv

# Hooks are spawned as separate processes by Claude Code — CWD may not be
# the project root.  Walk up from this file to find the project .env.
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")


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


def probe_health(host: str, port: str, timeout: int = 3, *, client: str = "hook") -> bool:
    """Check if the Airlock proxy is reachable."""
    # 0.0.0.0 is a bind address, not connectable — probe via loopback
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{probe_host}:{port}/health?client={client}"
    req = urllib.request.Request(url)
    master_key = os.environ.get("AIRLOCK_MASTER_KEY")
    if master_key:
        req.add_header("Authorization", f"Bearer {master_key}")
    try:
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        return True
    except Exception:
        return False


def get_blocked_keywords() -> list[str]:
    """Parse AIRLOCK_BLOCKED_KEYWORDS env var into lowercase keyword list."""
    raw = os.getenv("AIRLOCK_BLOCKED_KEYWORDS", "")
    return [kw.strip().lower() for kw in raw.split(",") if kw.strip()]
