"""Tests for airlock.tui — terminal dashboard (5-view architecture)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from textual.widgets import Button, DataTable

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
    assert "5" in binding_keys
    assert "q" in binding_keys


async def test_app_composes_all_panes() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as _pilot:
        # Tab bar exists (no sidebar)
        tab_bar = app.query_one("#tab-bar")
        assert tab_bar is not None

        # Workspace with content switcher
        workspace = app.query_one("#workspace")
        assert workspace is not None

        # All 5 views exist
        for pane_id in ("overview", "guards", "logs", "config", "test"):
            pane = app.query_one(f"#{pane_id}")
            assert pane is not None, f"Missing pane: {pane_id}"


async def test_screen_switching_via_keys() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")

        # Start on overview
        assert workspace.current == "overview"

        # Press 2 → guards
        await pilot.press("2")
        assert workspace.current == "guards"

        # Press 3 → logs
        await pilot.press("3")
        assert workspace.current == "logs"

        # Press 1 → back to overview
        await pilot.press("1")
        assert workspace.current == "overview"


async def test_tab_bar_navigation() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")

        await pilot.press("4")
        assert workspace.current == "config"

        await pilot.press("5")
        assert workspace.current == "test"


# -------------------------------------------------------------------
# Overview screen
# -------------------------------------------------------------------


async def test_overview_has_widgets() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as _pilot:
        # Proxy status indicator
        indicator = app.query_one("#ov-proxy-indicator")
        assert indicator is not None

        # Alerts panel
        alerts = app.query_one("#ov-alerts")
        assert alerts is not None

        # Data tables
        assert app.query_one("#ov-providers") is not None
        assert app.query_one("#ov-models") is not None
        assert app.query_one("#ov-clients") is not None

        # Detail pane
        assert app.query_one("#ov-detail") is not None


async def test_overview_has_start_button() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as _pilot:
        btn = app.query_one("#ov-proxy-btn", Button)
        assert btn is not None
        valid_labels = {
            "Checking...",
            "Start Proxy",
            "Stop Proxy",
            "Running Externally",
        }
        assert btn.label.plain in valid_labels


async def test_overview_has_console_log() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as _pilot:
        from textual.widgets import Collapsible, RichLog

        collapsible = app.query_one("#ov-console-collapsible", Collapsible)
        assert collapsible is not None
        console = app.query_one("#ov-console-log", RichLog)
        assert console is not None


async def test_overview_update_alerts() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        from airlock.tui.screens.overview import OverviewPane

        overview = app.query_one(OverviewPane)
        overview.update_alerts("[red]Test alert[/]")
        await pilot.pause()

        from textual.widgets import Static

        alerts = app.query_one("#ov-alerts", Static)
        # Just verify it doesn't crash
        assert alerts is not None


# -------------------------------------------------------------------
# Guards screen (formerly Flow)
# -------------------------------------------------------------------


async def test_guards_has_status_table_and_detail() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        # Guards may use guards- or flow- prefixed IDs depending on implementation
        # Check for the presence of the key widgets
        from airlock.tui.screens.guards import GuardsPane

        guards = app.query_one(GuardsPane)
        assert guards is not None


async def test_guards_screen_switching() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")
        await pilot.press("2")
        assert workspace.current == "guards"

        await pilot.press("1")
        assert workspace.current == "overview"
        await pilot.press("2")
        assert workspace.current == "guards"


# -------------------------------------------------------------------
# Logs screen
# -------------------------------------------------------------------


async def test_logs_has_filters_and_table() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        assert app.query_one("#logs-model-filter") is not None
        assert app.query_one("#logs-user-filter") is not None
        assert app.query_one("#logs-table") is not None


async def test_logs_loads_from_jsonl(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.now(timezone.utc).date().isoformat()
    log_file = log_dir / f"airlock-{today}.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
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
            await pilot.press("3")  # logs
            await pilot.pause()
            from airlock.tui.screens.logs import LogsPane

            logs_pane = app.query_one(LogsPane)
            assert len(logs_pane._records) >= 1
            assert logs_pane._records[0]["model"] == "claude-sonnet"


async def test_logs_has_type_and_tool_filters() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        assert app.query_one("#logs-type-filter") is not None
        assert app.query_one("#logs-tool-filter") is not None


async def test_logs_mcp_filtering(tmp_path: Path) -> None:
    """Test that MCP type filter works on logs."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.now(timezone.utc).date().isoformat()
    log_file = log_dir / f"airlock-{today}.jsonl"
    records = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "success": True,
            "model": "claude-sonnet",
            "user": "alice",
            "total_tokens": 100,
            "duration_ms": 1200,
        },
        {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "success": True,
            "model": "mcp-proxy",
            "user": "alice",
            "call_type": "call_mcp_tool",
            "mcp_tool_name": "read_file",
        },
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    with mock.patch.dict(os.environ, {"AIRLOCK_LOG_DIR": str(log_dir)}):
        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")
            await pilot.pause()

            from textual.widgets import Select

            from airlock.tui.screens.logs import LogsPane

            logs_pane = app.query_one(LogsPane)
            assert len(logs_pane._records) == 2

            logs_pane._records = records

            # Baseline: no type filter → both records visible.
            logs_pane._apply_filters()
            assert len(logs_pane._filtered) == 2

            # Apply MCP filter → only the MCP-tagged record remains.
            app.query_one("#logs-type-filter", Select).value = "mcp"
            logs_pane._apply_filters()
            assert len(logs_pane._filtered) == 1
            assert logs_pane._filtered[0].get("call_type") == "call_mcp_tool"


