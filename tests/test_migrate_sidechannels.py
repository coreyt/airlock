"""Pack 0.5.4-MIGRATE-sidechannels — per-request metrics driven from the
``RequestEvent``/recorder seam (behavior-preserving; metrics is an always-on sink).

The Prometheus counters emitted per request are identical to the old success/failure
LiteLLM callbacks; they now flow through ``metrics_callback.record_event`` and the
mutation counter reads the **asdict'd** ``event.mutations`` (dicts), not raw objects.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from airlock.callbacks import metrics as metrics_module
from airlock.callbacks.metrics import (
    AirlockMetricsCallback,
    _record_mutations_from_event,
    metrics_callback,
)


@pytest.fixture()
def fresh_metrics(monkeypatch):
    """Build metrics on a fresh registry to avoid duplicate registration."""
    registry = CollectorRegistry()
    fresh = {
        "requests_total": Counter(
            "test_airlock_requests_total",
            "Total LLM requests",
            ["model", "user", "success"],
            registry=registry,
        ),
        "request_duration": Histogram(
            "test_airlock_request_duration_seconds",
            "LLM request duration",
            ["model"],
            registry=registry,
        ),
        "mutations_total": Counter(
            "test_airlock_mutations_total",
            "Total ledger mutations",
            ["field", "op"],
            registry=registry,
        ),
        "circuit_breaker_state": Gauge(
            "test_airlock_circuit_breaker_state",
            "Circuit breaker state",
            ["model"],
            registry=registry,
        ),
    }
    monkeypatch.setattr(metrics_module, "_metrics", fresh)
    return fresh


def _event(
    *,
    success=True,
    model="gpt-4",
    user="alice",
    start=None,
    end=None,
    mutations=None,
):
    return SimpleNamespace(
        success=success,
        model=model,
        user=user,
        start_time=start,
        end_time=end,
        mutations=mutations or [],
    )


# ---------------------------------------------------------------------------
# 1. requests_total — both paths + "unknown" user default
# ---------------------------------------------------------------------------
def test_requests_total_success_path(fresh_metrics):
    AirlockMetricsCallback().record_event(_event(success=True, user="alice"))
    assert (
        fresh_metrics["requests_total"]
        .labels(model="gpt-4", user="alice", success="true")
        ._value.get()
        == 1
    )


def test_requests_total_failure_path(fresh_metrics):
    AirlockMetricsCallback().record_event(_event(success=False, user="bob"))
    assert (
        fresh_metrics["requests_total"]
        .labels(model="gpt-4", user="bob", success="false")
        ._value.get()
        == 1
    )


def test_requests_total_unknown_user_default(fresh_metrics):
    AirlockMetricsCallback().record_event(_event(success=True, user=None))
    assert (
        fresh_metrics["requests_total"]
        .labels(model="gpt-4", user="unknown", success="true")
        ._value.get()
        == 1
    )


# ---------------------------------------------------------------------------
# 2. request_duration — success only, when start/end present
# ---------------------------------------------------------------------------
def test_request_duration_observed_on_success(fresh_metrics):
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(seconds=2.5)
    AirlockMetricsCallback().record_event(_event(success=True, start=start, end=end))

    hist = fresh_metrics["request_duration"].labels(model="gpt-4")
    assert hist._sum.get() == pytest.approx(2.5)


def test_request_duration_not_observed_on_failure(fresh_metrics):
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(seconds=2.5)
    AirlockMetricsCallback().record_event(_event(success=False, start=start, end=end))

    hist = fresh_metrics["request_duration"].labels(model="gpt-4")
    assert hist._sum.get() == 0.0


def test_request_duration_not_observed_without_times(fresh_metrics):
    AirlockMetricsCallback().record_event(_event(success=True, start=None, end=None))

    hist = fresh_metrics["request_duration"].labels(model="gpt-4")
    assert hist._sum.get() == 0.0


# ---------------------------------------------------------------------------
# 3. mutations_total — from the asdict'd event.mutations (dict access)
# ---------------------------------------------------------------------------
def test_mutations_counted_from_event_dicts(fresh_metrics):
    mutations = [
        {"field": "model", "op": "rewrite", "before": "a", "after": "b"},
        {"field": "messages", "op": "redact", "before": None, "after": None},
    ]
    AirlockMetricsCallback().record_event(_event(success=True, mutations=mutations))

    assert (
        fresh_metrics["mutations_total"]
        .labels(field="model", op="rewrite")
        ._value.get()
        == 1
    )
    assert (
        fresh_metrics["mutations_total"]
        .labels(field="messages", op="redact")
        ._value.get()
        == 1
    )


def test_mutations_skip_missing_field_or_op(fresh_metrics):
    mutations = [
        {"op": "rewrite"},  # missing field
        {"field": "messages"},  # missing op
        {"field": "model", "op": "rewrite"},  # counted
    ]
    AirlockMetricsCallback().record_event(_event(success=True, mutations=mutations))

    assert (
        fresh_metrics["mutations_total"]
        .labels(field="model", op="rewrite")
        ._value.get()
        == 1
    )
    # Total samples collected == 1 (the two malformed entries are skipped).
    samples = list(fresh_metrics["mutations_total"].collect())[0].samples
    total_samples = [s for s in samples if s.name.endswith("_total")]
    assert len(total_samples) == 1


def test_mutations_not_counted_on_failure(fresh_metrics):
    mutations = [{"field": "model", "op": "rewrite"}]
    AirlockMetricsCallback().record_event(_event(success=False, mutations=mutations))

    samples = list(fresh_metrics["mutations_total"].collect())[0].samples
    total_samples = [s for s in samples if s.name.endswith("_total")]
    assert total_samples == []


def test_record_mutations_from_event_helper_direct(fresh_metrics):
    _record_mutations_from_event(
        [
            {"field": "model", "op": "rewrite"},
            {"field": None, "op": "rewrite"},
            {"field": "x", "op": None},
        ]
    )
    assert (
        fresh_metrics["mutations_total"]
        .labels(field="model", op="rewrite")
        ._value.get()
        == 1
    )


# ---------------------------------------------------------------------------
# 4. always-on registration + no double-emit
# ---------------------------------------------------------------------------
def test_metrics_registered_as_recorder_sink():
    from airlock.callbacks.recorder import request_recorder

    assert "metrics" in request_recorder.sink_names


def test_self_register_deleted():
    assert not hasattr(metrics_module, "_self_register")


def test_metrics_callback_absent_from_litellm_lists():
    import litellm

    for lst in (
        litellm.success_callback,
        litellm.failure_callback,
        litellm._async_success_callback,
        litellm._async_failure_callback,
    ):
        assert metrics_callback not in lst


def test_single_recorder_dispatch_increments_once(fresh_metrics):
    from airlock.callbacks.request_event import RequestRecorder

    recorder = RequestRecorder()
    recorder.register(metrics_callback.record_event, name="metrics")
    recorder.dispatch(_event(success=True, user="alice"), is_async=False)

    assert (
        fresh_metrics["requests_total"]
        .labels(model="gpt-4", user="alice", success="true")
        ._value.get()
        == 1
    )
