# Slice 90 — 0.5.15 release closeout (non-publishing)

**Status:** admitted for closeout planning and implementation on 2026-08-16;
release publication remains explicitly out of scope.

## Re-evaluation

Slices 10, 20, 30, 40, 50, 70, 71, and 80 are implemented on this branch.
Slice 60 is deferred to 0.6.0. Slice 71 subsequently supplied the combined
ordinary-suite evidence that earlier Slice 20/40/50 records could not capture:
`make test` passed 3421 tests, with 112 deselected and one expected XPASS.
Those historical records remain unchanged.

Exact-head PR CI initially exposed two generic-api-key detections in Slice 30's
non-secret test literals. The approved narrow correction derives the matching
name from the authoritative finite registry; it does not alter the Gitleaks
policy or baseline.

## Scope and requirements

1. Prepare the branch as version `0.5.15` using the sole updater
   `scripts/set-version.sh --set-version 0.5.15`; it owns the canonical
   package/runtime/tracing fields and the editable `airlock-llm` entry in
   `uv.lock`. Do not hand-edit the lock. Extend the version checker and its
   focused regression coverage so package/runtime/tracing/**lock** fields and
   `v0.5.15` agree.
2. Keep `CHANGELOG.md` under **Unreleased** until a separately authorized tag
   and publication. Add concise public-facing release notes covering the
   default-off Admin API read surface, secure CA-verified-TLS plus scoped-JWT
   remote/fleet read-only TUI,
   unused configured-provider warnings, and CI/Docker verification. Document
   the deferred virtual-key control plane separately.
3. Replace the one public stale `0.5.14` configuration wording with `0.5.15`;
   retain historical release references and never rewrite prior changelog
   entries or prior plan/status records.
4. Commit and push only reviewed branch changes. Obtain green exact-head CI
   (`CI` docs/test/lint/Docker/security and Gitleaks), then discover exact
   check-run context names/app IDs and require them through GitHub branch
   protection. Re-read protection and retain strict checks, required
   CODEOWNERS review, and no admin bypass.
5. Do not create a tag, dispatch `release.yml`, invoke PyPI/GitHub-release
   actions, merge PR #49, publish any package/image, weaken scanning/protection,
   or claim lower-Python support from the 3.12-only CI gate.

## Design and verification

The release workflow is tag-triggered and publication-capable, so it is never
invoked during this slice. `scripts/check-version-consistency.py --tag v0.5.15`
is a local string-consistency check; `scripts/verify-release-gates.py` runs
only after exact-head CI is green and receives an explicit non-created tag
argument. Neither creates a tag or registry artifact.

TDD:

1. **RED:** add/adjust a canonical-version contract and fixture that fail while
   the editable lock entry drifts from the other three fields; prove
   `--tag v0.5.15` fails before alignment.
2. **GREEN:** align all four fields, make the version/tag contract pass, and
   ensure public documentation has no stale active configuration claim.
3. Run `make sync && make verify`, `make test`, `make test-docker`, `uv lock
   --check`, strict MkDocs, documentation contracts, workflow contracts and
   actionlint, targeted startup-warning tests, and Gitleaks working-tree and
   commit-range scans.
4. On GitHub, record the exact green head SHA, run URLs, check-run names/app
   IDs, and protection readback. PR approval/merge is outside this slice.

## External authority

Changing GitHub branch protection is an owner/admin-only mutation. Do it only
after the exact head is green and its check-run names/app IDs are recorded;
apply only those contexts and preserve `strict=true`, the existing Gitleaks
requirement, CODEOWNERS review, and admin enforcement. The PR author/agent
does not self-approve or merge PR #49.

## Blast radius and rollback

The product/runtime blast radius is version strings and public release notes;
CI/protection changes affect future gating only. Rollback is a normal reviewed
branch revert. Never roll back by weakening branch protection or suppressing
new secrets. The Python `>=3.10` metadata versus 3.12-only CI discrepancy is
an explicitly deferred support-policy decision, not a release claim.
