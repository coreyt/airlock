"""Canonical UTC time handling.

Two helpers, existing because the obvious spelling is wrong in a way that is
easy to ship and hard to notice.

``datetime.utcnow()`` returns a **naive** datetime, so ``utcnow().isoformat()``
yields ``2026-08-05T12:00:00`` and appending ``"Z"`` produces a correct RFC 3339
timestamp. ``utcnow()`` is deprecated, but the timezone-aware replacement
``datetime.now(timezone.utc)`` renders as ``2026-08-05T12:00:00+00:00`` — so the
same ``+ "Z"`` suffix produces ``...+00:00Z``, which is not a valid timestamp.

That corruption has already reached this repository once: it is why
``tui/screens/logs.py::_parse_record_timestamp`` carries an explicit
``+00:00Z`` special case (``db76583``, "retain legacy UTC JSONL timestamps").
Use :func:`isoformat_z` rather than hand-appending the suffix.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utc_now", "isoformat_z", "parse_utc"]


def utc_now() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    """Render *value* as RFC 3339 UTC with a trailing ``Z``.

    Accepts naive datetimes, which are assumed to be UTC — the assumption
    ``utcnow()`` callers were already making implicitly.
    """
    if value.tzinfo is None:
        aware = value.replace(tzinfo=timezone.utc)
    else:
        aware = value.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "") + "Z"


def parse_utc(value: str) -> datetime | None:
    """Parse a JSONL timestamp to an aware UTC datetime, or ``None``.

    Accepts the canonical ``...Z`` spelling, a naive timestamp (assumed UTC),
    and the legacy ``...+00:00Z`` spelling written before ``db76583``. Returns
    ``None`` rather than raising, so a single malformed record does not abort a
    scan over a whole window.
    """
    if not isinstance(value, str) or not value:
        return None
    raw = value[:-1] if value.endswith("+00:00Z") else value
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
