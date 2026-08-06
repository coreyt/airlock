import inspect
import json
from datetime import datetime, timezone

import pytest

from airlock.api.queries import (
    DATASTORE_QUERY_LIMIT,
    get_billing_metrics,
    get_request_logs,
)
from airlock.datastore import init_engine


@pytest.fixture
def engine(tmp_path):
    engine = init_engine(str(tmp_path / "airlock-fathom.db"))
    assert engine is not None
    yield engine
    engine.close()


def _write_log(engine, logical_id, properties):
    engine.write(
        [
            {
                "kind": "RequestLog",
                "logical_id": logical_id,
                "source_id": "airlock:test",
                "body": json.dumps(properties),
            }
        ]
    )


def test_get_request_logs_returns_active_rows(engine):
    _write_log(engine, "1", {"model": "gpt-4"})
    _write_log(engine, "2", {"model": "gpt-3.5"})

    rows = get_request_logs(engine, limit=5)

    assert {row.logical_id for row in rows} == {"1", "2"}
    assert json.loads(next(r.body for r in rows if r.logical_id == "1")) == {
        "model": "gpt-4"
    }


def test_get_request_logs_respects_limit(engine):
    for i in range(5):
        _write_log(engine, str(i), {"model": "gpt-4"})

    assert len(get_request_logs(engine, limit=3)) == 3


def test_get_request_logs_no_capability_sniffing():
    """A non-engine raises a real error instead of silently returning []."""
    with pytest.raises(Exception):
        get_request_logs(object(), limit=5)


def test_no_read_defaults_unbounded():
    """The unbounded shape must not be expressible by accident (F-4).

    Both readers default to the shared datastore bound — the old
    limit=1000000 default was a limit in name only.
    """
    for fn in (get_request_logs, get_billing_metrics):
        default = inspect.signature(fn).parameters["limit"].default
        assert default == DATASTORE_QUERY_LIMIT


def test_get_billing_metrics(engine, monkeypatch):
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("airlock.api.queries.datetime", MockDatetime)

    # YTD only
    _write_log(engine, "1", {"cost": 10.0, "timestamp": "2023-02-01T00:00:00+00:00"})
    # MTD
    _write_log(engine, "2", {"cost": 5.0, "timestamp": "2023-06-05T00:00:00+00:00"})
    # Last year
    _write_log(engine, "3", {"cost": 2.0, "timestamp": "2022-12-31T00:00:00+00:00"})
    # Another MTD
    _write_log(engine, "4", {"cost": 1.5, "timestamp": "2023-06-10T00:00:00+00:00"})
    # No timestamp — skipped
    _write_log(engine, "5", {"cost": 99.0})

    metrics = get_billing_metrics(engine)

    assert metrics["MTD_cost"] == 6.5
    assert metrics["YTD_cost"] == 16.5
    assert metrics["truncated"] is False
    assert metrics["limit_hit"] is None


def test_get_billing_metrics_reports_truncation(engine, monkeypatch):
    """At the bound the scan is partial; the metrics must say so."""

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("airlock.api.queries.datetime", MockDatetime)

    for i in range(3):
        _write_log(
            engine,
            str(i),
            {"cost": 1.0, "timestamp": "2023-06-05T00:00:00+00:00"},
        )

    metrics = get_billing_metrics(engine, limit=2)

    assert metrics["truncated"] is True
    assert metrics["limit_hit"] == "datastore_limit"
    # The partial sum is a lower bound over the rows actually read.
    assert metrics["MTD_cost"] == 2.0


def test_get_billing_metrics_counts_superseded_rows_once(engine, monkeypatch):
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("airlock.api.queries.datetime", MockDatetime)

    _write_log(engine, "1", {"cost": 5.0, "timestamp": "2023-06-05T00:00:00+00:00"})
    # Same logical_id written again supersedes; only the active row counts.
    _write_log(engine, "1", {"cost": 7.0, "timestamp": "2023-06-05T00:00:00+00:00"})

    metrics = get_billing_metrics(engine)

    assert metrics["MTD_cost"] == 7.0
