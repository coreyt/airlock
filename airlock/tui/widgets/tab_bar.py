from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static


class TabBar(Horizontal):
    """Compact horizontal tab bar for view switching."""

    class TabActivated(Message):
        """Posted when a tab is clicked."""

        def __init__(self, view_id: str) -> None:
            super().__init__()
            self.view_id = view_id

    class _TabItem(Static):
        """Clickable tab label."""

        def __init__(self, view_id: str, label: str, **kwargs) -> None:
            super().__init__(label, id=f"tab-{view_id}", classes="tab-item", **kwargs)
            self.view_id = view_id
            self.base_label = label

        def on_click(self) -> None:
            self.post_message(TabBar.TabActivated(self.view_id))

    def __init__(self, tabs: list[tuple[str, str]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._tabs = tabs  # [(view_id, label), ...]
        self._active: str = tabs[0][0] if tabs else ""
        self._alert_count: int = 0

    def compose(self) -> ComposeResult:
        for view_id, label in self._tabs:
            yield self._TabItem(view_id, label)

    def on_mount(self) -> None:
        self._highlight_active()

    def activate(self, view_id: str) -> None:
        """Set the active tab programmatically."""
        self._active = view_id
        self._highlight_active()

    def update_badge(self, count: int) -> None:
        """Update alert badge on the first tab (Overview)."""
        self._alert_count = count
        if not self._tabs:
            return
        first_id = self._tabs[0][0]
        tab_item: TabBar._TabItem = self.query_one(f"#tab-{first_id}", self._TabItem)
        if count > 0:
            tab_item.update(f"{tab_item.base_label} !{count}")
        else:
            tab_item.update(tab_item.base_label)

    def _highlight_active(self) -> None:
        for view_id, _ in self._tabs:
            widget = self.query_one(f"#tab-{view_id}", Static)
            widget.set_class(view_id == self._active, "tab-active")
