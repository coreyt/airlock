"""
S14 — TUI: 5-view architecture, navigation, widget presence.
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

    async def test_overview_is_default(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as _pilot:
            workspace = app.query_one("#workspace")
            assert workspace.current == "overview"

    async def test_overview_has_widgets(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as _pilot:
            overview = app.query_one("#overview")
            assert overview is not None

    async def test_guards_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("2")
            guards = app.query_one("#guards")
            assert guards is not None

    async def test_logs_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")
            logs = app.query_one("#logs")
            assert logs is not None

    async def test_config_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("4")
            config = app.query_one("#config")
            assert config is not None

    async def test_test_screen_exists(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("5")
            test_pane = app.query_one("#test")
            assert test_pane is not None


class TestTUINavigation:
    async def test_navigation_by_number_keys(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        view_ids = ["overview", "guards", "logs", "config", "test"]
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one("#workspace")
            for i, expected_id in enumerate(view_ids, 1):
                await pilot.press(str(i))
                assert workspace.current == expected_id, (
                    f"Key {i} should switch to {expected_id}"
                )

    async def test_all_five_views_accessible(self):
        from airlock.tui.app import AirlockApp

        app = AirlockApp()
        async with app.run_test(size=(120, 40)) as pilot:
            for key in "12345":
                await pilot.press(key)
            # All panes should exist
            for pane_id in ["overview", "guards", "logs", "config", "test"]:
                assert app.query_one(f"#{pane_id}") is not None
