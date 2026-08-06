"""CLI: ``airlock admin ...`` — capability tokens and control-plane operations.

``mint-token`` signs tokens locally with the server-side secret; no network
call. ``erase-client`` calls the running proxy's loopback admin API — the
engine is single-owner at process level, so the proxy performs the erasure
and writes the audit record; the CLI never opens the database file itself.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_ttl(text: str) -> int:
    """Parse a TTL like ``30m`` / ``1h`` / ``24h`` / ``3600`` into seconds."""
    value = str(text).strip().lower()
    if not value:
        raise ValueError("empty ttl")
    if value[-1] in _UNITS:
        return int(float(value[:-1]) * _UNITS[value[-1]])
    return int(float(value))


def _erase_client(args: Any) -> None:
    if args.confirm != args.client_id:
        print(
            "error: --confirm must repeat the client id exactly "
            f"(got {args.confirm!r}, erasing {args.client_id!r})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    host = args.host or "127.0.0.1"
    port = args.port or os.getenv("AIRLOCK_PORT", "4000")
    url = (
        f"http://{host}:{port}/airlock/admin/clients/"
        f"{urllib.parse.quote(args.client_id, safe='')}/erase"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps({"confirm": args.confirm}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode())
        except (json.JSONDecodeError, ValueError):
            payload = {"error": f"HTTP {exc.code}"}
        if exc.code == 409 and payload.get("outcome") == "incomplete":
            # Never presented as done: the obligation is outstanding.
            print(
                "erasure INCOMPLETE — the obligation is outstanding: "
                f"{payload.get('error', 'unknown')}\n"
                "Retrying is safe (erasure is idempotent). Run the same "
                "command again.",
                file=sys.stderr,
            )
        else:
            print(f"error: {payload.get('error', exc)}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as exc:
        print(
            f"error: cannot reach the proxy admin API at {url}: {exc.reason}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    report = payload.get("erase_report") or {}
    print(
        f"erased client {args.client_id} from the FathomDB store: "
        f"{report.get('nodes_excised', 0)} nodes, "
        f"{report.get('edges_excised', 0)} edges, "
        f"{report.get('projections_invalidated', 0)} projections invalidated."
    )
    print(
        "note: JSONL logs are NOT touched by this operation; their retention "
        "is governed by AIRLOCK_MAX_LOG_DAYS."
    )


def run(args: Any) -> None:
    action = getattr(args, "admin_action", None)
    if action == "mint-token":
        from airlock.admin.tokens import TokenError, mint_token

        try:
            ttl = _parse_ttl(args.ttl)
            token = mint_token(args.sub, args.scopes, ttl)
        except (TokenError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(token)
    elif action == "erase-client":
        _erase_client(args)
    else:
        print(
            "usage: airlock admin {mint-token --sub <id> --scope <scope> [--ttl 1h] "
            "| erase-client <client-id> --confirm <client-id>}",
            file=sys.stderr,
        )
        raise SystemExit(2)
