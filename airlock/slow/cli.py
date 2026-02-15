"""
Airlock Slow — CLI entry point for offline log analysis.

Usage:
    airlock-analyze                # analyze last 7 days
    airlock-analyze --days 30      # analyze last 30 days
    airlock-analyze --json         # machine-readable JSON output
    airlock-analyze -o report.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .analyzer import analyze


def _format_text(report) -> str:
    """Render the analysis report as human-readable text."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("  AIRLOCK SLOW ANALYSIS REPORT")
    lines.append(f"  Generated : {report.generated_at}")
    lines.append(f"  Period    : {report.period_start}  ->  {report.period_end}")
    lines.append(f"  Requests  : {report.total_requests}")
    lines.append("=" * 72)

    # ---- Summary ----
    s = report.summary
    lines.append("")
    lines.append("  SUMMARY")
    lines.append("  " + "-" * 38)
    lines.append(f"    Successful requests : {s.get('successful', 0)}")
    lines.append(f"    Failed requests     : {s.get('failed', 0)}")
    lines.append(f"    Error rate          : {s.get('error_rate', 0):.1%}")
    lines.append(f"    Active users        : {s.get('active_users', 0)}")
    lines.append(f"    Total tokens        : {s.get('total_tokens', 0):,}")
    lines.append(f"    Models used         : {s.get('models_used', {})}")

    # ---- Optimizations ----
    if report.optimizations:
        lines.append("")
        lines.append(
            f"  OPTIMIZATIONS ({len(report.optimizations)} found)"
        )
        lines.append("  " + "-" * 38)
        for i, opt in enumerate(report.optimizations, 1):
            lines.append(
                f"    {i}. [{opt.impact.upper()}] {opt.description}"
            )
            lines.append(f"       Category: {opt.category}")

    # ---- Cache opportunities ----
    if report.cache_opportunities:
        lines.append("")
        lines.append(
            f"  CACHE OPPORTUNITIES "
            f"({len(report.cache_opportunities)} found)"
        )
        lines.append("  " + "-" * 38)
        for i, cache in enumerate(report.cache_opportunities, 1):
            lines.append(f"    {i}. {cache.pattern}")
            lines.append(
                f"       Model: {cache.model}  |  "
                f"Token savings: {cache.estimated_token_savings:,}"
            )
            lines.append(
                f"       Est. cost reduction: "
                f"{cache.estimated_cost_savings_pct:.1f}%"
            )

    # ---- Trends ----
    if report.trends:
        lines.append("")
        lines.append(f"  TRENDS ({len(report.trends)} detected)")
        lines.append("  " + "-" * 38)
        _arrows = {"increasing": "^", "decreasing": "v", "stable": "="}
        for i, trend in enumerate(report.trends, 1):
            arrow = _arrows.get(trend.direction, "?")
            lines.append(
                f"    {i}. [{arrow}] {trend.metric}: "
                f"{trend.direction} {trend.magnitude:.1f}% "
                f"over {trend.period_days}d"
            )

    # ---- Hypotheses ----
    if report.hypotheses:
        lines.append("")
        lines.append(
            f"  HYPOTHESES ({len(report.hypotheses)} generated)"
        )
        lines.append("  " + "-" * 38)
        for i, hyp in enumerate(report.hypotheses, 1):
            lines.append(f"    {i}. {hyp.statement}")
            lines.append(f"       Confidence : {hyp.confidence:.0%}")
            lines.append(f"       Test       : {hyp.test_proposal}")

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="airlock-analyze",
        description=(
            "Airlock Slow — offline log analysis, trend detection, "
            "and hypothesis generation"
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of logs to analyze (default: 7)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output raw JSON instead of formatted text",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Write report to file instead of stdout",
    )
    args = parser.parse_args()

    report = analyze(days=args.days)

    if args.json_output:
        output = json.dumps(asdict(report), indent=2, default=str)
    else:
        output = _format_text(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output + "\n")
        print(f"Report written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
