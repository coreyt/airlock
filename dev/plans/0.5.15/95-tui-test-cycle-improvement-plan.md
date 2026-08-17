# Slice 95 — evidence-backed TUI-test-cycle improvements

**Status:** admitted for the bounded RED/design/measurement work below. Slice
94's broad optimization claim remains rejected; no migration is accepted until
the explicit stale-callback RED test and stable alternating timing evidence pass.

## Purpose and admission

Improve the maintained TUI test cycle without altering production behavior.
Before RED, re-check Slice 94's comparability, current `airlock/tui` and test
changes, CI timing, Textual/pytest versions, and DFR-30 coverage mapping.
Reject any unmeasured recommendation. Write and independently review the
Slice 95 design, including worker/timer isolation, race/coverage equivalence,
and measurement method.

The re-evaluation identifies two candidates only: (1) an explicit harness mode
for the eight Phase 4 composition/navigation mounts, which assert neither
lifecycle nor worker effects; (2) later, separately measured, direct
`__wrapped__` dispatcher tests. This slice starts with candidate (1) only.
Before it, add a normal mount plus captured refresh-callback regression for the
stale `NoMatches` teardown race in `OverviewPane._refresh_state`. Retain named
normal-mode lifecycle, JSONL/MCP, real refresh, cancellation, and shutdown
tests.

## Permitted and prohibited work

Permitted only when Slice 94 supports it: explicit per-test use of the existing
composition harness; replacing a purposeless pause with immediate assertion or
deterministic synchronization; direct state/refresh testing with fake clients
while retaining actual-pane integration; and narrowly removing redundant setup.

Do not remove or weaken normal-mode lifecycle, cancellation, shutdown,
stale-callback, JSONL, MCP, thread-safety, or interaction coverage. Do not
change production timers/workers, add retries/sleeps/relaxed timeouts, use an
implicit all-harness fixture, enable xdist, alter CI requirements, dependencies,
or release behavior.

## TDD, acceptance, and evidence

For every accepted test change, record RED proof of preserved behavior and the
unnecessary source of work, implement the smallest GREEN test/test-support
change, then refactor only with focused tests green. Harness changes must prove
real pane composition/no unrelated workers; the retained normal-mode tests must
prove the corresponding lifecycle still runs. Map every replaced assertion
one-to-one.

Use an isolated experiment patch solely to run the exact Slice 94 focused
command in three comparable warm alternating pairs against an unmodified
baseline. The maintained migration is not accepted until those records show
the test counts, median/range, and slowest durations and independent review
confirms the coverage mapping. Report the result even if duration does not
improve. Then run focused TUI/harness and ordinary non-Docker suite, `make sync
&& make verify`, relevant lint/format, and docs checks. Obtain independent code
review and verification. Record all in
`95-tui-test-cycle-improvement-status.md`, including rollback and remaining
slow tests. Rollback is a reviewed reversion of the individual test change.
