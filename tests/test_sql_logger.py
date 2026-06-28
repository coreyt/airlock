"""Tests for airlock/callbacks/sql_logger.py"""

from __future__ import annotations

import json

import pytest

try:
    import sqlalchemy as sa

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False

from airlock.callbacks.request_event import (
    RequestRecorder,
    build_request_event,
)
from airlock.callbacks.sql_logger import AirlockSQLLogger


pytestmark = pytest.mark.skipif(not _SA_AVAILABLE, reason="sqlalchemy not installed")


def _event(kwargs, response_obj, start, end, *, success=True):
    """Build the canonical RequestEvent the recorder feeds to ``record_event``."""
    return build_request_event(kwargs, response_obj, start, end, success=success)


class TestSQLLogger:
    @pytest.fixture
    def sql_logger(self, monkeypatch, tmp_path):
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("AIRLOCK_SQL_URL", f"sqlite:///{db_path}")
        logger = AirlockSQLLogger()
        return logger

    def test_auto_creates_table(self, sql_logger):
        sql_logger._ensure_initialized()
        assert sql_logger._initialized is True
        assert sql_logger._table is not None

        # Verify table exists
        inspector = sa.inspect(sql_logger._engine)
        assert "airlock_logs" in inspector.get_table_names()

    def test_log_success_inserts_record(
        self, sql_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        sql_logger.record_event(
            _event(mock_logger_kwargs, mock_response_obj, start, end)
        )

        with sql_logger._engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM airlock_logs"))
            rows = result.fetchall()
        assert len(rows) == 1

    def test_success_record_fields(
        self, sql_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        sql_logger.record_event(
            _event(mock_logger_kwargs, mock_response_obj, start, end)
        )

        with sql_logger._engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM airlock_logs"))
            row = result.mappings().fetchone()

        assert row["success"] == 1  # SQLite bool
        assert row["model"] == "claude-sonnet"
        assert row["user"] == "dev-alice"
        assert row["team"] == "engineering"
        assert row["duration_ms"] == 1500
        assert row["prompt_tokens"] == 25
        assert row["total_tokens"] == 75

    def test_messages_json_encoded(
        self, sql_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        sql_logger.record_event(
            _event(mock_logger_kwargs, mock_response_obj, start, end)
        )

        with sql_logger._engine.connect() as conn:
            result = conn.execute(sa.text("SELECT messages FROM airlock_logs"))
            row = result.fetchone()

        messages = json.loads(row[0])
        assert isinstance(messages, list)
        assert messages[0]["role"] == "user"

    def test_log_failure_inserts(
        self, sql_logger, mock_failure_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        sql_logger.record_event(
            _event(mock_failure_kwargs, None, start, end, success=False)
        )

        with sql_logger._engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM airlock_logs"))
            row = result.mappings().fetchone()

        assert row["success"] == 0
        assert "timeout" in row["error"]

    def test_multiple_inserts(
        self, sql_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        for _ in range(5):
            sql_logger.record_event(
                _event(mock_logger_kwargs, mock_response_obj, start, end)
            )

        with sql_logger._engine.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM airlock_logs"))
            count = result.scalar()
        assert count == 5

    def test_async_dispatch_inserts(
        self, sql_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        # The sql logger is a NORMAL recorder sink; async dispatch reaches it the
        # same as sync (the deleted async_log_success_event delegated to _insert too).
        start, end = mock_start_end_times
        recorder = RequestRecorder()
        recorder.register(sql_logger.record_event, name="sql")
        recorder.dispatch(
            _event(mock_logger_kwargs, mock_response_obj, start, end), is_async=True
        )

        with sql_logger._engine.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM airlock_logs"))
            count = result.scalar()
        assert count == 1

    def test_no_url_skips(self, monkeypatch):
        # No AIRLOCK_SQL_URL set (clean_env autouse removed it)
        logger = AirlockSQLLogger()
        logger._ensure_initialized()
        assert logger._engine is None


class TestSQLGracefulDegradation:
    def test_module_loads_without_sqlalchemy(self):
        """Module imports fine even without sqlalchemy."""
        import airlock.callbacks.sql_logger as mod

        assert hasattr(mod, "AirlockSQLLogger")
