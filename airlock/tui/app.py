"""Airlock TUI — main application shell."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header, ListItem, ListView, Label

from airlock.tui.mcp_manager import McpServerManager
from airlock.tui.proxy_manager import ProxyManager
from airlock.tui.screens.dashboard import DashboardPane
from airlock.tui.screens.models import ModelsPane
from airlock.tui.screens.threats import ThreatsPane
from airlock.tui.screens.logs import LogsPane
from airlock.tui.screens.analysis import AnalysisPane
from airlock.tui.screens.settings import SettingsPane
from airlock.tui.screens.flow import FlowPane
from airlock.tui.screens.mcp_servers import McpServersPane

CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"

_SCREENS = [
    ("dashboard", "1 Dashboard"),
    ("models", "2 Models"),
    ("threats", "3 Threats"),
    ("logs", "4 Logs"),
    ("analysis", "5 Analysis"),
    ("settings", "6 Settings"),
    ("flow", "7 Flow"),
    ("mcp_servers", "8 MCP Servers"),
]


class AirlockApp(App):
    """Airlock terminal dashboard."""

    TITLE = "Airlock"
    SUB_TITLE = "Enterprise LLM Proxy"
    CSS_PATH = CSS_PATH

    BINDINGS = [
        ("1", "switch_screen('dashboard')", "Dashboard"),
        ("2", "switch_screen('models')", "Models"),
        ("3", "switch_screen('threats')", "Threats"),
        ("4", "switch_screen('logs')", "Logs"),
        ("5", "switch_screen('analysis')", "Analysis"),
        ("6", "switch_screen('settings')", "Settings"),
        ("7", "switch_screen('flow')", "Flow"),
        ("8", "switch_screen('mcp_servers')", "MCP Servers"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        host: str = "localhost",
        port: str = "4000",
        auto_start: bool = False,
    ) -> None:
        super().__init__()
        self._proxy_host = host
        self._proxy_port = port
        self._auto_start = auto_start
        self._proxy_manager = ProxyManager(host=host, port=port)
        self._mcp_manager = McpServerManager()
        self._mcp_manager.load_config()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield ListView(
                    *[ListItem(Label(label), id=f"nav-{sid}") for sid, label in _SCREENS],
                    id="nav-list",
                )
            with ContentSwitcher(id="workspace", initial="dashboard"):
                yield DashboardPane(
                    host=self._proxy_host,
                    port=self._proxy_port,
                    proxy_manager=self._proxy_manager,
                    id="dashboard",
                )
                yield ModelsPane(id="models")
                yield ThreatsPane(id="threats")
                yield LogsPane(id="logs")
                yield AnalysisPane(id="analysis")
                yield SettingsPane(id="settings")
                yield FlowPane(id="flow")
                yield McpServersPane(
                    mcp_manager=self._mcp_manager, id="mcp_servers",
                )
        yield Footer()

    def on_mount(self) -> None:
        self._mcp_manager.start_health_loop()
        if self._auto_start:
            dashboard = self.query_one(DashboardPane)
            dashboard.action_start_proxy()

    def on_unmount(self) -> None:
        self._mcp_manager.stop_all()
        self._proxy_manager.stop()

    def action_switch_screen(self, screen_id: str) -> None:
        self.query_one("#workspace", ContentSwitcher).current = screen_id

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id and item_id.startswith("nav-"):
            screen_id = item_id[4:]
            self.action_switch_screen(screen_id)


def run(host: str = "localhost", port: str = "4000", auto_start: bool = False) -> None:
    """Launch the TUI application."""
    app = AirlockApp(host=host, port=port, auto_start=auto_start)
    app.run()
