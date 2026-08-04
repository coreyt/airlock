"""Bounded reader for Airlock's JSONL request logs.

Every consumer that reads request logs — the slow analyzer, the advisor tools,
and the TUI log screen — goes through here, so the bounds and the truncation
semantics live in one place.

Why this exists
---------------
The TUI screen was given a record cap in 0.5.9; the other readers were not.
``slow/analyzer._load_logs`` read every record from every daily file into a
list, and ``advisor/tools.get_recent_errors`` passed ``limit=1000000`` — a limit
in name only. Both are reachable from the CLI and the TUI, so a deployment with
enough accumulated history would try to hold its whole log corpus in memory,
with no degradation path: it either fits or the process dies. Under the systemd
unit's ``MemoryMax``, that is an OOM kill of the proxy, not just of the
analysis.

Three properties matter, and the second matters most:

**Filter while scanning.** The predicate runs per line, so a narrow query never
materializes the whole corpus. This is what makes a generous record ceiling
generous rather than restrictive.

**Truncation is reported, never silent.** :attr:`LogPage.truncated` and
:attr:`LogPage.limit_hit` are meant to reach the caller's output. An analysis
that scanned half the window and presents itself as complete is worse than one
that refuses — it yields confident, wrong conclusions about traffic it never
saw.

**Newest-first.** Days are walked backwards from today, so a truncated result
retains the most recent records, which is what every consumer actually wants.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

#: Hard ceiling on retained records. Generous because the predicate filters
#: during the scan; a narrow query will not approach it.
DEFAULT_MAX_RECORDS = 50_000

#: Hard ceiling on bytes read from disk in one query.
DEFAULT_MAX_BYTES = 256 * 1024 * 1024

LIMIT_RECORDS = "max_records"
LIMIT_BYTES = "max_bytes"


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def log_dir() -> Path:
    return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


@dataclass(frozen=True)
class LogQuery:
    """A bounded request for log records.

    ``predicate`` is applied per record during the scan. Returning False costs
    only the parse, so callers should filter here rather than afterwards.
    """

    days: int = 7
    max_records: int = 0  # 0 → resolve from environment/default
    max_bytes: int = 0  # 0 → resolve from environment/default
    predicate: Callable[[dict[str, Any]], bool] | None = None
    directory: Path | None = None

    def resolved_max_records(self) -> int:
        return self.max_records or _env_int(
            "AIRLOCK_LOG_QUERY_MAX_RECORDS", DEFAULT_MAX_RECORDS
        )

    def resolved_max_bytes(self) -> int:
        return self.max_bytes or _env_int(
            "AIRLOCK_LOG_QUERY_MAX_BYTES", DEFAULT_MAX_BYTES
        )


@dataclass
class LogPage:
    """Result of a bounded scan.

    ``truncated`` means a limit stopped the scan before the requested window was
    exhausted — the caller is holding a partial view and must say so.
    """

    records: list[dict[str, Any]] = field(default_factory=list)
    scanned: int = 0
    bytes_read: int = 0
    truncated: bool = False
    limit_hit: str | None = None
    files_read: list[str] = field(default_factory=list)
    oldest_day: str | None = None

    def note(self) -> str | None:
        """One-line, human-facing description of truncation, or None."""
        if not self.truncated:
            return None
        if self.limit_hit == LIMIT_RECORDS:
            return (
                f"Results truncated at {len(self.records):,} records; "
                "older records in the requested window were not read."
            )
        return (
            f"Results truncated after reading {self.bytes_read / 1024 / 1024:.0f} MB; "
            "older records in the requested window were not read."
        )

    def as_metadata(self) -> dict[str, Any]:
        """Truncation state for embedding in reports and tool results."""
        return {
            "records": len(self.records),
            "scanned": self.scanned,
            "truncated": self.truncated,
            "limit_hit": self.limit_hit,
            "oldest_day": self.oldest_day,
        }


def _day_files(query: LogQuery) -> Iterator[tuple[str, Path]]:
    """Yield ``(iso_day, path)`` newest first for the requested window."""
    directory = query.directory or log_dir()
    today = datetime.now(timezone.utc).date()
    for offset in range(max(1, query.days)):
        day = today - timedelta(days=offset)
        path = directory / f"airlock-{day.isoformat()}.jsonl"
        if path.exists():
            yield day.isoformat(), path


def query_logs(query: LogQuery | None = None) -> LogPage:
    """Scan request logs newest-first under explicit bounds."""
    query = query or LogQuery()
    max_records = query.resolved_max_records()
    max_bytes = query.resolved_max_bytes()
    page = LogPage()

    for day, path in _day_files(query):
        page.files_read.append(path.name)
        page.oldest_day = day
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                page.bytes_read += len(line)
                if page.bytes_read > max_bytes:
                    page.truncated = True
                    page.limit_hit = LIMIT_BYTES
                    return page
                line = line.strip()
                if not line:
                    continue
                page.scanned += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if query.predicate is not None and not query.predicate(record):
                    continue
                page.records.append(record)
                if len(page.records) >= max_records:
                    page.truncated = True
                    page.limit_hit = LIMIT_RECORDS
                    return page
    return page


def load_records(
    days: int = 7,
    *,
    directory: Path | str | None = None,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
    max_records: int = 0,
) -> LogPage:
    """Convenience wrapper returning a :class:`LogPage`."""
    return query_logs(
        LogQuery(
            days=days,
            predicate=predicate,
            max_records=max_records,
            directory=Path(directory) if directory is not None else None,
        )
    )
