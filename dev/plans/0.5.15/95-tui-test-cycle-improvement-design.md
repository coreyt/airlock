# Slice 95 design — deterministic stale-callback coverage and pure-mount isolation

**Status:** design approved; implementation and final verification in progress.

## Scope

Add a deterministic **normal-mode** regression for the existing
background-refresh stale callback guard, then evaluate only the eight Phase 4
pure composition/navigation mounts for explicit `AirlockApp(test_harness=True)`.
The fixture-only
instantiation test remains unchanged. No timer, worker, runtime, dependency,
or CI behavior changes.

## TDD

**RED:** in a disposable detached worktree, temporarily remove only the
`except NoMatches: return` guard, add the same focused test, and record the
expected escaping `NoMatches` failure; restore the worktree afterward. The test
uses normal `AirlockApp()` mode. It mocks only unrelated pane startup refreshes
to avoid nondeterministic I/O while retaining app normal lifecycle behavior.
Before the normal-mode mount, save `raw_refresh =
OverviewPane._refresh_state.__wrapped__`, then replace the descriptor only for
the automatic startup invocation so it cannot race the captured raw callback.
App-level normal lifecycle remains enabled and is preserved by named
`test_default_app_keeps_mount_lifecycle_enabled`. Invoke `raw_refresh(pane)`
after mount. Patch
`pane.app.call_from_thread`, invoke that raw descriptor, assert exactly one
`call_from_thread(callback)` invocation with no callback arguments and
`pane.is_mounted is True`, make only `#ov-providers` lookup raise `NoMatches`,
then invoke the captured callback without exception. On current `eb75a44` the
same test passes; retain the separate normal-mode lifecycle contract.

**GREEN:** preserve the existing `is_mounted`/`NoMatches` guard and add the
regression. Then make exactly these eight Phase 4 mounts explicitly harness
mode: `test_overview_is_default`, `test_overview_has_widgets`,
`test_guards_screen_exists`, `test_logs_screen_exists`,
`test_config_screen_exists`, `test_test_screen_exists`,
`test_navigation_by_number_keys`, and `test_all_five_views_accessible`.
Their assertions remain pane presence/navigation only.

## Measurement and acceptance

Use identical-base Python 3.12 locked environments: an unmodified baseline
worktree and a reviewed test-only experiment patch worktree. Record base SHA
and patch identity, alternate A/B runs, and reject environmental drift.
one warm-up each, and alternate A/B/B/A/A/B phase4 commands with a 30s timeout.
Record exit, test count, elapsed wall time, all pytest durations, host facts,
and paired median/range. The literal command is

```bash
timeout --preserve-status 30s uv run python -m pytest \
  tests/harness/test_phase4_tui.py -q --durations=0 --durations-min=0
```

Capture all setup/call/teardown durations. Any timeout/failure/environment drift invalidates the
claim and prevents migration. Accept only if behavior tests, the stale RED,
harness and normal lifecycle contracts, and three complete alternated pairs
pass; otherwise retain normal Phase 4 mounts and record no speed claim.

## Coverage and rollback

Harness tests still compose real panes. Normal-mode tests retain lifecycle,
actual refresh, JSONL/MCP, cancellation, and shutdown coverage. Rollback is a
test-only reversion; no product behavior changes.
