"""Aggregate semantic-classifier verdicts out of the request JSONL.

This is the review tool for an **observe window**. With the local operational
corpus declined and semantic providers optionally disabled, observe-mode output
is the only source of false-positive evidence drawn from real traffic — and
without a way to read it, running in observe mode produces data nobody looks at.

What it answers, in priority order:

**What did the classifiers flag, and would any of it have blocked real work?**
Detections are grouped by classifier and by tripwire category so an operator can
see whether the flags cluster on a pattern that matches ordinary traffic.

**How often was the classifier unable to answer?** Unavailability fails open by
default, so a provider outage looks like quiet, healthy traffic. The
`unavailable` breakdown by reason is the alerting signal — in particular
`rate_limit`, which is the one an attacker can induce deliberately.

**What did Airlock actually do?** `status` is the classifier verdict; `action`
is the outcome. They differ in every mode except enforce, and the report keeps
them separate so an observed detection is never miscounted as a block.

The report contains **no prompt text** — classifiers never record it, and this
tool only reads what they wrote. Request IDs are included so a specific case can
be looked up deliberately, rather than by pasting content into a report.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from airlock.log_query import LogPage, LogQuery, query_logs

#: Verdict-bearing field written by the semantic guard.
SEMANTIC_KEY = "airlock_semantic"


def _semantic(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the semantic block, whether top-level or under metadata."""
    value = record.get(SEMANTIC_KEY)
    if isinstance(value, dict):
        return value
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        nested = metadata.get(SEMANTIC_KEY)
        if isinstance(nested, dict):
            return nested
    return None


def has_semantic_verdict(record: dict[str, Any]) -> bool:
    return _semantic(record) is not None


@dataclass
class ClassifierSummary:
    name: str
    runs: int = 0
    detections: int = 0
    clean: int = 0
    unavailable: int = 0
    errors: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    confidence: Counter = field(default_factory=Counter)
    latency_ms_total: float = 0.0

    @property
    def detection_rate(self) -> float:
        answered = self.detections + self.clean
        return round(self.detections / answered, 4) if answered else 0.0

    @property
    def unavailable_rate(self) -> float:
        return round(self.unavailable / self.runs, 4) if self.runs else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return round(self.latency_ms_total / self.runs, 2) if self.runs else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runs": self.runs,
            "detections": self.detections,
            "clean": self.clean,
            "unavailable": self.unavailable,
            "detection_rate": self.detection_rate,
            "unavailable_rate": self.unavailable_rate,
            "mean_latency_ms": self.mean_latency_ms,
            "categories": dict(self.categories.most_common()),
            "confidence": dict(self.confidence.most_common()),
            "errors": dict(self.errors.most_common()),
        }


@dataclass
class SemanticReport:
    days: int
    requests_with_verdicts: int = 0
    modes: Counter = field(default_factory=Counter)
    actions: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    input_kinds: Counter = field(default_factory=Counter)
    selection: Counter = field(default_factory=Counter)
    unavailable_reasons: Counter = field(default_factory=Counter)
    short_circuited: Counter = field(default_factory=Counter)
    classifiers: dict[str, ClassifierSummary] = field(default_factory=dict)
    detection_samples: list[dict[str, Any]] = field(default_factory=list)
    window: dict[str, Any] = field(default_factory=dict)

    @property
    def detections(self) -> int:
        return self.statuses.get("blocked", 0)

    @property
    def blocked_requests(self) -> int:
        """Requests actually rejected — `action`, never `status`."""
        return self.actions.get("blocked", 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "requests_with_verdicts": self.requests_with_verdicts,
            "detections": self.detections,
            "blocked_requests": self.blocked_requests,
            "modes": dict(self.modes),
            "actions": dict(self.actions),
            "statuses": dict(self.statuses),
            "input_kinds": dict(self.input_kinds),
            "selection": dict(self.selection),
            "unavailable_reasons": dict(self.unavailable_reasons.most_common()),
            "short_circuited": dict(self.short_circuited.most_common()),
            "classifiers": [c.as_dict() for c in self.classifiers.values()],
            "detection_samples": self.detection_samples,
            "window": self.window,
        }


