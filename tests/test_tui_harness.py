"""Slice 70 deterministic composition harness contracts."""

from __future__ import annotations

from unittest.mock import MagicMock

from airlock.tui.app import AirlockApp


async def test_harness_composes_production_widget_tree_without_mount_workers(
    monkeypatch,
) -> None:
    """Ordinary render/navigation tests need real panes, not background I/O."""
    start_health = MagicMock()
    monkeypatch.setattr(
        "airlock.tui.mcp_manager.McpServerManager.start_health_loop", start_health
    )
    monkeypatch.setattr(
        "airlock.tui.screens.overview.OverviewPane._refresh_state", MagicMock()
    )
    monkeypatch.setattr(
        "airlock.tui.screens.overview.OverviewPane._probe_external", MagicMock()
    )
    monkeypatch.setattr(
        "airlock.tui.screens.config.ConfigPane._refresh_mcp_servers", MagicMock()
    )
    monkeypatch.setattr("airlock.tui.screens.guards.GuardsPane._poll_logs", MagicMock())

    app = AirlockApp(test_harness=True)
    async with app.run_test(size=(120, 40)):
        for pane_id in ("overview", "guards", "logs", "config", "test", "advisor"):
            assert app.query_one(f"#{pane_id}") is not None

    start_health.assert_not_called()
    assert app._jsonl_thread is None


async def test_default_app_keeps_mount_lifecycle_enabled(monkeypatch) -> None:
    """The harness flag cannot silently disable production lifecycle behavior."""
    # Keep this a focused lifecycle assertion: normal mode must invoke the
    # app-level starters, but it must not leave the pane workers/timers alive
    # after Textual's test pilot exits.
    monkeypatch.setattr(
        "airlock.tui.screens.overview.OverviewPane._refresh_state", MagicMock()
    )
    monkeypatch.setattr(
        "airlock.tui.screens.overview.OverviewPane._probe_external", MagicMock()
    )
    monkeypatch.setattr(
        "airlock.tui.screens.config.ConfigPane._refresh_mcp_servers", MagicMock()
    )
    monkeypatch.setattr("airlock.tui.screens.logs.LogsPane._load_logs", MagicMock())
    monkeypatch.setattr("airlock.tui.screens.guards.GuardsPane._poll_logs", MagicMock())
    monkeypatch.setattr(
        "airlock.tui.screens.guards.GuardsPane._load_semantic_report", MagicMock()
    )
    app = AirlockApp()
    app._mcp_manager.start_health_loop = MagicMock()
    app._start_jsonl_tailer = MagicMock()
    async with app.run_test(size=(120, 40)):
        pass
    app._mcp_manager.start_health_loop.assert_called_once()
    app._start_jsonl_tailer.assert_called_once()
