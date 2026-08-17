# Slice 95 — TUI-test-cycle improvement status

**Status:** complete. The test-only Textual callback-signature correction is
independently reviewed and exact-head CI verified.
The independent code review confirmed the normal-mode guard coverage and exact
eight-test mapping. Its timing-evidence ordering observation is addressed by
the explicit experiment-before-acceptance wording in the approved plan and the
reproducible measurement record below. This is a test-only change; it does not
establish a material end-to-end speed improvement.

## Re-evaluation and approved scope

Slice 94 closed without a causal timing claim. Its current, explicit gate was
closed here only by a normal-mode stale-callback RED proof and three complete,
bounded paired focused measurements. The accepted scope is intentionally small:

- retain production `AirlockApp` and Textual worker/timer behavior unchanged;
- add a normal-mode regression for the existing `NoMatches` stale-refresh
  guard; and
- explicitly enable the already-existing composition harness for eight Phase 4
  tests that assert only pane presence or key navigation.

The non-mount fixture `TestTUIBasic.test_app_instantiates` remains unchanged.
No thread-safety tests, `pause()` calls, global fixtures, xdist, retry, timeout,
dependency, or CI changes were admitted. Named normal-mode lifecycle,
JSONL/MCP, refresh, cancellation, and shutdown tests remain the coverage
boundary required by DFR-30/DAC-30.

## TDD evidence

**RED.** A disposable detached worktree at the parent of stale-guard commit
`6e8fb90` (`5473e08`) received the same normal-mode test. The callback was
captured from the real raw worker descriptor, then only its providers-table
lookup was made to raise `NoMatches`. It failed as required:

```text
tests/test_tui.py::test_overview_stale_refresh_callback_is_ignored
NoMatches: providers table removed
1 failed, 66 deselected in 5.62s
```

**GREEN.** On merged-main base `eb75a44`, the retained guard makes the same
normal-mode test pass (`1 passed, 72 deselected in 3.25s`). The test saves
`OverviewPane._refresh_state.__wrapped__`, prevents only automatic overview
startup refresh from racing it, retains normal `AirlockApp()` mount behavior,
asserts one zero-argument `call_from_thread` callback while the pane is
mounted, and invokes it after the targeted `#ov-providers` lookup raises
`NoMatches`. The separate normal-lifecycle contract remains
`tests/test_tui_harness.py::test_default_app_keeps_mount_lifecycle_enabled`.

The eight Phase 4 tests now use explicit `AirlockApp(test_harness=True)` while
continuing to compose the production pane tree. Focused GREEN evidence:

```text
tests/harness/test_phase4_tui.py: 9 passed in 12.56s
tests/test_tui_harness.py: 2 passed in 3.51s
```

## Paired timing result

Both measurements used Python 3.12.3, Textual 6.2.1, Linux 7.0.0-28-generic
x86_64, the locked dependency set, and the identical `eb75a44` source base in
fresh isolated worktrees. The experiment was only the reviewed Phase4 patch,
identified reproducibly by `git patch-id --stable` as
`558a8f37a11f8d7ca3698febcb387ac281fc9c4a`; control had no working-tree diff.
One warm-up per side completed (control 14.10s; experiment 12.17s), then the
recorded sequence was control/experiment/experiment/control/control/experiment
(A/B/B/A/A/B). Each retained run used:

```bash
timeout --preserve-status 30s uv run python -m pytest \
  tests/harness/test_phase4_tui.py -q --durations=0 --durations-min=0
```

All six runs completed with exit 0, nine passes, and no timeout:

| Run | Side | Wall seconds | Pytest seconds | Slowest setup/call |
| --- | --- | ---: | ---: | --- |
| C1 | normal control | 14.33 | 12.63 | setup fixture 2.36; number navigation 2.38 |
| E1 | explicit harness | 16.39 | 14.81 | setup fixture 3.33; all-views navigation 2.55 |
| E2 | explicit harness | 14.65 | 12.93 | setup fixture 2.49; all-views navigation 2.54 |
| C2 | normal control | 14.74 | 12.92 | setup fixture 2.54; number navigation 2.38 |
| C3 | normal control | 13.65 | 12.15 | setup fixture 2.35; number navigation 2.26 |
| E3 | explicit harness | 13.25 | 11.75 | setup fixture 2.32; all-views navigation 2.28 |

| Group | Wall seconds | Median | Range |
| --- | ---: | ---: | ---: |
| normal control | 14.33, 14.74, 13.65 | 14.33 | 13.65–14.74 |
| explicit harness | 16.39, 14.65, 13.25 | 14.65 | 13.25–16.39 |

Pytest reported the fixture-only `test_app_instantiates` setup above and its
call as 0.00s. Every other setup and every teardown was 0.00s. The eight call
durations, in the order default/widgets/guards/logs/config/test/number/all-
views, were C1 `0.57/0.74/0.91/1.21/1.20/0.99/2.38/2.23`; E1
`0.86/1.01/1.01/1.40/1.17/1.14/2.27/2.55`; E2
`0.58/0.71/0.92/1.26/0.94/1.19/2.25/2.54`; C2
`0.61/0.79/0.96/1.25/1.16/0.97/2.38/2.21`; C3
`0.73/0.61/0.91/1.13/1.06/0.91/2.26/2.14`; and E3
`0.54/0.68/0.85/1.10/0.87/1.07/2.01/2.28` seconds.

The experiment median is 0.32s slower (about 2%) and its range is wider than
that difference. The data therefore validate only the test-safety and
coverage-mapping gate; they are **inconclusive for performance** and do not
support a speed claim or broader migration.

## Remaining verification and rollback

Independent code review approved after three evidence-only FIX cycles. The
independent verifier repeated focused GREEN, normal lifecycle, `make sync &&
make verify`, Ruff, format, diff integrity, and strict MkDocs successfully.
It found no implementation defect. Its local ordinary-suite attempt was
externally terminated at 14% with no pytest failure, timeout, or summary, so it
was correctly recorded as inconclusive. Exact-head GitHub CI for commit
`2a908c6` then passed every job: Docker (1m18s), docs (27s), lint (58s),
security (17s), Gitleaks scan (7s), and the ordinary Python 3.12 suite (15m06s).
That complete CI run closed the original condition. A later documentation-head
CI run exposed a test-only race: the stale test's class-level `Mock` callbacks
were retained by Textual intervals, whose dynamic `_param_count` was later used
as a slice index when invoking a timer. The focused regression passes after
replacing them with real zero-argument bound methods; FIX review approved and
exact-head CI passed docs, test (3.12) in 14m59s, lint, security, Docker, and a
rerun Gitleaks scan (the first scan failed only on GitHub codeload 429/503
before repository code ran). Rollback is a reviewed reversion of the two test
files and this status record; there is no runtime or configuration rollback.
