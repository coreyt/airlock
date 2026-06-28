"""Target tests for Pack 0.5.4-MIGRATE-sql.

The SQL logger as a recorder sink: ``project_sql(event)`` is what
``AirlockSQLLogger.record_event`` passes to ``_insert``, and the recorder registers
the sql sink ONLY when ``AIRLOCK_ENABLE_SQL_LOGGER`` is set — as a NORMAL sink
(success+failure, NOT async-only). The ``AIRLOCK_SQL_URL`` disabled-path and the
``_insert`` write path are unchanged. No network/db: events are built in-process and
``_insert`` is monkeypatched.
"""

from __future__ import annotations

import datetime

import pytest

import airlock.callbacks.recorder as recorder_mod
from airlock.callbacks.projections import project_sql
from airlock.callbacks.request_event import build_request_event
from airlock.callbacks.sql_logger import AirlockSQLLogger, proxy_sql_logger

try:
    import sqlalchemy as _sa  # noqa: F401

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False


class _FakeUsage:
    def __init__(self, prompt=3, completion=5, total=8) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _FakeResponse:
    def __init__(self) -> None:
        self.usage = _FakeUsage()


def _ts(secs: float) -> datetime.datetime:
    return datetime.datetime(
        2026, 6, 28, 12, 0, 0, tzinfo=datetime.timezone.utc
    ) + datetime.timedelta(seconds=secs)


def _kwargs(**over):
    metadata = {
        "user_api_key_alias": "alice",
        "user_api_key_team_alias": "team-a",
        "airlock_provider": "openai",
    }
    metadata.update(over.pop("metadata", {}))
    litellm_params = {"metadata": metadata}
    base = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "litellm_call_id": "call-123",
        "litellm_params": litellm_params,
        "response_cost": 0.0021,
        "headers": {"x-trace": "abc"},
    }
    base.update(over)
    return base


def _event(success=True, **over):
    resp = None if not success else _FakeResponse()
    return build_request_event(_kwargs(**over), resp, _ts(0), _ts(1), success=success)


# ---------------------------------------------------------------------------
# 1. record_event inserts exactly project_sql(event)
# ---------------------------------------------------------------------------
def test_record_event_inserts_project_sql(monkeypatch):
    logger = AirlockSQLLogger()
    captured = []
    monkeypatch.setattr(logger, "_insert", captured.append)
    event = _event()
    logger.record_event(event)
    assert len(captured) == 1
    assert captured[0] == project_sql(event)


def test_record_event_inserts_failure(monkeypatch):
    logger = AirlockSQLLogger()
    captured = []
    monkeypatch.setattr(logger, "_insert", captured.append)
    event = _event(success=False, exception=ValueError("boom"))
    logger.record_event(event)
    assert len(captured) == 1
    assert captured[0]["success"] is False
    assert captured[0]["error"] == "boom"
    assert captured[0]["response"] is None


def test_record_event_messages_response_are_json_strings(monkeypatch):
    logger = AirlockSQLLogger()
    captured = []
    monkeypatch.setattr(logger, "_insert", captured.append)
    logger.record_event(_event())
    record = captured[0]
    assert isinstance(record["messages"], str)
    assert isinstance(record["response"], str)


# ---------------------------------------------------------------------------
# 2. AIRLOCK_SQL_URL-unset disabled path is unchanged (no insert, no raise)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _SA_AVAILABLE, reason="sqlalchemy not installed")
def test_disabled_path_no_engine_no_raise(monkeypatch):
    # No AIRLOCK_SQL_URL set (clean_env autouse removed it)
    logger = AirlockSQLLogger()
    logger.record_event(_event())  # must not raise
    assert logger._engine is None


# ---------------------------------------------------------------------------
# 3. Gating — flag unset → no sql sink
# ---------------------------------------------------------------------------
def test_sql_sink_absent_when_flag_unset(monkeypatch):
    monkeypatch.delenv("AIRLOCK_ENABLE_SQL_LOGGER", raising=False)
    recorder = recorder_mod._build_recorder()
    assert "sql" not in recorder.sink_names


# ---------------------------------------------------------------------------
# 4. Gating — flag set → sql sink present, NORMAL (not async_only)
# ---------------------------------------------------------------------------
def test_sql_sink_present_and_normal_when_flag_set(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_SQL_LOGGER", "1")
    recorder = recorder_mod._build_recorder()
    assert "sql" in recorder.sink_names
    sql_reg = next(reg for reg in recorder._sinks if reg.name == "sql")
    assert sql_reg.async_only is False


def test_sql_sink_fires_on_sync_dispatch(monkeypatch):
    """A NORMAL sink fires on sync dispatch (async_only sinks would be skipped)."""
    monkeypatch.setenv("AIRLOCK_ENABLE_SQL_LOGGER", "1")
    captured = []
    monkeypatch.setattr(proxy_sql_logger, "_insert", captured.append)

    recorder = recorder_mod._build_recorder()
    event = _event()
    recorder.dispatch(event, is_async=False)
    assert captured[-1] == project_sql(event)
