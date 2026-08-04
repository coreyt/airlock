"""Tests for the bounded log reader (closeout finding F-4).

The property that matters most is not the cap — it is that truncation is
*reported*. An analysis that scanned half the window and presents itself as
complete produces confident, wrong conclusions about traffic it never saw.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from airlock.log_query import (
    LIMIT_BYTES,
    LIMIT_RECORDS,
    LogQuery,
    load_records,
    query_logs,
)


def _write_day(directory, day_offset: int, records: list[dict]) -> None:
    day = datetime.now(timezone.utc).date() - timedelta(days=day_offset)
    path = directory / f"airlock-{day.isoformat()}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path


class TestBounds:
    def test_record_ceiling_stops_the_scan_and_reports_it(self, log_dir):
        _write_day(log_dir, 0, [{"i": i, "success": True} for i in range(500)])
        page = query_logs(LogQuery(days=1, max_records=100, directory=log_dir))
        assert len(page.records) == 100
        assert page.truncated is True
        assert page.limit_hit == LIMIT_RECORDS

    def test_byte_ceiling_stops_the_scan_and_reports_it(self, log_dir):
        _write_day(log_dir, 0, [{"blob": "x" * 1000} for _ in range(200)])
        page = query_logs(LogQuery(days=1, max_bytes=5_000, directory=log_dir))
        assert page.truncated is True
        assert page.limit_hit == LIMIT_BYTES

    def test_untruncated_scan_reports_no_truncation(self, log_dir):
        _write_day(log_dir, 0, [{"i": i} for i in range(10)])
        page = query_logs(LogQuery(days=1, directory=log_dir))
        assert len(page.records) == 10
        assert page.truncated is False
        assert page.limit_hit is None
        assert page.note() is None

    def test_truncation_note_is_human_readable(self, log_dir):
        _write_day(log_dir, 0, [{"i": i} for i in range(50)])
        page = query_logs(LogQuery(days=1, max_records=10, directory=log_dir))
        assert "truncated" in (page.note() or "").lower()

    def test_env_overrides_the_default_ceiling(self, log_dir, monkeypatch):
        monkeypatch.setenv("AIRLOCK_LOG_QUERY_MAX_RECORDS", "7")
        _write_day(log_dir, 0, [{"i": i} for i in range(50)])
        page = query_logs(LogQuery(days=1, directory=log_dir))
        assert len(page.records) == 7

    def test_invalid_env_value_falls_back_to_default(self, log_dir, monkeypatch):
        monkeypatch.setenv("AIRLOCK_LOG_QUERY_MAX_RECORDS", "not-a-number")
        _write_day(log_dir, 0, [{"i": i} for i in range(5)])
        assert len(query_logs(LogQuery(days=1, directory=log_dir)).records) == 5


class TestFilteringWhileScanning:
    def test_predicate_keeps_only_matches(self, log_dir):
        records = [{"i": i, "success": i % 100 != 0} for i in range(1000)]
        _write_day(log_dir, 0, records)
        page = query_logs(
            LogQuery(
                days=1,
                predicate=lambda r: not r.get("success"),
                directory=log_dir,
            )
        )
        assert len(page.records) == 10
        assert page.scanned == 1000, "predicate must filter during the scan"
        assert page.truncated is False

    def test_narrow_query_does_not_approach_the_ceiling(self, log_dir):
        """The point of filtering while scanning: a rare match stays cheap."""
        _write_day(log_dir, 0, [{"i": i, "hit": i == 42} for i in range(5000)])
        page = query_logs(
            LogQuery(
                days=1,
                max_records=10,
                predicate=lambda r: bool(r.get("hit")),
                directory=log_dir,
            )
        )
        assert len(page.records) == 1
        assert page.truncated is False


class TestOrdering:
    def test_newest_day_is_read_first(self, log_dir):
        _write_day(log_dir, 0, [{"day": "today"}])
        _write_day(log_dir, 1, [{"day": "yesterday"}])
        page = query_logs(LogQuery(days=2, directory=log_dir))
        assert page.records[0]["day"] == "today"

    def test_truncation_retains_the_most_recent_records(self, log_dir):
        _write_day(log_dir, 0, [{"day": "today", "i": i} for i in range(5)])
        _write_day(log_dir, 1, [{"day": "yesterday", "i": i} for i in range(5)])
        page = query_logs(LogQuery(days=2, max_records=5, directory=log_dir))
        assert {r["day"] for r in page.records} == {"today"}


class TestResilience:
    def test_malformed_lines_are_skipped(self, log_dir):
        day = datetime.now(timezone.utc).date()
        path = log_dir / f"airlock-{day.isoformat()}.jsonl"
        path.write_text('{"ok": 1}\nnot json at all\n{"ok": 2}\n', encoding="utf-8")
        page = query_logs(LogQuery(days=1, directory=log_dir))
        assert len(page.records) == 2

    def test_missing_directory_returns_empty_page(self, tmp_path):
        page = query_logs(LogQuery(days=3, directory=tmp_path / "nope"))
        assert page.records == []
        assert page.truncated is False

    def test_blank_lines_are_ignored(self, log_dir):
        day = datetime.now(timezone.utc).date()
        path = log_dir / f"airlock-{day.isoformat()}.jsonl"
        path.write_text('{"ok": 1}\n\n\n{"ok": 2}\n', encoding="utf-8")
        assert len(query_logs(LogQuery(days=1, directory=log_dir)).records) == 2


class TestConsumerIntegration:
    def test_load_records_wrapper(self, log_dir):
        _write_day(log_dir, 0, [{"i": i} for i in range(3)])
        page = load_records(days=1, directory=log_dir)
        assert len(page.records) == 3

    def test_metadata_shape_for_embedding_in_reports(self, log_dir):
        _write_day(log_dir, 0, [{"i": i} for i in range(20)])
        meta = query_logs(
            LogQuery(days=1, max_records=5, directory=log_dir)
        ).as_metadata()
        assert meta["truncated"] is True
        assert meta["records"] == 5
        assert meta["limit_hit"] == LIMIT_RECORDS

    def test_advisor_errors_tool_reports_window_state(self, log_dir):
        from airlock.advisor.tools import get_recent_errors

        _write_day(
            log_dir,
            0,
            [{"success": False, "model": "m", "error_type": "Boom"} for _ in range(3)]
            + [{"success": True, "model": "m"} for _ in range(3)],
        )
        result = get_recent_errors(str(log_dir), days=1)
        assert result["total_errors"] == 3
        assert result["window"]["truncated"] is False

    def test_analyzer_exposes_window_truncation(self, log_dir, monkeypatch):
        from airlock.slow import analyzer

        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_dir))
        monkeypatch.setenv("AIRLOCK_LOG_QUERY_MAX_RECORDS", "5")
        _write_day(log_dir, 0, [{"i": i, "success": True} for i in range(50)])
        analyzer._load_logs(days=1)
        window = analyzer.last_window()
        assert window is not None and window.truncated is True
