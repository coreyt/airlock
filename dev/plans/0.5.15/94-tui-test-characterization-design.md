# Slice 94 design — causal, coverage-preserving TUI-test characterization

**Status:** proposed for independent design review.

## Decision boundary

Slice 94 is not an optimization patch. It establishes whether a test-cycle
change is justified, and supplies a reproducible experiment record for Slice
95. Product, test, Make/CI, dependency, and configuration files must be
byte-for-byte unchanged by experiments; required plan/design/status records are
permitted. Temporary experiment patches are disposable evidence, never delivery.

The known whole-suite CI durations (14:16 and 18:32 with equal test counts) are
release-impact observations only. They cannot identify a slow TUI test, because
they include collection, coverage, all modules, runner variation, and no
retained success-JUnit durations. The causal unit is instead a focused Textual
selection run with all setup/call/teardown durations.

## Measurement design

1. Start at exact merged `main` SHA and record a clean worktree, Python, uv,
   Textual, pytest, platform/CPU, available memory, `make sync && make verify`
   result, command, and cache state.
2. Run a CI-equivalent ordinary non-live/non-Docker selection once only as a
   trend/equivalence reference. Label it non-causal.
3. Run the focused selection three times after a single documented warm-up. Use
   unique, recorded IDs (for example `focused-control-r1` through `r3`, then
   experiment IDs), parse each bounded artifact before deletion, and exclude
   any inconclusive run from median/range:

   ```bash
   run_id=focused-control-r1
   log=/tmp/airlock-slice94-$run_id.log
   xml=/tmp/airlock-slice94-$run_id.xml
   set -o pipefail
   uv run python -m pytest tests/test_tui.py tests/test_tui_thread_safety.py \
     tests/test_tui_harness.py tests/harness/test_phase4_tui.py \
     --durations=0 --durations-min=0 \
     --junitxml="$xml" -o junit_logging=no 2>&1 | tee "$log"
   pytest_status=${PIPESTATUS[0]}
   if (( pytest_status != 0 )); then rm -f "$log" "$xml"; exit "$pytest_status"; fi
   if (( $(wc -c < "$log") > 5242880 || $(wc -c < "$xml") > 5242880 )); then
     rm -f "$log" "$xml"; exit 2
   fi
   ```

   Preserve complete duration output and JUnit only in bounded temporary
   artifacts; parse setup/call/teardown from pytest duration output and use
   JUnit only as aggregate per-test evidence. The exit status comes from
   `PIPESTATUS[0]`, with immediate nonzero exit, so a failing pytest process
   cannot be mistaken for a successful `tee` pipeline. Retain/read each `.log`
   and `.xml` only when it is at most 5 MiB; otherwise record the uniquely named
   run as oversized/inconclusive. Report each run plus median/range, then delete
   both artifacts. An incomplete capture is inconclusive, not a zero or a pass.
4. The literal CI-equivalent non-causal reference is:

   ```bash
   run_id=ci-reference-r1
   log=/tmp/airlock-slice94-$run_id.log
   xml=/tmp/airlock-slice94-$run_id.xml
   set -o pipefail
   uv run pytest -m "not live and not docker" --cov=airlock \
     --cov-report=xml --cov-report=term-missing \
     --junitxml="$xml" -o junit_logging=no --durations=0 --durations-min=0 \
     2>&1 | tee "$log"
   pytest_status=${PIPESTATUS[0]}
   if (( pytest_status != 0 )); then rm -f "$log" "$xml"; exit "$pytest_status"; fi
   if (( $(wc -c < "$log") > 5242880 || $(wc -c < "$xml") > 5242880 )); then
     rm -f "$log" "$xml"; exit 2
   fi
   ```

5. Repeat the same focused protocol after exactly one temporary experiment. Do not
   compare a warm experiment with a cold control, a different selection, or a
   different commit/environment.

## Classification and preservation matrix

Every `run_test` test gets one category and a target state:

| Category | Likely test style | Slice 94 experiment | Required retained proof |
| --- | --- | --- | --- |
| Pure render/navigation/state | Query widgets or drive key binding; no lifecycle assertion | Explicit harness candidate | Production pane composition and widget behavior |
| Direct worker-dispatch | Invoke `__wrapped__` body and assert UI dispatch | Explicit harness candidate | Exact dispatch and worker-boundary assertion |
| Scheduled interaction | Post/click/press whose handler queues work | Evaluate pause individually | Message/worker completion before assertion |
| Lifecycle and shutdown | Tests startup/cancellation/teardown/stale work | Normal mode only | App/pane worker start, cancellation, and cleanup |
| JSONL/MCP/external/topology | Actual data tailing, manager lifecycle, subprocess/network topology | Normal or dedicated integration mode | Named source-specific behavior |

The final status must name at least one retained normal-mode test for lifecycle,
cancellation, shutdown, stale callbacks, JSONL, and MCP—or record the concrete
gap. A gap becomes mandatory Slice 95 RED work and blocks migration of related
tests. A test’s incidental normal-mode mount does not satisfy that requirement.

## Controlled experiments

The priority order is Phase 4 composition/navigation, then thread-dispatch
tests, then individually classified pure tests. For a harness candidate, copy
only the candidate test into a disposable patch and make the constructor
explicitly `AirlockApp(test_harness=True)`; run the test, harness contracts, and
the mapped normal-mode tests. For a pause candidate, first run the exact test
with the pause, then remove precisely one pause in the disposable patch. The
candidate is rejected if its assertion is asynchronous, flakes, or removes an
intentional settlement boundary.

No experiment enables xdist, changes default mode, stubs behavior under test,
globally changes timers, retries tests, or adjusts a timeout. The only allowed
temporary change is a narrowly described test expression. Use a separate
worktree/patch and destroy it after recording results.

## Research interpretation

Textual documents `run_test()` as headless normal app execution and directs
tests to assert state after Pilot interactions. It recommends `pause()` to
process pending messages, not as a generic sleep. Its API exposes a message hook
and workers have explicit completion/cancellation semantics. These facts support
the categories above, but they do not prove an Airlock pause is removable;
Airlock experiments do. [Testing guide](https://textual.textualize.io/guide/testing/),
[App API](https://textual.textualize.io/api/app/), and [worker API](https://textual.textualize.io/api/worker/).

## Risks, acceptance, and rollback

The risks are benchmark noise, selecting a false root cause, and losing
background coverage. Fixed execution, paired repetitions, test-level mapping,
temporary-only changes, and independent review control them. Slice 94 accepts
only a recommendation with reproducible control/experiment evidence and a
named preservation mapping. Otherwise it recommends no maintained change.
Removing the temporary worktree/patch is complete rollback; the product and
test cycle are untouched.
