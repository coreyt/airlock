"""CLI: ``airlock admin ...`` — capability token minting (UN-11).

Signs tokens locally with the server-side secret; no network call. The minted
token is handed to a client out-of-band.
"""

from __future__ import annotations

import sys
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
    else:
        print(
            "usage: airlock admin mint-token --sub <id> --scope <scope> [--ttl 1h]",
            file=sys.stderr,
        )
        raise SystemExit(2)
