"""Unified configuration screen — merges Settings and MCP Servers into a tabbed view."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from airlock.guardrails import _env_flag
from airlock.tui.widgets.safe_data_table import _SafeDataTable
from airlock.fast.state import McpServerHealth

if TYPE_CHECKING:
    from airlock.tui.mcp_manager import McpServerManager

_HEALTH_MAP = {
    McpServerHealth.HEALTHY: "[green]\u25cf healthy[/]",
    McpServerHealth.UNHEALTHY: "[red]\u25cf unhealthy[/]",
    McpServerHealth.STARTING: "[yellow]\u25cf starting[/]",
    McpServerHealth.STOPPED: "[dim]\u25cf stopped[/]",
    McpServerHealth.UNKNOWN: "[dim]\u25cf unknown[/]",
}


class ConfigPane(Vertical):
    """Unified configuration management with tabbed sections."""

    def __init__(
        self,
        *,
        mcp_manager: McpServerManager | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._mcp_manager: McpServerManager | None = mcp_manager
        self._selected_server: str = ""

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with TabbedContent(id="config-tabs"):
            # Tab 1 — Providers
            with TabPane("Providers", id="cfg-tab-providers"):
                with VerticalScroll(classes="config-form"):
                    yield Label("Anthropic API Key")
                    yield Input(
                        value=self._mask_env("ANTHROPIC_API_KEY"),
                        password=True,
                        id="cfg-anthropic-key",
                    )
                    yield Label("OpenAI API Key")
                    yield Input(
                        value=self._mask_env("OPENAI_API_KEY"),
                        password=True,
                        id="cfg-openai-key",
                    )
                    yield Label("Master Key")
                    yield Input(
                        value=self._mask_env("AIRLOCK_MASTER_KEY"),
                        password=True,
                        id="cfg-master-key",
                    )

            # Tab 2 — Guardrails
            with TabPane("Guardrails", id="cfg-tab-guardrails"):
                with VerticalScroll(classes="config-form"):
                    # Enforcement mode
                    yield Label("Enforcement Mode")
                    yield Select(
                        [("Observe", "observe"), ("Shadow", "shadow"), ("Enforce", "enforce")],
                        value=os.getenv("AIRLOCK_ENFORCE_MODE", "observe"),
                        id="cfg-enforce-mode",
                        allow_blank=False,
                    )

                    # Signal weights
                    pii_w, kw_w, threat_w = self._load_signal_weights()
                    yield Label("Signal Weights (orchestrator)")
                    yield Label("pii_scan weight")
                    yield Input(value=pii_w, id="cfg-weight-pii")
                    yield Label("keyword_scan weight")
                    yield Input(value=kw_w, id="cfg-weight-keyword")
                    yield Label("threat_read weight")
                    yield Input(value=threat_w, id="cfg-weight-threat")

                    # Existing guardrail toggles
                    yield Label("PII Guard")
                    yield Switch(
                        value=_env_flag("AIRLOCK_PII_ENABLED"),
                        id="cfg-pii-enabled",
                    )
                    yield Label("PII Entity Types")
                    yield Input(
                        value=os.getenv(
                            "AIRLOCK_PII_ENTITIES",
                            "CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER",
                        ),
                        id="cfg-pii-entities",
                    )
                    yield Label("Keyword Guard")
                    yield Switch(
                        value=_env_flag("AIRLOCK_KW_ENABLED"),
                        id="cfg-kw-enabled",
                    )
                    yield Label("Blocked Keywords")
                    yield Input(
                        value=os.getenv("AIRLOCK_BLOCKED_KEYWORDS", ""),
                        id="cfg-blocked-keywords",
                    )

            # Tab 3 — Protection
            with TabPane("Protection", id="cfg-tab-protection"):
                with VerticalScroll(classes="config-form"):
                    threat_vals = self._load_threat_defaults()
                    yield Label("Block Threshold")
                    yield Input(value=threat_vals["block_threshold"], id="cfg-threat-block-threshold")
                    yield Label("Base Backoff (seconds)")
                    yield Input(value=threat_vals["base_backoff"], id="cfg-threat-base-backoff")
                    yield Label("Max Backoff (seconds)")
                    yield Input(value=threat_vals["max_backoff"], id="cfg-threat-max-backoff")
                    yield Label("Volume Spike Multiplier")
                    yield Input(value=threat_vals["volume_spike"], id="cfg-threat-volume-spike")
                    yield Label("Rapid-Fire Min Gap (seconds)")
                    yield Input(value=threat_vals["rapid_fire"], id="cfg-threat-rapid-fire")
                    yield Label("Payload Max Chars")
                    yield Input(value=threat_vals["payload_max"], id="cfg-threat-payload-max")
                    yield Label("Error Probe Rate")
                    yield Input(value=threat_vals["error_rate"], id="cfg-threat-error-rate")

            # Tab 4 — MCP
            with TabPane("MCP", id="cfg-tab-mcp"):
                with Vertical(classes="config-form"):
                    yield Static("MCP Servers: loading...", id="cfg-mcp-status")
                    with Horizontal(id="cfg-mcp-actions"):
                        yield Button("Start", id="cfg-mcp-start", variant="success", disabled=True)
                        yield Button("Stop", id="cfg-mcp-stop", variant="error", disabled=True)
                        yield Button("Restart", id="cfg-mcp-restart", variant="warning", disabled=True)
                        yield Button("Probe Now", id="cfg-mcp-probe", variant="primary")
                    table = _SafeDataTable(id="cfg-mcp-table", cursor_type="row")
                    table.add_columns("Name", "Type", "URL / Command", "Health", "Latency", "PID", "Uptime")
                    yield table
                    with TabbedContent(id="cfg-mcp-detail-tabs"):
                        with TabPane("Info", id="cfg-mcp-tab-info"):
                            yield Static("Select a server to view details.", id="cfg-mcp-info")
                        with TabPane("Console", id="cfg-mcp-tab-console"):
                            yield RichLog(id="cfg-mcp-console", max_lines=500)
                        with TabPane("Tools", id="cfg-mcp-tab-tools"):
                            tools_table = _SafeDataTable(id="cfg-mcp-tools-table", cursor_type="row")
                            tools_table.add_columns("Tool", "Calls", "Err%", "Avg Latency")
                            yield tools_table
                    yield Label("Allowed Tools (comma-separated)")
                    yield Input(
                        value=os.getenv("AIRLOCK_MCP_ALLOWED_TOOLS", ""),
                        id="cfg-mcp-allowed",
                    )
                    yield Label("Blocked Tools (comma-separated)")
                    yield Input(
                        value=os.getenv("AIRLOCK_MCP_BLOCKED_TOOLS", ""),
                        id="cfg-mcp-blocked",
                    )

            # Tab 5 — Logging
            with TabPane("Logging", id="cfg-tab-logging"):
                with VerticalScroll(classes="config-form"):
                    yield Label("Log Directory")
                    yield Input(
                        value=os.getenv("AIRLOCK_LOG_DIR", "./logs"),
                        id="cfg-log-dir",
                    )
                    yield Label("S3 Bucket (optional)")
                    yield Input(
                        value=os.getenv("AIRLOCK_S3_BUCKET", ""),
                        id="cfg-s3-bucket",
                    )
                    yield Label("SQL URL (optional)")
                    yield Input(
                        value=os.getenv("AIRLOCK_SQL_URL", ""),
                        id="cfg-sql-url",
                    )

            # Tab 6 — Advanced
            with TabPane("Advanced", id="cfg-tab-advanced"):
                with VerticalScroll(classes="config-form"):
                    yield Label("Host")
                    yield Input(
                        value=os.getenv("AIRLOCK_HOST", "0.0.0.0"),
                        id="cfg-host",
                    )
                    yield Label("Port")
                    yield Input(
                        value=os.getenv("AIRLOCK_PORT", "4000"),
                        id="cfg-port",
                    )
                    yield Label("Request Timeout (seconds)")
                    yield Input(value="300", id="cfg-timeout")
                    yield Label("Failover Map (JSON)")
                    yield Input(
                        value=os.getenv("AIRLOCK_FAILOVER_MAP", ""),
                        id="cfg-failover-map",
                    )

        yield Button("Apply Changes", id="cfg-apply", variant="primary")
        yield Static("", id="cfg-status")

    # ------------------------------------------------------------------
    # Mount / timers
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._refresh_mcp_servers()
        self.set_interval(10.0, self._refresh_mcp_servers)

    # ------------------------------------------------------------------
    # Button handler
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cfg-apply":
            self._apply_settings()
            return

        # MCP lifecycle buttons
        if self._mcp_manager is None:
            return
        name = self._selected_server
        if bid == "cfg-mcp-start" and name:
            self._do_mcp_start(name)
        elif bid == "cfg-mcp-stop" and name:
            self._do_mcp_stop(name)
        elif bid == "cfg-mcp-restart" and name:
            self._do_mcp_restart(name)
        elif bid == "cfg-mcp-probe":
            self._do_mcp_probe()

    # ------------------------------------------------------------------
    # Apply settings
    # ------------------------------------------------------------------

    def _apply_settings(self) -> None:
        status = self.query_one("#cfg-status", Static)
        try:
            env_map = {
                "AIRLOCK_PII_ENTITIES": "#cfg-pii-entities",
                "AIRLOCK_BLOCKED_KEYWORDS": "#cfg-blocked-keywords",
                "AIRLOCK_LOG_DIR": "#cfg-log-dir",
                "AIRLOCK_S3_BUCKET": "#cfg-s3-bucket",
                "AIRLOCK_SQL_URL": "#cfg-sql-url",
                "AIRLOCK_HOST": "#cfg-host",
                "AIRLOCK_PORT": "#cfg-port",
                "AIRLOCK_FAILOVER_MAP": "#cfg-failover-map",
                "AIRLOCK_MCP_ALLOWED_TOOLS": "#cfg-mcp-allowed",
                "AIRLOCK_MCP_BLOCKED_TOOLS": "#cfg-mcp-blocked",
            }
            for env_var, widget_id in env_map.items():
                val = self.query_one(widget_id, Input).value.strip()
                if val:
                    os.environ[env_var] = val

            # Enforcement mode
            mode_select = self.query_one("#cfg-enforce-mode", Select)
            if mode_select.value is not None and mode_select.value != Select.BLANK:
                os.environ["AIRLOCK_ENFORCE_MODE"] = str(mode_select.value)

            # Guardrail enable switches
            pii_switch = self.query_one("#cfg-pii-enabled", Switch)
            kw_switch = self.query_one("#cfg-kw-enabled", Switch)
            os.environ["AIRLOCK_PII_ENABLED"] = "true" if pii_switch.value else "false"
            os.environ["AIRLOCK_KW_ENABLED"] = "true" if kw_switch.value else "false"

            status.update(
                "[green]Settings applied to runtime environment. "
                "Restart proxy for full effect.[/]"
            )
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/]")

    # ------------------------------------------------------------------
    # MCP server table refresh
    # ------------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def _refresh_mcp_servers(self) -> None:
        from airlock.fast.state import store

        table = self.query_one("#cfg-mcp-table", _SafeDataTable)
        status = self.query_one("#cfg-mcp-status", Static)
        self.app.call_from_thread(table.clear)

        servers = store.all_mcp_servers()

        rows: list[tuple] = []
        healthy_count = 0

        if not servers:
            rows.append(
                ("(no MCP servers configured)", "-", "-", "-", "-", "-", "-", "_empty")
            )
        else:
            for name, srv in sorted(servers.items()):
                if srv.is_managed:
                    type_label = "local"
                elif srv.transport == "stdio":
                    type_label = "stdio"
                else:
                    type_label = "remote"

                url_display = srv.url or "-"
                if not srv.url and srv.transport == "stdio":
                    url_display = "(stdio)"

                health_str = _HEALTH_MAP.get(srv.health, "[dim]\u25cf ?[/]")
                if srv.health == McpServerHealth.HEALTHY:
                    healthy_count += 1

                lat_str = (
                    f"{srv.last_health_latency_ms:.0f}ms"
                    if srv.last_health_latency_ms > 0
                    else "-"
                )

                pid_str = str(srv.pid) if srv.pid > 0 else "-"

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

        def _apply() -> None:
            for *cells, key in rows:
                try:
                    table.add_row(*cells, key=key)
                except Exception:
                    break

            total = len(servers)
            if total:
                status.update(
                    f"MCP Servers: {total} configured, {healthy_count} healthy"
                )
            else:
                status.update("MCP Servers: none configured")

        self.app.call_from_thread(_apply)

    # ------------------------------------------------------------------
    # MCP table row selection
    # ------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        name = str(event.row_key.value)
        if name == "_empty":
            return
        self._selected_server = name
        self._update_mcp_buttons()
        self._show_mcp_detail(name)
        self._refresh_mcp_tools(name)

    # ------------------------------------------------------------------
    # MCP lifecycle workers
    # ------------------------------------------------------------------

    @work(thread=True, group="cfg-mcp-lifecycle", exclusive=True)
    def _do_mcp_start(self, name: str) -> None:
        if self._mcp_manager is None:
            return
        err = self._mcp_manager.start_server(name)
        if err:
            self.app.call_from_thread(self._set_mcp_status_error, err)
        else:
            self._refresh_mcp_servers()

    @work(thread=True, group="cfg-mcp-lifecycle", exclusive=True)
    def _do_mcp_stop(self, name: str) -> None:
        if self._mcp_manager is None:
            return
        self._mcp_manager.stop_server(name)
        self._refresh_mcp_servers()

    @work(thread=True, group="cfg-mcp-lifecycle", exclusive=True)
    def _do_mcp_restart(self, name: str) -> None:
        if self._mcp_manager is None:
            return
        err = self._mcp_manager.restart_server(name)
        if err:
            self.app.call_from_thread(self._set_mcp_status_error, err)
        else:
            self._refresh_mcp_servers()

    @work(thread=True, group="cfg-mcp-probe")
    def _do_mcp_probe(self) -> None:
        if self._mcp_manager is None:
            return
        self._mcp_manager.probe_all()
        self._refresh_mcp_servers()

    # ------------------------------------------------------------------
    # MCP button state
    # ------------------------------------------------------------------

    def _update_mcp_buttons(self) -> None:
        if self._mcp_manager is None:
            return

        mgr = self._mcp_manager
        name = self._selected_server
        entry = mgr.get_entry(name) if name else None
        is_managed = entry.is_managed if entry else False
        is_running = mgr.is_running(name) if entry and is_managed else False

        start_btn = self.query_one("#cfg-mcp-start", Button)
        stop_btn = self.query_one("#cfg-mcp-stop", Button)
        restart_btn = self.query_one("#cfg-mcp-restart", Button)

        start_btn.disabled = not is_managed or is_running
        stop_btn.disabled = not is_managed or not is_running
        restart_btn.disabled = not is_managed

    # ------------------------------------------------------------------
    # MCP detail panel
    # ------------------------------------------------------------------

    def _show_mcp_detail(self, name: str) -> None:
        from airlock.fast.state import store

        info = self.query_one("#cfg-mcp-info", Static)
        srv = store.get_mcp_server(name)
        if not srv.transport:
            info.update(f"No data for {name}")
            return

        lines = [f"[bold]{name}[/]", ""]

        if srv.is_managed:
            lines.append("  Type: local (airlock_managed)")
        elif srv.transport == "stdio":
            lines.append("  Type: stdio (LiteLLM per-call)")
        else:
            lines.append(f"  Type: remote ({srv.transport})")

        if srv.url:
            lines.append(f"  URL: {srv.url}")

        if srv.pid > 0:
            lines.append(f"  PID: {srv.pid}")
        uptime = srv.uptime_seconds()
        if uptime > 0:
            lines.append(f"  Uptime: {uptime:.0f}s")

        lines.append("")
        lines.append(f"  Health: {srv.health.value}")
        if srv.last_health_latency_ms > 0:
            lines.append(f"  Last probe latency: {srv.last_health_latency_ms:.0f}ms")
        if srv.consecutive_failures > 0:
            lines.append(f"  Consecutive failures: {srv.consecutive_failures}")

        rate = srv.recent_success_rate()
        history_len = len(srv.health_history)
        if history_len > 0:
            lines.append(
                f"  Success rate: {rate * 100:.0f}% ({history_len} checks)"
            )

        info.update("\n".join(lines))

        if srv.is_managed and self._mcp_manager is not None:
            self._stream_mcp_console(name)

    @work(thread=True, group="cfg-mcp-console")
    def _stream_mcp_console(self, name: str) -> None:
        import queue as _queue

        from textual.worker import get_current_worker

        if self._mcp_manager is None:
            return
        console = self.query_one("#cfg-mcp-console", RichLog)
        self.app.call_from_thread(console.clear)

        entry = self._mcp_manager.get_entry(name)
        if entry is None:
            return

        from rich.text import Text

        history_lines = [Text.from_ansi(line) for line in list(entry.ring)]

        def _write_history() -> None:
            for text in history_lines:
                console.write(text)

        self.app.call_from_thread(_write_history)

        current = get_current_worker()
        while self._selected_server == name:
            if current.is_cancelled:
                break
            try:
                line = entry.output_queue.get(timeout=0.5)
                self.app.call_from_thread(console.write, Text.from_ansi(line))
            except _queue.Empty:
                continue

    # ------------------------------------------------------------------
    # MCP tool metrics
    # ------------------------------------------------------------------

    def _refresh_mcp_tools(self, server_name: str) -> None:
        from airlock.fast.state import store

        table = self.query_one("#cfg-mcp-tools-table", _SafeDataTable)
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

    def _set_mcp_status_error(self, msg: str) -> None:
        status = self.query_one("#cfg-mcp-status", Static)
        status.update(f"[red]Error:[/] {msg}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mask_env(var: str) -> str:
        val = os.getenv(var, "")
        if val:
            return val[:4] + "*" * max(0, len(val) - 4)
        return ""

    @staticmethod
    def _load_signal_weights() -> tuple[str, str, str]:
        """Return (pii_weight, keyword_weight, threat_weight) as strings."""
        pii_w, kw_w, threat_w = "0.40", "0.40", "0.20"
        try:
            from airlock.slow.tuner import load_knobs

            knobs = load_knobs()
            if knobs and knobs.weights:
                w = knobs.weights
                pii_w = str(w.get("pii_scan", 0.40))
                kw_w = str(w.get("keyword_scan", 0.40))
                threat_w = str(w.get("threat_read", 0.20))
        except ImportError:
            pass
        return pii_w, kw_w, threat_w

    @staticmethod
    def _load_threat_defaults() -> dict[str, str]:
        """Load threat detector constants, falling back to defaults."""
        defaults = {
            "block_threshold": "0.7",
            "base_backoff": "2.0",
            "max_backoff": "3600",
            "volume_spike": "5.0",
            "rapid_fire": "0.1",
            "payload_max": "100000",
            "error_rate": "0.8",
        }
        try:
            from airlock.fast.threat_detector import (
                BASE_BACKOFF_S,
                ERROR_PROBE_RATE,
                LARGE_PAYLOAD_CHARS,
                MAX_BACKOFF_S,
                RAPID_FIRE_MIN_GAP_S,
                THREAT_BLOCK_THRESHOLD,
                VOLUME_SPIKE_MULTIPLIER,
            )

            defaults["block_threshold"] = str(THREAT_BLOCK_THRESHOLD)
            defaults["base_backoff"] = str(BASE_BACKOFF_S)
            defaults["max_backoff"] = str(MAX_BACKOFF_S)
            defaults["volume_spike"] = str(VOLUME_SPIKE_MULTIPLIER)
            defaults["rapid_fire"] = str(RAPID_FIRE_MIN_GAP_S)
            defaults["payload_max"] = str(LARGE_PAYLOAD_CHARS)
            defaults["error_rate"] = str(ERROR_PROBE_RATE)
        except ImportError:
            pass
        return defaults
