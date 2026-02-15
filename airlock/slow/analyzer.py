"""
Airlock Slow — Log analysis engine.

Reads the JSONL logs produced by the enterprise logger and performs
offline analysis across four dimensions:

  1. Optimizations  — reliability, latency, and cost patterns that can
                      be improved (high error-rate models, slow p95s,
                      outlier token usage).
  2. Cache opps     — repeated identical prompts that would benefit from
                      local or provider-side caching.
  3. Trends         — directional shifts in volume, model share, error
                      rate, latency, and user concentration.
  4. Hypotheses     — testable statements derived from the data with
                      a confidence score and a concrete test proposal.

This is the "slow" counterpart to the real-time fast subsystem.  It is
designed to be run periodically (cron, CI, or ad-hoc) and produces a
structured AnalysisReport that can be serialized to JSON or rendered as
human-readable text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("airlock.slow")

LOG_DIR = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------
@dataclass
class Optimization:
    category: str                   # reliability | performance | cost
    description: str
    impact: str                     # high | medium | low
    evidence: dict[str, Any]


@dataclass
class CacheOpportunity:
    pattern: str
    fingerprint: str                # hash of repeated content
    frequency: int
    model: str
    estimated_token_savings: int
    estimated_cost_savings_pct: float


@dataclass
class Trend:
    metric: str
    direction: str                  # increasing | decreasing | stable
    magnitude: float                # percent change
    period_days: int
    details: dict[str, Any]


@dataclass
class Hypothesis:
    statement: str
    evidence: dict[str, Any]
    confidence: float               # 0.0 → 1.0
    test_proposal: str


@dataclass
class AnalysisReport:
    generated_at: str
    period_start: str
    period_end: str
    total_requests: int
    optimizations: list[Optimization] = field(default_factory=list)
    cache_opportunities: list[CacheOpportunity] = field(default_factory=list)
    trends: list[Trend] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Log loading
# ---------------------------------------------------------------------------
def _load_logs(days: int = 7) -> list[dict[str, Any]]:
    """Load JSONL records from the last *days* days."""
    records: list[dict[str, Any]] = []
    today = datetime.utcnow().date()

    for i in range(days):
        day = today - timedelta(days=i)
        log_path = LOG_DIR / f"airlock-{day.isoformat()}.jsonl"
        if not log_path.exists():
            continue
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return records


def _fingerprint_messages(messages: list[dict] | None) -> str:
    """Content hash for deduplication detection."""
    if not messages:
        return ""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = " ".join(text_parts)
        parts.append(f"{role}:{content}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Dimension 1 — Optimizations
# ---------------------------------------------------------------------------
def find_optimizations(records: list[dict]) -> list[Optimization]:
    optimizations: list[Optimization] = []
    if not records:
        return optimizations

    # High error-rate models
    model_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"success": 0, "failure": 0}
    )
    for r in records:
        model = r.get("model", "unknown")
        if r.get("success"):
            model_stats[model]["success"] += 1
        else:
            model_stats[model]["failure"] += 1

    for model, stats in model_stats.items():
        total = stats["success"] + stats["failure"]
        if total >= 10:
            error_rate = stats["failure"] / total
            if error_rate > 0.1:
                optimizations.append(Optimization(
                    category="reliability",
                    description=(
                        f"Model '{model}' has a {error_rate:.0%} error rate "
                        f"over {total} requests"
                    ),
                    impact="high" if error_rate > 0.3 else "medium",
                    evidence={
                        "model": model,
                        "total": total,
                        "error_rate": round(error_rate, 3),
                    },
                ))

    # Slow p95 latency
    model_latencies: dict[str, list[float]] = defaultdict(list)
    for r in records:
        dur = r.get("duration_ms")
        if dur and r.get("success"):
            model_latencies[r.get("model", "unknown")].append(dur)

    for model, latencies in model_latencies.items():
        if len(latencies) >= 5:
            p95 = sorted(latencies)[int(len(latencies) * 0.95)]
            median = statistics.median(latencies)
            if p95 > 30_000:
                optimizations.append(Optimization(
                    category="performance",
                    description=(
                        f"Model '{model}' p95 latency is {p95:.0f} ms "
                        f"(median {median:.0f} ms)"
                    ),
                    impact="high" if p95 > 60_000 else "medium",
                    evidence={
                        "model": model,
                        "p95_ms": round(p95),
                        "median_ms": round(median),
                        "samples": len(latencies),
                    },
                ))

    # Outlier token usage
    model_tokens: dict[str, list[int]] = defaultdict(list)
    for r in records:
        total_tokens = r.get("total_tokens", 0)
        if total_tokens and r.get("success"):
            model_tokens[r.get("model", "unknown")].append(total_tokens)

    for model, tokens in model_tokens.items():
        if len(tokens) >= 10:
            p95_tokens = sorted(tokens)[int(len(tokens) * 0.95)]
            median_tokens = statistics.median(tokens)
            if median_tokens > 0 and p95_tokens > 10 * median_tokens:
                optimizations.append(Optimization(
                    category="cost",
                    description=(
                        f"Model '{model}' has outlier token usage: "
                        f"p95={p95_tokens} vs median={median_tokens:.0f}"
                    ),
                    impact="medium",
                    evidence={
                        "model": model,
                        "p95_tokens": p95_tokens,
                        "median_tokens": round(median_tokens),
                    },
                ))

    return optimizations


# ---------------------------------------------------------------------------
# Dimension 2 — Cache opportunities
# ---------------------------------------------------------------------------
def find_cache_opportunities(records: list[dict]) -> list[CacheOpportunity]:
    fingerprint_info: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "model": "", "total_tokens": 0}
    )

    for r in records:
        if not r.get("success"):
            continue
        fp = _fingerprint_messages(r.get("messages"))
        if not fp:
            continue
        info = fingerprint_info[fp]
        info["count"] += 1
        info["model"] = r.get("model", "unknown")
        info["total_tokens"] += r.get("total_tokens", 0)

    total_ok = sum(1 for r in records if r.get("success"))
    opportunities: list[CacheOpportunity] = []

    for fp, info in fingerprint_info.items():
        if info["count"] >= 3:
            savings_pct = (
                (info["count"] - 1) / total_ok * 100 if total_ok > 0 else 0
            )
            opportunities.append(CacheOpportunity(
                pattern=f"Repeated prompt (seen {info['count']} times)",
                fingerprint=fp,
                frequency=info["count"],
                model=info["model"],
                estimated_token_savings=(
                    info["total_tokens"]
                    - info["total_tokens"] // info["count"]
                ),
                estimated_cost_savings_pct=round(savings_pct, 2),
            ))

    opportunities.sort(key=lambda o: o.frequency, reverse=True)
    return opportunities[:20]


# ---------------------------------------------------------------------------
# Dimension 3 — Trends
# ---------------------------------------------------------------------------
def find_trends(records: list[dict], period_days: int = 7) -> list[Trend]:
    trends: list[Trend] = []
    if not records:
        return trends

    midpoint = period_days / 2
    now = datetime.utcnow()
    first_half: list[dict] = []
    second_half: list[dict] = []

    for r in records:
        ts = r.get("timestamp", "")
        try:
            record_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_days = (
                (now - record_time.replace(tzinfo=None)).total_seconds() / 86400
            )
            if age_days > midpoint:
                first_half.append(r)
            else:
                second_half.append(r)
        except (ValueError, TypeError):
            continue

    if not first_half or not second_half:
        return trends

    # Volume trend
    vol_change = (
        (len(second_half) - len(first_half))
        / max(len(first_half), 1)
        * 100
    )
    if abs(vol_change) > 10:
        trends.append(Trend(
            metric="request_volume",
            direction="increasing" if vol_change > 0 else "decreasing",
            magnitude=round(abs(vol_change), 1),
            period_days=period_days,
            details={
                "first_half": len(first_half),
                "second_half": len(second_half),
            },
        ))

    # Per-model share shift
    first_models = Counter(r.get("model", "unknown") for r in first_half)
    second_models = Counter(r.get("model", "unknown") for r in second_half)
    for model in set(first_models) | set(second_models):
        first_pct = (
            first_models.get(model, 0) / max(len(first_half), 1) * 100
        )
        second_pct = (
            second_models.get(model, 0) / max(len(second_half), 1) * 100
        )
        shift = second_pct - first_pct
        if abs(shift) > 5:
            trends.append(Trend(
                metric=f"model_share:{model}",
                direction="increasing" if shift > 0 else "decreasing",
                magnitude=round(abs(shift), 1),
                period_days=period_days,
                details={
                    "first_half_pct": round(first_pct, 1),
                    "second_half_pct": round(second_pct, 1),
                },
            ))

    # Error-rate trend
    first_errors = sum(1 for r in first_half if not r.get("success"))
    second_errors = sum(1 for r in second_half if not r.get("success"))
    first_err_rate = first_errors / max(len(first_half), 1) * 100
    second_err_rate = second_errors / max(len(second_half), 1) * 100
    err_shift = second_err_rate - first_err_rate
    if abs(err_shift) > 2:
        trends.append(Trend(
            metric="error_rate",
            direction="increasing" if err_shift > 0 else "decreasing",
            magnitude=round(abs(err_shift), 1),
            period_days=period_days,
            details={
                "first_half_rate": round(first_err_rate, 1),
                "second_half_rate": round(second_err_rate, 1),
            },
        ))

    # Latency trend
    first_lat = [
        r["duration_ms"]
        for r in first_half
        if r.get("duration_ms") and r.get("success")
    ]
    second_lat = [
        r["duration_ms"]
        for r in second_half
        if r.get("duration_ms") and r.get("success")
    ]
    if first_lat and second_lat:
        first_median = statistics.median(first_lat)
        second_median = statistics.median(second_lat)
        if first_median > 0:
            lat_change = (second_median - first_median) / first_median * 100
            if abs(lat_change) > 15:
                trends.append(Trend(
                    metric="median_latency",
                    direction=(
                        "increasing" if lat_change > 0 else "decreasing"
                    ),
                    magnitude=round(abs(lat_change), 1),
                    period_days=period_days,
                    details={
                        "first_half_ms": round(first_median),
                        "second_half_ms": round(second_median),
                    },
                ))

    # User concentration
    second_users = Counter(
        r.get("user") for r in second_half if r.get("user")
    )
    if second_users:
        total_second = sum(second_users.values())
        top_user, top_count = second_users.most_common(1)[0]
        top_pct = top_count / total_second * 100
        if top_pct > 50:
            trends.append(Trend(
                metric="user_concentration",
                direction="increasing",
                magnitude=round(top_pct, 1),
                period_days=period_days,
                details={
                    "top_user": top_user,
                    "top_user_pct": round(top_pct, 1),
                    "total_users": len(second_users),
                },
            ))

    return trends


# ---------------------------------------------------------------------------
# Dimension 4 — Hypotheses
# ---------------------------------------------------------------------------
def generate_hypotheses(
    records: list[dict],
    optimizations: list[Optimization],
    cache_opps: list[CacheOpportunity],
    trends: list[Trend],
) -> list[Hypothesis]:
    hypotheses: list[Hypothesis] = []

    # From cache opportunities
    total_cacheable_tokens = sum(c.estimated_token_savings for c in cache_opps)
    if total_cacheable_tokens > 0:
        total_tokens = sum(
            r.get("total_tokens", 0) for r in records if r.get("success")
        )
        savings_pct = total_cacheable_tokens / max(total_tokens, 1) * 100
        if savings_pct > 1:
            hypotheses.append(Hypothesis(
                statement=(
                    f"Enabling prompt caching could reduce token usage "
                    f"by ~{savings_pct:.1f}%"
                ),
                evidence={
                    "cacheable_patterns": len(cache_opps),
                    "token_savings": total_cacheable_tokens,
                    "total_tokens": total_tokens,
                },
                confidence=min(0.9, savings_pct / 20),
                test_proposal=(
                    "Enable LiteLLM cache for the top repeated prompts and "
                    "measure token usage reduction over a 24-hour period."
                ),
            ))

    # From error patterns
    error_models = [o for o in optimizations if o.category == "reliability"]
    if error_models:
        worst = max(
            error_models, key=lambda o: o.evidence.get("error_rate", 0)
        )
        hypotheses.append(Hypothesis(
            statement=(
                f"Configuring automatic failover for "
                f"'{worst.evidence['model']}' would reduce user-visible "
                f"errors by ~{worst.evidence['error_rate'] * 100:.0f}%"
            ),
            evidence=worst.evidence,
            confidence=0.7,
            test_proposal=(
                f"Enable the circuit breaker for "
                f"'{worst.evidence['model']}' with a fallback model and "
                f"compare error rates before/after over 48 hours."
            ),
        ))

    # From latency trends
    latency_trends = [
        t
        for t in trends
        if t.metric == "median_latency" and t.direction == "increasing"
    ]
    if latency_trends:
        t = latency_trends[0]
        hypotheses.append(Hypothesis(
            statement=(
                f"Median latency increased {t.magnitude:.0f}% over the last "
                f"{t.period_days} days — suggesting provider degradation or "
                f"increased prompt complexity"
            ),
            evidence=t.details,
            confidence=0.6,
            test_proposal=(
                "Compare average prompt length (token count) across the "
                "period to isolate whether the increase is due to larger "
                "prompts or provider slowdown."
            ),
        ))

    # From model concentration shifts
    model_trends = [
        t
        for t in trends
        if t.metric.startswith("model_share:") and t.direction == "increasing"
    ]
    if model_trends:
        t = model_trends[0]
        model_name = t.metric.split(":")[1]
        hypotheses.append(Hypothesis(
            statement=(
                f"Usage of '{model_name}' is increasing "
                f"({t.magnitude:.1f} pp shift). Consider negotiating "
                f"volume pricing or pre-provisioning capacity."
            ),
            evidence=t.details,
            confidence=0.5,
            test_proposal=(
                f"Monitor '{model_name}' usage daily for the next 2 weeks "
                f"to confirm the trend before acting on pricing."
            ),
        ))

    return hypotheses


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def analyze(days: int = 7) -> AnalysisReport:
    """Run the full slow analysis pipeline over the last *days* days."""
    records = _load_logs(days=days)
    now = datetime.utcnow()
    period_start = (now - timedelta(days=days)).isoformat() + "Z"
    period_end = now.isoformat() + "Z"

    optimizations = find_optimizations(records)
    cache_opps = find_cache_opportunities(records)
    trends = find_trends(records, period_days=days)
    hypotheses = generate_hypotheses(records, optimizations, cache_opps, trends)

    # Summary
    success_records = [r for r in records if r.get("success")]
    failure_records = [r for r in records if not r.get("success")]
    models_used = Counter(r.get("model", "unknown") for r in records)
    users_active = len(set(r.get("user") for r in records if r.get("user")))
    total_tokens = sum(r.get("total_tokens", 0) for r in success_records)

    summary = {
        "total_requests": len(records),
        "successful": len(success_records),
        "failed": len(failure_records),
        "error_rate": round(
            len(failure_records) / max(len(records), 1), 3
        ),
        "models_used": dict(models_used.most_common()),
        "active_users": users_active,
        "total_tokens": total_tokens,
        "optimizations_found": len(optimizations),
        "cache_opportunities_found": len(cache_opps),
        "trends_detected": len(trends),
        "hypotheses_generated": len(hypotheses),
    }

    return AnalysisReport(
        generated_at=now.isoformat() + "Z",
        period_start=period_start,
        period_end=period_end,
        total_requests=len(records),
        optimizations=optimizations,
        cache_opportunities=cache_opps,
        trends=trends,
        hypotheses=hypotheses,
        summary=summary,
    )
