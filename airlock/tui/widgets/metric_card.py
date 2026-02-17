"""Bordered metric display box."""

from __future__ import annotations

from textual.widgets import Static


class MetricCard(Static):
    """A titled metric value for dashboard display."""

    def __init__(
        self,
        title: str = "",
        value: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._value = value
        self._render_content()

    def set_value(self, value: str) -> None:
        self._value = value
        self._render_content()

    def _render_content(self) -> None:
        self.update(f"[bold]{self._title}[/]\n{self._value}")
