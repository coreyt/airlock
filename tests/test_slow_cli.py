"""Tests for airlock/slow/cli.py"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from airlock.slow.analyzer import AnalysisReport
from airlock.slow.cli import _format_text, main


# ---------------------------------------------------------------------------
# _format_text()
# ---------------------------------------------------------------------------
class TestFormatText:
    @pytest.fixture
    def sample_report(self):
        from airlock.slow.analyzer import (
            CacheOpportunity,
            Hypothesis,
            Optimization,
            Trend,
        )

        return AnalysisReport(
            generated_at="2024-01-15T10:00:00Z",
            period_start="2024-01-08T10:00:00Z",
            period_end="2024-01-15T10:00:00Z",
            total_requests=100,
            optimizations=[
                Optimization(
                    category="reliability",
                    description="Model X has 25% error rate",
                    impact="high",
                    evidence={"model": "X", "error_rate": 0.25},
                )
            ],
            cache_opportunities=[
                CacheOpportunity(
                    pattern="Repeated prompt (seen 5 times)",
                    fingerprint="abc123",
                    frequency=5,
                    model="gpt-4o",
                    estimated_token_savings=1000,
                    estimated_cost_savings_pct=2.5,
                )
            ],
            trends=[
                Trend(
                    metric="request_volume",
                    direction="increasing",
                    magnitude=25.0,
                    period_days=7,
                    details={"first_half": 40, "second_half": 60},
                )
            ],
            hypotheses=[
                Hypothesis(
                    statement="Caching could save tokens",
                    evidence={"savings": 1000},
                    confidence=0.7,
                    test_proposal="Enable caching",
                )
            ],
            summary={
                "total_requests": 100,
                "successful": 90,
                "failed": 10,
                "error_rate": 0.1,
                "active_users": 5,
                "total_tokens": 50000,
                "models_used": {"gpt-4o": 60, "claude-sonnet": 40},
            },
        )

    def test_contains_header(self, sample_report):
        text = _format_text(sample_report)
        assert "AIRLOCK SLOW ANALYSIS REPORT" in text

    def test_contains_summary_section(self, sample_report):
        text = _format_text(sample_report)
        assert "SUMMARY" in text
        assert "Successful requests" in text
        assert "Failed requests" in text

    def test_contains_optimizations_section(self, sample_report):
        text = _format_text(sample_report)
        assert "OPTIMIZATIONS" in text
        assert "error rate" in text

    def test_contains_cache_section(self, sample_report):
        text = _format_text(sample_report)
        assert "CACHE OPPORTUNITIES" in text
        assert "Token savings" in text

    def test_contains_trends_section(self, sample_report):
        text = _format_text(sample_report)
        assert "TRENDS" in text
        assert "request_volume" in text

    def test_contains_hypotheses_section(self, sample_report):
        text = _format_text(sample_report)
        assert "HYPOTHESES" in text
        assert "Confidence" in text

    def test_contains_semantic_section(self):
        from airlock.slow.analyzer import ClassifierStats, SemanticInsight

        report = AnalysisReport(
            generated_at="2024-01-15T10:00:00Z",
            period_start="2024-01-08T10:00:00Z",
            period_end="2024-01-15T10:00:00Z",
            total_requests=100,
            semantic_insights=SemanticInsight(
                total_evaluated=80,
                total_blocked=5,
                overall_block_rate=0.0625,
                classifier_stats=[
                    ClassifierStats(
                        name="injection_detector",
                        sample_count=80,
                        block_count=5,
                        block_rate=0.0625,
                        error_count=2,
                        error_rate=0.025,
                        score_mean=0.15,
                        score_p50=0.12,
                        score_p95=0.45,
                        score_p99=0.88,
                        current_threshold=0.5,
                        latency_mean_ms=25.0,
                        latency_p95_ms=48.0,
                        ambiguous_count=8,
                        ambiguous_rate=0.1,
                    ),
                ],
                classifier_agreement=[
                    {
                        "classifier_a": "injection",
                        "classifier_b": "topic",
                        "co_block_count": 3,
                        "co_occurrence_count": 50,
                        "agreement_rate": 0.06,
                    }
                ],
            ),
            summary={
                "total_requests": 100,
                "successful": 95,
                "failed": 5,
                "error_rate": 0.05,
                "active_users": 10,
                "total_tokens": 50000,
                "models_used": {"claude-sonnet": 100},
            },
        )
        text = _format_text(report)
        assert "SEMANTIC GUARD" in text
        assert "injection_detector" in text
        assert "Scores" in text
        assert "Threshold" in text
        assert "Latency" in text
        assert "Errors" in text
        assert "Ambiguous" in text
        assert "Cross-classifier agreement" in text
        assert "injection + topic" in text

    def test_semantic_section_omitted_when_none(self):
        report = AnalysisReport(
            generated_at="2024-01-15T10:00:00Z",
            period_start="2024-01-08T10:00:00Z",
            period_end="2024-01-15T10:00:00Z",
            total_requests=100,
            semantic_insights=None,
            summary={
                "total_requests": 100,
                "successful": 100,
                "failed": 0,
                "error_rate": 0,
                "active_users": 5,
                "total_tokens": 50000,
                "models_used": {"gpt-4o": 100},
            },
        )
        text = _format_text(report)
        assert "SEMANTIC GUARD" not in text

    def test_empty_report(self):
        report = AnalysisReport(
            generated_at="2024-01-15T10:00:00Z",
            period_start="2024-01-08T10:00:00Z",
            period_end="2024-01-15T10:00:00Z",
            total_requests=0,
            summary={
                "total_requests": 0,
                "successful": 0,
                "failed": 0,
                "error_rate": 0,
                "active_users": 0,
                "total_tokens": 0,
                "models_used": {},
            },
        )
        text = _format_text(report)
        assert "AIRLOCK SLOW ANALYSIS REPORT" in text
        assert "OPTIMIZATIONS" not in text


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
class TestMain:
    def test_default_text_output(self, populated_log_dir, capsys):
        with patch("sys.argv", ["airlock-analyze"]):
            main()
        captured = capsys.readouterr()
        assert "AIRLOCK SLOW ANALYSIS REPORT" in captured.out

    def test_json_output(self, populated_log_dir, capsys):
        with patch("sys.argv", ["airlock-analyze", "--json"]):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "total_requests" in data
        assert "optimizations" in data

    def test_days_argument(self, populated_log_dir):
        with patch("sys.argv", ["airlock-analyze", "--days", "3"]):
            with patch("airlock.slow.cli.analyze") as mock_analyze:
                mock_analyze.return_value = AnalysisReport(
                    generated_at="now",
                    period_start="start",
                    period_end="end",
                    total_requests=0,
                    summary={},
                )
                main()
                mock_analyze.assert_called_once_with(days=3)

    def test_output_to_file(self, populated_log_dir, tmp_path):
        out_file = tmp_path / "report.txt"
        with patch("sys.argv", ["airlock-analyze", "-o", str(out_file)]):
            main()
        assert out_file.exists()
        content = out_file.read_text()
        assert "AIRLOCK SLOW ANALYSIS REPORT" in content

    def test_json_output_to_file(self, populated_log_dir, tmp_path):
        out_file = tmp_path / "report.json"
        with patch("sys.argv", ["airlock-analyze", "--json", "-o", str(out_file)]):
            main()
        data = json.loads(out_file.read_text())
        assert "total_requests" in data
