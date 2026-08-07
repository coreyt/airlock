"""Incremental JSONL feed of ``airlock_failover`` events (#24).

The enterprise logger stamps ``airlock_failover`` on every circuit-breaker
swap (original model, failover target, reason). The Guards screen shows these
per-request; this feed gives the Overview screen the timeline view — "when did
failovers start, how many, what triggered them" — without re-reading the whole
log on every refresh.

Same incremental-read pattern as the Guards screen's ``_poll_logs``: remember
the file offset, follow today's file, reset on date rollover or truncation.
Pure I/O + parsing, no Textual — unit-testable directly.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any


def _epoch(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    try:
        parsed = _dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


class FailoverFeed:
    """Tail today's JSONL log for failover events, keeping the recent tail."""

    def __init__(self, log_dir: str | None = None, maxlen: int = 200):
        self._log_dir = log_dir
        self._path: Path | None = None
        self._pos = 0
        self.events: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def _today_path(self) -> Path:
        log_dir = Path(self._log_dir or os.getenv("AIRLOCK_LOG_DIR", "./logs"))
        today = _dt.datetime.now(_dt.timezone.utc).date()
        return log_dir / f"airlock-{today.isoformat()}.jsonl"

    def poll(self) -> None:
        """Read newly appended records; collect the failover-bearing ones."""
        path = self._today_path()
        if path != self._path:
            # Date rollover (or first poll): start from the top of the new file.
            self._path = path
            self._pos = 0
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as handle:
                handle.seek(0, os.SEEK_END)
                end = handle.tell()
                if end < self._pos:
                    # Truncated/rotated in place: re-read from the start.
                    self._pos = 0
                handle.seek(self._pos)
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    failover = record.get("airlock_failover")
                    if not failover:
                        continue
                    self.events.append(
                        {
                            "time": _epoch(record.get("timestamp")),
                            "timestamp": record.get("timestamp") or "",
                            "original_model": str(
                                failover.get("original_model") or "?"
                            ),
                            "failover_model": str(
                                failover.get("failover_model") or "?"
                            ),
                            "reason": str(failover.get("reason") or "-"),
                        }
                    )
                self._pos = handle.tell()
        except OSError:
            return

    def recent(
        self,
        window_seconds: float = 300.0,
        model: str | None = None,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Events within the window, oldest first; optionally for one model.

        ``model`` matches either side of the swap — the operator selecting a
        model wants both "failed away from" and "failed into" traffic.
        """
        cutoff = (now if now is not None else time.time()) - window_seconds
        return [
            event
            for event in self.events
            if event["time"] >= cutoff
            and (
                model is None
                or model in (event["original_model"], event["failover_model"])
            )
        ]

    def recent_count(
        self, window_seconds: float = 300.0, now: float | None = None
    ) -> int:
        return len(self.recent(window_seconds, now=now))
