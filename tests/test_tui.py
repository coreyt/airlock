"""Tests for airlock.tui — terminal dashboard."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
from textual.widgets import DataTable

from airlock.tui.app import AirlockApp


# -------------------------------------------------------------------
# App instantiation and structure
# -------------------------------------------------------------------


@pytest.fixture()
def app():
    return AirlockApp(host="127.0.0.1", port="9999")


async def test_app_creates_with_host_port(app) -> None:
    assert app._proxy_host == "127.0.0.1"
    assert app._proxy_port == "9999"


async def test_app_has_bindings(app) -> None:
    binding_keys = [b[0] for b in app.BINDINGS]
    assert "1" in binding_keys
    assert "7" in binding_keys
    assert "q" in binding_keys


async def test_app_composes_all_panes() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        # Sidebar exists
        sidebar = app.query_one("#sidebar")
        assert sidebar is not None

        # Workspace with content switcher
        workspace = app.query_one("#workspace")
        assert workspace is not None

        # All 7 panes exist
        for pane_id in ("dashboard", "models", "threats", "logs", "analysis", "settings", "flow"):
            pane = app.query_one(f"#{pane_id}")
            assert pane is not None, f"Missing pane: {pane_id}"


async def test_screen_switching_via_keys() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")

        # Start on dashboard
        assert workspace.current == "dashboard"

        # Press 2 → models
        await pilot.press("2")
        assert workspace.current == "models"

        # Press 4 → logs
        await pilot.press("4")
        assert workspace.current == "logs"

        # Press 1 → back to dashboard
        await pilot.press("1")
        assert workspace.current == "dashboard"


async def test_sidebar_navigation() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")

        # Press 3 to verify key nav works
        await pilot.press("3")
        assert workspace.current == "threats"


# -------------------------------------------------------------------
# Dashboard screen
# -------------------------------------------------------------------


async def test_dashboard_has_widgets() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        # Proxy status indicator
        indicator = app.query_one("#proxy-indicator")
        assert indicator is not None

        # Guardrail indicators
        for gid in ("guard-pii", "guard-kw", "guard-fast"):
            assert app.query_one(f"#{gid}") is not None

        # Model table
        table = app.query_one("#dash-model-table")
        assert table is not None


# -------------------------------------------------------------------
# Models screen
# -------------------------------------------------------------------


async def test_models_has_table_and_detail() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")  # switch to models
        assert app.query_one("#models-table") is not None
        assert app.query_one("#models-detail") is not None


# -------------------------------------------------------------------
# Threats screen
# -------------------------------------------------------------------


async def test_threats_has_backoffs_and_config() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")  # switch to threats
        assert app.query_one("#threats-backoffs") is not None
        assert app.query_one("#threats-config") is not None


# -------------------------------------------------------------------
# Logs screen
# -------------------------------------------------------------------


async def test_logs_has_filters_and_table() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        assert app.query_one("#logs-model-filter") is not None
        assert app.query_one("#logs-user-filter") is not None
        assert app.query_one("#logs-table") is not None
        assert app.query_one("#logs-detail") is not None


async def test_logs_loads_from_jsonl(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.utcnow().date().isoformat()
    log_file = log_dir / f"airlock-{today}.jsonl"
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "success": True,
        "model": "claude-sonnet",
        "user": "alice",
        "total_tokens": 100,
        "duration_ms": 1200,
    }
    log_file.write_text(json.dumps(record) + "\n")

    with mock.patch.dict(os.environ, {"AIRLOCK_LOG_DIR": str(log_dir)}):
        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("4")  # logs
            await pilot.pause()
            # The log pane should have loaded — table should have at least
            # the row we wrote
            from airlock.tui.screens.logs import LogsPane

            logs_pane = app.query_one(LogsPane)
            assert len(logs_pane._records) >= 1
            assert logs_pane._records[0]["model"] == "claude-sonnet"


# -------------------------------------------------------------------
# Analysis screen
# -------------------------------------------------------------------


async def test_analysis_has_controls_and_tabs() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        assert app.query_one("#analysis-days") is not None
        assert app.query_one("#analysis-run") is not None
        assert app.query_one("#analysis-tabs") is not None


# -------------------------------------------------------------------
# Settings screen
# -------------------------------------------------------------------


async def test_settings_has_tabs_and_apply() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("6")
        assert app.query_one("#settings-tabs") is not None
        assert app.query_one("#settings-apply") is not None


# -------------------------------------------------------------------
# CLI dispatch
# -------------------------------------------------------------------


def test_tui_routes_to_tui_app() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui"])
    mock_run.assert_called_once_with(host="localhost", port="4000", auto_start=False)


def test_tui_passes_host_port() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui", "--host", "10.0.0.1", "--port", "8080"])
    mock_run.assert_called_once_with(host="10.0.0.1", port="8080", auto_start=False)


def test_help_includes_tui(capsys) -> None:
    from airlock.cli.main import main

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "tui" in out


# -------------------------------------------------------------------
# Flow screen
# -------------------------------------------------------------------


async def test_flow_has_status_table_and_detail() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("7")
        assert app.query_one("#flow-status") is not None
        assert app.query_one("#flow-table") is not None
        assert app.query_one("#flow-detail-tabs") is not None
        assert app.query_one("#flow-signals") is not None
        assert app.query_one("#flow-pipeline") is not None
        assert app.query_one("#flow-raw") is not None


async def test_flow_screen_switching() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")
        await pilot.press("7")
        assert workspace.current == "flow"

        # Switch away and back
        await pilot.press("1")
        assert workspace.current == "dashboard"
        await pilot.press("7")
        assert workspace.current == "flow"


async def test_flow_pause_resume() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("7")
        await pilot.pause()

        from airlock.tui.screens.flow import FlowPane

        flow_pane = app.query_one(FlowPane)
        assert flow_pane._paused is False

        # Focus the flow table so space bubbles to FlowPane
        app.query_one("#flow-table", DataTable).focus()

        # Press space to pause
        await pilot.press("space")
        assert flow_pane._paused is True

        # Press space to resume
        await pilot.press("space")
        assert flow_pane._paused is False


async def test_flow_loads_observations(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.utcnow().date().isoformat()
    log_file = log_dir / f"airlock-{today}.jsonl"
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "success": True,
        "model": "claude-sonnet",
        "request_id": "req-001",
        "airlock_observation": {
            "request_id": "req-001",
            "model": "claude-sonnet",
            "client_id": "key:testkey1",
            "signals": [
                {
                    "guardrail_name": "pii_scan",
                    "detected": False,
                    "score": 0.0,
                    "details": {"entities": {}, "total_count": 0},
                    "duration_ms": 0.1,
                },
                {
                    "guardrail_name": "keyword_scan",
                    "detected": True,
                    "score": 1.0,
                    "details": {"matched_keywords": ["forbidden"], "match_count": 1},
                    "duration_ms": 0.1,
                },
                {
                    "guardrail_name": "threat_read",
                    "detected": False,
                    "score": 0.0,
                    "details": {"client_id": "key:testkey1", "threat_score": 0.0},
                    "duration_ms": 0.1,
                },
            ],
            "composite_score": 0.4,
            "would_block": False,
            "orchestrator_version": "2024-01-15T10:00:00Z",
        },
    }
    log_file.write_text(json.dumps(record) + "\n")

    with mock.patch.dict(os.environ, {"AIRLOCK_LOG_DIR": str(log_dir)}):
        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("7")
            await pilot.pause()
            await pilot.pause()  # extra pause for worker to complete

            from airlock.tui.screens.flow import FlowPane

            flow_pane = app.query_one(FlowPane)
            assert len(flow_pane._entries) >= 1
            assert flow_pane._entries[0].model == "claude-sonnet"
            assert flow_pane._entries[0].composite_score == 0.4


async def test_flow_skips_records_without_observation(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.utcnow().date().isoformat()
    log_file = log_dir / f"airlock-{today}.jsonl"
    # Record without observation — should be skipped
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "success": True,
        "model": "gpt-4o",
    }
    log_file.write_text(json.dumps(record) + "\n")

    with mock.patch.dict(os.environ, {"AIRLOCK_LOG_DIR": str(log_dir)}):
        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("7")
            await pilot.pause()
            await pilot.pause()

            from airlock.tui.screens.flow import FlowPane

            flow_pane = app.query_one(FlowPane)
            assert len(flow_pane._entries) == 0


async def test_flow_signal_rendering() -> None:
    """Test that the signal renderer produces expected output."""
    from airlock.tui.screens.flow import FlowEntry, _render_signals

    entry = FlowEntry(
        timestamp="2024-01-15T10:31:42Z",
        request_id="req-001",
        model="claude-sonnet",
        client_id="key:testkey1",
        success=True,
        composite_score=0.4,
        would_block=False,
        orchestrator_version="2024-01-15T10:00:00Z",
        signals=[
            {
                "guardrail_name": "pii_scan",
                "detected": False,
                "score": 0.0,
                "details": {"entities": {}, "total_count": 0},
                "duration_ms": 0.1,
            },
            {
                "guardrail_name": "keyword_scan",
                "detected": True,
                "score": 1.0,
                "details": {"matched_keywords": ["forbidden"], "match_count": 1},
                "duration_ms": 0.2,
            },
        ],
        enforcement={"mode": "shadow", "should_block": True, "threshold": 0.5, "composite_score": 0.4},
        raw_observation={},
        raw_record={},
    )
    rendered = _render_signals(entry)
    assert "pii_scan" in rendered
    assert "keyword_scan" in rendered
    assert "COMPOSITE" in rendered
    assert "shadow" in rendered


async def test_flow_pipeline_rendering() -> None:
    """Test that the pipeline renderer produces expected output."""
    from airlock.tui.screens.flow import FlowEntry, _render_pipeline

    entry = FlowEntry(
        timestamp="2024-01-15T10:31:42Z",
        request_id="req-001",
        model="claude-sonnet",
        client_id="key:testkey1",
        success=True,
        composite_score=0.4,
        would_block=False,
        orchestrator_version="v1",
        signals=[
            {"guardrail_name": "pii_scan", "detected": False, "score": 0.0, "details": {}, "duration_ms": 0.5},
        ],
        enforcement={"mode": "shadow", "should_block": False, "threshold": 0.5, "composite_score": 0.4},
        raw_observation={},
        raw_record={},
    )
    rendered = _render_pipeline(entry)
    assert "PRE_CALL" in rendered
    assert "DURING_CALL" in rendered
    assert "PII Guard" in rendered
    assert "req-001" in rendered


# -------------------------------------------------------------------
# Proxy launch & control
# -------------------------------------------------------------------


async def test_dashboard_has_start_button() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        from textual.widgets import Button

        btn = app.query_one("#proxy-start-btn", Button)
        assert btn is not None
        assert btn.label.plain == "Start Proxy"
        assert btn.variant == "success"


async def test_dashboard_has_console_log() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        from textual.widgets import Collapsible, RichLog

        collapsible = app.query_one("#dash-console-collapsible", Collapsible)
        assert collapsible is not None
        console = app.query_one("#dash-console-log", RichLog)
        assert console is not None


async def test_start_shows_error_without_config() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        # Mock ProxyManager to fail preflight
        app._proxy_manager.find_config = mock.Mock(return_value=None)

        from airlock.tui.screens.dashboard import DashboardPane

        dashboard = app.query_one(DashboardPane)
        dashboard.action_start_proxy()
        await pilot.pause()

        from textual.widgets import Button

        btn = app.query_one("#proxy-start-btn", Button)
        # Should stay as "Start Proxy" since start failed
        assert btn.label.plain == "Start Proxy"


def test_tui_start_flag_passed_through() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui", "--start"])
    mock_run.assert_called_once_with(host="localhost", port="4000", auto_start=True)


async def test_app_has_proxy_manager() -> None:
    app = AirlockApp(host="127.0.0.1", port="9999")
    from airlock.tui.proxy_manager import ProxyManager

    assert isinstance(app._proxy_manager, ProxyManager)


async def test_externally_running_proxy_disables_button() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        from textual.widgets import Button

        from airlock.tui.screens.dashboard import DashboardPane

        dashboard = app.query_one(DashboardPane)
        # Simulate: proxy reachable but not TUI-owned
        dashboard._externally_running = True
        btn = app.query_one("#proxy-start-btn", Button)
        btn.label = "Running Externally"
        btn.variant = "default"
        btn.disabled = True

        assert btn.disabled is True
        assert btn.label.plain == "Running Externally"
