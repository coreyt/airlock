# 0.5.15 Slice 10 — Gitleaks secret-scan status

**Status:** implemented and externally enforced — 2026-08-15. GitHub `main`
now requires `gitleaks / scan` and one CODEOWNERS approval. PR #49 remains
properly blocked pending that review and its ordinary CI completion.

## Ratified contract and changed controls

Slice 6 includes Slice 10. DFR-35/DAC-35 is now ratified in
`dev/requirements.md`: the control is a non-deploying Gitleaks `8.30.0`
pre-commit hook plus an isolated `gitleaks / scan` workflow. It has no Airlock
runtime, configuration, image, provider, or deployment-credential impact.

| File | Delivered control |
| --- | --- |
| `.pre-commit-config.yaml` | Staged Gitleaks hook from the official `v8.30.0` repository revision. It is a developer-local installation step and is not a Python project dependency. |
| `.gitleaksignore` | 38 individually reviewed exact fingerprints: local fixture findings plus commit-qualified history findings. No broad path/rule exception or inline allow comment is present. |
| `.github/workflows/gitleaks.yml` | Isolated `gitleaks / scan` workflow on PR/push to `main`, manual dispatch, and weekly history scan; full checkout; scanner `8.30.0`; read-only permissions; comments, artifacts, and summaries disabled. |
| `.github/CODEOWNERS` | `@coreyt` owns the baseline, hook configuration, and workflow paths. This becomes enforcement only after GitHub branch rules require code-owner review. |
| `dev/requirements.md` | Ratified DFR-35/DAC-35 contract and acceptance boundary. |

## Pin and scanner provenance

The workflow’s full-SHA pins were independently checked with read-only remote
tag lookups:

| Component | Required tag | Verified SHA |
| --- | --- | --- |
| `actions/checkout` | `v6` | `d23441a48e516b6c34aea4fa41551a30e30af803` |
| `gitleaks/gitleaks-action` | `v3` / `v3.0.0` | `e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e` |
| Local verifier | Gitleaks `v8.30.0` | `ghcr.io/gitleaks/gitleaks@sha256:691af3c7c5a48b16f187ce3446d5f194838f91238f27270ed36eef6359a574d9` |

The container digest was used read-only against the worktree and shared Git
metadata. It verifies the specified scanner version; the GitHub Action’s own
runtime-binary download remains the accepted no-secret-job residual risk from
the design review.

## RED/GREEN and local verification evidence

| Check | Result |
| --- | --- |
| `gitleaks dir . --redact` | Passed; 18.47 MB scanned, no leaks found. |
| `gitleaks git . --redact` | Passed; 686 commits / 10.43 MB scanned, no leaks found. The shared `.git` directory was mounted read-only because this is a linked worktree. |
| Controlled RED detector fixture | Passed: a temporary synthetic non-usable `sk-test-…` fixture produced one finding with `--redact`; it was deleted immediately and never added to the worktree. |
| Configured hook | Passed via ephemeral `pre-commit==4.1.0`: `pre-commit run gitleaks --all-files`. No project dependency, lockfile, or image change. |
| Workflow syntax | `actionlint .github/workflows/gitleaks.yml` passed. |
| Baseline integrity | 38 entries, no duplicate fingerprints. |
| Ordinary local readiness | `make ensure-spacy && make verify` passed after restoring `en_core_web_lg==3.8.0`; strict MkDocs build and documentation contract tests passed (8). |

The first container history scan could not see the linked worktree’s common Git
metadata and scanned zero commits. Re-running with that metadata mounted
read-only produced the 686-commit clean result above; this was a test-topology
correction, not a scanner suppression.

## External enforcement evidence

| Acceptance boundary | Evidence |
| --- | --- |
| Green PR scan | [PR #49](https://github.com/coreyt/airlock/pull/49) completed its `gitleaks / scan` job successfully: [run 31917799169](https://github.com/coreyt/airlock/actions/runs/31917799169). |
| Manual dispatch | The same workflow passed from `workflow_dispatch` on the feature branch: [run 31918015039](https://github.com/coreyt/airlock/actions/runs/31918015039). |
| Red CI proof | Disposable [PR #50](https://github.com/coreyt/airlock/pull/50) contained only the synthetic non-usable fixture. Its [scan job](https://github.com/coreyt/airlock/actions/runs/31917967497/job/95092956354) failed as expected, while GitHub reported `BLOCKED` and `REVIEW_REQUIRED`. The PR was closed and its remote/local branch and worktree were deleted immediately. |
| Required check | `main` branch protection now requires strict `gitleaks / scan`; the context is explicitly bound to the Gitleaks check. |
| Code-owner enforcement | `main` now requires one approving review and `require_code_owner_reviews=true`; the scanner files are owned by `@coreyt`. |
| Bypass/branch safety | Administrator enforcement is enabled; force pushes and deletion are disabled. The pre-existing disabled deletion/non-fast-forward ruleset was not modified. |

The initial inspection confirmed no active legacy branch-protection rule, so
this is a narrow new protection rather than a replacement of an existing active
policy.

## Remaining release evidence

- PR #49 still requires its designated code-owner approval and its ordinary
  Python 3.12 CI completion before merge. The Gitleaks check is already green.
- A protected `main` push will be evidenced after that reviewed merge. The
  workflow definition contains the required `push: main` trigger, but this
  slice did not bypass protection to manufacture a direct push.
- A separate public-fork PR was not created. The workflow uses `pull_request`,
  read-only job permissions, and no repository/deployment secrets; retain that
  as a deployment-policy review item if fork execution needs empirical proof.

## Residual risk and rollback

False positives require individual security review and an exact fingerprint;
they are not resolved by disabling a detector or excluding a directory. A
usable finding requires rotation/revocation before any baseline action. If the
workflow itself proves defective, revert only the named scanner-control change
or have an administrator temporarily remove the required check with a recorded
security decision—never bypass it by direct unprotected push.
