# Slice 94 — TUI-test characterization, research, and recommendation experiments

**Status:** complete — no maintained optimization admitted. Analysis and
controlled validation only; Airlock tests and product runtime did not change.

## Re-evaluation and evidence

Slice 90 is closed. This slice starts from merged `main` head `eb75a44`, not
the historical release branch. Completed ordinary CI runs ranged from 856.06s
to 1112.49s with the same test count, so they establish a suite-duration
concern but do **not** attribute it to TUI tests. Successful CI runs retain no
JUnit durations.

Initial inventory has 163 TUI-named tests and 61 Textual `run_test()` contexts:
43 in `tests/test_tui.py`, eight in `test_tui_thread_safety.py`, eight in
`tests/harness/test_phase4_tui.py`, and two harness contracts. Ten contexts
explicitly use `AirlockApp(test_harness=True)`, 51 mount normal mode, and 48
call `Pilot.pause()`. The existing harness composes production panes while
suppressing unrelated mount work; its contract retains a normal-mode lifecycle
test. These are candidates for individual investigation, not blanket migration.

Official sources show that `run_test()` is headless but otherwise runs the app
normally, `pause()` settles pending messages rather than providing a generic
delay, and workers support explicit lifetime/cancellation tests. [Textual
testing](https://textual.textualize.io/guide/testing/), [App API](https://textual.textualize.io/api/app/),
[Pilot API](https://textual.textualize.io/api/pilot/), and [worker API](https://textual.textualize.io/api/worker/)
are the primary research sources. Pytest's built-in duration report needs no
new dependency.

## Scope and revised requirements

1. Classify each mounted TUI test: pure render/navigation/state, scheduled
   message/interaction, direct worker-dispatch, normal lifecycle/shutdown, or
   external/topology. For lifecycle, cancellation, shutdown, stale callbacks,
   JSONL, and MCP, name a concrete retained normal-mode test or record a
   coverage gap. A gap becomes mandatory Slice 95 RED work and blocks migration
   of related tests; an incidental normal-mode mount is not coverage.
2. Measure two distinct things: a CI-equivalent ordinary-suite trend for release
   impact, and a focused Textual selection with per-test duration for causal
   experiments. Never infer the latter from the former.
3. Run three comparable warm control/experiment repetitions, recording commit,
   exact command, Python/Textual versions, host/runner facts, preparation/cache
   state, test counts, median, range, and all slowest-test durations.
4. Research and rank test-by-test options: explicit existing harness use for
   non-lifecycle checks, direct refresh/state tests with fakes, and removal of
   only demonstrably unnecessary `pause()` calls. Measure but do not enable
   process parallelism.
5. Use disposable experiment patches/worktrees; discard them after recording
   non-secret results. No maintained product, test, Make/CI, dependency, or
   configuration change belongs here; required plan/design/status records are
   permitted.

## Candidate experiments and acceptance

Highest-priority candidates are Phase 4 composition/navigation checks,
thread-safety tests that invoke `__wrapped__` worker bodies directly, then pure
`test_tui.py` structure/state cases. Do not pre-approve tests that assert actual
refresh workers, cancellation, JSONL tailing, MCP lifecycle, stale callbacks,
or shutdown.

Each harness experiment must prove production pane composition with no unrelated
mount workers, while its normal-mode counterpart proves the named lifecycle
behavior still runs. Each proposed pause removal requires a paired RED/GREEN
experiment demonstrating the assertion succeeds without it; retain pauses for
message propagation, modal scheduling, or worker settlement. A generic fixture
that silently enables harness mode is prohibited.

For each named repetition (for example `focused-control-r1` through `r3`, then
the corresponding experiment IDs), run this command and parse its artifacts
before deleting them:

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
if (( pytest_status != 0 )); then
  rm -f "$log" "$xml"; exit "$pytest_status"
fi
if (( $(wc -c < "$log") > 5242880 || $(wc -c < "$xml") > 5242880 )); then
  rm -f "$log" "$xml"; exit 2
fi
```

The CI-equivalent trend follows `make sync && make verify`, then:

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
if (( pytest_status != 0 )); then
  rm -f "$log" "$xml"; exit "$pytest_status"
fi
if (( $(wc -c < "$log") > 5242880 || $(wc -c < "$xml") > 5242880 )); then
  rm -f "$log" "$xml"; exit 2
fi
```

Pytest duration output, not JUnit, supplies setup/call/teardown times. Both
temporary `.log` and `.xml` artifacts are retained/read only when each is at
most 5 MiB. A nonzero, missing, or oversized capture is excluded from the
median/range and recorded as inconclusive. The status names every run ID and
its non-secret parsed summary before both artifacts are deleted. A complete
status must contain inventory,
research citations, measurements, controlled experiment results, rejected
alternatives, a recommendation matrix for Slice 95, and an explicit statement
of whether DFR-30 needs amendment (default: no amendment without evidence).

## Review, safety, and handoff

Independent design review is required before experiments, and independent
recommendation/report review before closeout. Verify focused TUI/harness tests
and the regression selection needed to prove experiments left maintained code
unchanged. The main risks are noisy/incomparable benchmarks and loss of real
asynchronous coverage; fixed commands, paired measurements, explicit DFR-30
mapping, and temporary-only experiments control them. Slice 95 must re-evaluate
the final status and current code/CI before admitting any change.

If the timing runner cannot capture the required comparable repetitions, record
the capture as incomplete and close Slice 94 only with a **no-change**
recommendation. That outcome is a hard no-admission gate for Slice 95, not a
waiver to claim a speedup or implement an optimization.
