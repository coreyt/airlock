"""Colored status dot with label."""

from __future__ import annotations

from textual.widgets import Static


class StatusIndicator(Static):
    """Displays ``● label`` with a semantic color class."""

    def __init__(
        self,
        label: str = "",
        *,
        status: str = "ok",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._label = label
        self.set_status(status)

    def set_status(self, status: str, label: str | None = None) -> None:
        """Update the indicator.  *status*: ``ok``, ``error``, or ``warn``."""
        if label is not None:
            self._label = label
        dot_class = {"ok": "status-ok", "error": "status-error", "warn": "status-warn"}
        css_class = dot_class.get(status, "status-ok")
        self.update(f"[{css_class}]\u25cf[/] {self._label}")
