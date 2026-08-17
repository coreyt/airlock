"""Slice 110 RED contracts for explicit, bounded operational read selection."""

from __future__ import annotations

import datetime
import json

from airlock.operational_reads import read_records


class _Node:
    def __init__(self, body: dict):
        self.body = json.dumps(body)


def _today_record(**extra) -> dict:
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": "gpt-4o-mini",
        **extra,
    }


def test_default_read_uses_jsonl_even_when_fathom_engine_exists(
    monkeypatch, log_dir
) -> None:
    import airlock.datastore

    monkeypatch.setattr(
        airlock.datastore,
        "get_engine",
        lambda: (_ for _ in ()).throw(AssertionError("default must not open FathomDB")),
    )
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    (log_dir / f"airlock-{day}.jsonl").write_text(json.dumps(_today_record()) + "\n")

    page = read_records(directory=log_dir, days=1)

    assert page.source == "jsonl"
    assert page.degraded_reason is None
    assert len(page.records) == 1


def test_jsonl_selection_excludes_non_record_json_values(monkeypatch, log_dir) -> None:
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    (log_dir / f"airlock-{day}.jsonl").write_text(
        json.dumps("not an event") + "\n" + json.dumps(_today_record()) + "\n"
    )

    page = read_records(directory=log_dir, days=1)

    assert len(page.records) == 1
    assert isinstance(page.records[0], dict)


def test_opted_in_fathom_read_is_bounded_and_source_labelled(
    monkeypatch, log_dir
) -> None:
    import airlock.datastore

    monkeypatch.setenv("AIRLOCK_OPERATIONAL_READ_BACKEND", "fathomdb")
    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: "engine")
    monkeypatch.setattr(
        "airlock.operational_reads.get_request_logs",
        lambda engine, limit: [
            _Node(_today_record(request_id=str(i))) for i in range(limit)
        ],
    )

    page = read_records(directory=log_dir, days=1, limit=2)

    assert page.source == "fathomdb"
    assert page.truncated is True
    assert page.limit_hit == "datastore_limit"
    assert len(page.records) == 2


def test_ui_fallback_never_opens_a_second_selected_engine(monkeypatch, log_dir) -> None:
    import airlock.datastore

    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    (log_dir / f"airlock-{day}.jsonl").write_text(json.dumps(_today_record()) + "\n")
    monkeypatch.setenv("AIRLOCK_OPERATIONAL_READ_BACKEND", "fathomdb")
    monkeypatch.setattr(
        airlock.datastore,
        "get_engine",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must use proxy bridge")),
    )

    page = read_records(directory=log_dir, days=1, allow_fathom=False)

    assert page.source == "jsonl"
    assert "proxy operational reads unavailable" in page.degraded_reason


def test_unavailable_or_invalid_opt_in_falls_back_to_labelled_jsonl(
    monkeypatch, log_dir
) -> None:
    import airlock.datastore

    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    (log_dir / f"airlock-{day}.jsonl").write_text(json.dumps(_today_record()) + "\n")
    monkeypatch.setenv("AIRLOCK_OPERATIONAL_READ_BACKEND", "fathomdb")
    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: None)

    page = read_records(directory=log_dir, days=1)
    assert page.source == "jsonl"
    assert (
        page.degraded_reason == "FathomDB selected but unavailable; using bounded JSONL"
    )

    monkeypatch.setenv("AIRLOCK_OPERATIONAL_READ_BACKEND", "unexpected")
    page = read_records(directory=log_dir, days=1)
    assert page.source == "jsonl"
    assert "invalid operational backend" in page.degraded_reason


def test_fathom_query_failure_falls_back_without_leaking_the_error(
    monkeypatch, log_dir
) -> None:
    import airlock.datastore

    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    (log_dir / f"airlock-{day}.jsonl").write_text(json.dumps(_today_record()) + "\n")
    monkeypatch.setenv("AIRLOCK_OPERATIONAL_READ_BACKEND", "fathomdb")
    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: "engine")
    monkeypatch.setattr(
        "airlock.operational_reads.get_request_logs",
        lambda engine, limit: (_ for _ in ()).throw(RuntimeError("provider secret")),
    )

    page = read_records(directory=log_dir, days=1)

    assert page.source == "jsonl"
    assert (
        page.degraded_reason == "FathomDB selected but unavailable; using bounded JSONL"
    )


def test_logs_source_text_labels_degradation_and_bounds() -> None:
    from airlock.tui.screens.logs import _operational_source_text

    rendered = _operational_source_text("jsonl", "FathomDB unavailable", True)

    assert "JSONL" in rendered
    assert "FathomDB unavailable" in rendered
    assert "bounded result" in rendered
