# 0.5.15 Slice 10 — Gitleaks secret-scan implementation plan

**Status:** repository-controlled implementation complete locally — updated
2026-08-15. External GitHub merge-blocking enforcement remains pending; see
`10-gitleaks-ci-secret-scan-status.md` for the exact evidence and limitation.

## Admission check

| Gate | Current evidence | Required before implementation starts |
| --- | --- | --- |
| Previous slice closure | Slices 0–5 and Slice 6 are complete; Slice 6 includes Slice 10 with external enforcement conditional. | **Passed.** Recheck the decision and all changes since this plan before beginning the slice. |
| Draft contract | Slice 3 proposes DFR-35/DAC-35, and Slice 4 confirms isolated architecture alignment. | Ratify the draft as the Slice 10 contract at slice start. |
| Security design review | `70-gitleaks-ci-secret-scan-design.md` and review `71` conditionally pass. | Security reviewer confirms the current pins, 38-entry baseline, and external-enforcement posture still match the accepted design. |
| Repository authority | Wake records approval of the least-privilege control direction and scoped scanner ownership. | GitHub administrator confirms authority for a disposable PR, required check, branch protection, and code-owner enforcement. |

Until all four gates pass, do not commit, push, enable the workflow, configure
GitHub settings, add dependencies, or claim that merge-blocking enforcement is
active.

## Changes since the original design

| Original assumption | Current reconciled state | Plan update |
| --- | --- | --- |
| Slice 0 found an incomplete Python 3.11 environment. | The worktree was rebuilt on Python 3.12.3 with `make sync && make verify`; the ordinary non-live CI baseline, docs, lint, mypy, Docker build, and audit passed. | Treat this as a healthy pre-feature baseline only. Slice 10 has no Python/runtime dependency and must not add one. |
| Local and historical baselines needed reconciliation. | `.gitleaksignore` now has 38 exact entries: the original local findings plus the 16 reviewed commit-qualified history findings described by design review 71. | Re-review the entries as exact fingerprints; do not broaden paths, disable rules, or add wildcard suppression. Prove both directory and history scans before commit. |
| Action SHAs were to be resolved. | The untracked workflow pins checkout v6 to `d23441a48e516b6c34aea4fa41551a30e30af803` and Gitleaks Action v3 to `e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e`; scanner remains `8.30.0`. | Independently reconfirm release provenance at slice start. Do not substitute mutable tags or combine this work with Actions-major PR #44. |
| Gitleaks and pre-commit tooling were absent from `PATH`. | They remain absent; only `actionlint` is presently available. | Use a reviewed, ephemeral pinned local-tool invocation or a checksum-verified Gitleaks 8.30.0 binary without adding it to `pyproject.toml`, `uv.lock`, Docker, or Airlock runtime. Record the exact invocation and version in the slice status. |
| Controls were proposed repository changes. | `.gitleaksignore`, `.pre-commit-config.yaml`, `.github/CODEOWNERS`, and `.github/workflows/gitleaks.yml` are still untracked worktree baseline. | Preserve them exactly until review; stage only these named files and the Slice 10 status/documentation files—never broad staging. |

## Scope and non-goals

**In scope:** an isolated Gitleaks GitHub workflow; a Gitleaks 8.30.0
pre-commit hook; a narrow exact-fingerprint baseline; scanner-file code
ownership; minimal developer instructions; local, disposable-PR, and external
GitHub-enforcement evidence.

**Out of scope:** Airlock runtime code, `config.yaml`, Docker image, provider
credentials, deployment/release workflows, Dependabot PRs, Action major
upgrades, GitHub Advanced Security/code scanning uploads, broad scanner
exceptions, and any secret-management redesign.

## Implementation sequence

1. **Ratify and snapshot scope.** Record the Slice 6 include decision, DFR-35/
   DAC-35 acceptance criteria, designated security owner, GitHub administrator,
   and the reviewed file list. Recheck that the branch/worktree still contains
   only the known untracked baseline; preserve unrelated user files.