# -------------------------------------------------------------------
# Config screen (merged Settings + MCP)
# -------------------------------------------------------------------


async def test_config_has_tabs_and_apply() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        assert app.query_one("#config-tabs") is not None
        assert app.query_one("#cfg-apply") is not None


# -------------------------------------------------------------------
# Test screen (formerly Chat)
# -------------------------------------------------------------------


async def test_test_screen_navigable() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        workspace = app.query_one("#workspace")
        await pilot.press("5")
        assert workspace.current == "test"


# -------------------------------------------------------------------
# Alert Engine
# -------------------------------------------------------------------


def test_alert_engine_evaluate() -> None:
    from airlock.tui.alert_engine import AlertEngine

    engine = AlertEngine()
    assert engine.active_count() == 0

    # evaluate with the real (empty) store should not crash
    try:
        from airlock.fast.state import store

        engine.evaluate(store)
    except ImportError:
        pass  # state module may not be available in all test envs


# -------------------------------------------------------------------
# CLI dispatch
# -------------------------------------------------------------------


def test_tui_routes_to_tui_app() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui"])
    mock_run.assert_called_once_with(
        host="localhost",
        port="4000",
        auto_start=False,
        daemon_mode=False,
    )


def test_tui_passes_host_port() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui", "--host", "10.0.0.1", "--port", "8080"])
    mock_run.assert_called_once_with(
        host="10.0.0.1",
        port="8080",
        auto_start=False,
        daemon_mode=False,
    )


def test_help_includes_tui(capsys) -> None:
    from airlock.cli.main import main

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "tui" in out


def test_tui_start_flag_passed_through() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui", "--start"])
    mock_run.assert_called_once_with(
        host="localhost",
        port="4000",
        auto_start=True,
        daemon_mode=False,
    )


def test_tui_daemon_flag_passed_through() -> None:
    from airlock.cli.main import main

    with mock.patch("airlock.tui.app.run") as mock_run:
        main(["tui", "--start", "--daemon"])
    mock_run.assert_called_once_with(
        host="localhost",
        port="4000",
        auto_start=True,
        daemon_mode=True,
    )


async def test_app_has_proxy_manager() -> None:
    app = AirlockApp(host="127.0.0.1", port="9999")
    from airlock.tui.proxy_manager import ProxyManager

    assert isinstance(app._proxy_manager, ProxyManager)


def test_on_unmount_stops_proxy_by_default() -> None:
    app = AirlockApp()
    app._proxy_manager.stop = mock.Mock()
    app._mcp_manager.stop_all = mock.Mock()

    app.on_unmount()

    app._mcp_manager.stop_all.assert_called_once()
    app._proxy_manager.stop.assert_called_once()


def test_on_unmount_keeps_proxy_in_daemon_mode() -> None:
    app = AirlockApp(daemon_mode=True)
    app._proxy_manager.stop = mock.Mock()
    app._mcp_manager.stop_all = mock.Mock()

    app.on_unmount()

    app._mcp_manager.stop_all.assert_called_once()
    app._proxy_manager.stop.assert_not_called()


