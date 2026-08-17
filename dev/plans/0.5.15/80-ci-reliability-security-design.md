# Slice 80 — CI reliability, diagnostic signal, and least privilege

**Status:** proposed — pending independent design review. Created 2026-08-16
after the Slice 80 audit and after Slice 71's Docker-test contract was approved.

## Re-evaluation evidence

The existing CI has a 3.12 test job, but `lint`, `security`, and `docker` all
depend on it. A failed test therefore suppresses independent diagnostics. PR
#49 is stale at `8b75365`; its 3.12 test failed with a generic exit annotation
and expired logs, while docs/Gitleaks passed and the dependent jobs were
skipped. Current release work is ahead locally and requires an exact-head GitHub
run before closeout.

Slice 71 owns the Docker topology test and its Make/marker contract. Slice 80
will make CI invoke that target; it must not redefine the topology fixture.

## Scope and requirements

1. Pin every `uses:` reference in `ci.yml`, `docs.yml`, and `release.yml` to a
   reviewed full commit SHA with an adjacent human version comment. Retain the
   existing Gitleaks pin and its scanner policy unchanged.
2. Add workflow/ref concurrency and bounded per-job timeouts. CI uses
   `cancel-in-progress: true`; documentation deploys use the same
   workflow/ref-scoped grouping and may cancel stale runs because the newest
   successful deployment is authoritative; release uses the group with
   `cancel-in-progress: false` so no tag publication is interrupted.
   `lint`, `security`, and `docker` must run independently of a test failure;
   their checks do not weaken or replace the test gate.
3. Ordinary CI tests select `not live and not docker`; Docker CI provisions
   locked test dependencies and runs only Slice 71's `make test-docker` flow.
   It builds its disposable local image only; it does not push an image.
4. Preserve `contents: read` CI permissions. Make docs deployment permissions
   job-local (`pages: write`, `id-token: write` only on deploy); make release
   default `contents: read`, PyPI OIDC job-local, and GitHub-release
   `contents: write` job-local. This hardens future manual/tag release runs but
   does not dispatch or publish one in this slice.
5. On test failure only, upload a bounded JUnit artifact from the ordinary test
   job to make future
   failures diagnosable without retaining test stdout, environment, generated
   credentials, or topology material. Normal test output/coverage behavior
   remains intact.
6. Keep the 3.12 release gate. Record—not silently alter—the discrepancy with
   metadata still declaring Python >=3.10 and 3.10–3.12 classifiers. A later
   owner choice must add lower-version CI or change public support metadata.
7. After implementation, push current release commits to the existing PR
   branch and obtain exact-head green GitHub CI plus Gitleaks. Query that SHA's
   checks, record the returned exact context names and app IDs, apply precisely
   those contexts through the branch-protection API, then re-read protection to
   prove CODEOWNERS and no-admin-bypass protections remain enabled.

## Reviewed immutable actions

| Action | Pinned commit | Human version |
| --- | --- | --- |
| `actions/checkout` | `d23441a48e516b6c34aea4fa41551a30e30af803` | v6 |
| `actions/setup-python` | `ece7cb06caefa5fff74198d8649806c4678c61a1` | v6 |
| `astral-sh/setup-uv` | `cec208311dfd045dd5311c1add060b2062131d57` | v8.0.0 |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | v7 |
| `actions/configure-pages` | `983d7736d9b0ae728b81ab479565c72886d7745b` | v5 |
| `actions/upload-pages-artifact` | `56afc609e74202658d3ffba0e8f6dda462b719fa` | v3 |
| `actions/deploy-pages` | `d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e` | v4 |
| `actions/download-artifact` | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | v8 |
| `pypa/gh-action-pypi-publish` | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` | v1.14.2 (currently `release/v1`) |
| `softprops/action-gh-release` | `3d0d9888cb7fd7b750713d6e236d1fcb99157228` | v3.0.2 |

## Design and safety boundary

Workflow changes are test/release-control infrastructure, not product behavior.
No PR workflow receives a write token, OIDC, registry credential, or deployment
secret. The only Docker action is a locally built, labelled Slice 71 image with
generated material. No tag, release dispatch, GitHub release, PyPI publish, or
container registry push is authorized by this slice.

Use stable group names derived from workflow/ref. CI cancels superseded runs;
documentation does likewise because a later successful pages deployment is the
only desired state; releases never cancel an in-progress publication. Apply the
following explicit timeouts: CI docs 15m, test 35m, lint 15m, Docker 30m, and
security 20m; Pages build 20m/deploy 10m; release test 40m, check-version 10m,
build 15m, publish 15m, GitHub-release 15m.

The JUnit file is an ordinary-test report only: pytest writes
`test-results/junit.xml` with `junit_logging=no`; on failure a size-check marks
it uploadable only at <=5 MiB, then `upload-artifact` retains it for one day.
The Docker job produces no JUnit artifact, so generated TLS/JWT material is
never uploaded. Neither report captures stdout, stderr, nor environment data.

Add versioned `tests/test_ci_workflows.py` contract tests. They parse the three
workflows and assert SHA pins/version comments, job permissions, workflow/ref
concurrency policy, exact timeouts, independent CI jobs, test markers, bounded
failure-only JUnit behavior, Docker's `make test-docker` target, and the absence
of registry push/release dispatch in CI. `actionlint`, when available, is an
additional syntax check, not the only contract proof.

## TDD and acceptance criteria

1. **RED:** workflow-contract tests/static assertions fail for mutable action
   refs, broad permissions, dependent jobs, missing timeouts/concurrency,
   incorrect test marker selection, absent Docker target, and unsafe artifacts.
2. **GREEN:** all three workflows have the reviewed SHA pins/version comments,
   exact concurrency/timeouts, and job-level permissions; CI jobs are
   independent; ordinary and release tests select `not live and not docker`;
   Docker selection is exact; versioned workflow-contract tests and action
   syntax pass.
3. **Local verification:** affected pytest/Make targets, strict docs, Ruff,
   workflow static tests, and no-publish checks pass.
4. **GitHub verification:** push only the reviewed branch commits; record the
   exact SHA and a green run containing all five CI contexts plus Gitleaks;
   verify branch-protection required contexts through GitHub API/CLI. Do not
   create a tag, invoke `release.yml`, or publish a registry artifact.

## Blast radius and rollback

The blast radius is GitHub scheduling/cost, action compatibility, diagnostics,
and future deployment/release permission placement. Rollback is a reviewed
workflow-only revert. Never weaken required checks, protection, or Gitleaks to
work around a failure.
