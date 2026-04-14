from datetime import datetime, timezone
from unittest.mock import MagicMock

from airlock.api.queries import get_billing_metrics, search_logs


class MockNodeRow:
    def __init__(self, logical_id, properties):
        self.logical_id = logical_id
        self.properties = properties


def test_get_billing_metrics(monkeypatch):
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("airlock.api.queries.datetime", MockDatetime)

    engine = MagicMock()

    engine._query_nodes.return_value = [
        # YTD only
        MockNodeRow("1", {"cost": 10.0, "timestamp": "2023-02-01T00:00:00+00:00"}),
        # MTD
        MockNodeRow("2", {"cost": 5.0, "timestamp": "2023-06-05T00:00:00+00:00"}),
        # Last year
        MockNodeRow("3", {"cost": 2.0, "timestamp": "2022-12-31T00:00:00+00:00"}),
        # Another MTD
        MockNodeRow("4", {"cost": 1.5, "timestamp": "2023-06-10T00:00:00+00:00"}),
    ]

    metrics = get_billing_metrics(engine)

    assert metrics["MTD_cost"] == 6.5
    assert metrics["YTD_cost"] == 16.5

    engine._query_nodes.assert_called_with("RequestLog", limit=1000)


def test_search_logs():
    engine = MagicMock()
    engine._query_nodes.return_value = [
        MockNodeRow("1", {"message": "User login failed", "level": "ERROR"}),
        MockNodeRow("2", {"message": "Data saved successfully", "level": "INFO"}),
        MockNodeRow("3", {"message": "Connection timeout error", "level": "ERROR"}),
    ]

    results = search_logs(engine, "error")
    assert len(results) == 2
    assert results[0].logical_id == "1"
    assert results[1].logical_id == "3"

    engine._query_nodes.assert_called_with("RequestLog", limit=1000)
