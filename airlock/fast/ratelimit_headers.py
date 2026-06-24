"""Parse upstream ``x-ratelimit-*`` headers into quota headroom (workstream C).

Tolerant by design: providers differ in which headers they send and in how they
format the reset values, so any missing/unparseable field yields ``None`` rather
than raising. Captured on both the success path (``response._hidden_params
["additional_headers"]``) and the 429 failure path (``exc.response.headers``).
"""

from __future__ import annotations

import re

_DURATION_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?"
)


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_reset_seconds(value: object) -> float | None:
    """Parse a reset value to seconds.

    Accepts a plain number (``"12"`` / ``"12.5"`` → seconds) or a Go-style
    duration (``"1s"``, ``"6m0s"``, ``"1h2m3s"``). Returns ``None`` if unparseable.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)  # plain seconds
    except ValueError:
        pass
    m = _DURATION_RE.fullmatch(text)
    if not m or not any(m.groups()):
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in m.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_ratelimit_headers(headers: object) -> dict:
    """Extract a normalized headroom dict from a header mapping.

    Returns keys ``remaining_tokens``, ``remaining_requests``, ``limit_tokens``,
    ``limit_requests``, ``reset_tokens_seconds``, ``reset_requests_seconds`` —
    each ``None`` when the corresponding header is absent or unparseable. Returns
    an all-``None`` dict for a non-mapping / empty input.
    """
    out = {
        "remaining_tokens": None,
        "remaining_requests": None,
        "limit_tokens": None,
        "limit_requests": None,
        "reset_tokens_seconds": None,
        "reset_requests_seconds": None,
    }
    if not hasattr(headers, "get"):
        return out

    def get(*names: str) -> object:
        for name in names:
            val = headers.get(name)
            if val is None and hasattr(headers, "get"):
                # case-insensitive fallback for plain dicts
                for k, v in getattr(headers, "items", lambda: [])():
                    if str(k).lower() == name:
                        val = v
                        break
            if val is not None:
                return val
        return None

    out["remaining_tokens"] = _to_int(get("x-ratelimit-remaining-tokens"))
    out["remaining_requests"] = _to_int(get("x-ratelimit-remaining-requests"))
    out["limit_tokens"] = _to_int(get("x-ratelimit-limit-tokens"))
    out["limit_requests"] = _to_int(get("x-ratelimit-limit-requests"))
    out["reset_tokens_seconds"] = _to_reset_seconds(get("x-ratelimit-reset-tokens"))
    out["reset_requests_seconds"] = _to_reset_seconds(get("x-ratelimit-reset-requests"))
    return out
