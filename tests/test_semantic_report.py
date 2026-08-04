"""Tests for the observe-window semantic report."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from airlock.semantic_report import build_report, has_semantic_verdict, render_text


def _record(**semantic):
    base = {
        "status": "passed",
        "mode": "observe",
        "action": "allowed",
        "input_kind": "user_prompt",
        "selection": "all",
        "results": [],
    }
    base.update(semantic)
    return {
        "timestamp": "2026-08-04T12:00:00+00:00",
        "request_id": "req-1",
        "airlock_client": "memex",
        "model": "claude-haiku",
        "airlock_semantic": base,
    }


def _result(name="input_injection_tripwire", **kwargs):
    result = {
        "name": name,
        "label": "clean",
        "blocked": False,
        "error": None,
        "duration_ms": 1.0,
        "metadata": {},
    }
    result.update(kwargs)
    return result


def _write(directory, records, day_offset=0):
    day = datetime.now(timezone.utc).date() - timedelta(days=day_offset)
    path = directory / f"airlock-{day.isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path


class TestExtraction:
    def test_finds_top_level_verdicts(self):
        assert has_semantic_verdict(_record()) is True

    def test_finds_nested_verdicts(self):
        assert has_semantic_verdict({"metadata": {"airlock_semantic": {}}}) is True

    def test_ignores_records_without_verdicts(self):
        assert has_semantic_verdict({"model": "x"}) is False


class TestAggregation:
    def test_counts_detections_and_clean(self, log_dir):
        _write(
            log_dir,
            [
                _record(results=[_result()]),
                _record(
                    status="blocked",
                    action="observed",
                    results=[_result(label="prompt_injection", blocked=True)],
                ),
            ],
        )
        report = build_report(days=1, directory=log_dir)
        summary = report.classifiers["input_injection_tripwire"]
        assert summary.detections == 1
        assert summary.clean == 1
        assert summary.detection_rate == 0.5

    def test_verdict_and_action_are_counted_separately(self, log_dir):
        """An observed detection must never be counted as a blocked request."""
        _write(
            log_dir,
            [
                _record(
                    status="blocked",
                    action="observed",
                    results=[_result(label="prompt_injection", blocked=True)],
                )
            ],
        )
        report = build_report(days=1, directory=log_dir)
        assert report.detections == 1
        assert report.blocked_requests == 0

    def test_enforce_mode_blocked_requests_are_counted(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    status="blocked",
                    mode="enforce",
                    action="blocked",
                    results=[_result(label="prompt_injection", blocked=True)],
                )
            ],
        )
        report = build_report(days=1, directory=log_dir)
        assert report.blocked_requests == 1

    def test_unavailable_reasons_are_aggregated(self, log_dir):
        """The alerting signal: unavailability fails open and looks like quiet."""
        _write(
            log_dir,
            [
                _record(
                    results=[
                        _result(
                            name="model_armor_prompt_injection",
                            label="unavailable",
                            error="http_429",
                            metadata={"unavailable_reason": "rate_limit"},
                        )
                    ]
                ),
                _record(
                    results=[
                        _result(
                            name="model_armor_prompt_injection",
                            label="unavailable",
                            error="timeout",
                            metadata={"unavailable_reason": "timeout"},
                        )
                    ]
                ),
            ],
        )
        report = build_report(days=1, directory=log_dir)
        assert report.unavailable_reasons["rate_limit"] == 1
        assert report.unavailable_reasons["timeout"] == 1
        summary = report.classifiers["model_armor_prompt_injection"]
        assert summary.unavailable == 2
        assert summary.unavailable_rate == 1.0

    def test_unavailable_is_excluded_from_detection_rate(self, log_dir):
        """A classifier that could not answer must not dilute its own rate."""
        _write(
            log_dir,
            [
                _record(results=[_result(label="prompt_injection", blocked=True)]),
                _record(results=[_result(label="unavailable", error="timeout")]),
            ],
        )
        summary = build_report(days=1, directory=log_dir).classifiers[
            "input_injection_tripwire"
        ]
        assert summary.detection_rate == 1.0  # 1 of 1 answered, not 1 of 2

    def test_tripwire_categories_are_aggregated(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    status="blocked",
                    results=[
                        _result(
                            blocked=True,
                            label="prompt_injection",
                            metadata={"categories": ["instruction_override"]},
                        )
                    ],
                )
            ],
        )
        summary = build_report(days=1, directory=log_dir).classifiers[
            "input_injection_tripwire"
        ]
        assert summary.categories["instruction_override"] == 1

    def test_provider_confidence_is_aggregated(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    results=[
                        _result(
                            name="model_armor_prompt_injection",
                            blocked=True,
                            label="prompt_injection",
                            metadata={
                                "provider_results": [
                                    {"provider": "model_armor", "confidence": "HIGH"}
                                ]
                            },
                        )
                    ]
                )
            ],
        )
        summary = build_report(days=1, directory=log_dir).classifiers[
            "model_armor_prompt_injection"
        ]
        assert summary.confidence["HIGH"] == 1

    def test_short_circuit_counts(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    short_circuited=[{"name": "model_armor_prompt_injection"}],
                    results=[_result(blocked=True, label="prompt_injection")],
                    status="blocked",
                )
            ],
        )
        report = build_report(days=1, directory=log_dir)
        assert report.short_circuited["model_armor_prompt_injection"] == 1


class TestPrivacy:
    def test_samples_carry_identifiers_never_prompt_text(self, log_dir):
        record = _record(
            status="blocked",
            action="observed",
            results=[_result(blocked=True, label="prompt_injection")],
        )
        record["messages"] = [{"role": "user", "content": "SENTINEL-secret-prompt"}]
        _write(log_dir, [record])
        report = build_report(days=1, directory=log_dir)
        rendered = json.dumps(report.as_dict()) + render_text(report)
        assert "SENTINEL-secret-prompt" not in rendered
        assert report.detection_samples[0]["request_id"] == "req-1"

    def test_sample_count_is_capped(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    status="blocked",
                    results=[_result(blocked=True, label="prompt_injection")],
                )
                for _ in range(50)
            ],
        )
        report = build_report(days=1, directory=log_dir, max_samples=5)
        assert len(report.detection_samples) == 5


class TestRendering:
    def test_empty_window_explains_itself(self, log_dir):
        text = render_text(build_report(days=1, directory=log_dir))
        assert "No semantic verdicts found" in text

    def test_observe_mode_states_nothing_was_rejected(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    status="blocked",
                    action="observed",
                    results=[_result(blocked=True, label="prompt_injection")],
                )
            ],
        )
        text = render_text(build_report(days=1, directory=log_dir))
        assert "nothing was actually rejected" in text

    def test_rate_limit_is_flagged_as_attacker_inducible(self, log_dir):
        _write(
            log_dir,
            [
                _record(
                    results=[
                        _result(
                            label="unavailable",
                            error="http_429",
                            metadata={"unavailable_reason": "rate_limit"},
                        )
                    ]
                )
            ],
        )
        text = render_text(build_report(days=1, directory=log_dir))
        assert "attacker-inducible" in text

    def test_truncated_window_is_disclosed(self, log_dir, monkeypatch):
        monkeypatch.setenv("AIRLOCK_LOG_QUERY_MAX_RECORDS", "2")
        _write(log_dir, [_record() for _ in range(10)])
        text = render_text(build_report(days=1, directory=log_dir))
        assert "TRUNCATED" in text

    def test_json_output_is_serializable(self, log_dir):
        _write(log_dir, [_record(results=[_result()])])
        payload = build_report(days=1, directory=log_dir).as_dict()
        assert (
            json.loads(json.dumps(payload, default=str))["requests_with_verdicts"] == 1
        )
