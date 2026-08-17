"""Manual, read-only, same-host fleet Admin TUI for Slice 70."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from airlock.tui.fleet_client import FleetAdminClient, FleetResult
from airlock.tui.fleet_profile import FleetProfileError, load_fleet_profile


class FleetAdminApp(App):
    """Explicit-selection view.  It has no polling, actions, or local persistence."""

    TITLE = "Airlock Fleet Admin"
    SUB_TITLE = "Read-only same-host inventory"

    def __init__(self, client: FleetAdminClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static(
                "Enter one or more configured IDs, separated by commas. "
                "Nothing refreshes automatically.",
                id="fleet-status",
            )
            yield Input(placeholder="target-a,target-b", id="fleet-selection")
            yield Button("Refresh selected", id="fleet-refresh")
            yield DataTable(id="fleet-results")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#fleet-results", DataTable)
        table.add_columns("ID", "Name", "Status", "Providers", "Observed")

    def on_unmount(self) -> None:
        self._client.close()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "fleet-refresh":
            return
        selected = [
            instance_id.strip()
            for instance_id in self.query_one("#fleet-selection", Input).value.split(
                ","
            )
            if instance_id.strip()
        ]
        try:
            if not selected:
                raise ValueError("select one or more configured target IDs")
            self._refresh(selected)
        except ValueError as exc:
            self.query_one("#fleet-status", Static).update(str(exc))

    @work(thread=True, group="fleet-refresh", exclusive=True)
    def _refresh(self, selected: list[str]) -> None:
        results = self._client.refresh(selected)
        self.call_from_thread(self._render, results)

    def _render(self, results: list[FleetResult]) -> None:
        table = self.query_one("#fleet-results", DataTable)
        table.clear()
        for result in results:
            table.add_row(
                result.instance_id,
                result.display_name,
                result.state,
                str(result.provider_count)
                if result.provider_count is not None
                else "—",
                result.observed_at or "—",
            )
        self.query_one("#fleet-status", Static).update("Refresh complete.")


def run(*, inventory_file: str) -> None:
    """Load one protected inventory and run only the fleet view."""
    try:
        targets = load_fleet_profile(Path(inventory_file))
    except FleetProfileError as exc:
        raise SystemExit(f"error: {exc}") from None
    FleetAdminApp(FleetAdminClient(targets)).run()
