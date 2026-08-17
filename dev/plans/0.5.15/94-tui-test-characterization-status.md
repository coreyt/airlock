# Slice 94 — TUI-test characterization status

**Status:** complete with a conservative recommendation: do not admit a bulk
harness or pause-removal change to Slice 95 from current evidence.

## Audit, design, and baseline

Slice 90 was closed; this slice used merged `main` `eb75a44` in isolated
worktree `0.5.15-s94` on Python 3.12.3/Textual 6.2.1. The independent audit
required exact-main measurement, focused causal timing, test-by-test harness
decisions, and a DFR-30 mapping. The independent design review required three
fixes: coverage gaps become Slice 95 RED gates; literal bounded,
failure-preserving capture commands; and unique artifact IDs/exit handling.
FIX-3 approved the design. `make sync && make verify` passed.

Historical ordinary CI is context only: exact merged head completed 3428
passed/112 deselected/1 XPASS in 856.06s, while its release-branch parent with
the same count took 1112.49s. Success JUnit was not retained, so neither run
proves a TUI cause.

## Inventory and preservation assessment

The audit counted 163 TUI-named tests, 61 `run_test()` contexts (43 in
`test_tui.py`, eight thread-safety, eight Phase 4, and two harness contracts),
10 explicit harness uses, 51 normal-mode mounts, and 48 `pause()` calls.

Phase 4 composition/navigation and direct `__wrapped__` thread-dispatch tests
were high-confidence *investigation* candidates, not pre-approved changes.
There is no concrete stale-callback normal-mode test. Slice 95 must add that
RED regression before migrating related tests; every lifecycle/cancellation/
shutdown/JSONL/MCP mapping must be rechecked there.

## Research and experiments

Textual documents `run_test()` as headless normal app execution and `pause()`
as pending-message settlement, not a generic delay. [Textual testing](https://textual.textualize.io/guide/testing/),
[App API](https://textual.textualize.io/api/app/), and [worker API](https://textual.textualize.io/api/worker/)
were reviewed. Pytest duration reporting is built in.

Focused exact-main harness/TabBar contracts passed: 5 passed in 6.02s; the five
mounted-app calls ranged 0.57–1.00s. A larger 91-test focused capture was
terminated by the terminal wrapper mid-run and is inconclusive.

The disposable Phase 4 experiment used unique XML/log run IDs, Python 3.12.3,
the same selection, and no maintained change. Normal controls were 13.17s,
13.57s, and 14.06s. Explicit-harness runs were 22.11s (cold), 15.13s, and
19.03s; a post-revert same-worktree normal run was 23.07s. Only two warmed
harness repetitions completed before the terminal wrapper interrupted further
full captures. The variance, direction change, and incomplete paired set make
this non-causal. The required comparable-repetition condition is therefore
unmet; the plan permits only this no-change closure, not a performance claim.
The exact disposable worktree was removed and no temporary report was retained
as durable evidence.

## Recommendation and handoff

Reject bulk harness migration and bulk `pause()` removal. Do not add xdist,
retries, sleeps, relaxed timeouts, or a global all-harness fixture. Slice 95
may proceed only after adding stale-callback RED coverage, obtaining a stable
runner for complete focused captures, and evaluating one explicit test mapping
at a time. If those gates are unavailable, its correct outcome is no maintained
test change. No DFR-30 amendment is recommended.

No product, test, Make/CI, dependency, or configuration file changed. The only
Slice 94 durable changes are plan/design/status documentation.
