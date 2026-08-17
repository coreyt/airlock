# Slice 96 design — TUI test methods and outcome record

**Status:** complete; independent documentation review and verification passed.

## Boundary

Add one engineering note and one `dev/README.md` link. Do not add public
operator documentation, a changelog entry, MkDocs navigation, runtime code,
test execution policy, dependencies, or CI changes.

## Required content

The note has four bounded sections:

1. **Question and limits.** Explain that it studies eight migrated Phase4
   `run_test()` mounts plus one unchanged non-mount fixture, not whole-suite or
   product performance. Exclude historical CI wall-clock comparisons from
   before/after conclusions.
2. **Test-mode policy.** Cite the official [Textual testing guide](https://textual.textualize.io/guide/testing/),
   [Pilot API](https://textual.textualize.io/api/pilot/), and
   [Worker API](https://textual.textualize.io/api/worker/).
   Use explicit `test_harness=True` only for pure pane composition/navigation;
   retain normal mode for lifecycle, cancellation, shutdown, stale callbacks,
   JSONL, MCP, and actual refresh assertions. `Pilot.pause()` is an explicit
   pending-message settlement tool, not a generic delay to delete.
3. **Evidence and mapping.** State baseline/current inventory counts: all eight
   named harness migrations (`TestTUIBasic.test_overview_is_default`,
   `test_overview_has_widgets`, `test_guards_screen_exists`,
   `test_logs_screen_exists`, `test_config_screen_exists`,
   `test_test_screen_exists`, and `TestTUINavigation.test_navigation_by_number_keys`
   and `test_all_five_views_accessible`); the unchanged non-mount fixture
   `TestTUIBasic.test_app_instantiates`; the
   normal-mode stale-callback RED/GREEN test, and retained lifecycle contract.
   Record Python/Textual/pytest/kernel facts, source base, exact command,
   warm-ups, A/B/B/A/A/B run order, timing medians/ranges, and the outcome:
   coverage safety was demonstrated; performance was inconclusive.
4. **Limitations and follow-up.** State that CPU/memory/load and raw historical
   duration logs were unavailable; no bulk migration, pause removal, xdist,
   retries, or timeout relaxation is admitted. New changes require fresh
   per-test mapping and controlled measurement.

## Verification

An independent reviewer verifies all numbers against Slice94/Slice95 status or
reproducible repository queries; links are direct official sources; arithmetic
is correct; no unsupported speed claim exists; and no sensitive environment
detail enters the note. Run a direct `dev/README.md` link check, the
documentation contract, strict MkDocs, diff-integrity check, and the focused
commands `uv run python -m pytest tests/test_tui.py -k stale_refresh_callback
-q` and `uv run python -m pytest tests/harness/test_phase4_tui.py -q`.
Rollback is documentation-only.
