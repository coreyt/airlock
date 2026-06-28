"""Seam tests for Pack 0.5.4-MIGRATE-entfathom-cutover (2b-ii).

Pin the CUTOVER contract: the recorder is the single live telemetry callback in
enterprise's slot (before monitor), enterprise is always-on, fathom is gated on
``AIRLOCK_ENABLE_FATHOM_LOGGER`` + async-only, no sink double-emits, a built event
is an immutable snapshot (monitor's post-build ``airlock_provider_protection`` never
leaks into enterprise's record), and a raising sink never breaks the request. No
network: events are built in-process.
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from airlock.callbacks import recorder as recorder_mod
from airlock.callbacks.fathom_logger import proxy_fathom_logger
from airlock.callbacks.projections import project_enterprise
from airlock.callbacks.request_event import (
    RequestRecorder,
    RequestRecorderCallback,
    build_request_event,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECORDER_CB = "airlock.callbacks.recorder.recorder_callback"
_MONITOR_CB = "airlock.fast.monitor.proxy_monitor"
_ENTERPRISE_CB = "airlock.callbacks.enterprise_logger.proxy_logger"
_FATHOM_CB = "airlock.callbacks.fathom_logger.proxy_fathom_logger"


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


def _kwargs(call_id="call-123", **over):
    metadata = {
        "user_api_key_alias": "alice",
        "user_api_key_team_alias": "team-a",
        "airlock_provider": "openai",
    }
    metadata.update(over.pop("metadata", {}))
    base = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "litellm_call_id": call_id,
        "litellm_params": {"metadata": metadata},
        "response_cost": 0.0021,
        "headers": {"x-trace": "abc"},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. Snapshot-immutability / ordering
# ---------------------------------------------------------------------------
def test_event_snapshot_excludes_post_build_provider_protection():
    """monitor.log_failure_event sets metadata['airlock_provider_protection'] AFTER
    a callback runs; because the recorder runs first and snapshots guardrail_meta at
    build time, enterprise's record must never gain that key (a regression guard)."""
    metadata = {"user_api_key_alias": "alice", "airlock_provider": "openai"}
    kwargs = _kwargs(metadata={}, litellm_params={"metadata": metadata})
    kwargs["litellm_params"] = {"metadata": metadata}

    event = build_request_event(kwargs, None, _ts(0), _ts(1), success=False)

    # Simulate monitor mutating the SAME metadata dict after the event was built.
    metadata["airlock_provider_protection"] = {"action": "provider_quarantine"}

    assert "airlock_provider_protection" not in event.guardrail_meta
    assert "airlock_provider_protection" not in project_enterprise(event)


def test_config_lists_recorder_before_monitor():
    cfg = yaml.safe_load((_REPO_ROOT / "config.yaml").read_text())
    settings = cfg["litellm_settings"]
    for key in ("success_callback", "failure_callback"):
        cbs = settings[key]
        assert _RECORDER_CB in cbs, f"{key} must register the recorder"
        assert _MONITOR_CB in cbs, f"{key} must register the monitor"
        assert cbs.index(_RECORDER_CB) < cbs.index(_MONITOR_CB), (
            f"{key}: recorder must come BEFORE monitor (snapshot ordering invariant)"
        )
        # the old enterprise proxy_logger slot is now owned by the recorder
        assert _ENTERPRISE_CB not in cbs
        # fathom is owned by the recorder (gated), never wired in config directly
        assert _FATHOM_CB not in cbs


