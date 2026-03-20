"""Clients screen — per-client request rate and provider protection status."""

from __future__ import annotations

import time

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class ClientsPane(Vertical):
    """Aggregate client health and provider protection status."""

    def compose(self) -> ComposeResult:
        table = DataTable(id="clients-table", cursor_type="row")
        table.add_columns(
            "Client", "Req/5m", "Err%", "Avg Latency", "Backoff", "Provider Quarantines",
            "Gemini Text", "Gemini Thought"
        )
        yield table
        yield Static("Select a client to view provider activity.", id="clients-detail")

    def on_mount(self) -> None:
        self._refresh_clients()
        self.set_interval(5.0, self._refresh_clients)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self._show_detail(str(event.row_key.value))

    @work(exclusive=True, thread=True)
    def _refresh_clients(self) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        table = self.query_one("#clients-table", DataTable)
        table.clear()
        now = time.time()
        cp_states = store.all_client_provider_states()

        for client_id, client in store.all_clients().items():
            quarantines = sum(
                1 for (cp_client, _), state in cp_states.items()
                if cp_client == client_id and state.is_quarantined(now)
            )
            avg_lat = client.recent_avg_latency()
            backoff = "-"
            if client.backoff_until > now:
                backoff = f"{client.backoff_until - now:.0f}s"
            table.add_row(
                client_id,
                str(client.recent_request_count()),
                f"{client.recent_error_rate() * 100:.1f}%",
                f"{avg_lat:.0f}ms" if avg_lat else "-",
                backoff,
                str(quarantines),
                str(client.recent_gemini_outcome_count("text")),
                str(client.recent_gemini_outcome_count("thought_only")),
                key=client_id,
            )

        if not store.all_clients():
            table.add_row("(no clients tracked)", "-", "-", "-", "-", "-", "-", "-", key="_empty")

    def _show_detail(self, client_id: str) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        detail = self.query_one("#clients-detail", Static)
        rows: list[str] = [f"[bold]{client_id}[/]"]
        now = time.time()
        found = False
        client = store.all_clients().get(client_id)
        if client:
            rows.append(
                f"  gemini text={client.recent_gemini_outcome_count('text')} "
                f"thought_only={client.recent_gemini_outcome_count('thought_only')} "
                f"tool={client.recent_gemini_outcome_count('tool')}"
            )
            rows.append("")

        for (cp_client, provider), state in sorted(store.all_client_provider_states().items()):
            if cp_client != client_id:
                continue
            found = True
            status = "QUARANTINED" if state.is_quarantined(now) else "healthy"
            cooldown = f"{state.cooldown_remaining(now):.0f}s" if state.is_quarantined(now) else "-"
            rows.append(
                f"  {provider:<12} status={status:<11} req/5m={state.recent_request_count():<4} "
                f"429s={state.recent_rate_limit_count():<3} cooldown={cooldown} reason={state.last_reason or '-'}"
            )

        if not found:
            rows.append("  No provider activity recorded.")

        detail.update("\n".join(rows))
