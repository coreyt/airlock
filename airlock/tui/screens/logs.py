"""Logs screen — browse JSONL log entries with filtering."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Select, Static


class LogsPane(Vertical):
    """Live log viewer with model/user/status filtering."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._records: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []

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
                [("All Types", "all"), ("LLM", "llm"), ("MCP", "mcp")],
                value="all",
                id="logs-type-filter",
                allow_blank=False,
            )
            yield Input(placeholder="Tool name", id="logs-tool-filter")
        table = DataTable(id="logs-table", cursor_type="row")
        table.add_columns("Timestamp", "Type", "Model", "User", "Tokens", "Duration", "OK")
        yield table
        yield Static("Select a log entry to view details.", id="logs-detail")

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
        self._apply_filters()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("logs-user-filter", "logs-tool-filter"):
            self._apply_filters()

    @work(exclusive=True, thread=True)
    def _load_logs(self) -> None:
        log_dir = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))
        records: list[dict[str, Any]] = []
        today = datetime.utcnow().date()

        for i in range(3):  # last 3 days
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

        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        self._records = records[:500]  # cap for UI performance

        # Populate model filter options
        models = sorted({r.get("model", "unknown") for r in self._records})
        model_select = self.query_one("#logs-model-filter", Select)
        options = [("All Models", "all")] + [(m, m) for m in models]
        model_select.set_options(options)

        self._apply_filters()

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
                r for r in filtered
                if user_val in (r.get("user") or "").lower()
            ]
        if status_val == "ok":
            filtered = [r for r in filtered if r.get("success")]
        elif status_val == "err":
            filtered = [r for r in filtered if not r.get("success")]
        if type_val == "mcp":
            filtered = [r for r in filtered if r.get("call_type") == "call_mcp_tool"]
        elif type_val == "llm":
            filtered = [r for r in filtered if r.get("call_type") != "call_mcp_tool"]
        if tool_val:
            filtered = [
                r for r in filtered
                if tool_val in (r.get("mcp_tool_name") or "").lower()
            ]

        self._filtered = filtered
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        table.clear()

        for i, r in enumerate(self._filtered[:200]):
            ts = r.get("timestamp", "")[:19]
            if r.get("call_type") == "call_mcp_tool":
                call_type = r.get("mcp_tool_name") or "MCP"
            else:
                call_type = "LLM"
            model = r.get("model", "-")
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
        if messages:
            msg_str = json.dumps(messages, indent=2, default=str)
            if len(msg_str) > 500:
                msg_str = msg_str[:500] + "..."
            parts.append(f"[bold]Messages:[/]\n{msg_str}")

        detail.update("\n".join(parts))