# ---------------------------------------------------------------------------
# 2. No double-emit
# ---------------------------------------------------------------------------
def test_no_double_emit_enterprise_once_fathom_once(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOM_LOGGER", "1")
    recorder = recorder_mod._build_recorder()
    assert recorder.sink_names == ["enterprise", "fathom"]
    cb = RequestRecorderCallback(recorder)

    engine = MagicMock()
    with (
        patch("airlock.callbacks.enterprise_logger._write_log") as mock_write,
        patch.object(proxy_fathom_logger, "_get_engine", return_value=engine),
        patch("airlock.callbacks.fathom_logger.WriteRequestBuilder") as MockBuilder,
    ):
        MockBuilder.return_value.build.return_value = "req"
        asyncio.run(
            cb.async_log_success_event(
                _kwargs(call_id="dbl-emit-1"), _FakeResponse(), _ts(0), _ts(1)
            )
        )

    assert mock_write.call_count == 1
    engine.write.assert_called_once_with("req")


def test_old_loggers_not_separately_registered_in_litellm():
    """The recorder owns dispatch; the old proxy_logger / proxy_fathom_logger
    instances must NOT be self-registered into litellm's async lists anymore."""
    import litellm
    from airlock.callbacks.enterprise_logger import proxy_logger

    for lst in (litellm._async_success_callback, litellm._async_failure_callback):
        assert proxy_logger not in lst
        assert proxy_fathom_logger not in lst


# ---------------------------------------------------------------------------
# 3. Fathom gating (flag + async-only + skip)
# ---------------------------------------------------------------------------
def test_fathom_sink_absent_when_flag_unset(monkeypatch):
    monkeypatch.delenv("AIRLOCK_ENABLE_FATHOM_LOGGER", raising=False)
    recorder = recorder_mod._build_recorder()
    assert recorder.sink_names == ["enterprise"]

    engine = MagicMock()
    with (
        patch("airlock.callbacks.enterprise_logger._write_log"),
        patch.object(proxy_fathom_logger, "_get_engine", return_value=engine),
        patch("airlock.callbacks.fathom_logger.WriteRequestBuilder"),
    ):
        recorder.dispatch(
            build_request_event(
                _kwargs(call_id="gate-off"), _FakeResponse(), _ts(0), _ts(1),
                success=True,
            ),
            is_async=True,
        )
    engine.write.assert_not_called()


def test_fathom_sink_present_and_async_only_when_flag_set(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOM_LOGGER", "1")
    recorder = recorder_mod._build_recorder()
    assert "fathom" in recorder.sink_names

    engine = MagicMock()
    with (
        patch("airlock.callbacks.enterprise_logger._write_log"),
        patch.object(proxy_fathom_logger, "_get_engine", return_value=engine),
        patch("airlock.callbacks.fathom_logger.WriteRequestBuilder") as MockBuilder,
    ):
        MockBuilder.return_value.build.return_value = "req"
        # sync dispatch -> async_only fathom sink skipped, no write
        recorder.dispatch(
            build_request_event(
                _kwargs(call_id="gate-sync"), _FakeResponse(), _ts(0), _ts(1),
                success=True,
            ),
            is_async=False,
        )
        engine.write.assert_not_called()

        # async dispatch -> fathom fires
        recorder.dispatch(
            build_request_event(
                _kwargs(call_id="gate-async"), _FakeResponse(), _ts(0), _ts(1),
                success=True,
            ),
            is_async=True,
        )
        engine.write.assert_called_once_with("req")


def test_fathom_skip_flag_honored(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOM_LOGGER", "1")
    recorder = recorder_mod._build_recorder()
    engine = MagicMock()
    with (
        patch("airlock.callbacks.enterprise_logger._write_log"),
        patch.object(proxy_fathom_logger, "_get_engine", return_value=engine),
        patch("airlock.callbacks.fathom_logger.WriteRequestBuilder"),
    ):
        recorder.dispatch(
            build_request_event(
                _kwargs(
                    call_id="gate-skip",
                    metadata={"airlock_skip_fathom_logger": True},
                ),
                _FakeResponse(),
                _ts(0),
                _ts(1),
                success=True,
            ),
            is_async=True,
        )
    engine.write.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Failure isolation
# ---------------------------------------------------------------------------
def test_raising_sink_does_not_break_request_or_other_sinks():
    recorder = RequestRecorder()
    calls: list[str] = []

    def _boom(_event):
        raise RuntimeError("sink exploded")

    recorder.register(_boom, name="raiser")
    recorder.register(lambda e: calls.append("ok"), name="ok")

    event = build_request_event(
        _kwargs(call_id="iso"), _FakeResponse(), _ts(0), _ts(1), success=True
    )
    # must NOT raise, and the healthy sink still runs
    recorder.dispatch(event, is_async=True)
    assert calls == ["ok"]


def test_recorder_module_installs_callback_live():
    """The cutover ACTIVATES the recorder: recorder_callback is now installed into
    litellm's callback lists (the old dormancy is gone)."""
    import litellm

    cb = recorder_mod.recorder_callback
    assert isinstance(cb, RequestRecorderCallback)
    all_cbs = litellm.logging_callback_manager._get_all_callbacks()
    assert cb in all_cbs
