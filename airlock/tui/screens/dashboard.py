"""Dashboard screen — proxy health, guardrails, and model overview."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import TYPE_CHECKING

from airlock.client_identity import add_airlock_client_header
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.strip import Strip
from textual.widgets import Button, Collapsible, DataTable, RichLog, Static

from airlock.tui.widgets.metric_card import MetricCard
from airlock.tui.widgets.status_indicator import StatusIndicator

if TYPE_CHECKING:
    from airlock.tui.proxy_manager import ProxyManager


class _SafeRichLog(RichLog):
    """RichLog that guards against an out-of-bounds index when max_lines is reached.

    Textual bug: when the log is exactly full and scrolled to the bottom,
    render_line requests self.lines[max_lines] which is one past the end.
    """

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        if y >= len(self.lines):
            return Strip.blank(width, self.rich_style)
        return super()._render_line(y, scroll_x, width)


class DashboardPane(Vertical):
    """At-a-glance proxy health and traffic overview."""

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: str = "4000",
        proxy_manager: ProxyManager | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._host = host
        self._port = port
        self._proxy_manager = proxy_manager
        self._externally_running = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="dash-top-row"):
            with Vertical(id="dash-proxy-status"):
                yield Static("[bold]Proxy Status[/]")
                yield StatusIndicator(
                    "Checking...", status="warn", id="proxy-indicator"
                )
                yield Static("", id="proxy-detail")
                yield Button(
                    "Checking...",
                    id="proxy-start-btn",
                    variant="default",
                    disabled=True,
                )
            with Vertical(id="dash-guardrails"):
                yield Static("[bold]Guardrails[/]")
                yield StatusIndicator("PII Guard", status="ok", id="guard-pii")
                yield StatusIndicator("Keyword Guard", status="ok", id="guard-kw")
                yield StatusIndicator("Fast Guardian", status="ok", id="guard-fast")
            with Vertical(id="dash-mcp-status"):
                yield Static("[bold]MCP Gateway[/]")
                yield StatusIndicator(
                    "No MCP traffic", status="warn", id="mcp-indicator"
                )
                yield MetricCard(
                    title="Traffic Split",
                    value="LLM: 0 | MCP: 0",
                    id="mcp-traffic-split",
                )
        with Collapsible(title="Proxy Console Output", id="dash-console-collapsible"):
            yield _SafeRichLog(id="dash-console-log", max_lines=500)
        with Horizontal(id="dash-export-row"):
            yield Button("Export Log", id="export-log-btn", variant="default")
        table = DataTable(id="dash-model-table")
        table.add_columns("Model", "Circuit", "Reqs", "Err%", "Avg Latency")
        yield table

    def on_mount(self) -> None:
        self._check_health()
        self._refresh_state()
        self.set_interval(5.0, self._check_health)
        self.set_interval(5.0, self._refresh_state)

    # -- collapsible toggle -----------------------------------------------

    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        if event.collapsible.id == "dash-console-collapsible":
            if event.collapsible.collapsed:
                event.collapsible.remove_class("-expanded")
            else:
                event.collapsible.add_class("-expanded")

    # -- button handling --------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export-log-btn":
            self._export_log()
            return
        if event.button.id != "proxy-start-btn":
            return
        if event.button.label.plain == "Stop Proxy":
            self.action_stop_proxy()
        else:
            self.action_start_proxy()

    def action_start_proxy(self) -> None:
        """Start the proxy via ProxyManager."""
        if self._proxy_manager is None:
            return
        err = self._proxy_manager.start()
        btn = self.query_one("#proxy-start-btn", Button)
        console = self.query_one("#dash-console-log", _SafeRichLog)
        if err:
            console.write(f"[red]Error:[/] {err}")
            return
        btn.label = "Stop Proxy"
        btn.variant = "error"
        collapsible = self.query_one("#dash-console-collapsible", Collapsible)
        collapsible.collapsed = False
        collapsible.add_class("-expanded")
        console.write("[green]Proxy started.[/]")
        self._stream_proxy_output()

    def action_stop_proxy(self) -> None:
        """Stop the TUI-owned proxy."""
        if self._proxy_manager is None:
            return
        self._proxy_manager.stop()
        btn = self.query_one("#proxy-start-btn", Button)
        btn.label = "Start Proxy"
        btn.variant = "success"
        console = self.query_one("#dash-console-log", _SafeRichLog)
        console.write("[yellow]Proxy stopped.[/]")

    def _export_log(self) -> None:
        """Export the proxy console ring buffer to ./exported_log."""
        from pathlib import Path

        console = self.query_one("#dash-console-log", _SafeRichLog)
        if self._proxy_manager is None:
            console.write("[red]No proxy manager available.[/]")
            return

        out_dir = Path("exported_log")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"console-{datetime.now():%Y%m%d-%H%M%S}.log"
        lines = list(self._proxy_manager._ring)
        out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.write(f"[green]Exported {len(lines)} lines to {out_file}[/]")

    @work(thread=True, group="proxy-stdout")
    def _stream_proxy_output(self) -> None:
        """Read proxy output lines into the RichLog."""
        import queue as _queue

        from rich.text import Text

        if self._proxy_manager is None:
            return
        q = self._proxy_manager.output_queue
        console = self.query_one("#dash-console-log", _SafeRichLog)
        while self._proxy_manager.is_tui_owned:
            try:
                line = q.get(timeout=0.5)
                console.write(Text.from_ansi(line))
            except _queue.Empty:
                continue

    # -- health check with button state -----------------------------------

    @work(exclusive=True, thread=True, group="health-check")
    def _check_health(self) -> None:
        # 0.0.0.0 is a bind address, not connectable — probe via loopback
        probe_host = "127.0.0.1" if self._host == "0.0.0.0" else self._host
        url = f"http://{probe_host}:{self._port}/health?client=tui-dashboard"

        proxy_reachable = False
        try:
            req = add_airlock_client_header(urllib.request.Request(url))
            master_key = os.environ.get("AIRLOCK_MASTER_KEY")
            if master_key:
                req.add_header("Authorization", f"Bearer {master_key}")
            urllib.request.urlopen(req, timeout=3)  # noqa: S310
            proxy_reachable = True
        except urllib.error.HTTPError:
            # Any HTTP response (even 401/403) means the proxy is alive
            proxy_reachable = True
        except Exception:
            pass

        mgr = self._proxy_manager
        tui_owned = mgr is not None and mgr.is_tui_owned

        def _update_ui() -> None:
            indicator = self.query_one("#proxy-indicator", StatusIndicator)
            detail = self.query_one("#proxy-detail", Static)
            btn = self.query_one("#proxy-start-btn", Button)

            if proxy_reachable:
                indicator.set_status("ok", f"Running at {self._host}:{self._port}")
                if tui_owned:
                    btn.label = "Stop Proxy"
                    btn.variant = "error"
                    btn.disabled = False
                else:
                    btn.label = "Running Externally"
                    btn.variant = "default"
                    btn.disabled = True
                    self._externally_running = True
            elif tui_owned:
                indicator.set_status("warn", "Starting...")
                btn.label = "Stop Proxy"
                btn.variant = "error"
                btn.disabled = False
            else:
                indicator.set_status(
                    "error", f"Not reachable at {self._host}:{self._port}"
                )
                btn.label = "Start Proxy"
                btn.variant = "success"
                btn.disabled = False
                self._externally_running = False

            detail.update(f"Last checked: {datetime.now().strftime('%H:%M:%S')}")

        self.app.call_from_thread(_update_ui)

    @work(exclusive=True, thread=True, group="state-refresh")
    def _refresh_state(self) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        # Collect all data on the worker thread before touching the DOM
        rows = []
        for name, model in store.all_models().items():
            avg_lat = model.recent_avg_latency()
            lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"
            total = len(model.success_times) + len(model.failure_times)
            err_count = len(model.failure_times)
            err_pct = f"{err_count / total * 100:.1f}%" if total > 0 else "-"
            circuit = model.circuit.value.upper()
            rows.append((name, circuit, str(total), err_pct, lat_str))

        llm_count, mcp_count = store.traffic_split()
        traffic_total = llm_count + mcp_count
        mcp_tools = store.all_mcp_tools()

        def _update_ui() -> None:
            table = self.query_one("#dash-model-table", DataTable)
            table.clear()
            table.add_rows(rows) if rows else table.add_row("-", "-", "-", "-", "-")

            split_card = self.query_one("#mcp-traffic-split", MetricCard)
            if traffic_total > 0:
                llm_pct = llm_count * 100 // traffic_total
                mcp_pct = mcp_count * 100 // traffic_total
                split_card.set_value(
                    f"LLM: {llm_count} ({llm_pct}%) | MCP: {mcp_count} ({mcp_pct}%)"
                )
            else:
                split_card.set_value("LLM: 0 | MCP: 0")

            mcp_indicator = self.query_one("#mcp-indicator", StatusIndicator)
            if not mcp_tools:
                mcp_indicator.set_status("warn", "No MCP traffic")
            elif any(t.recent_error_rate() > 0.5 for t in mcp_tools.values()):
                mcp_indicator.set_status("error", "High error rate")
            else:
                mcp_indicator.set_status("ok", f"{len(mcp_tools)} tools active")

        self.app.call_from_thread(_update_ui)