2. **Re-verify supply chain and baseline.** Independently verify both full
   Action SHAs against the stated official releases. Obtain the exact Gitleaks
   `8.30.0` scanner by a reviewed ephemeral/checksum-verified path. Run
   redacted `gitleaks dir .` and `gitleaks git .`; review any result
   individually. A clean result is required before the controls are committed.
3. **Record RED evidence.** In a disposable branch/PR, add a synthetic,
   non-usable detector fixture that is designed to be redacted. Demonstrate
   local-hook and CI failure without copying the value to logs, plans, or PR
   comments; remove the fixture immediately after evidence capture.
4. **Review and commit the repository controls.** Review only
   `.pre-commit-config.yaml`, `.gitleaksignore`, `.github/CODEOWNERS`, and
   `.github/workflows/gitleaks.yml`. Confirm stable `gitleaks / scan` naming,
   full checkout, PR/push/manual/weekly triggers, read-only permissions, no
   `pull_request_target`, no OIDC/write scope, and comments/artifact/summary
   disabled. Add a short developer instruction only if review identifies an
   existing authoritative location; do not create duplicate authority.
5. **Record GREEN evidence.** Re-run local directory/history scans, the staged
   pre-commit hook, and `actionlint`. Open a disposable PR, prove a redacted CI
   failure then a green run after fixture removal; validate manual dispatch and
   a normal `main`-equivalent push only through authorized repository workflow.
6. **Apply external enforcement.** The GitHub administrator configures `main`
   to require `gitleaks / scan` and code-owner review for the three scanner
   files. Prove a failing scan and an unapproved scanner/baseline modification
   cannot merge. Do not report this as active before this external evidence.
7. **Close the slice.** Write `10-gitleaks-ci-secret-scan-status.md` with
   ratified contract, exact pins/version, baseline review count, commands and
   redacted results, CI/PR URLs, GitHub-rule evidence, residual runtime-binary
   provenance risk, and rollback disposition.

## Required verification and acceptance evidence

| Boundary | Evidence |
| --- | --- |
| Local detection parity | Gitleaks `8.30.0` directory and reachable-history scans both produce zero reviewed findings; the hook invokes the same scanner version. |
| False-positive discipline | Baseline entries are exact fingerprints, individually reviewed, and line/path or commit changes create a new finding. No rule, directory, or generic test/docs exemption is introduced. |
| Workflow isolation | `actionlint` passes; workflow has only `contents: read` and `pull-requests: read`, full checkout, no repository/deployment secrets, no artifact/SARIF/comment/summary publication, and no runtime invocation. |
| Detection regression | Disposable synthetic fixture fails locally and in CI with redaction, then removal restores green. The fixture is not retained in mainline or copied into durable evidence. |
| Merge enforcement | GitHub administrator supplies branch-protection and code-owner proof that the stable check and designated review cannot be bypassed on `main`. |
| Existing CI health | Re-run the affected workflow validation plus the repository’s ordinary CI evidence proportionate to touched files; no Python dependency or product-test expansion is expected. |

## Failure handling and rollback

- A new finding is treated as potentially real until security review proves it
  is a non-usable fixture. Rotate/revoke any usable credential before changing
  a baseline.
- A false positive is resolved only by a reviewed exact fingerprint after
  preserving detector coverage. Never suppress the rule or an entire path to
  unblock a merge.
- If the workflow itself is defective, revert the named scanner-control commit
  or have the GitHub administrator temporarily remove the required check with a
  recorded security decision. Do not use an unprotected direct push as a
  workaround.
- If the official Action’s runtime-binary provenance becomes unacceptable,
  pause enforcement and design a separately reviewed digest-pinned alternative;
  do not add a second divergent scanner as an emergency replacement.

## Completion criteria

Slice 10 closes only when DFR-35/DAC-35 is ratified, repository controls are
committed and reviewable, all local/CI redacted evidence passes, the controlled
failure proof is complete, the GitHub administrator has demonstrated required
check and code-owner enforcement, and the status record names all residual
risks. If external GitHub authority is unavailable, close only the
repository-controlled portion as conditional and explicitly state that
merge-blocking enforcement is not active.
