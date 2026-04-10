"""Overview screen — unified btop++-style dense view.

Merges Dashboard, Models, Threats, and Clients into a single pane with
context-sensitive detail, provider→model filtering, proxy management,
and alert integration.
"""

from __future__ import annotations

import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.strip import Strip
from textual.widgets import Button, Collapsible, DataTable, RichLog, Static

from airlock.tui.widgets.safe_data_table import _SafeDataTable
from airlock.tui.widgets.status_indicator import StatusIndicator

if TYPE_CHECKING:
    from airlock.tui.proxy_manager import ProxyManager


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


class _SafeRichLog(RichLog):
    """RichLog with bounds guard, sticky scroll, and text selection.

    1. Bounds guard: Textual bug where render_line requests self.lines[max_lines]
       (one past the end) when the log is full and scrolled to the bottom.
    2. Sticky scroll: new writes only auto-scroll if the view is already at the
       bottom, so the user can freely scroll up to read history without the log
       jumping away.
    3. Text selection: overrides get_selection() so Textual's native click-drag
       selection works.  Click and drag to select; Ctrl+C copies via OSC52.
    """

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        if y >= len(self.lines):
            return Strip.blank(width, self.rich_style)
        return super()._render_line(y, scroll_x, width)

    def write(  # type: ignore[override]
        self,
        content,
        width=None,
        expand=False,
        shrink=True,
        scroll_end=None,
        animate=False,
    ) -> RichLog:
        """Only auto-scroll when already at the bottom (sticky scroll)."""
        if scroll_end is None:
            scroll_end = self.is_vertical_scroll_end
        return super().write(
            content,
            width=width,
            expand=expand,
            shrink=shrink,
            scroll_end=scroll_end,
            animate=animate,
        )

    def get_selection(self, selection) -> tuple[str, str] | None:
        """Extract plain text from Strip lines for Textual's selection / Ctrl+C copy."""
        if not self.lines:
            return None
        from textual.geometry import Offset
        from textual.selection import Selection as _Sel

        scroll_y = self.scroll_offset.y

        def _plain(strip: Strip) -> str:
            return "".join(seg.text for seg in strip).rstrip()

        start = selection.start
        end = selection.end
        adj_start = Offset(start.x, start.y + scroll_y) if start is not None else None
        adj_end = Offset(end.x, end.y + scroll_y) if end is not None else None
        full_text = "\n".join(_plain(line) for line in self.lines)
        return _Sel(adj_start, adj_end).extract(full_text), "\n"


def _enforce_color(mode: str) -> str:
    """Return Rich markup color for the enforcement mode."""
    if mode == "enforce":
        return "red"
    if mode == "shadow":
        return "yellow"
    return "dim"


# ---------------------------------------------------------------------------
# OverviewPane
# ---------------------------------------------------------------------------


