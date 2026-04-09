"""Thread-safety tests for TUI workers that mutate widgets.

Textual widgets are not thread-safe: any widget mutation performed from a
``@work(thread=True)`` worker must be dispatched to the main thread via
``self.app.call_from_thread``. These tests verify that each offending worker
in the config and logs panes routes its widget mutations through that
dispatcher.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from airlock.tui.app import AirlockApp


async def test_refresh_mcp_servers_dispatches_widget_mutations_via_call_from_thread() -> (
    None
):
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")  # config
        await pilot.pause()

        from airlock.tui.screens.config import ConfigPane

        pane = app.query_one(ConfigPane)

        mock_dispatch = MagicMock()
        pane.app.call_from_thread = mock_dispatch  # type: ignore[method-assign]

        # Bypass the @work decorator by invoking the raw function.
        ConfigPane._refresh_mcp_servers.__wrapped__(pane)

        assert mock_dispatch.call_count > 0, (
            "Expected _refresh_mcp_servers to dispatch widget mutations via "
            "call_from_thread, but it called zero."
        )


async def test_load_logs_dispatches_widget_mutations_via_call_from_thread(
    tmp_path,
) -> None:
    import json
    import os
    from datetime import datetime, timezone
    from unittest import mock

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.now(timezone.utc).date().isoformat()
    log_file = log_dir / f"airlock-{today}.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "success": True,
        "model": "claude-sonnet",
        "user": "alice",
    }
    log_file.write_text(json.dumps(record) + "\n")

    with mock.patch.dict(os.environ, {"AIRLOCK_LOG_DIR": str(log_dir)}):
        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")  # logs
            await pilot.pause()

            from airlock.tui.screens.logs import LogsPane

            pane = app.query_one(LogsPane)

            mock_dispatch = MagicMock()
            pane.app.call_from_thread = mock_dispatch  # type: ignore[method-assign]

            ConfigPane_load_logs = type(pane)._load_logs
            ConfigPane_load_logs.__wrapped__(pane)

            assert mock_dispatch.call_count > 0, (
                "Expected _load_logs to dispatch widget mutations via "
                "call_from_thread, but it called zero."
            )


async def test_export_filtered_dispatches_status_update_via_call_from_thread(
    tmp_path,
) -> None:
    import json
    import os
    from unittest import mock

    log_dir = tmp_path / "logs"

    with mock.patch.dict(os.environ, {"AIRLOCK_LOG_DIR": str(log_dir)}):
        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")  # logs
            await pilot.pause()

            from airlock.tui.screens.logs import LogsPane

            pane = app.query_one(LogsPane)

            pane._filtered = [{"a": 1}, {"b": 2}]

            mock_dispatch = MagicMock()
            pane.app.call_from_thread = mock_dispatch  # type: ignore[method-assign]

            # Bypass the @work decorator by invoking the raw function.
            type(pane)._export_filtered.__wrapped__(pane)

            assert mock_dispatch.call_count > 0, (
                "Expected _export_filtered to dispatch status update via "
                "call_from_thread, but it called zero."
            )

            # Verify the I/O still happened: one export-*.jsonl file written
            # with the serialized records.
            exports = sorted(log_dir.glob("export-*.jsonl"))
            assert len(exports) == 1, f"expected 1 export file, got {exports}"
            lines = exports[0].read_text().strip().splitlines()
            assert [json.loads(line) for line in lines] == [
                {"a": 1},
                {"b": 2},
            ]


async def test_do_mcp_start_dispatches_error_status_via_call_from_thread() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")  # config
        await pilot.pause()

        from airlock.tui.screens.config import ConfigPane

        pane = app.query_one(ConfigPane)

        # Install a fake manager whose start_server returns an error string,
        # forcing _do_mcp_start down the widget-mutation branch.
        fake_manager = MagicMock()
        fake_manager.start_server.return_value = "boom"
        pane._mcp_manager = fake_manager

        mock_dispatch = MagicMock()
        pane.app.call_from_thread = mock_dispatch  # type: ignore[method-assign]

        ConfigPane._do_mcp_start.__wrapped__(pane, "srv1")

        assert mock_dispatch.call_count > 0, (
            "Expected _do_mcp_start to dispatch error-status widget mutation "
            "via call_from_thread on failure."
        )


async def test_do_mcp_restart_dispatches_error_status_via_call_from_thread() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")  # config
        await pilot.pause()

        from airlock.tui.screens.config import ConfigPane

        pane = app.query_one(ConfigPane)

        fake_manager = MagicMock()
        fake_manager.restart_server.return_value = "boom"
        pane._mcp_manager = fake_manager

        mock_dispatch = MagicMock()
        pane.app.call_from_thread = mock_dispatch  # type: ignore[method-assign]

        ConfigPane._do_mcp_restart.__wrapped__(pane, "srv1")

        assert mock_dispatch.call_count > 0, (
            "Expected _do_mcp_restart to dispatch error-status widget mutation "
            "via call_from_thread on failure."
        )
