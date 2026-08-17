# Slice 71 — Slice 70 Docker topology verification status

**Status:** complete — verified 2026-08-16.

## Scope

Slice 71 verifies Slice 70's two-container, same-host read-only topology. It
adds no production Docker authority, manifests, routes, inventory fields, or
registry publication. Its only runtime resources are uniquely labelled local
test containers and generated temporary test material.

## Verification evidence

- Docker Engine `29.7.1`; `make test-docker` passed with local image
  `sha256:421b1017e786ca64294338011b86894da86d81e9c95226af63e25dd95327d8d6`,
  run label `1786935832-723127`, and source revision
  `d062f5e81a2e3aafb1abccb78df7f2813cfac2a5`. The test passed in 21.10s;
  its label-scoped cleanup left no Slice 71 containers.
- The live test proved two distinct loopback-only published ports, selected
  exact-scope read success, cross-target token rejection, CA rejection,
  mutation denial, and no connection to the unselected listener.
- Focused fleet/Admin/TUI regression, Ruff check/format, and `git diff --check`
  passed. `make sync && make verify`, strict MkDocs, and documentation-contract
  tests passed independently. The ordinary `make test` run passed: 3421 passed,
  112 deselected, 1 expected XPASS in 341.69s. Normal test selection excludes
  the Docker marker; CI's Docker job invokes only the opt-in target after
  locked test dependency provisioning.
- Exact-head GitHub CI independently ran that opt-in target successfully at
  `08d8737` ([Docker job](https://github.com/coreyt/airlock/actions/runs/31994296354/job/95283132492),
  1m10s). It used the disposable local-image contract only; no image was
  pushed or published.

## Boundary and follow-up

This test builds a local image only; it creates no registry artifact, tag, or
release. It creates no auxiliary worktree. Slice 80 owns the follow-on CI
hardening and exact-head GitHub verification.