def build_report(
    days: int = 7,
    *,
    directory: Any = None,
    max_samples: int = 25,
    page: LogPage | None = None,
) -> SemanticReport:
    """Aggregate semantic verdicts over the requested window."""
    if page is None:
        page = query_logs(
            LogQuery(days=days, predicate=has_semantic_verdict, directory=directory)
        )

    report = SemanticReport(days=days)
    report.window = page.as_metadata()

    for record in page.records:
        semantic = _semantic(record)
        if semantic is None:
            continue
        report.requests_with_verdicts += 1
        report.modes[str(semantic.get("mode", "unknown"))] += 1
        report.actions[str(semantic.get("action", "unknown"))] += 1
        report.statuses[str(semantic.get("status", "unknown"))] += 1
        report.input_kinds[str(semantic.get("input_kind", "unknown"))] += 1
        report.selection[str(semantic.get("selection", "unknown"))] += 1
        for entry in semantic.get("short_circuited") or []:
            if isinstance(entry, dict) and entry.get("name"):
                report.short_circuited[str(entry["name"])] += 1

        for result in semantic.get("results") or []:
            if not isinstance(result, dict):
                continue
            name = str(result.get("name", "unknown"))
            summary = report.classifiers.setdefault(name, ClassifierSummary(name))
            summary.runs += 1
            summary.latency_ms_total += float(result.get("duration_ms") or 0.0)

            label = str(result.get("label", ""))
            if label == "unavailable" or result.get("error"):
                summary.unavailable += 1
                error = str(result.get("error") or "unknown")
                summary.errors[error] += 1
                meta = result.get("metadata")
                reason = (
                    meta.get("unavailable_reason") if isinstance(meta, dict) else None
                )
                report.unavailable_reasons[str(reason or error)] += 1
            elif result.get("blocked"):
                summary.detections += 1
            else:
                summary.clean += 1

            meta = result.get("metadata")
            if isinstance(meta, dict):
                for category in meta.get("categories") or []:
                    summary.categories[str(category)] += 1
                for provider in meta.get("provider_results") or []:
                    if isinstance(provider, dict) and provider.get("confidence"):
                        summary.confidence[str(provider["confidence"])] += 1

        if (
            semantic.get("status") == "blocked"
            and len(report.detection_samples) < max_samples
        ):
            # Identifiers only — never prompt text. A reviewer looks the request
            # up deliberately rather than reading content out of a report.
            report.detection_samples.append(
                {
                    "timestamp": record.get("timestamp"),
                    "request_id": record.get("request_id"),
                    "client": record.get("airlock_client"),
                    "model": record.get("model"),
                    "action": semantic.get("action"),
                    "blocking_classifier": semantic.get("blocking_classifier"),
                    "input_kind": semantic.get("input_kind"),
                }
            )

    return report


def render_text(report: SemanticReport) -> str:
    """Human-readable rendering for the CLI."""
    lines: list[str] = []
    add = lines.append

    add("Semantic classifier report")
    add("=" * 60)
    add(f"Window:                 last {report.days} day(s)")
    add(f"Requests with verdicts: {report.requests_with_verdicts:,}")

    if report.window.get("truncated"):
        add(
            f"  ! window TRUNCATED ({report.window.get('limit_hit')}) — "
            "these totals cover only part of the requested period"
        )
    if not report.requests_with_verdicts:
        add("")
        add("No semantic verdicts found. Either no classifier is registered, or")
        add("the guard has not run over this window.")
        return "\n".join(lines)

    add(f"Detections (verdict):   {report.detections:,}")
    add(f"Blocked (action):       {report.blocked_requests:,}")
    if report.detections and not report.blocked_requests:
        add("  (observe/shadow mode — nothing was actually rejected)")

    add("")
    add(f"Modes:        {dict(report.modes)}")
    add(f"Actions:      {dict(report.actions)}")
    add(f"Input kinds:  {dict(report.input_kinds)}")
    if report.short_circuited:
        add(f"Short-circuited by light tier: {dict(report.short_circuited)}")

    if report.unavailable_reasons:
        add("")
        add("Unavailable verdicts by reason (fails open — watch this):")
        for reason, count in report.unavailable_reasons.most_common():
            marker = "  <-- attacker-inducible" if reason == "rate_limit" else ""
            add(f"  {count:6,}  {reason}{marker}")

    add("")
    add("Per classifier:")
    for summary in report.classifiers.values():
        add(f"  {summary.name}")
        add(
            f"    runs={summary.runs:,}  detections={summary.detections:,}  "
            f"clean={summary.clean:,}  unavailable={summary.unavailable:,}"
        )
        add(
            f"    detection_rate={summary.detection_rate:.4f}  "
            f"unavailable_rate={summary.unavailable_rate:.4f}  "
            f"mean_latency={summary.mean_latency_ms}ms"
        )
        if summary.categories:
            add(f"    categories: {dict(summary.categories.most_common(8))}")
        if summary.confidence:
            add(f"    confidence: {dict(summary.confidence)}")
        if summary.errors:
            add(f"    errors: {dict(summary.errors.most_common(5))}")

    if report.detection_samples:
        add("")
        add(
            f"Detection samples ({len(report.detection_samples)} shown, no prompt text):"
        )
        for sample in report.detection_samples:
            add(
                f"  {sample.get('timestamp', '')[:19]}  "
                f"{str(sample.get('client')):16s} {str(sample.get('model')):22s} "
                f"{sample.get('blocking_classifier')} -> {sample.get('action')}"
            )
        add("")
        add("Review these by request_id. A detection on ordinary work is a false")
        add("positive and is the evidence that should gate promoting the mode.")

    return "\n".join(lines)
