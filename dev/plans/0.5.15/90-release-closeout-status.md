# Slice 90 — 0.5.15 release closeout status

**Status:** implementation verified locally — awaiting the parent closeout's
exact-head GitHub CI and protection evidence; non-publishing throughout.

## Implemented scope

- Added a lock-aware version contract: `pyproject.toml`, `airlock.__version__`,
  tracing resource version, and the sole editable `airlock-llm` entry in
  `uv.lock` must agree, and an optional `v<version>` tag must agree with them.
- Applied the sole supported updater:
  `scripts/set-version.sh --set-version 0.5.15`. The lock was updated by `uv`;
  it was not hand edited.
- Added public Unreleased notes for the default-off Admin read surface, the
  CA-verified-TLS plus scoped-JWT fleet read-only TUI, startup provider-credential guidance, and
  CI/Docker verification. Virtual-key lifecycle remains explicitly deferred to
  the 0.6.0 identity/durability contract.
- Corrected the active DeepSeek configuration guide from 0.5.14 to 0.5.15;
  historical release references were left intact.
- The tag-triggered release gate delegates to the same lock-aware canonical
  version checker. This changes no trigger and this slice creates no tag.

## TDD evidence

- **RED:** the new `--tag v0.5.15` contract failed while canonical fields were
  still 0.5.14.
- **GREEN:** after the supported updater, the canonical/tag checker passed and
  the focused version/startup/workflow suite passed: **20 tests**.

## Local verification

- `uv run python scripts/check-version-consistency.py --tag v0.5.15` — pass.
- `uv lock --check`, focused Ruff/check-format, and `git diff --check` — pass.
- `uv run mkdocs build --strict` — pass.
- Working-tree `gitleaks detect --source . --no-git --redact --exit-code 1` —
  pass, with no leaks.
- Merge-base-to-head Gitleaks scan — pass after the two exact historic test
  fingerprints; it retains all new-finding detection.
- `make sync && make verify`, strict MkDocs, documentation/workflow/startup/
  version contracts (91 passed, 1 expected XPASS), `make test` (3427 passed,
  112 deselected, 1 expected XPASS), and `make test-docker` (1 passed) — pass.
- Changed-file Ruff/check-format and actionlint — pass. A deliberately broad
  Ruff run also reports four pre-existing style errors in the unrelated
  `scripts/benchmark_fathomdb.py`; no Slice 90 file is implicated.
- The independent code review required one FIX-1: change the future
  tag-triggered release gate from a pyproject-only comparison to the canonical
  checker. FIX-1 was approved; the tag trigger and all publication paths are
  unchanged.

## Gitleaks history note

The explicit commit-range scan from the merge base through the current
uncommitted head reports two historic generic-key test literals in the earlier
Slice 30 commit. The approved working-tree fixture correction removes those
literals. Because PR scanning retains history, `.gitleaksignore` adds only the
two exact reviewed commit/path/rule/line fingerprints; it adds no path, rule,
or broad baseline suppression. The parent closeout must record the exact-head
GitHub Gitleaks result after the reviewed changes are committed.

## Pending closeout evidence

This record will name final local-gate results, the exact GitHub head/check
contexts/app IDs, and branch-protection readback. It will not claim a tag,
merge, or package/image publication.
