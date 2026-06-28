"""Target tests for Pack 0.5.4-MIGRATE-entfathom-wire (2b-i).

The live recorder *mechanism* — async_only sinks + ``dispatch(..., is_async=)``,
the single ``RequestRecorderCallback`` seam, the enterprise/fathom ``record_event``
sinks, and the DORMANT module wiring. No network; events are built in-process.

Behavior change must be ZERO: the recorder is constructed but never installed into
LiteLLM. Test 6 pins that dormancy.
"""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import MagicMock, patch

from airlock.callbacks.enterprise_logger import proxy_logger
from airlock.callbacks.fathom_logger import AirlockFathomLogger
from airlock.callbacks.projections import project_enterprise, project_fathom
from airlock.callbacks.request_event import (
    RequestRecorder,
    RequestRecorderCallback,
    build_request_event,
)


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
# 1. Recorder async_only — sync dispatch skips async-only sinks
# ---------------------------------------------------------------------------
def test_async_only_sink_skipped_on_sync_dispatch():
    calls: list[str] = []
    recorder = RequestRecorder()
    recorder.register(lambda e: calls.append("always"), name="always")
    recorder.register(lambda e: calls.append("async"), name="async", async_only=True)

    assert list(recorder.sink_names) == ["always", "async"]

    event = _event()
    recorder.dispatch(event, is_async=False)
    assert calls == ["always"]

    calls.clear()
    recorder.dispatch(event, is_async=True)
    assert calls == ["always", "async"]


# ---------------------------------------------------------------------------
# 2. Backward compat — default dispatch is async; plain register always runs
# ---------------------------------------------------------------------------
def test_backward_compat_default_dispatch_and_register():
    calls: list[str] = []
    recorder = RequestRecorder()
    recorder.register(lambda e: calls.append("plain"), name="plain")
    recorder.register(lambda e: calls.append("async"), name="async", async_only=True)

    recorder.dispatch(_event())  # no is_async -> behaves as is_async=True
    assert calls == ["plain", "async"]


# ---------------------------------------------------------------------------
# 3. RequestRecorderCallback — builds once + dispatches with right is_async/success
# ---------------------------------------------------------------------------
class _CapturingRecorder:
    def __init__(self) -> None:
        self.captured: list[tuple] = []

    def dispatch(self, event, *, is_async=True) -> None:
        self.captured.append((event, is_async))


def test_callback_sync_success_and_failure():
    rec = _CapturingRecorder()
    cb = RequestRecorderCallback(rec)

    cb.log_success_event(_kwargs(), _FakeResponse(), _ts(0), _ts(1))
    cb.log_failure_event(_kwargs(exception=ValueError("boom")), None, _ts(0), _ts(1))

    assert len(rec.captured) == 2
    ev0, async0 = rec.captured[0]
    ev1, async1 = rec.captured[1]
    assert async0 is False and ev0.success is True
    assert async1 is False and ev1.success is False


def test_callback_async_variants_dispatch_is_async_true():
    rec = _CapturingRecorder()
    cb = RequestRecorderCallback(rec)

    asyncio.run(cb.async_log_success_event(_kwargs(), _FakeResponse(), _ts(0), _ts(1)))
    asyncio.run(
        cb.async_log_failure_event(
            _kwargs(exception=ValueError("boom")), None, _ts(0), _ts(1)
        )
    )

    assert len(rec.captured) == 2
    ev0, async0 = rec.captured[0]
    ev1, async1 = rec.captured[1]
    assert async0 is True and ev0.success is True
    assert async1 is True and ev1.success is False


# ---------------------------------------------------------------------------
# 4. enterprise.record_event — projects exactly project_enterprise(event)
# ---------------------------------------------------------------------------
def test_enterprise_record_event_success():
    event = _event(success=True)
    with patch("airlock.callbacks.enterprise_logger._write_log") as mock_write:
        proxy_logger.record_event(event)
    mock_write.assert_called_once_with(project_enterprise(event))


def test_enterprise_record_event_failure():
    event = _event(success=False, exception=ValueError("boom"))
    with patch("airlock.callbacks.enterprise_logger._write_log") as mock_write:
        proxy_logger.record_event(event)
    mock_write.assert_called_once_with(project_enterprise(event))


# ---------------------------------------------------------------------------
# 5. fathom.record_event — skip / engine / dedup / write
# ---------------------------------------------------------------------------
def test_fathom_record_event_skip_metadata():
    engine = MagicMock()
    flogger = AirlockFathomLogger(engine=engine)
    event = _event(metadata={"airlock_skip_fathom_logger": True})
    with patch("airlock.callbacks.fathom_logger.WriteRequestBuilder"):
        flogger.record_event(event)
    engine.write.assert_not_called()


def test_fathom_record_event_no_engine():
    flogger = AirlockFathomLogger(engine=None)
    event = _event()
    with (
        patch("airlock.callbacks.fathom_logger.WriteRequestBuilder"),
        patch.object(flogger, "_get_engine", return_value=None),
    ):
        flogger.record_event(event)  # no engine -> no crash, no write


def test_fathom_record_event_dedup():
    engine = MagicMock()
    flogger = AirlockFathomLogger(engine=engine)
    event = _event()  # request_id == "call-123"
    with patch("airlock.callbacks.fathom_logger.WriteRequestBuilder") as MockBuilder:
        MockBuilder.return_value.build.return_value = "req"
        flogger.record_event(event)
        flogger.record_event(event)  # same call_id -> skipped
    engine.write.assert_called_once_with("req")


def test_fathom_record_event_normal_write():
    engine = MagicMock()
    flogger = AirlockFathomLogger(engine=engine)
    event = _event()
    with patch("airlock.callbacks.fathom_logger.WriteRequestBuilder") as MockBuilder:
        builder = MockBuilder.return_value
        builder.build.return_value = "req"
        flogger.record_event(event)
        builder.add_node.assert_called_once()
        call_kwargs = builder.add_node.call_args[1]
        assert call_kwargs["properties"] == project_fathom(event)
        assert call_kwargs["logical_id"] == "call-123"
        assert call_kwargs["source_ref"] == "airlock:fathom_logger"
    engine.write.assert_called_once_with("req")


# ---------------------------------------------------------------------------
# 6. Activation — the 2b-ii cutover installs recorder_callback into litellm
# ---------------------------------------------------------------------------
def test_recorder_module_is_live():
    """Pack 2b-ii cutover: the recorder is no longer dormant — it self-registers
    into all four litellm callback lists (the single live telemetry callback)."""
    import litellm

    from airlock.callbacks import recorder as recorder_mod

    cb = recorder_mod.recorder_callback
    assert isinstance(cb, RequestRecorderCallback)
    assert cb in litellm.logging_callback_manager._get_all_callbacks()
    # installed into both sync and async lists (config populates sync; the proxy's
    # async path needs the async lists)
    assert cb in litellm._async_success_callback
    assert cb in litellm._async_failure_callback
