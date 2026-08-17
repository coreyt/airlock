# Slice 80 — CI reliability, diagnostic signal, and least privilege: status

**Status:** local implementation, code review, and independent verification
complete; awaiting release-branch push, exact-head GitHub verification, and
branch-protection reconciliation.

## Delivered

- All `uses:` actions in CI, documentation, and release workflows are pinned
  to reviewed immutable commits with adjacent human-readable version comments.
- CI workflows now use workflow/ref-scoped supersession, bounded job timeouts,
  and independent lint, security, and Docker diagnostics.
- The ordinary test job excludes `live` and `docker`; the Docker job delegates
  only to Slice 71's `make test-docker` contract, which builds a disposable
  local image and does not push it.
- Ordinary-test JUnit output is retained only after failure, only if it is at
  most 5 MiB, without pytest logging, and for one day. Docker topology
  material is never uploaded.
- Documentation and release permissions are least-privilege and job-local.
  Release is intentionally not dispatched, tagged, or published by this work.
- Versioned workflow-contract tests guard every action reference (full SHA plus
  adjacent version comment), those properties, and the explicit release
  selector `not live and not docker`.

## Deliberately deferred support decision

The published package metadata still declares Python `>=3.10` and classifiers
for 3.10 through 3.12, while this release’s CI gate runs 3.12 only. Slice 80
records the discrepancy but does not silently change either support metadata or
the tested-version matrix. A future owner decision must either restore lower
version CI coverage or revise the public support declaration.

## Local verification

- RED: `uv run pytest tests/test_ci_workflows.py -q` failed four contracts
  before workflow changes (mutable action refs, missing concurrency, and broad
  docs/release permissions).
- FIX-1 RED: the universal immutable-action guard was absent; the strengthened
  test suite failed two tests with `NameError` before that guard was added.
- FIX-1 GREEN: the guard rejects a synthetic mutable `actions/checkout@v6`
  reference and structurally confines JUnit generation/upload to the ordinary
  test job; `uv run pytest tests/test_ci_workflows.py -q` — 5 passed.
- FIX-2 RED: the guard skipped direct `- uses:` action steps, so a valid direct
  SHA/comment fixture was incorrectly reported as containing no action.
- FIX-2 GREEN: the guard inspects both nested `uses:` and direct `- uses:`
  forms; the test accepts a direct immutable fixture and rejects a direct
  mutable `@v6` sibling. The workflow-contract suite remains 5 passed.
- `uv run ruff check tests/test_ci_workflows.py` — passed.
- `uv run ruff format --check tests/test_ci_workflows.py` — passed.
- `actionlint .github/workflows/ci.yml .github/workflows/docs.yml
  .github/workflows/release.yml` — passed.
- `git diff --check` — passed.

## Remaining closeout verification

1. Push only the reviewed release branch; do not tag, dispatch `release.yml`,
   or publish a package/image.
2. Confirm exact-head green CI and Gitleaks, record returned check-context
   names/app IDs, then apply and re-read branch protection without weakening
   CODEOWNERS or admin-bypass policy.