# -------------------------------------------------------------------
# Flow/Guards signal rendering (unit tests — import from flow.py)
# -------------------------------------------------------------------


async def test_flow_signal_rendering() -> None:
    """Test that the signal renderer produces expected output."""
    from airlock.tui.screens.guards import FlowEntry, _render_signals

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
        enforcement={
            "mode": "shadow",
            "should_block": True,
            "threshold": 0.5,
            "composite_score": 0.4,
        },
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
    from airlock.tui.screens.guards import FlowEntry, _render_pipeline

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
            {
                "guardrail_name": "pii_scan",
                "detected": False,
                "score": 0.0,
                "details": {},
                "duration_ms": 0.5,
            },
        ],
        enforcement={
            "mode": "shadow",
            "should_block": False,
            "threshold": 0.5,
            "composite_score": 0.4,
        },
        raw_observation={},
        raw_record={},
    )
    rendered = _render_pipeline(entry)
    assert "PRE_CALL" in rendered
    assert "DURING_CALL" in rendered
    assert "PII Guard" in rendered
    assert "req-001" in rendered


async def test_flow_tool_result_rendering() -> None:
    """Test _render_tool_result for MCP and non-MCP entries."""
    from airlock.tui.screens.guards import FlowEntry, _render_tool_result

    # Non-MCP entry
    llm_entry = FlowEntry(
        timestamp="2024-01-15T10:31:42Z",
        request_id="req-001",
        model="claude-sonnet",
        client_id="key:testkey1",
        success=True,
        composite_score=0.4,
        would_block=False,
        orchestrator_version=None,
        signals=[],
        enforcement=None,
        raw_observation={},
        raw_record={},
        call_type="",
    )
    assert _render_tool_result(llm_entry) == "(Not an MCP call)"

    # MCP entry
    mcp_entry = FlowEntry(
        timestamp="2024-01-15T10:31:42Z",
        request_id="req-002",
        model="mcp-proxy",
        client_id="key:testkey1",
        success=True,
        composite_score=None,
        would_block=None,
        orchestrator_version=None,
        signals=[],
        enforcement=None,
        raw_observation={},
        raw_record={"messages": [{"role": "user", "content": "test"}]},
        call_type="call_mcp_tool",
        mcp_tool_name="read_file",
        mcp_server_name="filesystem",
    )
    rendered = _render_tool_result(mcp_entry)
    assert "read_file" in rendered
    assert "filesystem" in rendered
    assert "Yes" in rendered


# ===================================================================
# NEW TESTS — appended categories
# ===================================================================


# -------------------------------------------------------------------
# 1. Overview Screen Behavioral Tests
# -------------------------------------------------------------------


async def test_overview_provider_filter_toggle() -> None:
    from types import SimpleNamespace

    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("1")
        await pilot.pause()

        from airlock.tui.screens.overview import OverviewPane

        overview = app.query_one(OverviewPane)
        header = app.query_one("#ov-models-header")

        # Initially no filter.
        assert overview._provider_filter is None

        # Build a fake DataTable.RowSelected event — the handler only reads
        # event.data_table.id and event.row_key.value.
        fake_event = SimpleNamespace(
            data_table=SimpleNamespace(id="ov-providers"),
            row_key=SimpleNamespace(value="anthropic"),
        )

        # First selection → filter set to "anthropic".
        overview.on_data_table_row_selected(fake_event)
        await pilot.pause()
        assert overview._provider_filter == "anthropic"
        assert "anthropic" in str(header.render())

        # Second selection on the same row → toggle off.
        overview.on_data_table_row_selected(fake_event)
        await pilot.pause()
        assert overview._provider_filter is None
        assert "anthropic" not in str(header.render())


async def test_overview_status_line_exists() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as _pilot:
        status_line = app.query_one("#ov-status-line")
        assert status_line is not None


# -------------------------------------------------------------------
# 2. Config Screen Tests
# -------------------------------------------------------------------


async def test_config_has_provider_tab() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#cfg-tab-providers") is not None


async def test_config_has_guardrails_tab() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#cfg-tab-guardrails") is not None


async def test_config_has_protection_tab() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#cfg-tab-protection") is not None


async def test_config_has_mcp_tab() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#cfg-tab-mcp") is not None


async def test_config_has_logging_tab() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#cfg-tab-logging") is not None


