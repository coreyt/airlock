"""Tests for airlock/slow/analyzer.py"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from airlock.slow.analyzer import (
    AnalysisReport,
    ClassifierStats,
    SemanticInsight,
    _fingerprint_messages,
    _load_logs,
    _percentile,
    analyze,
    find_cache_opportunities,
    find_optimizations,
    find_semantic_insights,
    find_trends,
    generate_hypotheses,
)


# ---------------------------------------------------------------------------
# _load_logs()
# ---------------------------------------------------------------------------
class TestLoadLogs:
    def test_reads_jsonl_files(self, populated_log_dir, log_dir):
        records = _load_logs(days=7)
        assert len(records) > 0
        assert all(isinstance(r, dict) for r in records)

    def test_skips_missing_days(self, log_dir):
        # log_dir is empty — no files for any day
        records = _load_logs(days=7)
        assert records == []

    def test_skips_malformed_lines(self, log_dir):
        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        log_path.write_text(
            '{"valid": true}\n'
            'not-json\n'
            '{"also_valid": true}\n'
        )
        records = _load_logs(days=1)
        assert len(records) == 2

    def test_skips_empty_lines(self, log_dir):
        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        log_path.write_text(
            '{"a": 1}\n'
            '\n'
            '{"b": 2}\n'
        )
        records = _load_logs(days=1)
        assert len(records) == 2


# ---------------------------------------------------------------------------
# _fingerprint_messages()
# ---------------------------------------------------------------------------
class TestFingerprintMessages:
    def test_deterministic(self):
        messages = [{"role": "user", "content": "hello"}]
        fp1 = _fingerprint_messages(messages)
        fp2 = _fingerprint_messages(messages)
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_different_messages_different_hash(self):
        m1 = [{"role": "user", "content": "hello"}]
        m2 = [{"role": "user", "content": "world"}]
        assert _fingerprint_messages(m1) != _fingerprint_messages(m2)

    def test_none_returns_empty(self):
        assert _fingerprint_messages(None) == ""

    def test_empty_list_returns_empty(self):
        assert _fingerprint_messages([]) == ""

    def test_multipart_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {}},
                ],
            }
        ]
        fp = _fingerprint_messages(messages)
        assert len(fp) == 16


# ---------------------------------------------------------------------------
# find_optimizations()
# ---------------------------------------------------------------------------
class TestFindOptimizations:
    def test_empty_records(self):
        assert find_optimizations([]) == []

    def test_high_error_rate_model(self):
        records = []
        for i in range(20):
            records.append({
                "model": "bad-model",
                "success": i < 5,  # 75% error rate
                "duration_ms": 1000,
                "total_tokens": 100,
            })
        opts = find_optimizations(records)
        reliability = [o for o in opts if o.category == "reliability"]
        assert len(reliability) >= 1
        assert reliability[0].evidence["model"] == "bad-model"

    def test_no_optimization_for_low_error_rate(self):
        records = [
            {"model": "good-model", "success": True, "duration_ms": 500, "total_tokens": 100}
            for _ in range(20)
        ]
        opts = find_optimizations(records)
        reliability = [o for o in opts if o.category == "reliability"]
        assert len(reliability) == 0

    def test_slow_p95_latency(self):
        records = []
        for i in range(20):
            records.append({
                "model": "slow-model",
                "success": True,
                "duration_ms": 35_000 if i >= 18 else 5000,  # p95 > 30s
                "total_tokens": 100,
            })
        opts = find_optimizations(records)
        perf = [o for o in opts if o.category == "performance"]
        assert len(perf) >= 1

    def test_outlier_token_usage(self):
        records = []
        for i in range(20):
            records.append({
                "model": "chatty-model",
                "success": True,
                "duration_ms": 1000,
                "total_tokens": 50000 if i >= 18 else 100,  # p95 >> median
            })
        opts = find_optimizations(records)
        cost = [o for o in opts if o.category == "cost"]
        assert len(cost) >= 1


# ---------------------------------------------------------------------------
# find_cache_opportunities()
# ---------------------------------------------------------------------------
class TestFindCacheOpportunities:
    def test_repeated_prompts_flagged(self):
        messages = [{"role": "user", "content": "Same question"}]
        records = [
            {"success": True, "messages": messages, "model": "gpt-4o", "total_tokens": 100}
            for _ in range(5)
        ]
        opps = find_cache_opportunities(records)
        assert len(opps) >= 1
        assert opps[0].frequency >= 3

    def test_fewer_than_3_not_flagged(self):
        messages = [{"role": "user", "content": "Unique question"}]
        records = [
            {"success": True, "messages": messages, "model": "gpt-4o", "total_tokens": 100}
            for _ in range(2)
        ]
        opps = find_cache_opportunities(records)
        assert len(opps) == 0

    def test_failure_records_excluded(self):
        messages = [{"role": "user", "content": "Same"}]
        records = [
            {"success": False, "messages": messages, "model": "gpt-4o", "total_tokens": 100}
            for _ in range(5)
        ]
        opps = find_cache_opportunities(records)
        assert len(opps) == 0


# ---------------------------------------------------------------------------
# find_trends()
# ---------------------------------------------------------------------------
class TestFindTrends:
    def test_empty_records(self):
        assert find_trends([]) == []

    def test_volume_increase(self):
        now = datetime.datetime.utcnow()
        records = []
        # First half: 5 requests
        for i in range(5):
            records.append({
                "timestamp": (now - datetime.timedelta(days=5, hours=i)).isoformat() + "Z",
                "success": True,
                "model": "gpt-4o",
            })
        # Second half: 20 requests (4x increase)
        for i in range(20):
            records.append({
                "timestamp": (now - datetime.timedelta(hours=i)).isoformat() + "Z",
                "success": True,
                "model": "gpt-4o",
            })

        trends = find_trends(records, period_days=7)
        volume_trends = [t for t in trends if t.metric == "request_volume"]
        assert len(volume_trends) >= 1
        assert volume_trends[0].direction == "increasing"

    def test_error_rate_increase(self):
        now = datetime.datetime.utcnow()
        records = []
        # First half: all success
        for i in range(20):
            records.append({
                "timestamp": (now - datetime.timedelta(days=5, hours=i)).isoformat() + "Z",
                "success": True,
                "model": "gpt-4o",
            })
        # Second half: 50% errors
        for i in range(20):
            records.append({
                "timestamp": (now - datetime.timedelta(hours=i)).isoformat() + "Z",
                "success": i % 2 == 0,
                "model": "gpt-4o",
            })

        trends = find_trends(records, period_days=7)
        err_trends = [t for t in trends if t.metric == "error_rate"]
        assert len(err_trends) >= 1
        assert err_trends[0].direction == "increasing"

    def test_latency_trend(self):
        now = datetime.datetime.utcnow()
        records = []
        # First half: 500ms
        for i in range(20):
            records.append({
                "timestamp": (now - datetime.timedelta(days=5, hours=i)).isoformat() + "Z",
                "success": True,
                "duration_ms": 500,
                "model": "gpt-4o",
            })
        # Second half: 2000ms (4x increase)
        for i in range(20):
            records.append({
                "timestamp": (now - datetime.timedelta(hours=i)).isoformat() + "Z",
                "success": True,
                "duration_ms": 2000,
                "model": "gpt-4o",
            })

        trends = find_trends(records, period_days=7)
        lat_trends = [t for t in trends if t.metric == "median_latency"]
        assert len(lat_trends) >= 1
        assert lat_trends[0].direction == "increasing"


# ---------------------------------------------------------------------------
# _percentile()
# ---------------------------------------------------------------------------
class TestPercentile:
    def test_empty_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_value(self):
        assert _percentile([0.5], 50) == 0.5

    def test_p50(self):
        values = [0.1, 0.2, 0.3, 0.4, 0.5]
        assert _percentile(values, 50) == 0.3

    def test_p95_high(self):
        values = list(range(100))
        p95 = _percentile(values, 95)
        assert p95 == 95

    def test_unsorted_input(self):
        values = [0.9, 0.1, 0.5, 0.3, 0.7]
        assert _percentile(values, 50) == 0.5


# ---------------------------------------------------------------------------
# find_semantic_insights()
# ---------------------------------------------------------------------------
class TestFindSemanticInsights:
    @staticmethod
    def _make_semantic_record(
        results: list[dict],
        status: str = "passed",
        blocking_classifier: str | None = None,
    ) -> dict:
        """Helper to build a log record with airlock_semantic metadata."""
        return {
            "success": status != "blocked",
            "model": "claude-sonnet",
            "airlock_semantic": {
                "status": status,
                "blocking_classifier": blocking_classifier,
                "total_duration_ms": 50.0,
                "results": results,
            },
        }

    def test_no_semantic_data_returns_none(self):
        records = [{"success": True, "model": "gpt-4o"}]
        assert find_semantic_insights(records) is None

    def test_no_classifiers_status_returns_none(self):
        records = [{
            "success": True,
            "airlock_semantic": {"status": "no_classifiers", "results": []},
        }]
        assert find_semantic_insights(records) is None

    def test_single_classifier_basic_stats(self):
        records = [
            self._make_semantic_record([
                {"name": "injection", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 20.0}
            ]),
            self._make_semantic_record([
                {"name": "injection", "score": 0.2, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 25.0}
            ]),
            self._make_semantic_record([
                {"name": "injection", "score": 0.3, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 30.0}
            ]),
        ]
        insight = find_semantic_insights(records)
        assert insight is not None
        assert insight.total_evaluated == 3
        assert insight.total_blocked == 0
        assert insight.overall_block_rate == 0.0
        assert len(insight.classifier_stats) == 1

        cs = insight.classifier_stats[0]
        assert cs.name == "injection"
        assert cs.sample_count == 3
        assert cs.block_count == 0
        assert cs.error_count == 0
        assert 0.19 <= cs.score_mean <= 0.21  # ~0.2
        assert cs.current_threshold == 0.5

    def test_blocking_classifier_counted(self):
        records = [
            self._make_semantic_record([
                {"name": "injection", "score": 0.9, "threshold": 0.5,
                 "blocked": True, "label": "injection", "duration_ms": 15.0}
            ], status="blocked", blocking_classifier="injection"),
            self._make_semantic_record([
                {"name": "injection", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 20.0}
            ]),
        ]
        insight = find_semantic_insights(records)
        assert insight.total_blocked == 1
        assert insight.overall_block_rate == 0.5

        cs = insight.classifier_stats[0]
        assert cs.block_count == 1
        assert cs.block_rate == 0.5

    def test_classifier_errors_tracked(self):
        records = [
            self._make_semantic_record([
                {"name": "topic", "score": 0.0, "threshold": 0.5,
                 "blocked": False, "label": "error", "duration_ms": 5.0,
                 "error": "model not loaded"}
            ]),
            self._make_semantic_record([
                {"name": "topic", "score": 0.2, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 20.0}
            ]),
        ]
        insight = find_semantic_insights(records)
        cs = insight.classifier_stats[0]
        assert cs.error_count == 1
        assert cs.error_rate == 0.5
        # Errored result's score should not be in score distribution
        assert cs.sample_count == 2
        assert len([s for s in [0.2] if s == cs.score_mean]) > 0  # only score=0.2

    def test_multiple_classifiers(self):
        records = [
            self._make_semantic_record([
                {"name": "injection", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 20.0},
                {"name": "topic", "score": 0.3, "threshold": 0.5,
                 "blocked": False, "label": "on_topic", "duration_ms": 15.0},
            ]),
        ]
        insight = find_semantic_insights(records)
        assert len(insight.classifier_stats) == 2
        names = [cs.name for cs in insight.classifier_stats]
        assert "injection" in names
        assert "topic" in names

    def test_ambiguous_zone_detection(self):
        """Scores within ±20% of threshold are flagged as ambiguous."""
        # threshold=0.5, so ambiguous zone is 0.4–0.6
        records = [
            self._make_semantic_record([
                {"name": "clf", "score": 0.45, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 10.0}
            ]),
            self._make_semantic_record([
                {"name": "clf", "score": 0.55, "threshold": 0.5,
                 "blocked": True, "label": "risky", "duration_ms": 10.0}
            ]),
            self._make_semantic_record([
                {"name": "clf", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 10.0}
            ]),
        ]
        insight = find_semantic_insights(records)
        cs = insight.classifier_stats[0]
        assert cs.ambiguous_count == 2  # 0.45 and 0.55 are in zone
        assert abs(cs.ambiguous_rate - 2 / 3) < 0.01

    def test_score_percentiles(self):
        scores = [0.01 * i for i in range(101)]  # 0.00 to 1.00
        records = [
            self._make_semantic_record([
                {"name": "clf", "score": s, "threshold": 0.5,
                 "blocked": s >= 0.5, "label": "test", "duration_ms": 10.0}
            ])
            for s in scores
        ]
        insight = find_semantic_insights(records)
        cs = insight.classifier_stats[0]
        assert 0.49 <= cs.score_p50 <= 0.51
        assert 0.94 <= cs.score_p95 <= 0.96
        assert cs.score_p99 >= 0.98

    def test_latency_stats(self):
        latencies = [10.0, 20.0, 30.0, 40.0, 100.0]
        records = [
            self._make_semantic_record([
                {"name": "clf", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": lat}
            ])
            for lat in latencies
        ]
        insight = find_semantic_insights(records)
        cs = insight.classifier_stats[0]
        assert cs.latency_mean_ms == 40.0  # mean of 10,20,30,40,100
        assert cs.latency_p95_ms == 100.0

    def test_cross_classifier_agreement(self):
        """Two classifiers that both block the same request are tracked."""
        records = [
            # Both block
            self._make_semantic_record([
                {"name": "clf_a", "score": 0.9, "threshold": 0.5,
                 "blocked": True, "label": "bad", "duration_ms": 10.0},
                {"name": "clf_b", "score": 0.8, "threshold": 0.5,
                 "blocked": True, "label": "bad", "duration_ms": 10.0},
            ], status="blocked", blocking_classifier="clf_a"),
            # Both block again
            self._make_semantic_record([
                {"name": "clf_a", "score": 0.7, "threshold": 0.5,
                 "blocked": True, "label": "bad", "duration_ms": 10.0},
                {"name": "clf_b", "score": 0.6, "threshold": 0.5,
                 "blocked": True, "label": "bad", "duration_ms": 10.0},
            ], status="blocked", blocking_classifier="clf_a"),
            # Only one blocks
            self._make_semantic_record([
                {"name": "clf_a", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 10.0},
                {"name": "clf_b", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 10.0},
            ]),
        ]
        insight = find_semantic_insights(records)
        assert len(insight.classifier_agreement) == 1
        ag = insight.classifier_agreement[0]
        assert ag["co_block_count"] == 2
        assert ag["co_occurrence_count"] == 3
        assert abs(ag["agreement_rate"] - 2 / 3) < 0.01

    def test_no_agreement_when_single_classifier(self):
        records = [
            self._make_semantic_record([
                {"name": "solo", "score": 0.9, "threshold": 0.5,
                 "blocked": True, "label": "bad", "duration_ms": 10.0},
            ], status="blocked", blocking_classifier="solo"),
        ]
        insight = find_semantic_insights(records)
        assert insight.classifier_agreement == []

    def test_mixed_records_only_semantic_counted(self):
        """Records without airlock_semantic are ignored."""
        records = [
            {"success": True, "model": "gpt-4o"},  # no semantic data
            {"success": True, "model": "gpt-4o"},  # no semantic data
            self._make_semantic_record([
                {"name": "clf", "score": 0.1, "threshold": 0.5,
                 "blocked": False, "label": "safe", "duration_ms": 10.0}
            ]),
        ]
        insight = find_semantic_insights(records)
        assert insight.total_evaluated == 1


# ---------------------------------------------------------------------------
# generate_hypotheses()
# ---------------------------------------------------------------------------
class TestGenerateHypotheses:
    def test_from_cache_opportunities(self):
        from airlock.slow.analyzer import CacheOpportunity, Optimization, Trend

        records = [{"success": True, "total_tokens": 1000} for _ in range(10)]
        cache_opps = [
            CacheOpportunity(
                pattern="test",
                fingerprint="abc",
                frequency=5,
                model="gpt-4o",
                estimated_token_savings=500,
                estimated_cost_savings_pct=5.0,
            )
        ]
        hypotheses = generate_hypotheses(records, [], cache_opps, [])
        assert len(hypotheses) >= 1
        assert any("caching" in h.statement.lower() for h in hypotheses)

    def test_from_error_models(self):
        from airlock.slow.analyzer import CacheOpportunity, Optimization, Trend

        records = []
        optimizations = [
            Optimization(
                category="reliability",
                description="test",
                impact="high",
                evidence={"model": "bad-model", "error_rate": 0.5, "total": 100},
            )
        ]
        hypotheses = generate_hypotheses(records, optimizations, [], [])
        assert len(hypotheses) >= 1
        assert any("failover" in h.statement.lower() for h in hypotheses)

    def test_empty_inputs(self):
        hypotheses = generate_hypotheses([], [], [], [])
        assert hypotheses == []

    def test_from_semantic_high_block_rate(self):
        """High block rate triggers threshold tuning hypothesis."""
        semantic = SemanticInsight(
            total_evaluated=100,
            total_blocked=20,
            overall_block_rate=0.2,
            classifier_stats=[
                ClassifierStats(
                    name="injection",
                    sample_count=100,
                    block_count=15,
                    block_rate=0.15,
                    error_count=0,
                    error_rate=0.0,
                    score_mean=0.3,
                    score_p50=0.25,
                    score_p95=0.65,
                    score_p99=0.9,
                    current_threshold=0.5,
                    latency_mean_ms=20.0,
                    latency_p95_ms=45.0,
                    ambiguous_count=10,
                    ambiguous_rate=0.1,
                ),
            ],
        )
        hypotheses = generate_hypotheses([], [], [], [], semantic)
        threshold_hyps = [
            h for h in hypotheses if "threshold" in h.statement.lower()
        ]
        assert len(threshold_hyps) >= 1
        assert "injection" in threshold_hyps[0].statement

    def test_from_semantic_high_ambiguity(self):
        """High ambiguous rate triggers escalation hypothesis."""
        semantic = SemanticInsight(
            total_evaluated=100,
            total_blocked=0,
            overall_block_rate=0.0,
            classifier_stats=[
                ClassifierStats(
                    name="topic",
                    sample_count=100,
                    block_count=0,
                    block_rate=0.0,
                    error_count=0,
                    error_rate=0.0,
                    score_mean=0.45,
                    score_p50=0.45,
                    score_p95=0.55,
                    score_p99=0.6,
                    current_threshold=0.5,
                    latency_mean_ms=15.0,
                    latency_p95_ms=30.0,
                    ambiguous_count=30,
                    ambiguous_rate=0.30,
                ),
            ],
        )
        hypotheses = generate_hypotheses([], [], [], [], semantic)
        ambig_hyps = [
            h for h in hypotheses if "ambiguous" in h.statement.lower()
        ]
        assert len(ambig_hyps) >= 1

    def test_from_semantic_high_error_rate(self):
        """High classifier error rate triggers reliability hypothesis."""
        semantic = SemanticInsight(
            total_evaluated=50,
            total_blocked=0,
            overall_block_rate=0.0,
            classifier_stats=[
                ClassifierStats(
                    name="broken_clf",
                    sample_count=50,
                    block_count=0,
                    block_rate=0.0,
                    error_count=10,
                    error_rate=0.2,
                    score_mean=0.1,
                    score_p50=0.1,
                    score_p95=0.2,
                    score_p99=0.3,
                    current_threshold=0.5,
                    latency_mean_ms=10.0,
                    latency_p95_ms=20.0,
                    ambiguous_count=0,
                    ambiguous_rate=0.0,
                ),
            ],
        )
        hypotheses = generate_hypotheses([], [], [], [], semantic)
        error_hyps = [
            h for h in hypotheses if "failing" in h.statement.lower()
        ]
        assert len(error_hyps) >= 1
        assert "broken_clf" in error_hyps[0].statement

    def test_from_semantic_high_latency(self):
        """High classifier latency triggers optimization hypothesis."""
        semantic = SemanticInsight(
            total_evaluated=50,
            total_blocked=0,
            overall_block_rate=0.0,
            classifier_stats=[
                ClassifierStats(
                    name="slow_clf",
                    sample_count=50,
                    block_count=0,
                    block_rate=0.0,
                    error_count=0,
                    error_rate=0.0,
                    score_mean=0.1,
                    score_p50=0.1,
                    score_p95=0.2,
                    score_p99=0.3,
                    current_threshold=0.5,
                    latency_mean_ms=3000.0,
                    latency_p95_ms=6000.0,
                    ambiguous_count=0,
                    ambiguous_rate=0.0,
                ),
            ],
        )
        hypotheses = generate_hypotheses([], [], [], [], semantic)
        lat_hyps = [
            h for h in hypotheses if "latency" in h.statement.lower()
        ]
        assert len(lat_hyps) >= 1
        assert "slow_clf" in lat_hyps[0].statement

    def test_semantic_none_no_hypotheses(self):
        """When semantic is None, no semantic hypotheses are generated."""
        hypotheses = generate_hypotheses([], [], [], [], None)
        assert hypotheses == []

    def test_semantic_low_sample_count_no_hypothesis(self):
        """Small sample counts don't trigger hypotheses (avoid noise)."""
        semantic = SemanticInsight(
            total_evaluated=5,
            total_blocked=3,
            overall_block_rate=0.6,
            classifier_stats=[
                ClassifierStats(
                    name="tiny",
                    sample_count=5,  # below the 20 threshold
                    block_count=3,
                    block_rate=0.6,
                    error_count=0,
                    error_rate=0.0,
                    score_mean=0.6,
                    score_p50=0.6,
                    score_p95=0.9,
                    score_p99=0.95,
                    current_threshold=0.5,
                    latency_mean_ms=10.0,
                    latency_p95_ms=20.0,
                    ambiguous_count=2,
                    ambiguous_rate=0.4,
                ),
            ],
        )
        hypotheses = generate_hypotheses([], [], [], [], semantic)
        # block_rate and ambiguous_rate are high but sample_count < 20
        assert hypotheses == []


# ---------------------------------------------------------------------------
# analyze() — full pipeline
# ---------------------------------------------------------------------------
class TestAnalyze:
    def test_full_pipeline(self, populated_log_dir):
        report = analyze(days=7)
        assert isinstance(report, AnalysisReport)
        assert report.total_requests > 0
        assert report.generated_at.endswith("Z")
        assert report.period_start.endswith("Z")
        assert "total_requests" in report.summary

    def test_empty_logs(self, log_dir):
        report = analyze(days=7)
        assert report.total_requests == 0
        assert report.optimizations == []
        assert report.cache_opportunities == []
        assert report.trends == []
        assert report.semantic_insights is None
        assert report.hypotheses == []
