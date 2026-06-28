"""Consolidated cross-sink equivalence harness (Pack 0.5.4-VERIFY-harness).

Proves the holistic **AC-EQUIV**: ONE ``RequestEvent`` built via
``build_request_event`` reproduces ALL FOUR sink records
(``project_enterprise``/``project_fathom``/``project_s3``/``project_sql``) against
the existing frozen goldens (ignoring only ``timestamp``), PLUS the per-request
metrics counters (``record_event`` -> requests_total/request_duration/
mutations_total), all fanned out through a single ``RequestRecorder`` dispatch,
with cross-sink failure isolation (**AC-SEAM**).

Test-only / additive: NO production module is touched. The frozen goldens and the
representative request set / env-flag matrix are **imported** from
``tests.test_projections_equiv`` (not duplicated or recaptured) so this harness
stays pinned to the same oracle the per-sink equivalence tests use.
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, Counter, Histogram

from airlock.callbacks import metrics as metrics_module
from airlock.callbacks.metrics import AirlockMetricsCallback
from airlock.callbacks.projections import (
    project_enterprise,
    project_fathom,
    project_s3,
    project_sql,
)
from airlock.callbacks.request_event import RequestRecorder, build_request_event

# Reuse the SAME frozen goldens + representative inputs the per-sink equivalence
# tests use — import/share, never duplicate (keeps the representative set identical).
from tests.test_projections_equiv import (
    _ENV_MATRIX,
    _GOLDEN,
    _S3_GOLDEN,
    _SQL_GOLDEN,
    REQUEST_SET,
    _jsonify,
)

_BY_NAME = dict(REQUEST_SET)
_ALL_ON = dict(_ENV_MATRIX)["all_on"]


def _build(name: str):
    """Build ONE event for a named representative fixture (the sourced-once superset)."""
    kwargs, resp, start, end, success = _BY_NAME[name]()
    return build_request_event(kwargs, resp, start, end, success=success)


# ---------------------------------------------------------------------------
# 1. Build once -> project many: one event reproduces ALL FOUR sink records.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,make_inputs", REQUEST_SET)
def test_one_event_reproduces_all_four_sinks(name, make_inputs, monkeypatch):
    """Build ONE ``RequestEvent`` and assert every sink projection of that SAME
    event matches its frozen golden field-for-field (and key-order), proving the
    single sourced event reproduces all four sinks (the core AC-EQUIV claim)."""
    kwargs, resp, start, end, success = make_inputs()
    event = build_request_event(kwargs, resp, start, end, success=success)

    # enterprise
    ent = _jsonify(project_enterprise(event))
    expected_ent = _GOLDEN["enterprise"][name]
    assert ent == expected_ent
    assert list(ent.keys()) == list(expected_ent.keys())

    # s3 (narrow, redacted set)
    s3 = _jsonify(project_s3(event))
    expected_s3 = _S3_GOLDEN["s3"][name]
    assert s3 == expected_s3
    assert list(s3.keys()) == list(expected_s3.keys())

    # sql (narrow, string-encoded set)
    sql = _jsonify(project_sql(event))
    expected_sql = _SQL_GOLDEN["sql"][name]
    assert sql == expected_sql
    assert list(sql.keys()) == list(expected_sql.keys())

    # fathom: the SAME event projected across the full env-flag matrix (the flags
    # gate the projection, not the build — so one event covers every matrix cell).
    for env_name, env in _ENV_MATRIX:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        fathom = _jsonify(project_fathom(event))
        expected_fathom = _GOLDEN["fathom"][f"{name}::{env_name}"]
        assert fathom == expected_fathom, f"fathom mismatch for {name}::{env_name}"
        assert list(fathom.keys()) == list(expected_fathom.keys())


# ---------------------------------------------------------------------------
# 2. Metrics from the SAME event (record_event -> the per-request counters).
# ---------------------------------------------------------------------------
@pytest.fixture()
def fresh_metrics(monkeypatch):
    """Per-request metrics on a fresh registry (avoids duplicate registration and
    cross-test bleed) — only the counters ``record_event`` touches are needed."""
    registry = CollectorRegistry()
    fresh = {
        "requests_total": Counter(
            "xsink_airlock_requests_total",
            "Total LLM requests",
            ["model", "user", "success"],
            registry=registry,
        ),
        "request_duration": Histogram(
            "xsink_airlock_request_duration_seconds",
            "LLM request duration",
            ["model"],
            registry=registry,
        ),
        "mutations_total": Counter(
            "xsink_airlock_mutations_total",
            "Total ledger mutations",
            ["field", "op"],
            registry=registry,
        ),
    }
    monkeypatch.setattr(metrics_module, "_metrics", fresh)
    return fresh


def test_metrics_success_event(fresh_metrics):
    """A success event increments ``requests_total`` (success=true) and observes
    ``request_duration`` (1.5s for the plain-success fixture)."""
    cb = AirlockMetricsCallback()
    cb.record_event(_build("plain_success"))

    assert (
        fresh_metrics["requests_total"]
        .labels(model="gpt-4o", user="alice", success="true")
        ._value.get()
        == 1
    )
    assert fresh_metrics["request_duration"].labels(model="gpt-4o")._sum.get() == 1.5


def test_metrics_failure_event(fresh_metrics):
    """A failure event increments ``requests_total`` (success=false) and does NOT
    observe ``request_duration`` (duration is success-only — §5b)."""
    cb = AirlockMetricsCallback()
    cb.record_event(_build("provider_failure"))

    assert (
        fresh_metrics["requests_total"]
        .labels(model="gpt-4o", user="alice", success="false")
        ._value.get()
        == 1
    )
    assert fresh_metrics["request_duration"].labels(model="gpt-4o")._sum.get() == 0


def test_metrics_mutations_from_event(fresh_metrics):
    """The same event drives ``mutations_total`` per ledger mutation (field/op)."""
    cb = AirlockMetricsCallback()
    cb.record_event(_build("mutations_and_served"))

    assert (
        fresh_metrics["mutations_total"]
        .labels(field="model", op="override")
        ._value.get()
        == 1
    )


# ---------------------------------------------------------------------------
# 3. Cross-sink dispatch: one dispatch() drives every sink consistently.
# ---------------------------------------------------------------------------
def _capturing_sink(projector, key, captured, received):
    def _sink(event):
        received[key] = event
        captured[key] = projector(event)

    return _sink


def test_one_dispatch_drives_all_sinks(monkeypatch):
    """Register capturing test-double sinks wrapping the real projections with a
    ``RequestRecorder``; ONE ``dispatch`` must reach every sink and each must
    produce its correct record from the single fanned-out event."""
    for key, value in _ALL_ON.items():
        monkeypatch.setenv(key, value)
    name = "mutations_and_served"
    event = _build(name)

    captured: dict = {}
    received: dict = {}
    recorder = RequestRecorder()
    recorder.register(
        _capturing_sink(project_enterprise, "enterprise", captured, received),
        name="enterprise",
    )
    recorder.register(
        _capturing_sink(project_fathom, "fathom", captured, received),
        name="fathom",
        async_only=True,
    )
    recorder.register(_capturing_sink(project_s3, "s3", captured, received), name="s3")
    recorder.register(
        _capturing_sink(project_sql, "sql", captured, received), name="sql"
    )

    recorder.dispatch(event)  # is_async=True default -> async_only fathom fires too

    # every sink received the SAME single event...
    assert set(received) == {"enterprise", "fathom", "s3", "sql"}
    assert all(ev is event for ev in received.values())

    # ...and each produced its correct record vs the frozen goldens.
    assert _jsonify(captured["enterprise"]) == _GOLDEN["enterprise"][name]
    assert _jsonify(captured["s3"]) == _S3_GOLDEN["s3"][name]
    assert _jsonify(captured["sql"]) == _SQL_GOLDEN["sql"][name]
    assert _jsonify(captured["fathom"]) == _GOLDEN["fathom"][f"{name}::all_on"]


def test_async_only_fathom_skipped_on_sync_dispatch(monkeypatch):
    """A sync dispatch (``is_async=False``) skips the async-only fathom sink but
    still drives the always-on sinks — the firing-surface invariant (§5a)."""
    for key, value in _ALL_ON.items():
        monkeypatch.setenv(key, value)
    name = "plain_success"
    event = _build(name)

    captured: dict = {}
    received: dict = {}
    recorder = RequestRecorder()
    recorder.register(
        _capturing_sink(project_enterprise, "enterprise", captured, received),
        name="enterprise",
    )
    recorder.register(
        _capturing_sink(project_fathom, "fathom", captured, received),
        name="fathom",
        async_only=True,
    )

    recorder.dispatch(event, is_async=False)

    assert "fathom" not in captured
    assert _jsonify(captured["enterprise"]) == _GOLDEN["enterprise"][name]


# ---------------------------------------------------------------------------
# 4. Cross-sink failure isolation (AC-SEAM, holistic).
# ---------------------------------------------------------------------------
def test_cross_sink_failure_isolation(monkeypatch):
    """With one sink raising, the OTHER sinks still produce their correct records
    and ``dispatch`` itself never raises (cross-sink failure isolation)."""
    for key, value in _ALL_ON.items():
        monkeypatch.setenv(key, value)
    name = "plain_success"
    event = _build(name)

    captured: dict = {}
    received: dict = {}

    def boom(_event):
        raise RuntimeError("deliberate sink failure")

    recorder = RequestRecorder()
    recorder.register(boom, name="enterprise_boom")  # raising sink, dispatched first
    recorder.register(_capturing_sink(project_s3, "s3", captured, received), name="s3")
    recorder.register(
        _capturing_sink(project_sql, "sql", captured, received), name="sql"
    )
    recorder.register(
        _capturing_sink(project_fathom, "fathom", captured, received),
        name="fathom",
    )

    # must NOT raise despite the boom sink failing
    recorder.dispatch(event)

    # the raising sink captured nothing; every OTHER sink still produced its record
    assert "enterprise_boom" not in captured
    assert set(captured) == {"s3", "sql", "fathom"}
    assert _jsonify(captured["s3"]) == _S3_GOLDEN["s3"][name]
    assert _jsonify(captured["sql"]) == _SQL_GOLDEN["sql"][name]
    assert _jsonify(captured["fathom"]) == _GOLDEN["fathom"][f"{name}::all_on"]
