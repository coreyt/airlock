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
    # metrics is an always-on sink registered after enterprise (sidechannels pack).
    assert recorder.sink_names == ["enterprise", "metrics", "fathom"]
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


def test_no_double_emit_live_callback_list_cardinality():
    """No-double-emit at the LIVE registration level (review med finding).

    recorder_callback is registered from TWO sources — config.yaml (sync lists) AND
    recorder._self_register() (all four lists). No-double-emit therefore depends on
    litellm.logging_callback_manager DEDUPING recorder_callback to exactly one entry
    per list. This is the same dedup the old enterprise proxy_logger relied on (it too
    sat in config.yaml + a module self_register). Prove it directly: repeat
    registration is idempotent, each list holds recorder_callback exactly once, and the
    deleted enterprise/fathom instances are in none of them.
    """
    import litellm
    from airlock.callbacks.enterprise_logger import proxy_logger

    cb = recorder_mod.recorder_callback
    mgr = litellm.logging_callback_manager

    # (1) idempotency: re-run self_register (import already ran it once).
    recorder_mod._self_register()
    recorder_mod._self_register()
    # (4) also simulate litellm resolving config.yaml's recorder_callback string to the
    # SAME instance and adding it to the sync lists — must still dedupe to one.
    mgr.add_litellm_success_callback(cb)
    mgr.add_litellm_failure_callback(cb)

    lists = {
        "success_callback (sync)": litellm.success_callback,
        "failure_callback (sync)": litellm.failure_callback,
        "_async_success_callback": litellm._async_success_callback,
        "_async_failure_callback": litellm._async_failure_callback,
    }
    for name, lst in lists.items():
        # (2) recorder_callback present exactly once (dedup holds → no double-emit)
        assert lst.count(cb) == 1, (
            f"{name}: recorder_callback registered {lst.count(cb)}x"
        )
        # (3) no legacy double-path: deleted sinks are not separately registered
        assert proxy_logger not in lst, f"{name}: stale enterprise proxy_logger present"
        assert proxy_fathom_logger not in lst, (
            f"{name}: stale proxy_fathom_logger present"
        )


# ---------------------------------------------------------------------------
# 3. Fathom gating (flag + async-only + skip)
# ---------------------------------------------------------------------------
def test_fathom_sink_absent_when_flag_unset(monkeypatch):
    monkeypatch.delenv("AIRLOCK_ENABLE_FATHOM_LOGGER", raising=False)
    recorder = recorder_mod._build_recorder()
    # metrics is always-on; fathom stays absent while its flag is unset.
    assert recorder.sink_names == ["enterprise", "metrics"]
    assert "fathom" not in recorder.sink_names

    engine = MagicMock()
    with (
        patch("airlock.callbacks.enterprise_logger._write_log"),
        patch.object(proxy_fathom_logger, "_get_engine", return_value=engine),
        patch("airlock.callbacks.fathom_logger.WriteRequestBuilder"),
    ):
        recorder.dispatch(
            build_request_event(
                _kwargs(call_id="gate-off"),
                _FakeResponse(),
                _ts(0),
                _ts(1),
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
                _kwargs(call_id="gate-sync"),
                _FakeResponse(),
                _ts(0),
                _ts(1),
                success=True,
            ),
            is_async=False,
        )
        engine.write.assert_not_called()

        # async dispatch -> fathom fires
        recorder.dispatch(
            build_request_event(
                _kwargs(call_id="gate-async"),
                _FakeResponse(),
                _ts(0),
                _ts(1),
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
