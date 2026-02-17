"""Dashboard screen — proxy health, guardrails, and model overview."""

from __future__ import annotations

import urllib.request
from datetime import datetime

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from airlock.tui.widgets.status_indicator import StatusIndicator


class DashboardPane(Vertical):
    """At-a-glance proxy health and traffic overview."""

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: str = "4000",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._host = host
        self._port = port

    def compose(self) -> ComposeResult:
        with Horizontal(id="dash-top-row"):
            with Vertical(id="dash-proxy-status"):
                yield Static("[bold]Proxy Status[/]")
                yield StatusIndicator(
                    "Checking...", status="warn", id="proxy-indicator"
                )
                yield Static("", id="proxy-detail")
            with Vertical(id="dash-guardrails"):
                yield Static("[bold]Guardrails[/]")
                yield StatusIndicator("PII Guard", status="ok", id="guard-pii")
                yield StatusIndicator("Keyword Guard", status="ok", id="guard-kw")
                yield StatusIndicator("Fast Guardian", status="ok", id="guard-fast")
        table = DataTable(id="dash-model-table")
        table.add_columns("Model", "Circuit", "Reqs", "Err%", "Avg Latency")
        yield table

    def on_mount(self) -> None:
        self._check_health()
        self._refresh_state()
        self.set_interval(5.0, self._check_health)
        self.set_interval(5.0, self._refresh_state)

    @work(exclusive=True, thread=True)
    def _check_health(self) -> None:
        indicator = self.query_one("#proxy-indicator", StatusIndicator)
        detail = self.query_one("#proxy-detail", Static)
        url = f"http://{self._host}:{self._port}/health"
        try:
            urllib.request.urlopen(url, timeout=3)  # noqa: S310
            indicator.set_status("ok", f"Running at {self._host}:{self._port}")
            detail.update(f"Last checked: {datetime.now().strftime('%H:%M:%S')}")
        except Exception:
            indicator.set_status("error", f"Not reachable at {self._host}:{self._port}")
            detail.update(f"Last checked: {datetime.now().strftime('%H:%M:%S')}")

    @work(exclusive=True, thread=True)
    def _refresh_state(self) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        table = self.query_one("#dash-model-table", DataTable)
        table.clear()

        for name, model in store.all_models().items():
            avg_lat = model.recent_avg_latency()
            lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"
            total = len(model.success_times) + len(model.failure_times)
            err_count = len(model.failure_times)
            err_pct = f"{err_count / total * 100:.1f}%" if total > 0 else "-"
            circuit = model.circuit.value.upper()
            table.add_row(name, circuit, str(total), err_pct, lat_str)

        if not store.all_models():
            table.add_row("-", "-", "-", "-", "-")
