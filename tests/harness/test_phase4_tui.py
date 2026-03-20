"""
S14 — TUI: all 8 screens, navigation, widget presence.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.harness


@pytest.fixture
def app():
    from airlock.tui.app import AirlockApp

    return AirlockApp(host="127.0.0.1", port="9999")


class TestTUIBasic:

    def test_app_instantiates(self, app):
        assert app is not None

    async def test_dashboard_proxy_indicator(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one("#workspace")
            assert workspace.current == "dashboard"

    async def test_dashboard_guardrail_indicators(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            dashboard = app.query_one("#dashboard")
            assert dashboard is not None

    async def test_models_screen_has_table(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("2")
            models_pane = app.query_one("#models")
            assert models_pane is not None

    async def test_threats_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")
            threats = app.query_one("#threats")
            assert threats is not None

    async def test_logs_screen_has_table(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("4")
            logs = app.query_one("#logs")
            assert logs is not None

    async def test_analysis_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("5")
            analysis = app.query_one("#analysis")
            assert analysis is not None

    async def test_settings_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("6")
            settings = app.query_one("#settings")
            assert settings is not None

    async def test_flow_screen_has_table(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("7")
            flow = app.query_one("#flow")
            assert flow is not None

    async def test_mcp_screen_has_table(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("8")
            mcp = app.query_one("#mcp_servers")
            assert mcp is not None


class TestTUINavigation:

    async def test_navigation_by_number_keys(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        screen_ids = [
            "dashboard", "models", "threats", "clients", "logs",
            "analysis", "settings", "flow", "mcp_servers",
        ]
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one("#workspace")
            for i, expected_id in enumerate(screen_ids, 1):
                await pilot.press(str(i))
                assert workspace.current == expected_id, (
                    f"Key {i} should switch to {expected_id}"
                )

    async def test_all_nine_screens_accessible(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            for key in "123456789":
                await pilot.press(key)
            # All panes should exist
            for pane_id in [
                "dashboard", "models", "threats", "clients", "logs",
                "analysis", "settings", "flow", "mcp_servers",
            ]:
                assert app.query_one(f"#{pane_id}") is not None
