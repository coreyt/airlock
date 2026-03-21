"""Dashboard screen — proxy health, guardrails, and model overview."""

from __future__ import annotations

import os
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING

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
    """RichLog with three enhancements over the stock widget.

    1. Bounds guard: Textual bug where render_line requests self.lines[max_lines]
       (one past the end) when the log is full and scrolled to the bottom.
    2. Sticky scroll: new writes only auto-scroll if the view is already at the
       bottom, so the user can freely scroll up to read history without the log
       jumping away.
    3. Text selection: overrides get_selection() so Textual's native click-drag
       selection works.  Click and drag to select; Ctrl+C copies via OSC52.
       Terminal-level Shift+drag also works in most emulators.
    """

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        if y >= len(self.lines):
            return Strip.blank(width, self.rich_style)
        return super()._render_line(y, scroll_x, width)

    def write(self, content, **kwargs) -> "RichLog":
        """Only auto-scroll when already at the bottom (sticky scroll)."""
        if "scroll_end" not in kwargs:
            kwargs["scroll_end"] = self.is_vertical_scroll_end
        return super().write(content, **kwargs)

    def get_selection(self, selection) -> "tuple[str, str] | None":
        """Extract plain text from Strip lines for Textual's selection / Ctrl+C copy."""
        if not self.lines:
            return None
        from textual.geometry import Offset
        from textual.selection import Selection as _Sel

        scroll_y = self.scroll_offset.y

        def _plain(strip: Strip) -> str:
            return "".join(seg.text for seg in strip).rstrip()

        # selection offsets are visual-row relative; shift by scroll_y to get
        # the actual index into self.lines.
        start = selection.start
        end = selection.end
        adj_start = Offset(start.x, start.y + scroll_y) if start is not None else None
        adj_end = Offset(end.x, end.y + scroll_y) if end is not None else None
        full_text = "\n".join(_plain(line) for line in self.lines)
        return _Sel(adj_start, adj_end).extract(full_text), "\n"


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
        self._stopping = False  # True during action_stop_proxy to suppress watcher

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
        self._probe_external()
        self._refresh_state()
        self.set_interval(300.0, self._probe_external)
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
        self._stopping = False  # clear any prior stop before starting
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
        indicator = self.query_one("#proxy-indicator", StatusIndicator)
        indicator.set_status("warn", "Starting...")
        self._stream_proxy_output()
        self._watch_proxy_process()

    def action_stop_proxy(self) -> None:
        """Stop the TUI-owned proxy."""
        if self._proxy_manager is None:
            return
        self._stopping = True
        self._proxy_manager.stop()
        # Do NOT reset _stopping here. _watch_proxy_process checks it in a
        # worker thread and races with proc.wait() returning — resetting here
        # can lose the race. _stopping is cleared at the start of the next
        # action_start_proxy call.
        btn = self.query_one("#proxy-start-btn", Button)
        btn.label = "Start Proxy"
        btn.variant = "success"
        btn.disabled = False
        indicator = self.query_one("#proxy-indicator", StatusIndicator)
        indicator.set_status("error", f"Not reachable at {self._host}:{self._port}")
        detail = self.query_one("#proxy-detail", Static)
        detail.update(f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
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
        out_file = out_dir / f"console-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}Z.log"
        lines = list(self._proxy_manager._ring)
        out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.write(f"[green]Exported {len(lines)} lines to {out_file}[/]")

    @work(thread=True, group="proxy-stdout")
    def _stream_proxy_output(self) -> None:
        """Read proxy output lines into the RichLog; detect startup completion."""
        import queue as _queue

        from rich.text import Text

        if self._proxy_manager is None:
            return
        q = self._proxy_manager.output_queue
        console = self.query_one("#dash-console-log", _SafeRichLog)
        _ready = False
        while self._proxy_manager.is_tui_owned:
            try:
                line = q.get(timeout=0.5)
                console.write(Text.from_ansi(line))
                if not _ready and (
                    "application startup complete" in line.lower()
                    or "uvicorn running on" in line.lower()
                ):
                    _ready = True
                    self.app.call_from_thread(self._on_proxy_ready)
            except _queue.Empty:
                continue

    def _on_proxy_ready(self) -> None:
        """Called when proxy stdout signals it is ready to serve requests."""
        indicator = self.query_one("#proxy-indicator", StatusIndicator)
        indicator.set_status("ok", f"Running at {self._host}:{self._port}")
        detail = self.query_one("#proxy-detail", Static)
        detail.update(f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")

    @work(thread=True, group="proxy-watcher")
    def _watch_proxy_process(self) -> None:
        """Block until the TUI-owned process exits, then update UI if unexpected."""
        if self._proxy_manager is None or self._proxy_manager._process is None:
            return
        proc = self._proxy_manager._process
        proc.wait()
        if not self._stopping:
            self.app.call_from_thread(self._on_proxy_exited)

    def _on_proxy_exited(self) -> None:
        """Called when the proxy exits without action_stop_proxy being invoked."""
        indicator = self.query_one("#proxy-indicator", StatusIndicator)
        indicator.set_status("error", "Proxy exited unexpectedly")
        btn = self.query_one("#proxy-start-btn", Button)
        btn.label = "Start Proxy"
        btn.variant = "success"
        btn.disabled = False
        detail = self.query_one("#proxy-detail", Static)
        detail.update(f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
        console = self.query_one("#dash-console-log", _SafeRichLog)
        console.write("[red]Proxy exited unexpectedly.[/]")

    # -- external proxy probe (HTTP) --------------------------------------

    @work(exclusive=True, thread=True, group="health-check")
    def _probe_external(self) -> None:
        """HTTP probe for externally-running proxy only.

        Skipped entirely when the TUI owns the process — process state is
        authoritative in that case and no network traffic is needed.
        """
        mgr = self._proxy_manager
        if mgr is not None and mgr.is_tui_owned:
            return

        probe_host = "127.0.0.1" if self._host == "0.0.0.0" else self._host
        url = f"http://{probe_host}:{self._port}/health"
        master_key = os.environ.get("AIRLOCK_MASTER_KEY", "")
        req = urllib.request.Request(  # noqa: S310
            url,
            headers={"Authorization": f"Bearer {master_key}"} if master_key else {},
        )

        proxy_reachable = False
        try:
            urllib.request.urlopen(req, timeout=3)  # noqa: S310
            proxy_reachable = True
        except Exception:
            pass

        def _update_ui() -> None:
            # Re-check: TUI may have taken ownership between probe start and now
            if self._proxy_manager is not None and self._proxy_manager.is_tui_owned:
                return

            indicator = self.query_one("#proxy-indicator", StatusIndicator)
            detail = self.query_one("#proxy-detail", Static)
            btn = self.query_one("#proxy-start-btn", Button)

            if proxy_reachable:
                indicator.set_status("ok", f"Running at {self._host}:{self._port}")
                btn.label = "Running Externally"
                btn.variant = "default"
                btn.disabled = True
                self._externally_running = True
            else:
                indicator.set_status(
                    "error", f"Not reachable at {self._host}:{self._port}"
                )
                btn.label = "Start Proxy"
                btn.variant = "success"
                btn.disabled = False
                self._externally_running = False

            detail.update(f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")

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
