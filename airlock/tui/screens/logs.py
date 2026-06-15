"""Logs screen — browse JSONL log entries with filtering and analysis."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from airlock.tui.widgets.safe_data_table import _SafeDataTable


class LogsPane(VerticalScroll):
    """Live log viewer with model/user/status filtering and analysis."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._records: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="logs-filters"):
            yield Select(
                [("All Models", "all")],
                value="all",
                id="logs-model-filter",
                allow_blank=False,
            )
            yield Input(placeholder="User filter", id="logs-user-filter")
            yield Select(
                [("All", "all"), ("Success", "ok"), ("Errors", "err")],
                value="all",
                id="logs-status-filter",
                allow_blank=False,
            )
            yield Select(
                [
                    ("All Types", "all"),
                    ("LLM", "llm"),
                    ("MCP", "mcp"),
                    ("Batch", "batch"),
                ],
                value="all",
                id="logs-type-filter",
                allow_blank=False,
            )
            yield Input(placeholder="Tool name", id="logs-tool-filter")
        with Horizontal(id="logs-controls"):
            yield Input(value="7", id="logs-days", type="integer", placeholder="Days")
            yield Select(
                [("Manual", "manual"), ("1 min", "1min"), ("Real-time", "realtime")],
                value="manual",
                id="logs-refresh-mode",
                allow_blank=False,
            )
            yield Button("Run Analysis", id="logs-run-analysis", variant="primary")
            yield Button("Export", id="logs-export-btn")
            yield Static("", id="logs-analysis-status")
        with TabbedContent(id="logs-tabs"):
            with TabPane("Detail", id="tab-detail"):
                yield Static(
                    "Select a log entry to view details.",
                    id="logs-detail",
                )
            with TabPane("Optimizations", id="tab-opts"):
                yield Static(
                    "[dim]Press 'Run Analysis' to generate a report.[/]",
                    id="logs-analysis-opts",
                )
            with TabPane("Cache", id="tab-cache"):
                yield Static(
                    "[dim]Press 'Run Analysis' to generate a report.[/]",
                    id="logs-analysis-cache",
                )
            with TabPane("Trends", id="tab-trends"):
                yield Static(
                    "[dim]Press 'Run Analysis' to generate a report.[/]",
                    id="logs-analysis-trends",
                )
            with TabPane("Hypotheses", id="tab-hyp"):
                yield Static(
                    "[dim]Press 'Run Analysis' to generate a report.[/]",
                    id="logs-analysis-hyp",
                )
        with Collapsible(
            title="Log Entries", collapsed=False, id="logs-stream-collapsible"
        ):
            table = _SafeDataTable(id="logs-table", cursor_type="row")
            table.add_columns(
                "Timestamp", "Type", "Model", "User", "Tokens", "Duration", "OK"
            )
            yield table

    def on_mount(self) -> None:
        self._load_logs()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        try:
            idx = int(str(event.row_key.value))
        except (ValueError, TypeError):
            return
        if 0 <= idx < len(self._filtered):
            self._show_detail(self._filtered[idx])

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "logs-refresh-mode":
            self._update_refresh_mode(str(event.value))
        else:
            self._apply_filters()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("logs-user-filter", "logs-tool-filter"):
            self._apply_filters()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "logs-run-analysis":
            self._run_analysis()
        elif event.button.id == "logs-export-btn":
            self._export_filtered()

    def _update_refresh_mode(self, mode: str) -> None:
        """Cancel existing timer and set a new one based on mode."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

        if mode == "1min":
            self._refresh_timer = self.set_interval(60.0, self._load_logs)
        elif mode == "realtime":
            self._refresh_timer = self.set_interval(1.0, self._load_logs)
        # "manual" — no timer

    @work(exclusive=True, thread=True)
    def _load_logs(self) -> None:
        log_dir = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))
        records: list[dict[str, Any]] = []
        today = datetime.now(timezone.utc).date()

        try:
            days = int(self.query_one("#logs-days", Input).value)
        except (ValueError, TypeError):
            days = 7

        for i in range(days):
            day = today - timedelta(days=i)
            path = log_dir / f"airlock-{day.isoformat()}.jsonl"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        records.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        self._records = records[:500]  # cap for UI performance

        # Populate model filter options. Batch/file routes (/v1/batches, /v1/files)
        # log records with no model (model is None) — coerce so sorted() doesn't
        # hit "'<' not supported between NoneType and str".
        models = sorted({(r.get("model") or "unknown") for r in self._records})
        options = [("All Models", "all")] + [(m, m) for m in models]

        def update_ui():
            try:
                model_select = self.query_one("#logs-model-filter", Select)
                model_select.set_options(options)
            except Exception:
                pass
            try:
                self._apply_filters()
            except Exception:
                pass

        self.app.call_from_thread(update_ui)

    def _apply_filters(self) -> None:
        model_val = self.query_one("#logs-model-filter", Select).value
        user_val = self.query_one("#logs-user-filter", Input).value.strip().lower()
        status_val = self.query_one("#logs-status-filter", Select).value
        type_val = self.query_one("#logs-type-filter", Select).value
        tool_val = self.query_one("#logs-tool-filter", Input).value.strip().lower()

        filtered = self._records
        if model_val and model_val != "all":
            filtered = [r for r in filtered if r.get("model") == model_val]
        if user_val:
            filtered = [
                r for r in filtered if user_val in (r.get("user") or "").lower()
            ]
        if status_val == "ok":
            filtered = [r for r in filtered if r.get("success")]
        elif status_val == "err":
            filtered = [r for r in filtered if not r.get("success")]
        if type_val == "mcp":
            filtered = [r for r in filtered if r.get("call_type") == "call_mcp_tool"]
        elif type_val == "batch":
            filtered = [r for r in filtered if r.get("call_type") == "batch"]
        elif type_val == "llm":
            filtered = [
                r
                for r in filtered
                if r.get("call_type") not in ("call_mcp_tool", "batch")
            ]
        if tool_val:
            filtered = [
                r
                for r in filtered
                if tool_val in (r.get("mcp_tool_name") or "").lower()
            ]

        self._filtered = filtered
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#logs-table", _SafeDataTable)
        table.clear()

        for i, r in enumerate(self._filtered[:200]):
            ts = r.get("timestamp", "")[:19]
            if r.get("call_type") == "call_mcp_tool":
                call_type = r.get("mcp_tool_name") or "MCP"
            elif r.get("call_type") == "batch":
                call_type = "BATCH"
            else:
                call_type = "LLM"
            model = r.get("model") or "-"
            user = r.get("user") or "-"
            tokens = str(r.get("total_tokens", "-"))
            dur = r.get("duration_ms")
            dur_str = f"{dur}ms" if dur else "-"
            ok = "\u2713" if r.get("success") else "\u2717"
            table.add_row(ts, call_type, model, user, tokens, dur_str, ok, key=str(i))

        if not self._filtered:
            table.add_row("(no entries)", "-", "-", "-", "-", "-", "-", key="_empty")

    def _show_detail(self, record: dict[str, Any]) -> None:
        detail = self.query_one("#logs-detail", Static)
        req_id = record.get("request_id", "-")
        error = record.get("error")
        messages = record.get("messages")

        parts = [f"[bold]Request ID:[/] {req_id}"]
        if record.get("mcp_tool_name"):
            parts.append(f"[bold]MCP Tool:[/] {record['mcp_tool_name']}")
        if record.get("mcp_server_name"):
            parts.append(f"[bold]MCP Server:[/] {record['mcp_server_name']}")
        if error:
            parts.append(f"[bold]Error:[/] {error}")

        # Enhanced detail fields
        failover = record.get("airlock_failover")
        if failover and isinstance(failover, dict):
            parts.append(
                f"[bold]Failover:[/] {failover.get('original', '?')} \u2192 "
                f"{failover.get('failover', '?')} ({failover.get('reason', '?')})"
            )

        override = record.get("airlock_model_override")
        if override and isinstance(override, dict):
            parts.append(
                f"[bold]Override:[/] {override.get('requested', '?')} \u2192 "
                f"{override.get('final', '?')} ({override.get('reason', '?')})"
            )

        observation = record.get("airlock_observation")
        if observation and isinstance(observation, dict):
            score = observation.get("composite_score", 0)
            verdict = observation.get("would_block", "?")
            parts.append(f"[bold]Guard score:[/] {score:.2f}, verdict: {verdict}")

        protection = record.get("airlock_provider_protection")
        if protection and isinstance(protection, dict):
            parts.append(
                f"[bold]Protection:[/] {protection.get('action', '?')} "
                f"provider={protection.get('provider', '?')}"
            )

        if messages:
            msg_str = json.dumps(messages, indent=2, default=str)
            if len(msg_str) > 500:
                msg_str = msg_str[:500] + "..."
            parts.append(f"[bold]Messages:[/]\n{msg_str}")

        detail.update("\n".join(parts))

    @work(exclusive=True, thread=True)
    def _export_filtered(self) -> None:
        """Write filtered records to a JSONL file in the log directory."""
        log_dir = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        out = log_dir / f"export-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.jsonl"
        records = list(self._filtered)
        try:
            with open(out, "w") as f:
                for r in records:
                    f.write(json.dumps(r, default=str) + "\n")
            msg = f"[green]Exported {len(records)} records to {out.name}[/]"
        except Exception as exc:
            msg = f"[red]Export failed: {exc}[/]"

        def _show(message: str = msg) -> None:
            status = self.query_one("#logs-analysis-status", Static)
            status.update(message)

        self.app.call_from_thread(_show)

    @work(exclusive=True, thread=True)
    def _run_analysis(self) -> None:
        """Run offline analysis and populate the analysis tabs."""
        days_input = self.query_one("#logs-days", Input)
        status = self.query_one("#logs-analysis-status", Static)

        try:
            days = int(days_input.value)
        except ValueError:
            self.app.call_from_thread(status.update, "[red]Invalid number of days[/]")
            return

        self.app.call_from_thread(status.update, "[yellow]Analyzing...[/]")

        try:
            from airlock.slow.analyzer import analyze

            report = analyze(days=days)
        except Exception as exc:
            self.app.call_from_thread(status.update, f"[red]Error: {exc}[/]")
            return

        self.app.call_from_thread(
            status.update,
            f"Done \u2014 {report.total_requests} requests analyzed",
        )

        # Optimizations
        if report.optimizations:
            lines = []
            for i, o in enumerate(report.optimizations, 1):
                lines.append(f"  {i}. [{o.impact.upper()}] {o.description}")
            opts_text = "\n".join(lines)
        else:
            opts_text = "[dim]No optimizations found.[/]"
        self.app.call_from_thread(
            self.query_one("#logs-analysis-opts", Static).update, opts_text
        )

        # Cache
        if report.cache_opportunities:
            lines = []
            for c in report.cache_opportunities:
                lines.append(
                    f"  {c.pattern} \u2014 model: {c.model}, "
                    f"~{c.estimated_token_savings:,} tokens saveable"
                )
            cache_text = "\n".join(lines)
        else:
            cache_text = "[dim]No cache opportunities found.[/]"
        self.app.call_from_thread(
            self.query_one("#logs-analysis-cache", Static).update, cache_text
        )

        # Trends
        if report.trends:
            lines = []
            for t in report.trends:
                lines.append(
                    f"  {t.metric}: {t.direction} "
                    f"({t.magnitude:.1f}% over {t.period_days}d)"
                )
            trends_text = "\n".join(lines)
        else:
            trends_text = "[dim]No significant trends detected.[/]"
        self.app.call_from_thread(
            self.query_one("#logs-analysis-trends", Static).update, trends_text
        )

        # Hypotheses
        if report.hypotheses:
            lines = []
            for h in report.hypotheses:
                lines.append(
                    f"  [{h.confidence:.0%}] {h.statement}\n"
                    f"        Test: {h.test_proposal}"
                )
            hyp_text = "\n".join(lines)
        else:
            hyp_text = "[dim]No hypotheses generated.[/]"
        self.app.call_from_thread(
            self.query_one("#logs-analysis-hyp", Static).update, hyp_text
        )
