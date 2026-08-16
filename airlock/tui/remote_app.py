"""Restricted host-console UI for the explicit remote Admin profile.

This intentionally does not reuse the full local dashboard: it owns no proxy,
does not read host files or state, and speaks only to the Admin HTTP perimeter.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Footer, Header, Input, Static

from airlock.tui.admin_client import (
    AdminConnection,
    AdminConnectionError,
    clear_provider_quarantine,
    client_snapshot,
    provider_configuration_snapshot,
    provider_snapshot,
)


class RemoteAdminApp(App):
    """Small, capability-scoped remote Admin viewer and reversible action UI."""

    TITLE = "Airlock Remote Admin"
    SUB_TITLE = "TLS capability connection"

    def __init__(self, connection: AdminConnection) -> None:
        super().__init__()
        self._connection = connection

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("Connecting to remote Admin…", id="remote-admin-status")
            yield Static("", id="remote-admin-providers")
            yield Static("", id="remote-admin-clients")
            yield Static("", id="remote-admin-config")
            yield Input(
                placeholder="Provider to clear quarantine", id="remote-provider"
            )
            yield Button("Clear quarantine", id="remote-clear", variant="warning")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(15.0, self._refresh)

    @work(thread=True, group="remote-admin-refresh", exclusive=True)
    def _refresh(self) -> None:
        connection = self._connection
        providers = provider_snapshot("", "", connection=connection)
        clients = client_snapshot("", "", connection=connection)
        config_status, config = provider_configuration_snapshot(
            "", "", connection=connection
        )

        if providers is None:
            self.call_from_thread(
                self._set_status,
                "Remote Admin unavailable (TLS, token, scope, or service).",
            )
            return
        self.call_from_thread(
            self._render, providers, clients, config if config_status == 200 else None
        )

    def _render(
        self, providers: dict, clients: dict | None, config: dict | None
    ) -> None:
        self.query_one("#remote-admin-status", Static).update(
            "Remote Admin connected — TLS capability access."
        )
        names = ", ".join(sorted(providers.get("providers", {}))) or "none"
        self.query_one("#remote-admin-providers", Static).update(f"Providers: {names}")
        client_count = len((clients or {}).get("clients", {}))
        self.query_one("#remote-admin-clients", Static).update(
            f"Clients: {client_count}"
        )
        configured = len((config or {}).get("providers", []))
        self.query_one("#remote-admin-config", Static).update(
            f"Configured providers: {configured}"
            if config
            else "Configured providers unavailable."
        )

    def _set_status(self, message: str) -> None:
        self.query_one("#remote-admin-status", Static).update(message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "remote-clear":
            return
        provider = self.query_one("#remote-provider", Input).value.strip()
        if not provider:
            self.query_one("#remote-admin-status", Static).update(
                "Enter a provider name."
            )
            return
        self._clear(provider)

    @work(thread=True, group="remote-admin-clear", exclusive=True)
    def _clear(self, provider: str) -> None:
        status, _payload = clear_provider_quarantine(
            "", "", provider, connection=self._connection
        )
        message = (
            f"Cleared quarantine for {provider}."
            if status == 200
            else "Quarantine clear unavailable (token scope or service)."
        )
        self.call_from_thread(self._set_status, message)
        if status == 200:
            self._refresh()


def run(*, host: str, port: str, token_file: str, ca_file: str) -> None:
    """Create the secret-blind remote transport and launch the limited UI."""
    try:
        connection = AdminConnection.from_files(
            host, port, Path(token_file), Path(ca_file)
        )
    except AdminConnectionError as exc:
        raise SystemExit(f"error: {exc}") from None
    RemoteAdminApp(connection).run()
