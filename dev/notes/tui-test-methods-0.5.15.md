# TUI test methods and outcome — 0.5.15

**Status:** accepted engineering guidance. This note covers test maintenance,
not Airlock runtime or operator performance.

## Question and boundary

Slice 94 characterized Textual TUI tests, and Slice 95 tested one narrow
change: whether eight pure Phase4 composition/navigation tests should use the
existing explicit `AirlockApp(test_harness=True)` mode. This is not a claim
about end-user TUI speed, proxy throughput, or the whole test suite.

Historical CI wall times are deliberately excluded from before/after results.
Successful runs did not retain per-test JUnit timing and ran in different CI
contexts, so they cannot establish a TUI cause.

## Test-mode policy

Textual's [testing guide](https://textual.textualize.io/guide/testing/) describes
`run_test()` as headless app execution; it does not turn lifecycle behavior off.
The [Pilot API](https://textual.textualize.io/api/pilot/) defines `pause()` as a
way to wait for pending messages and CPU-idle settlement. It is not a generic
delay to delete. The [Worker API](https://textual.textualize.io/api/worker/)
documents worker state and cancellation as lifecycle behavior to test directly.

Use `AirlockApp(test_harness=True)` explicitly and only for tests that assert
the production pane tree, static rendering, or deterministic navigation while
not asserting lifecycle effects. Keep normal `AirlockApp()` mode for worker and
timer lifecycle, cancellation, shutdown, stale callbacks, JSONL and MCP
integration, and actual refresh behavior. Do not introduce a global harness
fixture, remove `pause()` without a deterministic replacement, add xdist,
retries, or relaxed timeouts merely to reduce duration.

## Evidence and coverage mapping

The baseline at `eb75a44` had 61 `run_test()` contexts: 10 explicit harness
uses, 51 normal-mode mounts, and 48 `pilot.pause()` calls. The Slice 95 tree
has 62 contexts, 18 explicit harness uses, 44 normal mounts, and the same 48
pauses. The delta is a new normal-mode regression plus eight deliberate
per-test harness choices.

The harness migrations are:

- `TestTUIBasic.test_overview_is_default`
- `TestTUIBasic.test_overview_has_widgets`
- `TestTUIBasic.test_guards_screen_exists`
- `TestTUIBasic.test_logs_screen_exists`
- `TestTUIBasic.test_config_screen_exists`
- `TestTUIBasic.test_test_screen_exists`
- `TestTUINavigation.test_navigation_by_number_keys`
- `TestTUINavigation.test_all_five_views_accessible`

`TestTUIBasic.test_app_instantiates` remains an unchanged non-mount fixture.
The new normal-mode
`test_overview_stale_refresh_callback_is_ignored` captures the raw refresh
worker callback, proves the pane remains mounted, makes only the providers
table lookup raise `NoMatches`, and verifies the callback returns safely. Its
RED proof on the parent of the guard change raised `NoMatches`; current GREEN
coverage protects the existing teardown race. The named normal-mode contract
`test_default_app_keeps_mount_lifecycle_enabled` remains in place, alongside
existing lifecycle, JSONL/MCP, refresh, cancellation, and shutdown coverage.

## Measurement and outcome

The experiment used Python 3.12.3, Textual 6.2.1, pytest 9.1.1, Linux
7.0.0-28-generic x86_64, the locked dependencies, and base `eb75a44`. Its only
test delta is the stable Phase4 patch ID
`558a8f37a11f8d7ca3698febcb387ac281fc9c4a`. One warm-up per side preceded
three recorded A/B/B/A/A/B runs of:

```bash
timeout --preserve-status 30s uv run python -m pytest \
  tests/harness/test_phase4_tui.py -q --durations=0 --durations-min=0
```

All six retained runs passed nine tests without a timeout:

| Mode | Wall times (s) | Median (s) | Range (s) |
| --- | --- | ---: | --- |
| normal control | 14.33, 14.74, 13.65 | 14.33 | 13.65–14.74 |
| explicit harness | 16.39, 14.65, 13.25 | 14.65 | 13.25–16.39 |

The explicit-harness median was 0.32s (about 2.2%) slower and had a wider
range. The navigation tests and fixture setup dominated both modes. The result
therefore validates only the safety and coverage mapping; it is **inconclusive
for performance**. It authorizes neither a bulk harness migration nor a
whole-suite speed claim.

CPU model, memory/load state, and raw historical CI duration logs were not
retained. A new timing run can check whether this focused command still works,
but cannot make past measurements reproducible. Any new candidate must have
its own test-by-test coverage decision and controlled comparison.
