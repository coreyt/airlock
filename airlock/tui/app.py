"""Airlock TUI — main application shell.

5-view architecture: Overview, Guards, Logs, Config, Test.
Navigation via tab bar + number keys + command palette.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Header

from airlock.tui.alert_engine import AlertEngine
from airlock.tui.mcp_manager import McpServerManager
from airlock.tui.proxy_manager import ProxyManager
from airlock.tui.screens.config import ConfigPane
from airlock.tui.screens.guards import GuardsPane
from airlock.tui.screens.logs import LogsPane
from airlock.tui.screens.overview import OverviewPane
from airlock.tui.screens.test import TestPane
from airlock.tui.widgets.tab_bar import TabBar

CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"

_VIEWS = [
    ("overview", "Overview"),
    ("guards", "Guards"),
    ("logs", "Logs"),
    ("config", "Config"),
    ("test", "Test"),
]


class AirlockApp(App):
    """Airlock terminal dashboard."""

    TITLE = "Airlock"
    SUB_TITLE = "Enterprise LLM Proxy"
    CSS_PATH = CSS_PATH

    BINDINGS = [
        ("1", "switch_view('overview')", "Overview"),
        ("2", "switch_view('guards')", "Guards"),
        ("3", "switch_view('logs')", "Logs"),
        ("4", "switch_view('config')", "Config"),
        ("5", "switch_view('test')", "Test"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        host: str = "localhost",
        port: str = "4000",
        auto_start: bool = False,
        daemon_mode: bool = False,
    ) -> None:
        super().__init__()
        self._proxy_host = host
        self._proxy_port = port
        self._auto_start = auto_start
        self._daemon_mode = daemon_mode
        self._proxy_manager = ProxyManager(
            host=host,
            port=port,
            daemon_mode=daemon_mode,
        )
        self._mcp_manager = McpServerManager()
        self._mcp_manager.load_config()
        self._jsonl_stop = threading.Event()
        self._jsonl_thread: threading.Thread | None = None
        self._alert_engine = AlertEngine()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield TabBar(tabs=_VIEWS, id="tab-bar")
        with ContentSwitcher(id="workspace", initial="overview"):
            yield OverviewPane(
                host=self._proxy_host,
                port=self._proxy_port,
                proxy_manager=self._proxy_manager,
                id="overview",
            )
            yield GuardsPane(id="guards")
            yield LogsPane(id="logs")
            yield ConfigPane(
                mcp_manager=self._mcp_manager,
                id="config",
            )
            yield TestPane(id="test")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Overview"
        self._mcp_manager.start_health_loop()
        self._start_jsonl_tailer()
        self.set_interval(5.0, self._run_alerts)
        if self._auto_start:
            overview = self.query_one(OverviewPane)
            overview.action_start_proxy()

    # -- navigation -----------------------------------------------------------

    def action_switch_view(self, view_id: str) -> None:
        self.query_one("#workspace", ContentSwitcher).current = view_id
        self.query_one("#tab-bar", TabBar).activate(view_id)
        for vid, label in _VIEWS:
            if vid == view_id:
                self.sub_title = label
                break

    def on_tab_bar_tab_activated(self, event: TabBar.TabActivated) -> None:
        self.action_switch_view(event.view_id)

    # -- alert engine ---------------------------------------------------------

    def _run_alerts(self) -> None:
        """Evaluate alert rules and update Overview."""
        try:
            from airlock.fast.state import store
        except ImportError:
            return

        new_alerts = self._alert_engine.evaluate(store)
        active = self._alert_engine.active

        # Update badge on tab bar
        unack = self._alert_engine.active_count()
        self.query_one("#tab-bar", TabBar).update_badge(unack)

        # Update Overview alerts panel
        if active:
            lines = []
            for a in active[:20]:
                if a.acknowledged:
                    continue
                icon = {"critical": "[red]![/]", "warning": "[yellow]![/]", "info": "[dim]i[/]"}.get(a.severity, "?")
                lines.append(f"  {icon} {a.title}")
            text = "\n".join(lines) if lines else "[dim]No alerts[/]"
        else:
            text = "[dim]No alerts[/]"

        try:
            overview = self.query_one(OverviewPane)
            overview.update_alerts(text)
        except Exception:
            pass

    # -- JSONL tailer ---------------------------------------------------------

    def _start_jsonl_tailer(self) -> None:
        """Start background thread that tails JSONL logs into StateStore."""
        from airlock.fast.state import tail_jsonl

        log_dir = os.getenv("AIRLOCK_LOG_DIR", "./logs")
        self._jsonl_stop.clear()
        self._jsonl_thread = threading.Thread(
            target=tail_jsonl,
            args=(log_dir, self._jsonl_stop),
            daemon=True,
        )
        self._jsonl_thread.start()

    # -- shutdown -------------------------------------------------------------

    def on_unmount(self) -> None:
        self._jsonl_stop.set()
        if self._jsonl_thread is not None:
            self._jsonl_thread.join(timeout=3)
        if not self._daemon_mode:
            self._proxy_manager.stop()
        self._mcp_manager.stop_all()


def run(
    host: str = "localhost",
    port: str = "4000",
    auto_start: bool = False,
    daemon_mode: bool = False,
) -> None:
    """Launch the TUI application."""
    app = AirlockApp(
        host=host,
        port=port,
        auto_start=auto_start,
        daemon_mode=daemon_mode,
    )
    app.run()
