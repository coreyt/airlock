"""Threats screen — active backoffs and threat detection config."""

from __future__ import annotations

import time

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class ThreatsPane(Vertical):
    """Active threat monitoring and detection configuration."""

    def compose(self) -> ComposeResult:
        table = DataTable(id="threats-backoffs", cursor_type="row")
        table.add_columns("Client", "Threat Score", "Backoff Until", "Remaining")
        yield table
        yield Static("", id="threats-config")

    def on_mount(self) -> None:
        self._refresh_threats()
        self._show_config()
        self.set_interval(5.0, self._refresh_threats)

    @work(exclusive=True, thread=True)
    def _refresh_threats(self) -> None:
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        table = self.query_one("#threats-backoffs", DataTable)
        table.clear()
        now = time.time()
        any_backoff = False

        for name, client in store.all_clients().items():
            if client.backoff_until > now:
                any_backoff = True
                remaining = client.backoff_until - now
                backoff_str = time.strftime(
                    "%H:%M:%S", time.localtime(client.backoff_until)
                )
                table.add_row(
                    name,
                    f"{client.threat_score:.2f}",
                    backoff_str,
                    f"{remaining:.0f}s",
                    key=name,
                )

        if not any_backoff:
            table.add_row("(all clear)", "-", "-", "-", key="_empty")

    def _show_config(self) -> None:
        try:
            from airlock.fast.threat_detector import (
                THREAT_BLOCK_THRESHOLD,
                BASE_BACKOFF_S,
                MAX_BACKOFF_S,
                VOLUME_SPIKE_MULTIPLIER,
                RAPID_FIRE_MIN_GAP_S,
                LARGE_PAYLOAD_CHARS,
                ERROR_PROBE_RATE,
            )
        except ImportError:
            self.query_one("#threats-config", Static).update(
                "[dim]Fast subsystem not available[/]"
            )
            return

        self.query_one("#threats-config", Static).update(
            "[bold]Threat Detection Config[/]\n\n"
            f"  Block threshold:     {THREAT_BLOCK_THRESHOLD}        "
            f"Base backoff:    {BASE_BACKOFF_S}s\n"
            f"  Max backoff:         {MAX_BACKOFF_S:.0f}s       "
            f"Decay factor:    0.95\n"
            f"  Volume spike ratio:  {VOLUME_SPIKE_MULTIPLIER}x        "
            f"Rapid-fire gap:  {RAPID_FIRE_MIN_GAP_S * 1000:.0f}ms\n"
            f"  Payload max chars:   {LARGE_PAYLOAD_CHARS:,}   "
            f"Error probe pct: {ERROR_PROBE_RATE:.0%}"
        )