class OverviewPane(VerticalScroll):
    """Unified dense overview — proxy status, providers, models, clients."""

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
        self._stopping = False
        self._alert_text: str = "[dim]No alerts[/]"
        self._provider_filter: str | None = None

    # -- compose ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # --- top row: status + alerts ---
        with Horizontal(id="ov-top-row"):
            with Vertical(id="ov-status-panel"):
                yield StatusIndicator(
                    "Checking...",
                    status="warn",
                    id="ov-proxy-indicator",
                )
                yield Static("", id="ov-proxy-detail")
                yield Button(
                    "Checking...",
                    id="ov-proxy-btn",
                    variant="default",
                    disabled=True,
                )
                yield Static("", id="ov-status-line")
            with Vertical(id="ov-alerts-panel"):
                yield Static("[bold]Alerts[/]", id="ov-alerts-header")
                yield Static(self._alert_text, id="ov-alerts")

        # --- proxy console (collapsible) ---
        with Collapsible(title="Proxy Console", id="ov-console-collapsible"):
            yield _SafeRichLog(id="ov-console-log", max_lines=500)

        # --- providers table ---
        yield Static("[bold]Providers[/]", id="ov-providers-header")
        providers = _SafeDataTable(id="ov-providers", cursor_type="row")
        providers.add_columns(
            "Provider",
            "Status",
            "Req/5m",
            "Err%",
            "Recovery",
            "Impacted",
        )
        yield providers

        # --- models table ---
        yield Static("[bold]Models[/]", id="ov-models-header")
        models = _SafeDataTable(id="ov-models", cursor_type="row")
        models.add_columns(
            "Model",
            "Circuit",
            "Failures",
            "Latency",
            "p95",
            "Failover Chain",
        )
        yield models

        # --- clients table ---
        yield Static("[bold]Clients[/]", id="ov-clients-header")
        clients = _SafeDataTable(id="ov-clients", cursor_type="row")
        clients.add_columns(
            "Client",
            "Req/5m",
            "Err%",
            "Latency",
            "Threat",
            "Backoff",
            "Quarantines",
        )
        yield clients

        # --- context-sensitive detail ---
        yield Static("", id="ov-detail")

    # -- lifecycle ----------------------------------------------------------

    def on_mount(self) -> None:
        self._probe_external()
        self._refresh_state()
        self.set_interval(300.0, self._probe_external)
        self.set_interval(5.0, self._refresh_state)

    # -- collapsible toggle -------------------------------------------------

    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        if event.collapsible.id == "ov-console-collapsible":
            if event.collapsible.collapsed:
                event.collapsible.remove_class("-expanded")
            else:
                event.collapsible.add_class("-expanded")

    # -- button handling ----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ov-proxy-btn":
            return
        if event.button.label.plain == "Stop Proxy":
            self.action_stop_proxy()
        else:
            self.action_start_proxy()

    # -- proxy management ---------------------------------------------------

    def action_start_proxy(self) -> None:
        """Start the proxy via ProxyManager."""
        if self._proxy_manager is None:
            return
        self._stopping = False
        err = self._proxy_manager.start()
        btn = self.query_one("#ov-proxy-btn", Button)
        console = self.query_one("#ov-console-log", _SafeRichLog)
        if err:
            console.write(f"[red]Error:[/] {err}")
            return
        btn.label = "Stop Proxy"
        btn.variant = "error"
        collapsible = self.query_one("#ov-console-collapsible", Collapsible)
        collapsible.collapsed = False
        collapsible.add_class("-expanded")
        console.write("[green]Proxy started.[/]")
        indicator = self.query_one("#ov-proxy-indicator", StatusIndicator)
        indicator.set_status("warn", "Starting...")
        self._stream_proxy_output()
        self._watch_proxy_process()

    def action_stop_proxy(self) -> None:
        """Stop the TUI-owned proxy."""
        if self._proxy_manager is None:
            return
        self._stopping = True
        self._proxy_manager.stop()
        btn = self.query_one("#ov-proxy-btn", Button)
        btn.label = "Start Proxy"
        btn.variant = "success"
        btn.disabled = False
        indicator = self.query_one("#ov-proxy-indicator", StatusIndicator)
        indicator.set_status("error", f"Not reachable at {self._host}:{self._port}")
        detail = self.query_one("#ov-proxy-detail", Static)
        detail.update(
            f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
        )
        console = self.query_one("#ov-console-log", _SafeRichLog)
        console.write("[yellow]Proxy stopped.[/]")

    @work(thread=True, group="ov-proxy-stdout")
    def _stream_proxy_output(self) -> None:
        """Read proxy output lines into the RichLog; detect startup completion."""
        import queue as _queue

        from rich.text import Text

        if self._proxy_manager is None:
            return
        q = self._proxy_manager.output_queue
        console = self.query_one("#ov-console-log", _SafeRichLog)
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
        indicator = self.query_one("#ov-proxy-indicator", StatusIndicator)
        indicator.set_status("ok", f"Running at {self._host}:{self._port}")
        detail = self.query_one("#ov-proxy-detail", Static)
        detail.update(
            f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
        )

    @work(thread=True, group="ov-proxy-watcher")
    def _watch_proxy_process(self) -> None:
        """Block until the TUI-owned process exits, then update UI if unexpected."""
        if self._proxy_manager is None:
            return
        rc = self._proxy_manager.wait_for_exit()
        if rc is None:
            return
        if not self._stopping:
            self.app.call_from_thread(self._on_proxy_exited)

    def _on_proxy_exited(self) -> None:
        """Called when the proxy exits without action_stop_proxy being invoked."""
        indicator = self.query_one("#ov-proxy-indicator", StatusIndicator)
        indicator.set_status("error", "Proxy exited unexpectedly")
        btn = self.query_one("#ov-proxy-btn", Button)
        btn.label = "Start Proxy"
        btn.variant = "success"
        btn.disabled = False
        detail = self.query_one("#ov-proxy-detail", Static)
        detail.update(
            f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
        )
        console = self.query_one("#ov-console-log", _SafeRichLog)
        console.write("[red]Proxy exited unexpectedly.[/]")

    # -- external proxy probe (HTTP) ----------------------------------------

    @work(exclusive=True, thread=True, group="ov-health-check")
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
            if self._proxy_manager is not None and self._proxy_manager.is_tui_owned:
                return

            indicator = self.query_one("#ov-proxy-indicator", StatusIndicator)
            detail = self.query_one("#ov-proxy-detail", Static)
            btn = self.query_one("#ov-proxy-btn", Button)

            if proxy_reachable:
                indicator.set_status("ok", f"Running at {self._host}:{self._port}")
                btn.label = "Running Externally"
                btn.variant = "default"
                btn.disabled = True
                self._externally_running = True
            else:
                indicator.set_status(
                    "error",
                    f"Not reachable at {self._host}:{self._port}",
                )
                btn.label = "Start Proxy"
                btn.variant = "success"
                btn.disabled = False
                self._externally_running = False

            detail.update(
                f"Last checked: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
            )

        self.app.call_from_thread(_update_ui)

    # -- alert integration --------------------------------------------------

    def update_alerts(self, text: str) -> None:
        """Called by AlertEngine to update the alerts panel."""
        self._alert_text = text
        try:
            self.query_one("#ov-alerts", Static).update(text)
        except Exception:
            pass

    # -- provider → model filtering -----------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle provider→model filter on Enter."""
        if event.data_table.id != "ov-providers":
            return
        if event.row_key is None:
            return
        provider_name = str(event.row_key.value)
        if provider_name.startswith("_empty"):
            return

        if self._provider_filter == provider_name:
            self._provider_filter = None
        else:
            self._provider_filter = provider_name

        # Update header to show filter state
        header = self.query_one("#ov-models-header", Static)
        if self._provider_filter:
            header.update(
                f"[bold]Models[/] [dim](filtered: {escape(self._provider_filter)})[/]"
            )
        else:
            header.update("[bold]Models[/]")

        # Trigger immediate refresh
        self._refresh_state()

    # -- context-sensitive detail -------------------------------------------

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        if event.row_key is None:
            return
        key = str(event.row_key.value)
        if key.startswith("_empty"):
            return

        table_id = event.data_table.id
        if table_id == "ov-providers":
            self._show_provider_detail(key)
        elif table_id == "ov-models":
            self._show_model_detail(key)
        elif table_id == "ov-clients":
            self._show_client_detail(key)

    def _show_provider_detail(self, provider_name: str) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        detail = self.query_one("#ov-detail", Static)
        provider = store.all_providers().get(provider_name)
        if not provider:
            detail.update(f"No data for {escape(provider_name)}")
            return

        now = time.time()
        status = "QUARANTINED" if provider.is_quarantined(now) else "HEALTHY"
        mode = provider.recent_gemini_mode() or "-"
        impacted = sorted(provider.impacted_clients())
        impacted_str = ", ".join(impacted) if impacted else "none"

        detail.update(
            f"[bold]{escape(provider_name)}[/]\n\n"
            f"  Status: {status}\n"
            f"  Gemini mode: {mode}\n"
            f"  Gemini text: {provider.recent_gemini_outcome_count('text')}  "
            f"thought_only: {provider.recent_gemini_outcome_count('thought_only')}  "
            f"tool: {provider.recent_gemini_outcome_count('tool')}\n"
            f"  Impacted clients: {impacted_str}"
        )

    def _show_model_detail(self, model_name: str) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        detail = self.query_one("#ov-detail", Static)
        model = store.all_models().get(model_name)
        if not model:
            detail.update(f"No data for {escape(model_name)}")
            return

        avg_lat = model.recent_avg_latency()
        lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"

        recent = [lat for _, lat in model.latencies_ms if lat > 0]
        if recent:
            sorted_lat = sorted(recent)
            p50 = sorted_lat[len(sorted_lat) // 2]
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
            percentiles = f"p50: {p50:.0f}ms  p95: {p95:.0f}ms"
        else:
            percentiles = "No latency data"

        circuit_cfg = (
            f"Failure threshold: {model.FAILURE_THRESHOLD}   "
            f"Recovery timeout: {model.RECOVERY_TIMEOUT}s   "
            f"Success threshold: {model.SUCCESS_THRESHOLD}"
        )

        detail.update(
            f"[bold]{escape(model_name)}[/]\n\n"
            f"  Circuit: {model.circuit.value.upper()}   "
            f"Avg latency: {lat_str}\n"
            f"  {percentiles}\n\n"
            f"  {circuit_cfg}"
        )

    def _show_client_detail(self, client_id: str) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        detail = self.query_one("#ov-detail", Static)
        rows: list[str] = [f"[bold]{escape(client_id)}[/]"]
        now = time.time()

        client = store.all_clients().get(client_id)
        if client:
            rows.append(
                f"  Threat score: {client.threat_score:.2f}   "
                f"Backoff: {'active' if client.backoff_until > now else 'none'}"
            )
            rows.append(
                f"  Gemini text={client.recent_gemini_outcome_count('text')} "
                f"thought_only={client.recent_gemini_outcome_count('thought_only')} "
                f"tool={client.recent_gemini_outcome_count('tool')}"
            )
            rows.append("")

        found = False
        for (cp_client, provider), state in sorted(
            store.all_client_provider_states().items(),
        ):
            if cp_client != client_id:
                continue
            found = True
            status = "QUARANTINED" if state.is_quarantined(now) else "healthy"
            cooldown = (
                f"{state.cooldown_remaining(now):.0f}s"
                if state.is_quarantined(now)
                else "-"
            )
            rows.append(
                f"  {provider:<12} status={status:<11} req/5m={state.recent_request_count():<4} "
                f"429s={state.recent_rate_limit_count():<3} cooldown={cooldown} "
                f"reason={state.last_reason or '-'}"
            )

        if not found:
            rows.append("  No provider activity recorded.")

        detail.update("\n".join(rows))

    # -- unified data refresh -----------------------------------------------

    @work(exclusive=True, thread=True, group="ov-state-refresh")
    def _refresh_state(self) -> None:
        """Single worker that refreshes all tables and status indicators."""
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        try:
            from airlock.fast.circuit_breaker import _load_failover_map
        except ImportError:
            _load_failover_map = None  # type: ignore[assignment]

        now = time.time()

        # --- providers ---
        provider_rows: list[tuple[str, ...]] = []
        provider_keys: list[str] = []
        for name, provider in store.all_providers().items():
            status = "QUARANTINED" if provider.is_quarantined(now) else "HEALTHY"
            recovery = "-"
            if provider.is_quarantined(now):
                recovery = f"{provider.cooldown_remaining(now):.0f}s left"
            requests = str(provider.recent_request_count())
            err_rate = f"{provider.recent_error_rate() * 100:.1f}%"
            impacted = str(len(provider.impacted_clients()))
            provider_rows.append(
                (name, status, requests, err_rate, recovery, impacted),
            )
            provider_keys.append(name)

        # --- models (with optional provider filter) ---
        model_rows: list[tuple[str, ...]] = []
        model_keys: list[str] = []
        failover_map: dict[str, list[str]] = {}
        if _load_failover_map is not None:
            failover_map = _load_failover_map()

        all_models = store.all_models()
        for name, model in all_models.items():
            # Apply provider filter if active
            if self._provider_filter:
                # Infer provider from model name
                try:
                    from airlock.fast.router import infer_provider

                    model_provider = infer_provider(name)
                except ImportError:
                    model_provider = None
                if model_provider != self._provider_filter:
                    continue

            circuit = model.circuit.value.upper()
            failures = str(model.consecutive_failures)
            avg_lat = model.recent_avg_latency()
            lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"

            recent = [lat for _, lat in model.latencies_ms if lat > 0]
            if recent:
                sorted_lat = sorted(recent)
                p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
                p95_str = f"{p95:.0f}ms"
            else:
                p95_str = "-"

            chain = " -> ".join(failover_map.get(name, ["-"]))
            model_rows.append(
                (name, circuit, failures, lat_str, p95_str, chain),
            )
            model_keys.append(name)

        # --- clients ---
        client_rows: list[tuple[str, ...]] = []
        client_keys: list[str] = []
        cp_states = store.all_client_provider_states()

        for client_id, client in store.all_clients().items():
            quarantines = sum(
                1
                for (cp_client, _), state in cp_states.items()
                if cp_client == client_id and state.is_quarantined(now)
            )
            avg_lat = client.recent_avg_latency()
            lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"
            threat = f"{client.threat_score:.2f}" if client.threat_score > 0 else "-"
            backoff = "-"
            if client.backoff_until > now:
                backoff = f"{client.backoff_until - now:.0f}s"
            client_rows.append(
                (
                    client_id,
                    str(client.recent_request_count()),
                    f"{client.recent_error_rate() * 100:.1f}%",
                    lat_str,
                    threat,
                    backoff,
                    str(quarantines),
                )
            )
            client_keys.append(client_id)

        # --- traffic split / MCP ---
        llm_count, mcp_count = store.traffic_split()
        traffic_total = llm_count + mcp_count
        mcp_tools = store.all_mcp_tools()

        # --- enforcement mode ---
        enforce_mode = os.getenv("AIRLOCK_ENFORCE_MODE", "observe")
        enforce_clr = _enforce_color(enforce_mode)

        # --- push to UI ---
        def _update_ui() -> None:
            # Providers table
            ptable = self.query_one("#ov-providers", _SafeDataTable)
            ptable.clear()
            if provider_rows:
                for row, key in zip(provider_rows, provider_keys):
                    ptable.add_row(*row, key=key)
            else:
                ptable.add_row(
                    "(no providers tracked)",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    key="_empty-providers",
                )

            # Models table
            mtable = self.query_one("#ov-models", _SafeDataTable)
            mtable.clear()
            if model_rows:
                for row, key in zip(model_rows, model_keys):
                    mtable.add_row(*row, key=key)
            else:
                mtable.add_row(
                    "(no models tracked)",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    key="_empty-models",
                )

            # Clients table
            ctable = self.query_one("#ov-clients", _SafeDataTable)
            ctable.clear()
            if client_rows:
                for row, key in zip(client_rows, client_keys):
                    ctable.add_row(*row, key=key)
            else:
                ctable.add_row(
                    "(no clients tracked)",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    key="_empty-clients",
                )

            # Status line: enforcement mode + traffic split + MCP
            if traffic_total > 0:
                llm_pct = llm_count * 100 // traffic_total
                mcp_pct = mcp_count * 100 // traffic_total
                split_str = (
                    f"LLM: {llm_count} ({llm_pct}%) | MCP: {mcp_count} ({mcp_pct}%)"
                )
            else:
                split_str = "LLM: 0 | MCP: 0"

            mcp_str = "MCP: 0"
            if mcp_tools:
                if any(t.recent_error_rate() > 0.5 for t in mcp_tools.values()):
                    mcp_str = f"MCP: [red]{len(mcp_tools)} tools (errors)[/]"
                else:
                    mcp_str = f"MCP: {len(mcp_tools)} tools"

            status_line = self.query_one("#ov-status-line", Static)
            status_line.update(
                f"Guard: [{enforce_clr}]{enforce_mode}[/]  {split_str}  {mcp_str}"
            )

        self.app.call_from_thread(_update_ui)
