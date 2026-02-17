"""Analysis screen — run offline analysis and browse reports."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static, TabbedContent, TabPane


class AnalysisPane(Vertical):
    """Run offline log analysis and view structured reports."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="analysis-controls"):
            yield Label("Days:", classes="label")
            yield Input(value="7", id="analysis-days", type="integer")
            yield Button("Run Analysis", id="analysis-run", variant="primary")
            yield Static("", id="analysis-status")
        with TabbedContent(id="analysis-tabs"):
            with TabPane("Optimizations", id="tab-opts"):
                yield Static(
                    "[dim]Press 'Run Analysis' to generate a report.[/]",
                    id="analysis-opts",
                )
            with TabPane("Cache", id="tab-cache"):
                yield Static("", id="analysis-cache")
            with TabPane("Trends", id="tab-trends"):
                yield Static("", id="analysis-trends")
            with TabPane("Hypotheses", id="tab-hyp"):
                yield Static("", id="analysis-hyp")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "analysis-run":
            self._run_analysis()

    @work(exclusive=True, thread=True)
    def _run_analysis(self) -> None:
        days_input = self.query_one("#analysis-days", Input)
        status = self.query_one("#analysis-status", Static)

        try:
            days = int(days_input.value)
        except ValueError:
            status.update("[red]Invalid number of days[/]")
            return

        status.update("[yellow]Analyzing...[/]")

        try:
            from airlock.slow.analyzer import analyze

            report = analyze(days=days)
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/]")
            return

        status.update(
            f"Done — {report.total_requests} requests analyzed"
        )

        # Optimizations
        if report.optimizations:
            lines = []
            for i, o in enumerate(report.optimizations, 1):
                lines.append(
                    f"  {i}. [{o.impact.upper()}] {o.description}"
                )
            self.query_one("#analysis-opts", Static).update("\n".join(lines))
        else:
            self.query_one("#analysis-opts", Static).update(
                "[dim]No optimizations found.[/]"
            )

        # Cache
        if report.cache_opportunities:
            lines = []
            for c in report.cache_opportunities:
                lines.append(
                    f"  {c.pattern} — model: {c.model}, "
                    f"~{c.estimated_token_savings:,} tokens saveable"
                )
            self.query_one("#analysis-cache", Static).update("\n".join(lines))
        else:
            self.query_one("#analysis-cache", Static).update(
                "[dim]No cache opportunities found.[/]"
            )

        # Trends
        if report.trends:
            lines = []
            for t in report.trends:
                lines.append(
                    f"  {t.metric}: {t.direction} "
                    f"({t.magnitude:.1f}% over {t.period_days}d)"
                )
            self.query_one("#analysis-trends", Static).update("\n".join(lines))
        else:
            self.query_one("#analysis-trends", Static).update(
                "[dim]No significant trends detected.[/]"
            )

        # Hypotheses
        if report.hypotheses:
            lines = []
            for h in report.hypotheses:
                lines.append(
                    f"  [{h.confidence:.0%}] {h.statement}\n"
                    f"        Test: {h.test_proposal}"
                )
            self.query_one("#analysis-hyp", Static).update("\n".join(lines))
        else:
            self.query_one("#analysis-hyp", Static).update(
                "[dim]No hypotheses generated.[/]"
            )
