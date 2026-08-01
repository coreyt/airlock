# Pre-0.5.7 dependency sweep — 2026-07-31

## Scope and authority

`pyproject.toml` is the only dependency manifest and `uv.lock` is the resolved
environment record. The Docker-only `requirements.txt` duplicate was removed:
the image installs the editable project and runs
`scripts/check_docker_dependencies.py`, which verifies its installed LiteLLM
version against the project requirement.

This is a compatibility maintenance sweep. It does not change an Airlock client
API or configuration contract.

## Direct inventory selected from the refreshed lock

| Group | Packages selected |
| --- | --- |
| Core proxy | LiteLLM 1.94.1; Presidio Analyzer/Anonymizer 2.2.364; python-dotenv 1.2.2; PyYAML 6.0.3; Textual 6.2.1 |
| Core/test support | FastAPI 0.141.1; SQLAlchemy 2.0.51; Prometheus client 0.26.0; pytest 9.1.1; pytest-asyncio 1.3.0; pytest-cov 7.0.0 |
| Provider/integration extras | FathomDB 0.3.1; boto3 1.43.29; Google Auth 2.54.0; Google GenAI 2.8.0; Tavily 0.7.22; NewsCatcher 1.0.0; Mistral 1.12.4; OpenTelemetry API/SDK 1.39.1 |
| Docs | MkDocs 1.6.1; MkDocs Material 9.7.6 |

The transitive refresh also selects patched aiohttp 3.14.3, idna 3.18,
pyasn1 0.6.4, pydantic-settings 2.14.2, pytest 9.1.1, python-dotenv 1.2.2,
setuptools 83.0.0, and urllib3 2.7.0. LiteLLM remains deliberately at the
validated 1.94.1 baseline rather than floating to a later release during this
sweep.

## Intentional migration boundaries

| Deferred migration | Current boundary | Follow-up required |
| --- | --- | --- |
| FathomDB 0.8 | `fathomdb<0.4` | Storage API design/migration and FathomDB integration tests. |
| Mistral 2.x | `mistralai<2` | Update the batch adapter import/client API and Mistral batch tests. |
| NewsCatcher 3.x | `newscatcher-catchall-sdk<2` | Update search client API and integration tests. |
| Textual 7/8 | `textual<7` | Dedicated TUI rendering/event-loop migration and TUI regression suite. |

These are intentionally separate API-level changes, not lockfile updates.

## Automation policy

`.github/dependabot.yml` opens weekly grouped updates for the root `uv` manifest
and lock (`core-proxy`, `optional-integrations`, and `developer-tooling`) plus a
weekly `github-actions` group. It does not configure auto-merge: every Dependabot
PR, including security and patch updates, requires human review and the relevant
CI group. Inline CI pins for ruff, mypy, and pip-audit remain manually managed.

## Validation

- `uv lock` resolves the manifest and records LiteLLM 1.94.1.
- `uv sync --all-extras` completes from the final 187-package lock.
- `python scripts/check_docker_dependencies.py` passes with LiteLLM 1.94.1;
  `tests/test_docker_dependencies.py` passes (2 tests). The Docker image builds
  and its in-container smoke check reports the same result.
- A wheel built with `uv build --wheel` installs cleanly into an isolated virtual
  environment and reports `airlock-llm 0.5.6`.
- Ruff and the configured fast-subsystem mypy check pass.
- The non-live suite was invoked with all extras (2,703 selected tests; live
  tests excluded). Focused final regressions pass: adapter (31), header (34),
  models/batch/routing (36), guardian (38), request-event projection (20),
  proxy bootstrap (1), FathomDB/S3/SQL (41), and
  metrics/tracing/AI Studio/Mistral (44 passed, 1 skipped).
- The initial `pip-audit` found 24 advisories. Compatible transitive patches,
  including aiohttp 3.14.3 and Click 8.4.2, reduced the final audit to zero
  known vulnerabilities.
