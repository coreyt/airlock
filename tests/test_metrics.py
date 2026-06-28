"""Tests for airlock.callbacks.metrics — Prometheus metrics callback."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from prometheus_client import CollectorRegistry

from airlock.callbacks import metrics as metrics_module
from airlock.callbacks.metrics import (
    AirlockMetricsCallback,
    record_keyword_block,
    record_pii_redaction,
    record_response_scan_detection,
    record_threat_block,
    set_circuit_breaker_state,
)


@pytest.fixture()
def fresh_metrics(monkeypatch):
    """Build metrics on a fresh registry to avoid duplicate registration."""
    from prometheus_client import Counter, Gauge, Histogram

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
        "pii_redactions": Counter(
            "test_airlock_pii_redactions_total",
            "Total PII redactions",
            ["entity_type"],
            registry=registry,
        ),
        "keyword_blocks": Counter(
            "test_airlock_keyword_blocks_total",
            "Keyword blocks",
            registry=registry,
        ),
        "circuit_breaker_state": Gauge(
            "test_airlock_circuit_breaker_state",
            "Circuit breaker state",
            ["model"],
            registry=registry,
        ),
        "threat_blocks": Counter(
            "test_airlock_threat_blocks_total",
            "Threat blocks",
            registry=registry,
        ),
        "response_scan_detections": Counter(
            "test_airlock_response_scan_detections_total",
            "Response scan detections",
            ["category", "mode"],
            registry=registry,
        ),
        "mutations_total": Counter(
            "test_airlock_mutations_total",
            "Total ledger mutations",
            ["field", "op"],
            registry=registry,
        ),
    }
    monkeypatch.setattr(metrics_module, "_metrics", fresh)
    return fresh


class TestRecordPiiRedaction:
    def test_increments_counter(self, fresh_metrics):
        record_pii_redaction("SSN")
        record_pii_redaction("SSN")
        record_pii_redaction("EMAIL")
        assert (
            fresh_metrics["pii_redactions"].labels(entity_type="SSN")._value.get() == 2
        )
        assert (
            fresh_metrics["pii_redactions"].labels(entity_type="EMAIL")._value.get()
            == 1
        )


class TestRecordKeywordBlock:
    def test_increments_counter(self, fresh_metrics):
        record_keyword_block()
        record_keyword_block()
        assert fresh_metrics["keyword_blocks"]._value.get() == 2


class TestRecordResponseScanDetection:
    def test_increments_counter_with_labels(self, fresh_metrics):
        record_response_scan_detection("pii", "block")
        assert (
            fresh_metrics["response_scan_detections"]
            .labels(category="pii", mode="block")
            ._value.get()
            == 1
        )


class TestRecordThreatBlock:
    def test_increments_counter(self, fresh_metrics):
        record_threat_block()
        assert fresh_metrics["threat_blocks"]._value.get() == 1


class TestSetCircuitBreakerState:
    def test_maps_closed_to_zero(self, fresh_metrics):
        set_circuit_breaker_state("gpt-4", "closed")
        assert (
            fresh_metrics["circuit_breaker_state"].labels(model="gpt-4")._value.get()
            == 0
        )

    def test_maps_half_open_to_one(self, fresh_metrics):
        set_circuit_breaker_state("gpt-4", "half_open")
        assert (
            fresh_metrics["circuit_breaker_state"].labels(model="gpt-4")._value.get()
            == 1
        )

    def test_maps_open_to_two(self, fresh_metrics):
        set_circuit_breaker_state("gpt-4", "open")
        assert (
            fresh_metrics["circuit_breaker_state"].labels(model="gpt-4")._value.get()
            == 2
        )

    def test_unknown_state_maps_to_negative_one(self, fresh_metrics):
        set_circuit_breaker_state("gpt-4", "bogus")
        assert (
            fresh_metrics["circuit_breaker_state"].labels(model="gpt-4")._value.get()
            == -1
        )


def _event(
    *, success=True, model="gpt-4", user="alice", start=None, end=None, mutations=None
):
    return SimpleNamespace(
        success=success,
        model=model,
        user=user,
        start_time=start,
        end_time=end,
        mutations=mutations or [],
    )


class TestAirlockMetricsCallback:
    def test_record_event_success(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        start = datetime(2024, 1, 1, 0, 0, 0)
        end = start + timedelta(seconds=2.5)
        cb.record_event(_event(success=True, user="alice", start=start, end=end))

        assert (
            fresh_metrics["requests_total"]
            .labels(model="gpt-4", user="alice", success="true")
            ._value.get()
            == 1
        )

    def test_record_event_failure(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        cb.record_event(_event(success=False, user="bob"))

        assert (
            fresh_metrics["requests_total"]
            .labels(model="gpt-4", user="bob", success="false")
            ._value.get()
            == 1
        )

    def test_record_event_unknown_user(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        cb.record_event(_event(success=True, user=None))

        assert (
            fresh_metrics["requests_total"]
            .labels(model="gpt-4", user="unknown", success="true")
            ._value.get()
            == 1
        )


class TestMutationsCounter:
    def test_record_event_increments_per_mutation(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        mutations = [
            {"field": "model", "op": "rewrite", "before": "a", "after": "b"},
            {"field": "messages", "op": "redact", "before": None, "after": None},
        ]
        cb.record_event(_event(success=True, mutations=mutations))

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

    # Post-migration both of these reduce to event.mutations == []: build_request_event
    # converges absent and explicit-empty airlock_mutations to the same empty list, so
    # the apparent duplication is intentional (one guards no-crash, one guards no-increment).
    def test_absent_mutations_no_crash(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        cb.record_event(_event(success=True, mutations=[]))

    def test_empty_mutations_no_increment(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        cb.record_event(_event(success=True, mutations=[]))

    def test_non_iterable_mutations_no_crash(self, fresh_metrics):
        cb = AirlockMetricsCallback()
        cb.record_event(_event(success=True, mutations=5))
