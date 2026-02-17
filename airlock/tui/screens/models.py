"""Models screen — circuit breaker states and per-model metrics."""

from __future__ import annotations

import time

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class ModelsPane(Vertical):
    """Per-model health with circuit breaker detail."""

    def compose(self) -> ComposeResult:
        table = DataTable(id="models-table", cursor_type="row")
        table.add_columns(
            "Model", "Circuit", "Failures", "Recovery", "Failover Chain"
        )
        yield table
        yield Static("Select a model to view details.", id="models-detail")

    def on_mount(self) -> None:
        self._refresh_models()
        self.set_interval(5.0, self._refresh_models)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self._show_detail(str(event.row_key.value))

    @work(exclusive=True, thread=True)
    def _refresh_models(self) -> None:
        try:
            from airlock.fast.state import store
            from airlock.fast.circuit_breaker import _load_failover_map
        except ImportError:
            return

        table = self.query_one("#models-table", DataTable)
        table.clear()
        failover_map = _load_failover_map()
        now = time.time()

        for name, model in store.all_models().items():
            circuit = model.circuit.value.upper()
            failures = str(model.consecutive_failures)
            recovery = "-"
            if circuit == "OPEN":
                remaining = model.RECOVERY_TIMEOUT - (now - model.last_state_change)
                recovery = f"{max(0, remaining):.0f}s left" if remaining > 0 else "ready"
            chain = " -> ".join(failover_map.get(name, ["-"]))
            table.add_row(name, circuit, failures, recovery, chain, key=name)

        if not store.all_models():
            table.add_row("(no models tracked)", "-", "-", "-", "-", key="_empty")

    def _show_detail(self, model_name: str) -> None:
        try:
            from airlock.fast.state import store, CircuitState
        except ImportError:
            return

        detail = self.query_one("#models-detail", Static)
        model = store.all_models().get(model_name)
        if not model:
            detail.update(f"No data for {model_name}")
            return

        avg_lat = model.recent_avg_latency()
        lat_str = f"{avg_lat:.0f}ms" if avg_lat else "-"

        # Compute p95 from recent latencies
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
            f"[bold]{model_name}[/]\n\n"
            f"  Circuit: {model.circuit.value.upper()}   "
            f"Avg latency: {lat_str}\n"
            f"  {percentiles}\n\n"
            f"  {circuit_cfg}"
        )
