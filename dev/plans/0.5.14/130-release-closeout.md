# Slice 130 — 0.5.14 release closeout

**Status:** ready to tag locally.

## Purpose

Convert the completed, independently reviewed 0.5.14 delivery slices into a
reproducible local release candidate. This is release engineering, not new
feature scope.

## Inputs

- Approved and complete delivery slices 10–90, 110, and 120.
- Slice 100 remains deferred to 0.5.15.
- Funded, isolated-loopback smokes for embeddings, OpenRouter, and DeepSeek
  passed; their safe evidence is retained in the relevant slice records.
- PII egress remains observe-only under its existing owner decision.

## Closeout design

1. Bump the package and runtime version to `0.5.14` and write a changelog
   section based only on completed scope.
2. Run the local equivalents of every GitHub CI/release gate with the locked
   dependency graph: strict documentation/contracts, non-live tests with
   coverage, lint/format/type checks, Docker build, `pip-audit`, lock check,
   and package build.
3. Record exact commands and terminal results here. Any failure is a release
   blocker: repair it, rerun the affected gate, then rerun the final matrix as
   warranted.
4. Once all local gates pass, create an annotated **local** `v0.5.14` tag on
   the exact release commit and verify its version/artifacts.
5. Do not push `main` or the tag, trigger GitHub Actions, create a GitHub
   release, or publish to PyPI without a further explicit operator approval.
   A tag push invokes the release workflow and trusted publishing.

## Local acceptance criteria

- `pyproject.toml` and `airlock.__version__` both equal `0.5.14`.
- `CHANGELOG.md` accurately represents delivered 0.5.14 work and names no
  deferred item as shipped.
- `uv lock --check` passes.
- Every command listed in `.github/workflows/ci.yml` and the local-safe checks
  from `.github/workflows/release.yml` has a terminal success result, or an
  explicitly justified environment limitation is elevated to HITL.
- `uv build` produces an sdist and wheel whose metadata reports `0.5.14`.
- The final release commit and local annotated tag are clean and reproducible.

## Evidence log

| Gate | Command | Result |
| --- | --- | --- |
| Version/changelog | `tests/test_config_consistency.py::TestVersionConsistency::test_versions_agree`, `tests/test_tracing.py`, `scripts/check-version-consistency.py --tag v0.5.14` | Passed: 5 tests; package, runtime, tracing, and tag values agree on `0.5.14`. |
| Locked environment | `UV_CACHE_DIR=/tmp/airlock-uv-cache make sync`; `uv lock --check` | Passed. |
| Documentation | strict MkDocs build; `uv run pytest tests/test_documentation_contract.py -q` | Passed: strict build completed and 8 contract tests passed. |
| Test | `uv run pytest -q -m "not live" --cov=airlock --cov-report=xml --cov-report=term-missing` | The full run completed except for one static tracing-version literal mismatch. The one-line literal repair received the focused version/tracing regression above; owner approved proportional revalidation rather than a second full run. |
| Quality | `uv run ruff check airlock/ tests/`; `uv run ruff format --check airlock/ tests/`; `uv run mypy airlock/fast/ --ignore-missing-imports` | Passed: Ruff check, 326-file format check, and mypy (16 source files). |
| Container | `docker build -t airlock:0.5.14-local .` | Passed. Local image ID: `sha256:2eba403f8f58da836523772404dcbfacde64395b183001c5306c38eec559965d`. |
| Security | `uv run pip-audit --ignore-vuln PYSEC-2026-3552 --ignore-vuln PYSEC-2026-3553 --ignore-vuln PYSEC-2026-3554` | Passed: no known vulnerabilities; the three documented suppressions remain. Local/project-only `airlock-llm` and `en-core-web-lg` are not on PyPI and were reported as unauditable. |
| Artifact | `uv build`; wheel/sdist `METADATA` / `PKG-INFO` checks | Passed: both artifacts report `Version: 0.5.14`. SHA-256: sdist `f83953f3bf6c069f132d782818258182d96a6d750a1aee6b0d1f409a38255086`; wheel `9445945fc3439e7914b00cd498dfbb38d7b58a44be2893eb7289a137819d1af3`. |
| Release commit/tag | local commit and annotated `v0.5.14` tag | Pending: create on the exact checked release revision. |
| Remote publication | push / GitHub Actions / PyPI | intentionally pending explicit approval |
