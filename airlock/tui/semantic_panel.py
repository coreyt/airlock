"""Rendering for the Guards screen's Semantic tab.

Deliberately a pure function over a :class:`~airlock.semantic_report.SemanticReport`:
the aggregation already exists and is already bounded, so this module only decides
how to show it, and can be tested without standing up a TUI.

Two invariants drive the layout.

**Status is the verdict; action is what Airlock did.** They differ in every mode
except ``enforce``. A panel that shows only one of them makes an observed
detection look like a blocked request, so they are rendered as separate rows and
labelled as such.

**Unavailable is never clean.** A classifier that could not answer is counted in
its own column, never folded into ``clean``, and the reason breakdown calls out
``rate_limit`` specifically: it is the attacker-inducible cause, and because it
fails open, an outage looks exactly like quiet traffic.
"""

from __future__ import annotations

from rich.markup import escape

from airlock.semantic_report import SemanticReport

#: Unavailability causes worth flagging rather than merely listing.
#: rate_limit is attacker-inducible and fails open.
_ALARMING_REASONS = {"rate_limit", "rate_limited", "quota_exceeded"}


def _styled_mode(mode: str) -> str:
    if mode == "enforce":
        return "[red bold]enforce[/]"
    if mode == "shadow":
        return "[yellow]shadow[/]"
    return "[dim]observe[/]"


def _counter_line(counts: dict[str, int]) -> str:
    if not counts:
        return "[dim]none[/]"
    return "  ".join(
        f"{escape(str(name))} [bold]{value}[/]" for name, value in counts.items()
    )


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_semantic_panel(report: SemanticReport) -> str:
    """Rich markup for one semantic report."""
    lines: list[str] = []
    add = lines.append

    modes = dict(report.modes)
    mode_label = (
        "  ".join(f"{_styled_mode(m)} [bold]{c}[/]" for m, c in modes.items())
        or "[dim]none[/]"
    )

    window = report.window or {}
    truncated = bool(window.get("truncated"))
    window_note = (
        f"  │  [yellow]partial window[/] ({escape(str(window.get('limit_hit') or 'limit hit'))})"
        if truncated
        else ""
    )

    add(
        f"Window: [bold]{report.days}d[/]  │  "
        f"requests with verdicts: [bold]{report.requests_with_verdicts}[/]  │  "
        f"mode: {mode_label}{window_note}"
    )
    add("")

    if report.requests_with_verdicts == 0:
        add("[dim]No semantic verdicts in this window.[/]")
        add("")
        add(
            "[dim]This is not the same as 'no threats'. If the registry is empty "
            "or every classifier is unavailable, there is nothing to report.[/]"
        )
        return "\n".join(lines)

    # -- Verdict vs action ------------------------------------------------
    # Rendered adjacently and labelled, because conflating them turns an
    # observation into an apparent block.
    add("[bold]Verdict vs. action[/]")
    add(f"  status (verdict)   {_counter_line(dict(report.statuses))}")
    add(f"  action (what ran)  {_counter_line(dict(report.actions))}")
    if report.detections and not report.blocked_requests:
        add(
            f"  [dim]→ {report.detections} detection(s), "
            f"{report.blocked_requests} request(s) actually blocked — "
            "expected outside enforce mode.[/]"
        )
    add("")

    # -- Per-classifier ---------------------------------------------------
    add("[bold]Classifiers[/]")
    if not report.classifiers:
        add("  [dim]none ran[/]")
    else:
        add(
            f"  [dim]{'name':<22}{'runs':>7}{'detected':>10}{'clean':>8}"
            f"{'unavail':>9}{'det%':>8}{'unavail%':>10}{'mean ms':>10}[/]"
        )
        for summary in report.classifiers.values():
            # Pad first, then wrap in markup: Rich tags are zero-width on
            # screen but count toward an f-string field width, so styling
            # before padding silently breaks column alignment.
            unavail = f"{summary.unavailable:>9}"
            if summary.unavailable:
                unavail = f"[yellow]{unavail}[/]"
            add(
                f"  {escape(summary.name):<22}"
                f"{summary.runs:>7}"
                f"{summary.detections:>10}"
                f"{summary.clean:>8}"
                f"{unavail}"
                f"{_pct(summary.detection_rate):>8}"
                f"{_pct(summary.unavailable_rate):>10}"
                f"{summary.mean_latency_ms:>10.1f}"
            )
    add("")

    # -- Category histogram ----------------------------------------------
    add("[bold]Categories[/]")
    categories: dict[str, int] = {}
    for summary in report.classifiers.values():
        for name, count in summary.categories.items():
            categories[name] = categories.get(name, 0) + count
    if not categories:
        add("  [dim]none[/]")
    else:
        widest = max(categories.values())
        for name, count in sorted(categories.items(), key=lambda kv: -kv[1]):
            bar = "█" * max(1, round(20 * count / widest))
            add(f"  {escape(name):<28}{count:>6}  [cyan]{bar}[/]")
    add("")

    # -- Unavailability ---------------------------------------------------
    add("[bold]Unavailable reasons[/]  [dim]— never counted as clean[/]")
    reasons = dict(report.unavailable_reasons)
    if not reasons:
        add("  [dim]none — every classifier answered[/]")
    else:
        for name, count in reasons.items():
            if name.lower() in _ALARMING_REASONS:
                add(
                    f"  [red bold]{escape(name):<28}{count:>6}[/]  "
                    "[red]fails open — an outage looks like quiet traffic[/]"
                )
            else:
                add(f"  {escape(name):<28}{count:>6}")

    if report.short_circuited:
        add("")
        add("[bold]Short-circuited[/]  [dim]— skipped by adaptive selection[/]")
        for name, count in report.short_circuited.items():
            add(f"  {escape(str(name)):<28}{count:>6}")

    return "\n".join(lines)
