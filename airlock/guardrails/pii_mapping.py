"""Bounded, request-scoped storage for PII placeholder reverse maps.

The map is intentionally not request metadata: LiteLLM and third-party callback
code may serialize metadata wholesale.  A random opaque handle is the only value
that crosses that boundary.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class _Entry:
    mapping: dict[str, str]
    expires_at: float


class PIIMapStore:
    """Thread-safe bounded map store with consume-on-read semantics."""

    def __init__(self, *, max_entries: int = 1024, ttl_seconds: float = 300.0) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, mapping: dict[str, str]) -> str | None:
        """Store one request map or return ``None`` when safely saturated."""
        now = time.monotonic()
        with self._lock:
            self._sweep_locked(now)
            if len(self._entries) >= self._max_entries:
                return None
            handle = secrets.token_urlsafe(24)
            self._entries[handle] = _Entry(
                mapping=dict(mapping), expires_at=now + self._ttl_seconds
            )
            return handle

    def take(self, handle: str) -> dict[str, str] | None:
        """Atomically consume a live map; expired/missing handles return ``None``."""
        now = time.monotonic()
        with self._lock:
            self._sweep_locked(now)
            entry = self._entries.pop(handle, None)
            return dict(entry.mapping) if entry is not None else None

    def discard(self, handle: str) -> None:
        with self._lock:
            self._entries.pop(handle, None)

    def sweep(self) -> int:
        with self._lock:
            return self._sweep_locked(time.monotonic())

    def __len__(self) -> int:
        with self._lock:
            self._sweep_locked(time.monotonic())
            return len(self._entries)

    def _sweep_locked(self, now: float) -> int:
        expired = [
            handle
            for handle, entry in self._entries.items()
            if entry.expires_at <= now
        ]
        for handle in expired:
            del self._entries[handle]
        return len(expired)
