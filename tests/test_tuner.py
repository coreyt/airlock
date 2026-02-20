"""Tests for airlock/slow/tuner.py"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airlock.guardrails.schemas import GuardrailKnobs, default_knobs
from airlock.slow.tuner import (
    load_knobs,
    tune_guardrails,
    write_knobs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def knobs_dir(tmp_path):
    return tmp_path


def _make_observation(
    *,
    pii_detected: bool = False,
    pii_score: float = 0.0,
    keyword_detected: bool = False,
    keyword_score: float = 0.0,
    threat_score: float = 0.0,
    request_id: str = "req-001",
) -> dict:
    """Build a minimal airlock_observation dict."""
    return {
        "request_id": request_id,
        "model": "claude-sonnet",
        "client_id": "key:testkey1",
        "signals": [
            {
                "guardrail_name": "pii_scan",
                "detected": pii_detected,
                "score": pii_score,
                "details": {
                    "entities": {"EMAIL_ADDRESS": 1} if pii_detected else {},
                    "total_count": 1 if pii_detected else 0,
                },
                "duration_ms": 0.1,
            },
            {
                "guardrail_name": "keyword_scan",
                "detected": keyword_detected,
                "score": keyword_score,
                "details": {
                    "matched_keywords": ["forbidden"] if keyword_detected else [],
                    "match_count": 1 if keyword_detected else 0,
                },
                "duration_ms": 0.1,
            },
            {
                "guardrail_name": "threat_read",
                "detected": threat_score >= 0.8,
                "score": threat_score,
                "details": {"client_id": "key:testkey1", "threat_score": threat_score},
                "duration_ms": 0.1,
            },
        ],
        "composite_score": None,
        "would_block": None,
        "orchestrator_version": None,
    }


def _make_record(
    *,
    success: bool = True,
    observation: dict | None = None,
    request_id: str = "req-001",
) -> dict:
    return {
        "timestamp": "2024-01-15T10:30:00Z",
        "success": success,
        "model": "claude-sonnet",
        "request_id": request_id,
        "airlock_observation": observation,
    }


# ---------------------------------------------------------------------------
# tune_guardrails
# ---------------------------------------------------------------------------
class TestTuneGuardrails:
    def test_empty_records_returns_defaults(self):
        knobs = tune_guardrails([])
        assert knobs.version == "default"
        assert knobs.threshold == 0.5
        assert "pii_scan" in knobs.weights

    def test_no_observations_returns_defaults(self):
        records = [{"success": True, "model": "gpt-4o"}]
        knobs = tune_guardrails(records)
        assert knobs.version == "default"

    def test_records_with_observations_compute_weights(self):
        obs = _make_observation(pii_detected=True, pii_score=0.4)
        records = [_make_record(observation=obs)]
        knobs = tune_guardrails(records)
        assert knobs.version != "default"
        assert sum(knobs.weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_high_detection_rate_lowers_weight(self):
        """A guardrail that fires on every request is noisy → lower weight."""
        records = []
        for i in range(20):
            obs = _make_observation(
                pii_detected=True,
                pii_score=0.5,
                request_id=f"req-{i:03d}",
            )
            records.append(_make_record(observation=obs, request_id=f"req-{i:03d}"))

        knobs = tune_guardrails(records)
        # PII fires on 100% of requests → should have reduced weight
        # compared to keyword_scan which fires 0%
        assert knobs.weights.get("pii_scan", 0) < knobs.weights.get("keyword_scan", 1)

    def test_per_guardrail_pii_entities(self):
        obs = _make_observation(pii_detected=True, pii_score=0.4)
        records = [_make_record(observation=obs)]
        knobs = tune_guardrails(records)
        assert "pii_scan" in knobs.per_guardrail
        assert knobs.per_guardrail["pii_scan"]["total_detections"] > 0


# ---------------------------------------------------------------------------
# write_knobs / load_knobs round-trip
# ---------------------------------------------------------------------------
class TestWriteLoadKnobs:
    def test_round_trip(self, knobs_dir):
        knobs = GuardrailKnobs(
            version="2024-01-15T10:00:00Z",
            weights={"pii_scan": 0.5, "keyword_scan": 0.3, "threat_read": 0.2},
            threshold=0.6,
            per_guardrail={"pii_scan": {"entity_frequency": {"EMAIL_ADDRESS": 10}}},
        )
        path = write_knobs(knobs, directory=knobs_dir)
        assert path.exists()

        loaded = load_knobs(directory=knobs_dir)
        assert loaded is not None
        assert loaded.version == "2024-01-15T10:00:00Z"
        assert loaded.weights == knobs.weights
        assert loaded.threshold == 0.6
        assert loaded.per_guardrail == knobs.per_guardrail

    def test_load_missing_file(self, knobs_dir):
        result = load_knobs(directory=knobs_dir)
        assert result is None

    def test_load_corrupt_file(self, knobs_dir):
        (knobs_dir / "airlock-knobs.json").write_text("not json")
        result = load_knobs(directory=knobs_dir)
        assert result is None

    def test_write_creates_directory(self, tmp_path):
        new_dir = tmp_path / "nested" / "dir"
        knobs = default_knobs()
        path = write_knobs(knobs, directory=new_dir)
        assert path.exists()