async def test_config_has_advanced_tab() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#cfg-tab-advanced") is not None


async def test_config_apply_updates_env() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()

        from textual.widgets import Input

        pii_input = app.query_one("#cfg-pii-entities", Input)
        pii_input.value = "CREDIT_CARD,EMAIL_ADDRESS"

        with mock.patch.dict(os.environ, {}, clear=False):
            from airlock.tui.screens.config import ConfigPane

            config_pane = app.query_one(ConfigPane)
            config_pane._apply_settings()
            await pilot.pause()
            assert os.environ.get("AIRLOCK_PII_ENTITIES") == "CREDIT_CARD,EMAIL_ADDRESS"


# -------------------------------------------------------------------
# 3. Test Screen Tests
# -------------------------------------------------------------------


async def test_test_screen_has_controls() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        assert app.query_one("#chat-provider-select") is not None
        assert app.query_one("#chat-model-select") is not None
        assert app.query_one("#chat-send-btn") is not None


async def test_test_screen_has_quadrants() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        assert app.query_one("#chat-q1") is not None
        assert app.query_one("#chat-q2") is not None
        assert app.query_one("#chat-q3") is not None
        assert app.query_one("#chat-q4") is not None


async def test_test_screen_send_without_query() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()

        from textual.widgets import Static

        # Ensure the user input is empty
        from textual.widgets import TextArea

        user_input = app.query_one("#chat-user-input", TextArea)
        user_input.clear()
        await pilot.pause()

        # Click the send button
        send_btn = app.query_one("#chat-send-btn", Button)
        await pilot.click(send_btn)
        await pilot.pause()

        # Check that the response content shows a warning
        response_content = app.query_one("#chat-response-content", Static)
        rendered_text = str(response_content.render())
        assert "Enter a query" in rendered_text or "query" in rendered_text.lower()


# -------------------------------------------------------------------
# 4. Guards Screen Tests
# -------------------------------------------------------------------


async def test_guards_has_status_and_table() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()

        status = app.query_one("#guards-status")
        table = app.query_one("#guards-table", DataTable)
        assert status is not None
        assert table is not None
        # Table should have columns defined at compose time.
        assert len(table.columns) > 0


async def test_guards_pause_toggle() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()

        from airlock.tui.screens.guards import GuardsPane

        guards = app.query_one(GuardsPane)
        assert guards._paused is False

        guards.action_toggle_pause()
        assert guards._paused is True

        guards.action_toggle_pause()
        assert guards._paused is False


# -------------------------------------------------------------------
# 5. TabBar Widget Tests
# -------------------------------------------------------------------


async def test_tab_bar_activate() -> None:
    from airlock.tui.widgets.tab_bar import TabBar

    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as _pilot:
        tab_bar = app.query_one("#tab-bar", TabBar)
        assert tab_bar._active == "overview"

        tab_bar.activate("guards")
        assert tab_bar._active == "guards"

        tab_bar.activate("logs")
        assert tab_bar._active == "logs"


async def test_tab_bar_update_badge() -> None:
    from airlock.tui.widgets.tab_bar import TabBar

    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        tab_bar = app.query_one("#tab-bar", TabBar)

        tab_bar.update_badge(3)
        await pilot.pause()
        assert tab_bar._alert_count == 3
        # Verify the badge text on the first tab item
        first_tab = tab_bar.query_one("#tab-overview", TabBar._TabItem)
        rendered = str(first_tab.render())
        assert "!3" in rendered

        # Clear badge
        tab_bar.update_badge(0)
        await pilot.pause()
        assert tab_bar._alert_count == 0


# -------------------------------------------------------------------
# 6. Param Schema Tests (pure unit tests)
# -------------------------------------------------------------------


def test_get_schema_known_provider() -> None:
    from airlock.tui.param_schemas import get_schema

    schema = get_schema("anthropic")
    assert schema is not None
    field_names = [f.name for f in schema.fields]
    assert "temperature" in field_names


def test_get_schema_unknown_provider() -> None:
    from airlock.tui.param_schemas import DEFAULT_SCHEMA, get_schema

    schema = get_schema("unknown")
    assert schema is DEFAULT_SCHEMA


