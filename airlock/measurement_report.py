"""Deterministic reports for Airlock's warn-only measurement windows.

The command consumes the canonical enterprise JSONL projection rather than text
logs.  It is deliberately an analysis tool: a report can prove that supplied
records are queryable and summarize them, but cannot claim that those records
represent a deployed production billing cycle.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEASUREMENT_FIELDS = {
    "reasoning-effort": "reasoning_effort_would_reject",
    "cross-tier-fuzzy": "model_alias_would_reject",
}
DISPOSITIONS = frozenset({"notify", "grace-extend", "enforce", "investigate"})


@dataclass(frozen=True)
class MeasurementReport:
    """A JSON-serializable summary of one supplied measurement population."""

    kind: str
    window_start: str | None
    window_end: str | None
    total_events: int
    affected_clients: list[str]
    unknown_client_events: int
    combinations: list[dict[str, Any]]
    dispositions: dict[str, str]
    undisposed_clients: list[str]
    source_records: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "total_events": self.total_events,
            "affected_clients": self.affected_clients,
            "unknown_client_events": self.unknown_client_events,
            "combinations": self.combinations,
            "dispositions": self.dispositions,
            "undisposed_clients": self.undisposed_clients,
            "source_records": self.source_records,
        }


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_bound(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}")
    return parsed


def iter_jsonl_records(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    """Yield valid object records; malformed lines cannot poison a report."""
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record


def _has_marker(record: dict[str, Any], field: str) -> bool:
    return any(
        isinstance(mutation, dict) and mutation.get("field") == field
        for mutation in record.get("mutations") or []
    )


def _client(record: dict[str, Any]) -> str:
    value = record.get("airlock_client")
    return value.strip() if isinstance(value, str) and value.strip() else "<unknown>"


def _dimensions(kind: str, record: dict[str, Any]) -> tuple[str, ...]:
    if kind == "reasoning-effort":
        for mutation in record.get("mutations") or []:
            if (
                isinstance(mutation, dict)
                and mutation.get("field") == MEASUREMENT_FIELDS[kind]
            ):
                return (str(mutation.get("before")), str(record.get("model")))
        raise AssertionError("marker check and mutation lookup diverged")

    detail = record.get("airlock_cross_tier_fuzzy_measurement")
    if not isinstance(detail, dict):
        # This should only occur for data emitted before the structured payload
        # was introduced.  Keep it visible rather than silently treating an
        # unclassifiable measurement as a safe zero.
        return ("<missing structured measurement>",) * 5
    return tuple(
        str(detail.get(key))
        for key in ("requested", "served", "suggested", "from_tier", "to_tier")
    )


def build_measurement_report(
    records: Iterable[dict[str, Any]],
    *,
    kind: str,
    window_start: str | None = None,
    window_end: str | None = None,
    dispositions: dict[str, str] | None = None,
) -> MeasurementReport:
    """Summarize queryable markers from the supplied JSONL population."""
    if kind not in MEASUREMENT_FIELDS:
        raise ValueError(f"unknown measurement kind: {kind!r}")
    start = _parse_bound(window_start)
    end = _parse_bound(window_end)
    if start and end and end < start:
        raise ValueError("window end must not precede window start")
    dispositions = dispositions or {}
    unknown_dispositions = set(dispositions.values()) - DISPOSITIONS
    if unknown_dispositions:
        raise ValueError(
            "invalid disposition(s): " + ", ".join(sorted(unknown_dispositions))
        )

    marker = MEASUREMENT_FIELDS[kind]
    clients: set[str] = set()
    combinations: Counter[tuple[str, ...]] = Counter()
    total = 0
    unknown = 0
    source_records = 0
    for record in records:
        timestamp = _parse_timestamp(record.get("timestamp"))
        if (
            timestamp is None
            or (start and timestamp < start)
            or (end and timestamp > end)
        ):
            continue
        source_records += 1
        if not _has_marker(record, marker):
            continue
        total += 1
        client = _client(record)
        clients.add(client)
        if client == "<unknown>":
            unknown += 1
        combinations[_dimensions(kind, record)] += 1

    labels = (
        ("requested", "model")
        if kind == "reasoning-effort"
        else ("requested", "served", "suggested", "from_tier", "to_tier")
    )
    rendered_combinations = [
        {"count": count, **dict(zip(labels, dimensions, strict=True))}
        for dimensions, count in sorted(
            combinations.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    reported_dispositions = {
        client: dispositions[client]
        for client in sorted(clients)
        if client in dispositions
    }
    return MeasurementReport(
        kind=kind,
        window_start=window_start,
        window_end=window_end,
        total_events=total,
        affected_clients=sorted(clients),
        unknown_client_events=unknown,
        combinations=rendered_combinations,
        dispositions=reported_dispositions,
        undisposed_clients=sorted(clients - set(reported_dispositions)),
        source_records=source_records,
    )


def _paths_from_args(values: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("airlock-*.jsonl")))
        else:
            paths.append(path)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"JSONL input not found: {', '.join(missing)}")
    return paths


def _load_dispositions(path: str | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        with Path(path).open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read dispositions: {exc}") from exc
    if not isinstance(loaded, dict) or not all(
        isinstance(client, str) and isinstance(disposition, str)
        for client, disposition in loaded.items()
    ):
        raise ValueError("dispositions must be a JSON object of client to disposition")
    return loaded


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(MEASUREMENT_FIELDS))
    parser.add_argument("inputs", nargs="+", help="JSONL files or directories")
    parser.add_argument("--start", help="inclusive ISO-8601 window start")
    parser.add_argument("--end", help="inclusive ISO-8601 window end")
    parser.add_argument(
        "--dispositions",
        help="JSON object mapping each affected client to notify, grace-extend, enforce, or investigate",
    )
    parser.add_argument(
        "--require-dispositions",
        action="store_true",
        help="fail unless every affected client, including <unknown>, has a disposition",
    )
    args = parser.parse_args(argv)
    try:
        report = build_measurement_report(
            iter_jsonl_records(_paths_from_args(args.inputs)),
            kind=args.kind,
            window_start=args.start,
            window_end=args.end,
            dispositions=_load_dispositions(args.dispositions),
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if args.require_dispositions and report.undisposed_clients:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
