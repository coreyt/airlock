"""Tests for airlock/slow/analyzer.py"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from airlock.slow.analyzer import (
    AnalysisReport,
    _fingerprint_messages,
    _load_logs,
    analyze,
    find_cache_opportunities,
    find_optimizations,
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
        assert report.hypotheses == []