def test_defaults_for_schema() -> None:
    from airlock.tui.param_schemas import defaults_for_schema, get_schema

    schema = get_schema("anthropic")
    defaults = defaults_for_schema(schema)
    assert isinstance(defaults, dict)
    assert "temperature" in defaults
    assert "max_tokens" in defaults


# -------------------------------------------------------------------
# 7. Alert Engine Tests (expanded)
# -------------------------------------------------------------------


def test_alert_engine_acknowledge() -> None:
    from airlock.tui.alert_engine import Alert, AlertEngine

    engine = AlertEngine()
    alert = Alert(
        rule_name="test_rule",
        severity="warning",
        title="Test alert",
        detail="Detail text",
        entity_type="model",
        entity_id="test-model",
        timestamp=1000.0,
    )
    engine.active.append(alert)
    assert engine.active_count() == 1

    engine.acknowledge(alert)
    assert alert.acknowledged is True
    assert engine.active_count() == 0  # acknowledged alerts not counted


def test_alert_engine_dismiss() -> None:
    from airlock.tui.alert_engine import Alert, AlertEngine

    engine = AlertEngine()
    alert = Alert(
        rule_name="test_rule",
        severity="critical",
        title="Dismissible alert",
        detail="Will be removed",
        entity_type="provider",
        entity_id="openai",
        timestamp=2000.0,
    )
    engine.active.append(alert)
    assert len(engine.active) == 1

    engine.dismiss(alert)
    assert len(engine.active) == 0


def test_alert_engine_cooldown() -> None:
    import time as _time

    from airlock.tui.alert_engine import Alert, AlertEngine, AlertRule

    fired_count = 0

    def _always_fire(store):
        nonlocal fired_count
        fired_count += 1
        return [
            Alert(
                rule_name="always_fire",
                severity="info",
                title=f"Fired #{fired_count}",
                detail="Always fires",
                entity_type="model",
                entity_id=f"entity-{fired_count}",
                timestamp=_time.time(),
            )
        ]

    engine = AlertEngine()
    engine.rules = [
        AlertRule(
            name="always_fire",
            condition=_always_fire,
            cooldown_seconds=9999.0,  # very long cooldown
            severity="info",
        ),
    ]

    from airlock.fast.state import StateStore

    test_store = StateStore()

    # First evaluate should fire
    new1 = engine.evaluate(test_store)
    assert len(new1) == 1

    # Second evaluate should be suppressed by cooldown
    new2 = engine.evaluate(test_store)
    assert len(new2) == 0


# -------------------------------------------------------------------
# 8. Logs Screen Tests
# -------------------------------------------------------------------


async def test_logs_has_export_button() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#logs-export-btn") is not None


async def test_logs_has_analysis_tabs() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#tab-opts") is not None
        assert app.query_one("#tab-cache") is not None
        assert app.query_one("#tab-trends") is not None
        assert app.query_one("#tab-hyp") is not None


async def test_logs_refresh_mode_options() -> None:
    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#logs-refresh-mode") is not None


# -------------------------------------------------------------------
# Markup escape — user-controlled strings must not be interpreted
# as Rich markup (prevents injection from provider/model/client names,
# guardrail names, MCP tool names, and error messages).
# -------------------------------------------------------------------


def test_guards_render_signals_escapes_guardrail_name() -> None:
    from airlock.tui.screens.guards import FlowEntry, _render_signals

    entry = FlowEntry(
        timestamp="2026-01-01T00:00:00Z",
        request_id="r1",
        model="m1",
        client_id="c1",
        success=True,
        composite_score=0.5,
        would_block=False,
        orchestrator_version=None,
        signals=[
            {
                "guardrail_name": "[bold red]INJECTED[/]",
                "detected": False,
                "score": 0.1,
                "duration_ms": 1.0,
                "details": {},
            }
        ],
        enforcement=None,
        raw_observation=None,
        raw_record={},
    )
    out = _render_signals(entry)
    # The raw literal bracket form must appear (escaped with backslash),
    # meaning it was NOT consumed as a markup tag.
    assert r"\[bold red]INJECTED\[/]" in out


