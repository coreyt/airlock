"""Target tests for Pack 0.5.4-MIGRATE-s3.

The S3 logger as a recorder sink: ``project_s3(event)`` is what
``AirlockS3Logger.record_event`` appends to the existing buffer, and the recorder
registers the s3 sink ONLY when ``AIRLOCK_ENABLE_S3_LOGGER`` is set — as a NORMAL
sink (success+failure, NOT async-only). The ``AIRLOCK_S3_BUCKET`` write-gate and the
buffering path are unchanged. No network: events are built in-process.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import airlock.callbacks.recorder as recorder_mod
from airlock.callbacks.projections import project_s3
from airlock.callbacks.request_event import build_request_event
from airlock.callbacks.s3_logger import AirlockS3Logger, proxy_s3_logger


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


def _fresh_logger(monkeypatch, *, bucket="test-bucket", batch="100"):
    monkeypatch.setenv("AIRLOCK_S3_BUCKET", bucket)
    monkeypatch.setenv("AIRLOCK_S3_BATCH", batch)
    logger = AirlockS3Logger()
    logger._client = MagicMock()
    return logger


# ---------------------------------------------------------------------------
# 1. record_event appends exactly project_s3(event)
# ---------------------------------------------------------------------------
def test_record_event_appends_project_s3(monkeypatch):
    logger = _fresh_logger(monkeypatch)
    event = _event()
    logger.record_event(event)
    assert len(logger._buffer) == 1
    assert logger._buffer[0] == project_s3(event)
    logger._client.put_object.assert_not_called()


def test_record_event_buffers_failure(monkeypatch):
    logger = _fresh_logger(monkeypatch)
    event = _event(success=False, exception=ValueError("boom"))
    logger.record_event(event)
    assert len(logger._buffer) == 1
    assert logger._buffer[0]["success"] is False
    assert logger._buffer[0]["error"] == "boom"


def test_record_event_triggers_batch_flush(monkeypatch):
    logger = _fresh_logger(monkeypatch, batch="3")
    for _ in range(3):
        logger.record_event(_event())
    logger._client.put_object.assert_called_once()
    assert len(logger._buffer) == 0


# ---------------------------------------------------------------------------
# 2. Gating — flag unset → no s3 sink
# ---------------------------------------------------------------------------
def test_s3_sink_absent_when_flag_unset(monkeypatch):
    monkeypatch.delenv("AIRLOCK_ENABLE_S3_LOGGER", raising=False)
    recorder = recorder_mod._build_recorder()
    assert "s3" not in recorder.sink_names


# ---------------------------------------------------------------------------
# 3. Gating — flag set → s3 sink present, NORMAL (not async_only)
# ---------------------------------------------------------------------------
def test_s3_sink_present_and_normal_when_flag_set(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_S3_LOGGER", "1")
    recorder = recorder_mod._build_recorder()
    assert "s3" in recorder.sink_names
    s3_reg = next(reg for reg in recorder._sinks if reg.name == "s3")
    assert s3_reg.async_only is False


def test_s3_sink_fires_on_sync_dispatch(monkeypatch):
    """A NORMAL sink fires on sync dispatch (async_only sinks would be skipped)."""
    monkeypatch.setenv("AIRLOCK_ENABLE_S3_LOGGER", "1")
    monkeypatch.setattr(proxy_s3_logger, "_bucket", "test-bucket")
    monkeypatch.setattr(proxy_s3_logger, "_batch_size", 100)
    proxy_s3_logger._client = MagicMock()
    with proxy_s3_logger._lock:
        proxy_s3_logger._buffer.clear()

    recorder = recorder_mod._build_recorder()
    event = _event()
    recorder.dispatch(event, is_async=False)
    assert proxy_s3_logger._buffer[-1] == project_s3(event)


# ---------------------------------------------------------------------------
# 4. Bucket-unset discard unchanged
# ---------------------------------------------------------------------------
def test_no_bucket_discards(monkeypatch):
    logger = _fresh_logger(monkeypatch, batch="1")
    monkeypatch.setattr(logger, "_bucket", "")
    logger.record_event(_event())
    logger._client.put_object.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Redaction — project_s3 applies _redact_record
# ---------------------------------------------------------------------------
def test_record_event_applies_redaction(monkeypatch):
    logger = _fresh_logger(monkeypatch)
    monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages,model")
    event = _event()
    logger.record_event(event)
    record = logger._buffer[0]
    assert record["messages"] == "[REDACTED]"
    assert record["model"] == "[REDACTED]"
    assert record["success"] is True
