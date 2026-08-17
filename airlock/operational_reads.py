"""Explicit, bounded operational-read backend selection.

FathomDB is a write/analysis store unless an operator deliberately selects it
for reads. This module centralizes the selection and the truthful fallback
shape, so consumers cannot silently change their source merely because a local
engine happens to be open.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from airlock.api.queries import DATASTORE_QUERY_LIMIT, get_request_logs, node_properties
from airlock.log_query import LogQuery, query_logs

_BACKEND_ENV = "AIRLOCK_OPERATIONAL_READ_BACKEND"
_FATHOMDB = "fathomdb"
_JSONL = "jsonl"


@dataclass(frozen=True)
class OperationalReadPage:
    records: list[dict[str, Any]]
    source: str
    degraded_reason: str | None
    truncated: bool
    limit_hit: str | None


def _requested_backend() -> tuple[str, str | None]:
    raw = (os.getenv(_BACKEND_ENV) or _JSONL).strip().lower()
    if raw in {_JSONL, _FATHOMDB}:
        return raw, None
    return _JSONL, f"invalid operational backend {raw!r}; using bounded JSONL"


def selected_fathom_engine() -> tuple[Any | None, str | None]:
    """Return the explicitly selected engine, never opening it by default."""
    requested, invalid_reason = _requested_backend()
    if requested != _FATHOMDB:
        return None, invalid_reason
    try:
        import airlock.datastore

        engine = airlock.datastore.get_engine()
    except Exception:
        engine = None
    if engine is None:
        return None, "FathomDB selected but unavailable; using bounded JSONL"
    return engine, None


def fathomdb_selected() -> bool:
    """Whether the operator selected FathomDB without opening an engine."""
    return _requested_backend()[0] == _FATHOMDB


def _within_days(record: dict[str, Any], days: int) -> bool:
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        return False
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed >= datetime.now(timezone.utc) - timedelta(days=max(1, days))


def read_records(
    *,
    directory: Path | str,
    days: int,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
    limit: int = DATASTORE_QUERY_LIMIT,
    allow_fathom: bool = True,
) -> OperationalReadPage:
    """Read selected operational history with explicit source and degradation.

    ``limit`` bounds both source paths. The FathomDB API currently lists then
    filters Airlock records client-side, so filling its list limit is reported
    as partial even if later predicates select fewer records.
    """
    limit = max(1, min(limit, DATASTORE_QUERY_LIMIT))
    if allow_fathom:
        engine, fallback_reason = selected_fathom_engine()
    else:
        engine = None
        fallback_reason = (
            "FathomDB selected but proxy operational reads unavailable; using bounded JSONL"
            if fathomdb_selected()
            else _requested_backend()[1]
        )
    if engine is not None:
        try:
            nodes = get_request_logs(engine, limit=limit)
        except Exception:
            nodes = None
            fallback_reason = "FathomDB selected but unavailable; using bounded JSONL"
        if nodes is not None:
            records = [node_properties(node) for node in nodes]
            records = [record for record in records if _within_days(record, days)]
            if predicate is not None:
                records = [record for record in records if predicate(record)]
            return OperationalReadPage(
                records=records[:limit],
                source=_FATHOMDB,
                degraded_reason=None,
                truncated=len(nodes) >= limit,
                limit_hit="datastore_limit" if len(nodes) >= limit else None,
            )

    page = query_logs(
        LogQuery(
            days=days,
            max_records=limit,
            predicate=predicate,
            directory=Path(directory),
        )
    )
    # JSONL is an operator-facing file boundary.  LogQuery preserves valid
    # JSON scalars for generic callers, but this operational-record contract
    # must never hand a scalar to a history consumer.
    records = [record for record in page.records if isinstance(record, dict)]
    return OperationalReadPage(
        records=records,
        source=_JSONL,
        degraded_reason=fallback_reason,
        truncated=page.truncated,
        limit_hit=page.limit_hit,
    )
