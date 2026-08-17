"""Unified configuration screen — merges Settings and MCP Servers into a tabbed view."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
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


@dataclass(frozen=True)
class _ConfigField:
    """One ConfigPane control and the effect of applying it."""

    widget_id: str
    label: str
    env_var: str | None
    widget_type: str
    restart_required: bool = False
    applies: bool = True


# The Config screen is a runtime-environment editor, not a config-file editor. Keep
# this table as the one authoritative applicability contract for its controls.
_CONFIG_FIELDS: tuple[_ConfigField, ...] = (
    _ConfigField(
        "cfg-anthropic-key",
        "Anthropic API Key",
        "ANTHROPIC_API_KEY",
        "input",
        True,
        False,
    ),
    _ConfigField(
        "cfg-openai-key", "OpenAI API Key", "OPENAI_API_KEY", "input", True, False
    ),
    _ConfigField(
        "cfg-master-key", "Master Key", "AIRLOCK_MASTER_KEY", "input", True, False
    ),
    _ConfigField(
        "cfg-enforce-mode", "Enforcement Mode", "AIRLOCK_ENFORCE_MODE", "select"
    ),
    _ConfigField("cfg-weight-pii", "pii_scan weight", None, "input", applies=False),
    _ConfigField(
        "cfg-weight-keyword", "keyword_scan weight", None, "input", applies=False
    ),
    _ConfigField(
        "cfg-weight-threat", "threat_read weight", None, "input", applies=False
    ),
    _ConfigField("cfg-pii-enabled", "PII Guard", "AIRLOCK_PII_ENABLED", "switch"),
    _ConfigField(
        "cfg-pii-entities", "PII Entity Types", "AIRLOCK_PII_ENTITIES", "input"
    ),
    _ConfigField("cfg-kw-enabled", "Keyword Guard", "AIRLOCK_KW_ENABLED", "switch"),
    _ConfigField(
        "cfg-blocked-keywords", "Blocked Keywords", "AIRLOCK_BLOCKED_KEYWORDS", "input"
    ),
    _ConfigField(
        "cfg-threat-block-threshold", "Block Threshold", None, "input", applies=False
    ),
    _ConfigField(
        "cfg-threat-base-backoff",
        "Base Backoff (seconds)",
        None,
        "input",
        applies=False,
    ),
    _ConfigField(
        "cfg-threat-max-backoff", "Max Backoff (seconds)", None, "input", applies=False
    ),
    _ConfigField(
        "cfg-threat-volume-spike",
        "Volume Spike Multiplier",
        None,
        "input",
        applies=False,
    ),
    _ConfigField(
        "cfg-threat-rapid-fire",
        "Rapid-Fire Min Gap (seconds)",
        None,
        "input",
        applies=False,
    ),
    _ConfigField(
        "cfg-threat-payload-max", "Payload Max Chars", None, "input", applies=False
    ),
    _ConfigField(
        "cfg-threat-error-rate", "Error Probe Rate", None, "input", applies=False
    ),
    _ConfigField(
        "cfg-mcp-allowed",
        "Allowed Tools (comma-separated)",
        "AIRLOCK_MCP_ALLOWED_TOOLS",
        "input",
    ),
    _ConfigField(
        "cfg-mcp-blocked",
        "Blocked Tools (comma-separated)",
        "AIRLOCK_MCP_BLOCKED_TOOLS",
        "input",
    ),
    _ConfigField("cfg-log-dir", "Log Directory", "AIRLOCK_LOG_DIR", "input", True),
    _ConfigField(
        "cfg-s3-bucket", "S3 Bucket (optional)", "AIRLOCK_S3_BUCKET", "input", True
    ),
    _ConfigField("cfg-sql-url", "SQL URL (optional)", "AIRLOCK_SQL_URL", "input", True),
    _ConfigField("cfg-host", "Host", "AIRLOCK_HOST", "input", True),
    _ConfigField("cfg-port", "Port", "AIRLOCK_PORT", "input", True),
    _ConfigField(
        "cfg-timeout", "Request Timeout (seconds)", None, "input", applies=False
    ),
    _ConfigField(
        "cfg-failover-map", "Failover Map (JSON)", "AIRLOCK_FAILOVER_MAP", "input", True
    ),
)
_CONFIG_FIELD_BY_ID = {field.widget_id: field for field in _CONFIG_FIELDS}


class ConfigPane(Vertical):
    """Unified configuration management with tabbed sections."""

    def __init__(
        self,
        *,
        mcp_manager: McpServerManager | None = None,
        host: str = "localhost",
        port: str = "4000",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._mcp_manager: McpServerManager | None = mcp_manager
        self._host = host
        self._port = port
        self._selected_server: str = ""
        self._applied_values: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with TabbedContent(id="config-tabs"):
            # Tab 1 — Providers
            with TabPane("Providers", id="cfg-tab-providers"):
                with VerticalScroll(classes="config-form"):
                    yield Label(self._field_label("cfg-anthropic-key"))
                    yield Input(
                        value=self._mask_env("ANTHROPIC_API_KEY"),
                        password=True,
                        id="cfg-anthropic-key",
                    )
                    yield Label(self._field_label("cfg-openai-key"))
                    yield Input(
                        value=self._mask_env("OPENAI_API_KEY"),
                        password=True,
                        id="cfg-openai-key",
                    )
                    yield Label(self._field_label("cfg-master-key"))
                    yield Input(
                        value=self._mask_env("AIRLOCK_MASTER_KEY"),
                        password=True,
                        id="cfg-master-key",
                    )

            # This is deliberately separate from the local environment editor
            # above.  It reads only the proxy child snapshot over Admin HTTP.
            with TabPane("Configured", id="cfg-tab-configured-providers"):
                yield Static(
                    "Loading configured provider state from the proxy…",
                    id="cfg-provider-config-status",
                )
                configured = _SafeDataTable(
                    id="cfg-provider-config-table", cursor_type="row"
                )
                configured.add_columns(
                    "Provider", "Alias", "Underlying", "Endpoints", "Credential"
                )
                yield configured
                yield Static(
                    "Read-only child startup configuration. Changes require deployment workflow and proxy restart.",
                    id="cfg-provider-config-detail",
                )

            # Tab 2 — Guardrails
            with TabPane("Guardrails", id="cfg-tab-guardrails"):
                with VerticalScroll(classes="config-form"):
                    # Enforcement mode
                    yield Label(self._field_label("cfg-enforce-mode"))
                    yield Select(
                        [
                            ("Observe", "observe"),
                            ("Shadow", "shadow"),
                            ("Enforce", "enforce"),
                        ],
                        value=os.getenv("AIRLOCK_ENFORCE_MODE", "observe"),
                        id="cfg-enforce-mode",
                        allow_blank=False,
                    )

                    # Signal weights
                    pii_w, kw_w, threat_w = self._load_signal_weights()
                    yield Label("Signal Weights (orchestrator; not applied by Apply)")
                    yield Label(self._field_label("cfg-weight-pii"))
                    yield Input(value=pii_w, id="cfg-weight-pii")
                    yield Label(self._field_label("cfg-weight-keyword"))
                    yield Input(value=kw_w, id="cfg-weight-keyword")
                    yield Label(self._field_label("cfg-weight-threat"))
                    yield Input(value=threat_w, id="cfg-weight-threat")

                    # Existing guardrail toggles
                    yield Label(self._field_label("cfg-pii-enabled"))
                    yield Switch(
                        value=_env_flag("AIRLOCK_PII_ENABLED"),
                        id="cfg-pii-enabled",
                    )
                    yield Label(self._field_label("cfg-pii-entities"))
                    yield Input(
                        value=os.getenv(
                            "AIRLOCK_PII_ENTITIES",
                            "CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER",
                        ),
                        id="cfg-pii-entities",
                    )
                    yield Label(self._field_label("cfg-kw-enabled"))
                    yield Switch(
                        value=_env_flag("AIRLOCK_KW_ENABLED"),
                        id="cfg-kw-enabled",
                    )
                    yield Label(self._field_label("cfg-blocked-keywords"))
                    yield Input(
                        value=os.getenv("AIRLOCK_BLOCKED_KEYWORDS", ""),
                        id="cfg-blocked-keywords",
                    )

            # Tab 3 — Protection
            with TabPane("Protection", id="cfg-tab-protection"):
                with VerticalScroll(classes="config-form"):
                    threat_vals = self._load_threat_defaults()
                    yield Label(self._field_label("cfg-threat-block-threshold"))
                    yield Input(
                        value=threat_vals["block_threshold"],
                        id="cfg-threat-block-threshold",
                    )
                    yield Label(self._field_label("cfg-threat-base-backoff"))
                    yield Input(
                        value=threat_vals["base_backoff"], id="cfg-threat-base-backoff"
                    )
                    yield Label(self._field_label("cfg-threat-max-backoff"))
                    yield Input(
                        value=threat_vals["max_backoff"], id="cfg-threat-max-backoff"
                    )
                    yield Label(self._field_label("cfg-threat-volume-spike"))
                    yield Input(
                        value=threat_vals["volume_spike"], id="cfg-threat-volume-spike"
                    )
                    yield Label(self._field_label("cfg-threat-rapid-fire"))
                    yield Input(
                        value=threat_vals["rapid_fire"], id="cfg-threat-rapid-fire"
                    )
                    yield Label(self._field_label("cfg-threat-payload-max"))
                    yield Input(
                        value=threat_vals["payload_max"], id="cfg-threat-payload-max"
                    )
                    yield Label(self._field_label("cfg-threat-error-rate"))
                    yield Input(
                        value=threat_vals["error_rate"], id="cfg-threat-error-rate"
                    )

            # Tab 4 — MCP
            with TabPane("MCP", id="cfg-tab-mcp"):
                with Vertical(classes="config-form"):
                    yield Static("MCP Servers: loading...", id="cfg-mcp-status")
                    with Horizontal(id="cfg-mcp-actions"):
                        yield Button(
                            "Start",
                            id="cfg-mcp-start",
                            variant="success",
                            disabled=True,
                        )
                        yield Button(
                            "Stop", id="cfg-mcp-stop", variant="error", disabled=True
                        )
                        yield Button(
                            "Restart",
                            id="cfg-mcp-restart",
                            variant="warning",
                            disabled=True,
                        )
                        yield Button("Probe Now", id="cfg-mcp-probe", variant="primary")
                    table = _SafeDataTable(id="cfg-mcp-table", cursor_type="row")
                    table.add_columns(
                        "Name",
                        "Type",
                        "URL / Command",
                        "Health",
                        "Latency",
                        "PID",
                        "Uptime",
                    )
                    yield table
                    with TabbedContent(id="cfg-mcp-detail-tabs"):
                        with TabPane("Info", id="cfg-mcp-tab-info"):
                            yield Static(
                                "Select a server to view details.", id="cfg-mcp-info"
                            )
                        with TabPane("Console", id="cfg-mcp-tab-console"):
                            yield RichLog(id="cfg-mcp-console", max_lines=500)
                        with TabPane("Tools", id="cfg-mcp-tab-tools"):
                            tools_table = _SafeDataTable(
                                id="cfg-mcp-tools-table", cursor_type="row"
                            )
                            tools_table.add_columns(
                                "Tool", "Calls", "Err%", "Avg Latency"
                            )
                            yield tools_table
                    yield Label(self._field_label("cfg-mcp-allowed"))
                    yield Input(
                        value=os.getenv("AIRLOCK_MCP_ALLOWED_TOOLS", ""),
                        id="cfg-mcp-allowed",
                    )
                    yield Label(self._field_label("cfg-mcp-blocked"))
                    yield Input(
                        value=os.getenv("AIRLOCK_MCP_BLOCKED_TOOLS", ""),
                        id="cfg-mcp-blocked",
                    )

            # Tab 5 — Logging
            with TabPane("Logging", id="cfg-tab-logging"):
                with VerticalScroll(classes="config-form"):
                    yield Label(self._field_label("cfg-log-dir"))
                    yield Input(
                        value=os.getenv("AIRLOCK_LOG_DIR", "./logs"),
                        id="cfg-log-dir",
                    )
                    yield Label(self._field_label("cfg-s3-bucket"))
                    yield Input(
                        value=os.getenv("AIRLOCK_S3_BUCKET", ""),
                        id="cfg-s3-bucket",
                    )
                    yield Label(self._field_label("cfg-sql-url"))
                    yield Input(
                        value=os.getenv("AIRLOCK_SQL_URL", ""),
                        id="cfg-sql-url",
                    )

            # Tab 6 — Advanced
            with TabPane("Advanced", id="cfg-tab-advanced"):
                with VerticalScroll(classes="config-form"):
                    yield Label(self._field_label("cfg-host"))
                    yield Input(
                        value=os.getenv("AIRLOCK_HOST", "127.0.0.1"),
                        id="cfg-host",
                    )
                    yield Label(self._field_label("cfg-port"))
                    yield Input(
                        value=os.getenv("AIRLOCK_PORT", "4000"),
                        id="cfg-port",
                    )
                    yield Label(self._field_label("cfg-timeout"))
                    yield Input(value="300", id="cfg-timeout")
                    yield Label(self._field_label("cfg-failover-map"))
                    yield Input(
                        value=os.getenv("AIRLOCK_FAILOVER_MAP", ""),
                        id="cfg-failover-map",
                    )

        yield Static(
            "* restart required after Apply · controls marked not applied are unchanged",
            id="cfg-applicability-legend",
        )
        yield Button("Apply Changes", id="cfg-apply", variant="primary")
        yield Static("", id="cfg-status")

    # ------------------------------------------------------------------
    # Mount / timers
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._applied_values = self._current_values()
        if getattr(self.app, "_test_harness", False):
            return
        self._refresh_mcp_servers()
        self.set_interval(10.0, self._refresh_mcp_servers)
        self._refresh_provider_configuration()
        self.set_interval(30.0, self._refresh_provider_configuration)

    @work(thread=True)
    def _refresh_provider_configuration(self) -> None:
        """Refresh only through the Admin client; failure is an explicit state."""
        from airlock.tui.admin_client import provider_configuration_snapshot

        status, payload = provider_configuration_snapshot(self._host, self._port)
        if status == 200 and isinstance(payload.get("providers"), list):
            self.app.call_from_thread(
                self._render_provider_configuration, payload, None
            )
            return
        if status == 403:
            message = "Configured provider state unavailable: Admin token lacks admin:read_config."
        elif status == 404:
            message = "Configured provider state unavailable: Admin API is disabled."
        else:
            message = "Configured provider state unavailable: proxy did not return its startup snapshot."
        self.app.call_from_thread(self._render_provider_configuration, None, message)

    def _render_provider_configuration(
        self, payload: dict | None, error: str | None
    ) -> None:
        """Update read-only widgets on the UI thread from a bounded DTO."""
        status = self.query_one("#cfg-provider-config-status", Static)
        table = self.query_one("#cfg-provider-config-table", _SafeDataTable)
        detail = self.query_one("#cfg-provider-config-detail", Static)
        table.clear()
        if error:
            status.update(f"[yellow]{error}[/]")
            detail.update("No local configuration fallback is used.")
            return
        assert payload is not None
        for provider in payload["providers"]:
            for alias in provider.get("aliases", []):
                credential = alias.get("credential", {})
                credential_text = (
                    f"{credential.get('kind', 'none')}"
                    f" ({'configured' if credential.get('configured') else 'not configured'})"
                )
                table.add_row(
                    str(provider.get("provider", "-")),
                    str(alias.get("alias", "-")),
                    str(alias.get("underlying") or "-"),
                    ", ".join(alias.get("endpoints") or []) or "-",
                    credential_text,
                )
        status.update(
            f"Source: {payload.get('source', 'startup_config')} · loaded {payload.get('loaded_at', 'unknown')} · restart required"
        )
        truncation = payload.get("truncated", {})
        detail.update(
            "Read-only child startup configuration. Changes require deployment workflow and proxy restart."
            + (" Results were truncated." if any(truncation.values()) else "")
        )

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
            current = self._current_values()
            changed = [
                field
                for field in _CONFIG_FIELDS
                if current[field.widget_id]
                != self._applied_values.get(field.widget_id, "")
            ]
            if not changed:
                status.update("[dim]No settings changed.[/]")
                return

            applied: list[_ConfigField] = []
            ignored: list[_ConfigField] = []
            for field in changed:
                if not field.applies or field.env_var is None:
                    ignored.append(field)
                    continue

                value = current[field.widget_id]
                # Preserve the screen's prior contract: blank input values do not
                # overwrite the runtime environment. Report that honestly instead of
                # claiming the change took effect.
                if field.widget_type == "input" and not value:
                    ignored.append(field)
                    continue

                os.environ[field.env_var] = value
                self._applied_values[field.widget_id] = value
                applied.append(field)

            status.update(self._apply_status(applied, ignored))
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/]")

    def _current_values(self) -> dict[str, str]:
        """Return normalized widget values without exposing secret contents."""
        values: dict[str, str] = {}
        for field in _CONFIG_FIELDS:
            if field.widget_type == "input":
                values[field.widget_id] = self.query_one(
                    f"#{field.widget_id}", Input
                ).value.strip()
            elif field.widget_type == "switch":
                switch = self.query_one(f"#{field.widget_id}", Switch)
                values[field.widget_id] = "true" if switch.value else "false"
            else:
                select = self.query_one(f"#{field.widget_id}", Select)
                value = select.value
                values[field.widget_id] = (
                    "" if value is None or value == Select.BLANK else str(value)
                )
        return values

    @staticmethod
    def _apply_status(applied: list[_ConfigField], ignored: list[_ConfigField]) -> str:
        """Compose an accurate, value-free Apply result for the operator."""
        live = [field.label for field in applied if not field.restart_required]
        restart = [field.label for field in applied if field.restart_required]
        not_applied = [field.label for field in ignored]

        lines: list[str] = []
        if live:
            lines.append(f"[green]Effective immediately: {', '.join(live)}.[/]")
        if restart:
            lines.append(
                f"[yellow]Proxy restart required for: {', '.join(restart)}.[/]"
            )
        if not_applied:
            lines.append(
                f"[yellow]Not applied by this screen: {', '.join(not_applied)}.[/]"
            )
        return " ".join(lines) or "[dim]No settings were applied.[/]"

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
                    (
                        name,
                        type_label,
                        url_display,
                        health_str,
                        lat_str,
                        pid_str,
                        uptime_str,
                        name,
                    )
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
            lines.append(f"  Success rate: {rate * 100:.0f}% ({history_len} checks)")

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
    def _field_label(widget_id: str) -> str:
        """Render a field label with its applicability, never its value."""
        field = _CONFIG_FIELD_BY_ID[widget_id]
        qualifiers: list[str] = []
        if field.restart_required:
            qualifiers.append("restart required")
        if not field.applies:
            qualifiers.append("not applied by Apply")
        return f"{field.label} ({'; '.join(qualifiers)})" if qualifiers else field.label

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
