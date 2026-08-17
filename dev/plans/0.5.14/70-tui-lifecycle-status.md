# Slice 70 — TUI lifecycle status

**Status:** complete

## Ratified requirement and acceptance criteria

Slice 70 ratified DFR-30/DAC-30. The explicit constructor-only
`AirlockApp(test_harness=True)` flag composes the same production pane tree but
suppresses mount-time MCP, JSONL, overview, Guards, Logs, and Config background
work. It is never environment-derived and production defaults remain unchanged.
Pure composition, navigation, and static-widget tests now use this harness;
lifecycle tests remain normal-mode and mock only the individual worker seams
needed to assert lifecycle startup/teardown.

Initial Textual filter-change events were found during independent review to
schedule a Logs worker despite the mount guard. The event dispatch paths now
also return in the explicit harness, so the test does not hide the regression
by mocking the Logs loader.

## Evidence

- RED/GREEN regression: `tests/test_tui_harness.py` proves the real production
  tree is present, no app-level health/tailer work begins in the harness, and
  normal mode invokes its app-level lifecycle starters.
- Migration evidence: six existing composition/navigation/widget tests in
  `tests/test_tui.py` use the harness.
- `timeout 45s .venv/bin/python -m pytest tests/test_tui_harness.py -q`:
  **2 passed in 3.57s**.
- `timeout 60s .venv/bin/python -m pytest tests/test_tui.py -q -k
  'app_composes_all_panes or screen_switching_via_keys or tab_bar_navigation or
  overview_has_widgets or overview_has_start_button or overview_has_console_log'`:
  **6 passed, 60 deselected in 7.74s**.

An independent high-reasoning review found and verified the initial-select
worker fix. Full non-live duration comparison remains release-closeout evidence;
the focused slice tests are bounded and terminate.
