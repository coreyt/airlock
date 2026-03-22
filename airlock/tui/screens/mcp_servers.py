"""MCP Servers screen — health probes and lifecycle management."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, RichLog, Static, TabbedContent, TabPane

from airlock.tui.widgets.safe_data_table import _SafeDataTable

from airlock.fast.state import McpServerHealth

if TYPE_CHECKING:
    from airlock.tui.mcp_manager import McpServerManager

_HEALTH_MAP = {
    McpServerHealth.HEALTHY: "[green]● healthy[/]",
    McpServerHealth.UNHEALTHY: "[red]● unhealthy[/]",
    McpServerHealth.STARTING: "[yellow]● starting[/]",
    McpServerHealth.STOPPED: "[dim]● stopped[/]",
    McpServerHealth.UNKNOWN: "[dim]● unknown[/]",
}


class McpServersPane(Vertical):
    """MCP server health and lifecycle management."""

    def __init__(
        self,
        *,
        mcp_manager: McpServerManager | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._mcp_manager: McpServerManager | None = mcp_manager
        self._selected_server: str = ""

    def compose(self) -> ComposeResult:
        yield Static("MCP Servers: loading...", id="mcp-srv-status")
        with Horizontal(id="mcp-srv-actions"):
            yield Button("Start", id="mcp-srv-start", variant="success", disabled=True)
            yield Button("Stop", id="mcp-srv-stop", variant="error", disabled=True)
            yield Button("Restart", id="mcp-srv-restart", variant="warning", disabled=True)
            yield Button("Probe Now", id="mcp-srv-probe", variant="primary")
        table = _SafeDataTable(id="mcp-srv-table", cursor_type="row")
        table.add_columns("Name", "Type", "URL / Command", "Health", "Latency", "PID", "Uptime")
        yield table
        with TabbedContent(id="mcp-srv-detail-tabs"):
            with TabPane("Info", id="mcp-srv-tab-info"):
                yield Static("Select a server to view details.", id="mcp-srv-info")
            with TabPane("Console", id="mcp-srv-tab-console"):
                yield RichLog(id="mcp-srv-console", max_lines=500)
            with TabPane("Tools", id="mcp-srv-tab-tools"):
                tools_table = _SafeDataTable(id="mcp-srv-tools-table", cursor_type="row")
                tools_table.add_columns("Tool", "Calls", "Err%", "Avg Latency")
                yield tools_table

    def on_mount(self) -> None:
        self._refresh_servers()
        self.set_interval(10.0, self._refresh_servers)

    @work(exclusive=True, thread=True)
    def _refresh_servers(self) -> None:
        from airlock.fast.state import store

        table = self.query_one("#mcp-srv-table", _SafeDataTable)
        status = self.query_one("#mcp-srv-status", Static)
        table.clear()

        servers = store.all_mcp_servers()

        rows: list[tuple] = []
        healthy_count = 0

        if not servers:
            rows.append(
                ("(no MCP servers configured)", "-", "-", "-", "-", "-", "-", "_empty")
            )
        else:
            for name, srv in sorted(servers.items()):
                # Type label
                if srv.is_managed:
                    type_label = "local"
                elif srv.transport == "stdio":
                    type_label = "stdio"
                else:
                    type_label = "remote"

                # URL or command display
                url_display = srv.url or "-"
                if not srv.url and srv.transport == "stdio":
                    url_display = "(stdio)"

                # Health indicator
                health_str = _HEALTH_MAP.get(srv.health, "[dim]● ?[/]")
                if srv.health == McpServerHealth.HEALTHY:
                    healthy_count += 1

                # Latency
                lat_str = (
                    f"{srv.last_health_latency_ms:.0f}ms"
                    if srv.last_health_latency_ms > 0
                    else "-"
                )

                # PID
                pid_str = str(srv.pid) if srv.pid > 0 else "-"

                # Uptime
                uptime = srv.uptime_seconds()
                if uptime > 0:
                    if uptime >= 3600:
                        uptime_str = f"{uptime / 3600:.1f}h"
                    elif uptime >= 60:
                        uptime_str = f"{uptime / 60:.0f}m"
                    else:
                        uptime_str = f"{uptime:.0f}s"
                else:
                    uptime_str = "-"

                rows.append(
                    (name, type_label, url_display, health_str, lat_str, pid_str, uptime_str, name)
                )

        for *cells, key in rows:
            try:
                table.add_row(*cells, key=key)
            except Exception:
                break  # table may have been cleared by another refresh

        total = len(servers)
        if total:
            status.update(
                f"MCP Servers: {total} configured, {healthy_count} healthy"
            )
        else:
            status.update("MCP Servers: none configured")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        name = str(event.row_key.value)
        if name == "_empty":
            return
        self._selected_server = name
        self._update_buttons()
        self._show_detail(name)
        self._refresh_tools(name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if self._mcp_manager is None:
            return

        name = self._selected_server

        if bid == "mcp-srv-start" and name:
            self._do_start(name)
        elif bid == "mcp-srv-stop" and name:
            self._do_stop(name)
        elif bid == "mcp-srv-restart" and name:
            self._do_restart(name)
        elif bid == "mcp-srv-probe":
            self._do_probe()

    @work(thread=True, group="mcp-lifecycle", exclusive=True)
    def _do_start(self, name: str) -> None:
        if self._mcp_manager is None:
            return
        err = self._mcp_manager.start_server(name)
        if err:
            self._set_status_error(err)
        else:
            self._refresh_servers()

    @work(thread=True, group="mcp-lifecycle", exclusive=True)
    def _do_stop(self, name: str) -> None:
        if self._mcp_manager is None:
            return
        self._mcp_manager.stop_server(name)
        self._refresh_servers()

    @work(thread=True, group="mcp-lifecycle", exclusive=True)
    def _do_restart(self, name: str) -> None:
        if self._mcp_manager is None:
            return
        err = self._mcp_manager.restart_server(name)
        if err:
            self._set_status_error(err)
        else:
            self._refresh_servers()

    @work(thread=True, group="mcp-probe")
    def _do_probe(self) -> None:
        if self._mcp_manager is None:
            return
        self._mcp_manager.probe_all()
        self._refresh_servers()

    def _update_buttons(self) -> None:
        if self._mcp_manager is None:
            return

        mgr = self._mcp_manager
        name = self._selected_server
        entry = mgr.get_entry(name) if name else None
        is_managed = entry.is_managed if entry else False
        is_running = mgr.is_running(name) if entry and is_managed else False

        start_btn = self.query_one("#mcp-srv-start", Button)
        stop_btn = self.query_one("#mcp-srv-stop", Button)
        restart_btn = self.query_one("#mcp-srv-restart", Button)

        start_btn.disabled = not is_managed or is_running
        stop_btn.disabled = not is_managed or not is_running
        restart_btn.disabled = not is_managed

    def _show_detail(self, name: str) -> None:
        from airlock.fast.state import store

        info = self.query_one("#mcp-srv-info", Static)
        srv = store.get_mcp_server(name)
        if not srv.transport:
            info.update(f"No data for {name}")
            return

        lines = [f"[bold]{name}[/]"]
        lines.append("")

        # Type
        if srv.is_managed:
            lines.append("  Type: local (airlock_managed)")
        elif srv.transport == "stdio":
            lines.append("  Type: stdio (LiteLLM per-call)")
        else:
            lines.append(f"  Type: remote ({srv.transport})")

        if srv.url:
            lines.append(f"  URL: {srv.url}")

        # Runtime
        if srv.pid > 0:
            lines.append(f"  PID: {srv.pid}")
        uptime = srv.uptime_seconds()
        if uptime > 0:
            lines.append(f"  Uptime: {uptime:.0f}s")

        # Health
        lines.append("")
        lines.append(f"  Health: {srv.health.value}")
        if srv.last_health_latency_ms > 0:
            lines.append(f"  Last probe latency: {srv.last_health_latency_ms:.0f}ms")
        if srv.consecutive_failures > 0:
            lines.append(f"  Consecutive failures: {srv.consecutive_failures}")

        # Success rate
        rate = srv.recent_success_rate()
        history_len = len(srv.health_history)
        if history_len > 0:
            lines.append(
                f"  Success rate: {rate * 100:.0f}% ({history_len} checks)"
            )

        info.update("\n".join(lines))

        # Stream console for managed servers
        if srv.is_managed and self._mcp_manager is not None:
            self._stream_console(name)

    @work(thread=True, group="mcp-console")
    def _stream_console(self, name: str) -> None:
        import queue as _queue

        from textual.worker import get_current_worker

        if self._mcp_manager is None:
            return
        console = self.query_one("#mcp-srv-console", RichLog)
        console.clear()

        entry = self._mcp_manager.get_entry(name)
        if entry is None:
            return

        from rich.text import Text

        # Snapshot ring buffer to avoid RuntimeError from concurrent append
        for line in list(entry.ring):
            console.write(Text.from_ansi(line))

        # Stream new output
        current = get_current_worker()
        while self._selected_server == name:
            if current.is_cancelled:
                break
            try:
                line = entry.output_queue.get(timeout=0.5)
                console.write(Text.from_ansi(line))
            except _queue.Empty:
                continue

    def _refresh_tools(self, server_name: str) -> None:
        from airlock.fast.state import store

        table = self.query_one("#mcp-srv-tools-table", _SafeDataTable)
        table.clear()

        all_tools = store.all_mcp_tools()
        found = False
        for key, tool in all_tools.items():
            if tool.server_name != server_name:
                continue
            found = True
            calls = tool.recent_call_count()
            err_rate = tool.recent_error_rate()
            err_str = f"{err_rate * 100:.1f}%" if calls > 0 else "-"
            avg_lat = tool.recent_avg_latency()
            lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"
            table.add_row(tool.tool_name, str(calls), err_str, lat_str, key=key)

        if not found:
            table.add_row("(no tools tracked)", "-", "-", "-", key="_empty")

    def _set_status_error(self, msg: str) -> None:
        status = self.query_one("#mcp-srv-status", Static)
        status.update(f"[red]Error:[/] {msg}")
