# Slice 96 — TUI-test performance methods and outcome report

**Status:** revised after Slice 96 audit; awaits Slice 95 independent
verification before implementation. The report begins only after that closure,
including the valid outcome that no material speedup was demonstrated.

## Purpose and inputs

Create a durable engineering note, `dev/notes/tui-test-methods-0.5.15.md`,
from Slice 94 characterization and Slice 95's reviewed RED/GREEN/verification
evidence. Link it from `dev/README.md` and this slice's status record. It must
report only comparable measurements and never turn internal test timing into a
product-performance promise. It is not an operator-facing documentation or
release-note change.

## Required write-up

Document the question; baseline/current inventory boundary; base
commit/command/environment/test count/repeats; median/range and slowest tests;
official Textual sources; test classification; when to use the production
composition harness, normal mode, and `Pilot.pause`; before/after
test-to-coverage mapping; retained lifecycle safety tests; rejected options;
and remaining limitations. The baseline is `61/10/51/48` for
`run_test`/harness/normal/pause; current Slice 95 state is
`62/18/44/48`. Separate focused execution time from CI checkout/setup/queue/
Docker time. Exclude historical whole-CI duration comparisons from any
before/after conclusion because successful CI had no retained per-test timing.
Include no tokens, local secret paths, raw CI environment, or unbounded failure
output.

The report must name these eight harness migrations:
`TestTUIBasic.test_overview_is_default`, `test_overview_has_widgets`,
`test_guards_screen_exists`, `test_logs_screen_exists`,
`test_config_screen_exists`, `test_test_screen_exists`, and
`TestTUINavigation.test_navigation_by_number_keys` and
`test_all_five_views_accessible`. It must also name the unchanged non-mount
fixture `TestTUIBasic.test_app_instantiates` and the new normal-mode stale test;
identify the experiment by the stable Phase4 patch ID
`558a8f37a11f8d7ca3698febcb387ac281fc9c4a` or the eight test names plus source
base, never the obsolete unverifiable hash. It must call the paired result
inconclusive for performance (control median 14.33s; harness median 14.65s),
not a speedup. Update developer documentation and write
`96-tui-test-performance-report-status.md`. Independent technical/documentation
review must verify every number against prior evidence, source links,
arithmetic, and scope labels. Run `git diff --check`, a direct check that
`dev/README.md` links to the new note, focused documentation-contract tests,
strict MkDocs, and `uv run python -m pytest tests/test_tui.py -k
stale_refresh_callback -q` plus `uv run python -m pytest
tests/harness/test_phase4_tui.py -q`. The report must preserve DFR-30 and state
that runtime behavior was not changed merely to accelerate testing.
Documentation is the only blast radius; a normal documentation correction is
the rollback.
