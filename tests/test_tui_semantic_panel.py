"""Tests for the Guards screen's Semantic tab (0.5.10 pack B-1, issue #33).

The panel exists to surface a subsystem that shipped in 0.5.9 with no operator
surface at all. Its two load-bearing properties are asserted here rather than
eyeballed, because both are the kind of thing a later refactor quietly breaks:

  - status (verdict) and action (what Airlock did) stay distinct;
  - unavailable is never folded into clean.
"""

from __future__ import annotations

import json

from airlock.semantic_report import build_report
from airlock.tui.semantic_panel import render_semantic_panel


def _record(**semantic):
    base = {
        "timestamp": "2026-08-05T12:00:00Z",
        "request_id": "req-1",
        "model": "gpt-4o",
        "airlock_client": "cli",
        "airlock_semantic": semantic,
    }
    return base


def _write(tmp_path, records):
    path = tmp_path / "airlock-2026-08-05.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return tmp_path


def _report_from(tmp_path, records, days=3650):
    _write(tmp_path, records)
    return build_report(days=days, directory=tmp_path)


class TestVerdictVersusAction:
    def test_detection_in_observe_mode_is_not_shown_as_a_block(self, tmp_path):
        """The exact confusion the separation exists to prevent."""
        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="observe",
                    status="blocked",
                    action="allowed",
                    results=[
                        {"name": "tripwire", "label": "injection", "blocked": True}
                    ],
                )
            ],
        )
        assert report.detections == 1
        assert report.blocked_requests == 0

        panel = render_semantic_panel(report)
        assert "status (verdict)" in panel
        assert "action (what ran)" in panel
        # The reconciling sentence must appear, so an operator reading
        # "blocked" in the status row is not left to infer the difference.
        assert "actually blocked" in panel

    def test_enforce_mode_shows_both_as_blocked(self, tmp_path):
        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="enforce",
                    status="blocked",
                    action="blocked",
                    results=[
                        {"name": "tripwire", "label": "injection", "blocked": True}
                    ],
                )
            ],
        )
        assert report.detections == 1
        assert report.blocked_requests == 1
        panel = render_semantic_panel(report)
        assert "enforce" in panel


class TestUnavailableIsNeverClean:
    def test_unavailable_is_counted_separately_from_clean(self, tmp_path):
        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="observe",
                    status="clean",
                    action="allowed",
                    results=[
                        {
                            "name": "model_armor",
                            "label": "unavailable",
                            "error": "rate_limit",
                            "metadata": {"unavailable_reason": "rate_limit"},
                        }
                    ],
                )
            ],
        )
        summary = report.classifiers["model_armor"]
        assert summary.unavailable == 1
        assert summary.clean == 0
        assert summary.detections == 0

        panel = render_semantic_panel(report)
        assert "Unavailable reasons" in panel
        assert "never counted as clean" in panel

    def test_rate_limit_is_called_out_as_failing_open(self, tmp_path):
        """rate_limit is attacker-inducible and fails open, so it is not
        merely listed alongside ordinary errors."""
        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="observe",
                    status="clean",
                    action="allowed",
                    results=[
                        {
                            "name": "model_armor",
                            "label": "unavailable",
                            "error": "rate_limit",
                            "metadata": {"unavailable_reason": "rate_limit"},
                        }
                    ],
                )
            ],
        )
        panel = render_semantic_panel(report)
        assert "fails open" in panel
        assert "[red bold]" in panel

    def test_ordinary_unavailable_reason_is_not_alarmed(self, tmp_path):
        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="observe",
                    status="clean",
                    action="allowed",
                    results=[
                        {
                            "name": "model_armor",
                            "label": "unavailable",
                            "error": "timeout",
                            "metadata": {"unavailable_reason": "timeout"},
                        }
                    ],
                )
            ],
        )
        panel = render_semantic_panel(report)
        assert "timeout" in panel
        assert "fails open" not in panel


class TestEmptyAndPartialWindows:
    def test_empty_window_does_not_read_as_no_threats(self, tmp_path):
        report = build_report(days=7, directory=tmp_path)
        panel = render_semantic_panel(report)
        assert "No semantic verdicts" in panel
        # An empty registry and a clean window look identical in the data;
        # the panel must not let them look identical to the operator.
        assert "not the same as 'no threats'" in panel

    def test_truncated_window_is_disclosed(self, tmp_path):
        report = build_report(days=7, directory=tmp_path)
        report.requests_with_verdicts = 5
        report.window = {"truncated": True, "limit_hit": "max_records"}
        panel = render_semantic_panel(report)
        assert "partial window" in panel
        assert "max_records" in panel


class TestColumnAlignment:
    def test_styled_unavailable_column_stays_aligned(self, tmp_path):
        """Rich tags are zero-width on screen but count toward f-string widths.

        Styling a cell before padding it silently shifts every column to its
        right — visible only when a classifier actually has unavailable runs.
        """
        from rich.console import Console
        from rich.text import Text

        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="observe",
                    status="clean",
                    action="allowed",
                    results=[{"name": "aaa", "label": "clean", "blocked": False}],
                ),
                _record(
                    mode="observe",
                    status="no_verdict",
                    action="allowed",
                    results=[
                        {"name": "bbb", "label": "unavailable", "error": "timeout"}
                    ],
                ),
            ],
        )
        panel = render_semantic_panel(report)
        console = Console(width=200)
        rendered = [
            line.rstrip()
            for line in Text.from_markup(panel).plain.split("\n")
            if line.startswith("  aaa") or line.startswith("  bbb")
        ]
        assert len(rendered) == 2, rendered
        # Same visible length once markup is stripped: columns line up.
        assert len(rendered[0]) == len(rendered[1]), rendered
        assert console  # console constructed to prove markup is well-formed


class TestCategoryHistogram:
    def test_categories_are_aggregated_across_classifiers(self, tmp_path):
        report = _report_from(
            tmp_path,
            [
                _record(
                    mode="observe",
                    status="blocked",
                    action="allowed",
                    results=[
                        {
                            "name": "tripwire",
                            "label": "injection",
                            "blocked": True,
                            "metadata": {"categories": ["instruction_override"]},
                        },
                        {
                            "name": "heuristic",
                            "label": "injection",
                            "blocked": True,
                            "metadata": {"categories": ["instruction_override"]},
                        },
                    ],
                )
            ],
        )
        panel = render_semantic_panel(report)
        assert "instruction_override" in panel
        assert "Categories" in panel


class TestNeverRendersPromptText:
    def test_panel_contains_no_record_content(self, tmp_path):
        """Reports carry counts and identifiers, never prompt text."""
        secret = "ThisIsThePromptBody"
        report = _report_from(
            tmp_path,
            [
                {
                    **_record(
                        mode="observe",
                        status="blocked",
                        action="allowed",
                        results=[
                            {"name": "tripwire", "label": "injection", "blocked": True}
                        ],
                    ),
                    "messages": [{"role": "user", "content": secret}],
                }
            ],
        )
        panel = render_semantic_panel(report)
        assert secret not in panel