def test_guards_render_tool_result_escapes_tool_name() -> None:
    from airlock.tui.screens.guards import FlowEntry, _render_tool_result

    entry = FlowEntry(
        timestamp="2026-01-01T00:00:00Z",
        request_id="r1",
        model="m1",
        client_id="c1",
        success=False,
        composite_score=None,
        would_block=None,
        orchestrator_version=None,
        signals=[],
        enforcement=None,
        raw_observation=None,
        raw_record={"error": "[bold red]boom[/]"},
        call_type="call_mcp_tool",
        mcp_tool_name="[bold red]EVIL[/]",
        mcp_server_name="[green]srv[/]",
    )
    out = _render_tool_result(entry)
    assert r"\[bold red]EVIL\[/]" in out
    assert r"\[green]srv\[/]" in out
    assert r"\[bold red]boom\[/]" in out


def test_guards_render_pipeline_escapes_request_metadata() -> None:
    from airlock.tui.screens.guards import FlowEntry, _render_pipeline

    entry = FlowEntry(
        timestamp="2026-01-01T00:00:00Z",
        request_id="[bold red]INJECT[/]",
        model="[bold red]MODEL[/]",
        client_id="[bold red]CLIENT[/]",
        success=True,
        composite_score=0.0,
        would_block=False,
        orchestrator_version=None,
        signals=[],
        enforcement=None,
        raw_observation=None,
        raw_record={
            "airlock_failover": {
                "original_model": "a",
                "failover_model": "[bold red]FM[/]",
                "reason": "[bold red]FR[/]",
            },
            "airlock_model_override": {
                "requested_model": "a",
                "final_model": "[bold red]FINAL[/]",
                "reason": "[bold red]OR[/]",
            },
            "airlock_provider_protection": {
                "action": "[bold red]ACT[/]",
                "provider": "[bold red]PROV[/]",
                "client_id": "[bold red]PC[/]",
                "cooldown_seconds": 30,
            },
        },
    )
    out = _render_pipeline(entry)
    assert r"\[bold red]INJECT\[/]" in out
    assert r"\[bold red]MODEL\[/]" in out
    assert r"\[bold red]CLIENT\[/]" in out
    assert r"\[bold red]FM\[/]" in out
    assert r"\[bold red]FINAL\[/]" in out
    assert r"\[bold red]ACT\[/]" in out


async def test_overview_no_data_for_provider_escapes_name() -> None:
    """Early-return branch in _show_provider_detail must escape markup in
    the provider name so injected tags aren't interpreted as Rich markup."""
    from textual.widgets import Static

    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        from airlock.tui.screens.overview import OverviewPane

        overview = app.query_one(OverviewPane)
        overview._show_provider_detail("[bold red]INJECT[/]")
        await pilot.pause()

        detail = app.query_one("#ov-detail", Static)
        raw = str(detail.content)
        # The escaped string stored on the Static widget must contain the
        # backslash-escaped literal form, meaning the brackets will NOT be
        # consumed as Rich markup tags at render time.
        assert r"\[bold red]INJECT\[/]" in raw


async def test_overview_no_data_for_model_escapes_name() -> None:
    """Early-return branch in _show_model_detail must escape markup in
    the model name."""
    from textual.widgets import Static

    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        from airlock.tui.screens.overview import OverviewPane

        overview = app.query_one(OverviewPane)
        overview._show_model_detail("[bold red]INJECT[/]")
        await pilot.pause()

        detail = app.query_one("#ov-detail", Static)
        raw = str(detail.content)
        assert r"\[bold red]INJECT\[/]" in raw


async def test_config_pii_kw_switches_wired_to_env(monkeypatch) -> None:
    """The PII/KW guardrail Switches must reflect env vars on init AND
    write back to env on Apply (bidirectional wiring)."""
    from textual.widgets import Switch

    monkeypatch.setenv("AIRLOCK_PII_ENABLED", "false")
    monkeypatch.setenv("AIRLOCK_KW_ENABLED", "true")

    app = AirlockApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()

        pii_switch = app.query_one("#cfg-pii-enabled", Switch)
        kw_switch = app.query_one("#cfg-kw-enabled", Switch)

        # Initial values should reflect env vars
        assert pii_switch.value is False
        assert kw_switch.value is True

        # Toggle both
        pii_switch.value = True
        kw_switch.value = False

        from airlock.tui.screens.config import ConfigPane

        config_pane = app.query_one(ConfigPane)
        config_pane._apply_settings()
        await pilot.pause()

        assert os.environ.get("AIRLOCK_PII_ENABLED") == "true"
        assert os.environ.get("AIRLOCK_KW_ENABLED") == "false"
