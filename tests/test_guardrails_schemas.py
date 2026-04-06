"""Tests for airlock.guardrails.schemas — guardrail data schemas."""

from __future__ import annotations

from airlock.guardrails.schemas import (
    GuardrailKnobs,
    GuardrailObservation,
    GuardrailSignal,
    default_knobs,
)


class TestGuardrailSignal:
    def test_construction(self):
        sig = GuardrailSignal(
            guardrail_name="pii_scan",
            detected=True,
            score=0.95,
            details={"entity": "SSN"},
            duration_ms=12.5,
        )
        assert sig.guardrail_name == "pii_scan"
        assert sig.detected is True
        assert sig.score == 0.95
        assert sig.details == {"entity": "SSN"}
        assert sig.duration_ms == 12.5


class TestGuardrailObservation:
    def test_construction_with_defaults(self):
        obs = GuardrailObservation(
            request_id="req-1",
            model="gpt-4",
            client_id="client-a",
            signals=[],
        )
        assert obs.request_id == "req-1"
        assert obs.model == "gpt-4"
        assert obs.client_id == "client-a"
        assert obs.signals == []
        assert obs.composite_score is None
        assert obs.would_block is None
        assert obs.orchestrator_version is None

    def test_construction_with_all_fields(self):
        obs = GuardrailObservation(
            request_id="req-2",
            model="claude-3",
            client_id="client-b",
            signals=[],
            composite_score=0.8,
            would_block=True,
            orchestrator_version="1.0",
        )
        assert obs.composite_score == 0.8
        assert obs.would_block is True
        assert obs.orchestrator_version == "1.0"


class TestGuardrailKnobs:
    def test_construction(self):
        knobs = GuardrailKnobs(
            version="2024-01-01",
            weights={"pii_scan": 0.5},
            threshold=0.7,
        )
        assert knobs.version == "2024-01-01"
        assert knobs.weights == {"pii_scan": 0.5}
        assert knobs.threshold == 0.7
        assert knobs.per_guardrail == {}


class TestDefaultKnobs:
    def test_returns_correct_defaults(self):
        knobs = default_knobs()
        assert knobs.version == "default"
        assert knobs.threshold == 0.5
        assert knobs.weights == {
            "pii_scan": 0.4,
            "keyword_scan": 0.4,
            "threat_read": 0.2,
        }
        assert len(knobs.weights) == 3
        assert knobs.per_guardrail == {}
