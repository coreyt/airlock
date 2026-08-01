# Pre-0.5.7 dependency sweep — 2026-07-31

## Scope and authority

`pyproject.toml` is the only dependency manifest and `uv.lock` is the resolved
environment record. The Docker-only `requirements.txt` duplicate was removed.
Docker, CI, and source setup install through the committed lock; the image then
runs `scripts/check_docker_dependencies.py`, which verifies every direct runtime
dependency (including LiteLLM) against the project requirement. The separately
distributed Presidio model is installed from its exact `en_core_web_lg` 3.8.0
wheel URL, shared by `scripts/tool-versions.sh`; keeping that 382 MB model out of
the package metadata avoids making it a mandatory download for every library user.

This is a compatibility maintenance sweep. It does not change an Airlock client
API or configuration contract.

## Direct inventory selected from the refreshed lock

| Group | Packages selected |
| --- | --- |
| Core proxy | LiteLLM 1.94.1; Presidio Analyzer/Anonymizer 2.2.364; python-dotenv 1.2.2; PyYAML 6.0.3; Textual 6.2.1 |
| Core/test support | FastAPI 0.141.1; SQLAlchemy 2.0.51; Prometheus client 0.26.0; pytest 9.1.1; pytest-asyncio 1.4.0; pytest-cov 7.1.0 |
| Provider/integration extras | FathomDB 0.3.1; boto3 1.43.62; Google Auth 2.56.2; Google GenAI 2.16.0; Tavily 0.7.27; NewsCatcher 1.5.1; Mistral 2.8.0; OpenTelemetry API/SDK 1.39.1 |
| Docs | MkDocs 1.6.1; MkDocs Material 9.7.7 |

The transitive refresh also selects patched aiohttp 3.14.3, idna 3.18,
pyasn1 0.6.4, pydantic-settings 2.14.2, pytest 9.1.1, python-dotenv 1.2.2,
setuptools 83.0.0, and urllib3 2.7.0. LiteLLM remains deliberately at the
validated 1.94.1 baseline rather than floating to a later release during this
sweep.

## Intentional migration boundaries

| Deferred migration | Current boundary | Follow-up required |
| --- | --- | --- |
| FathomDB 0.8 | `fathomdb<0.4` | Storage API design/migration and FathomDB integration tests. |
| Mistral 3.x | `mistralai>=2,<3` | Reassess the batch client API and focused Mistral batch tests. |
| NewsCatcher 3.x | `newscatcher-catchall-sdk<2` | Update search client API and integration tests. |
| Textual 6.3+ / 7/8 | `textual<6.3` | Wait for a LiteLLM proxy release compatible with Rich 14, then run a dedicated TUI rendering/event-loop migration and regression suite. |

These are intentionally separate API-level changes, not lockfile updates.

### Follow-up sequencing — revisited 2026-08-01

Mistral 2.x was completed on 2026-08-01: the lazy import moved to
`mistralai.client.Mistral`, while the files and `batch.jobs` surface remains
compatible; a v2 contract test guards that fact.
Keep the remaining compatibility caps until each item has its own design,
implementation, and focused CI group. The recommended order is:

1. **Textual 6.3+ / 7/8** — wait for a LiteLLM proxy release that supports Rich 14,
   then migrate the active TUI rendering and event-loop surface with its regression
   suite. The resolver incompatibility is recorded separately.
2. **NewsCatcher 3.x** — update the optional MCP search client and its polling/error
   handling, with MCP server tests. Its isolation makes it lower risk than the TUI
   migration.
3. **FathomDB 0.8** — first settle the storage API/schema direction against the
   existing Fathom design notes, then perform a storage migration and integration
   suite. Do not treat the version cap as a mechanical package update.

Tavily accounting remains intentionally set aside and is not folded into any of these
dependency migrations.

### 2026-08-01 completion addendum

The Mistral migration updates the optional extra to `mistralai>=2.0.0,<3` and
changes the lazy SDK import to `mistralai.client.Mistral`. Its v2 `files` and
`batch.jobs` operations retain the keyword interface used by Airlock; a focused
contract test checks the import, version, operation presence, and batch-create
parameters without a provider request. The credential-gated Mistral live test
remains opt-in and was not run.

The final all-extras resolution has 182 packages. Mistral 2.8.0 requires
`opentelemetry-semantic-conventions>=0.60b1,<0.61`, so the shared optional
tracing selection resolves OpenTelemetry API/SDK 1.39.1 rather than 1.44.0;
both remain within Airlock's declared `>=1.20.0` range and the tracing suite
passes. This resolver consequence is explicit rather than an unnoticed
transitive downgrade.

The Rich/LiteLLM investigation established a narrower Textual ceiling:
`textual>=6.2.1,<6.3`. Textual 6.3 and later require Rich 14 while LiteLLM
1.94.1's proxy extra caps Rich below 14. The manifest, optional TUI extra, and
Dependabot ignore rule now express that bound; the complete Textual TUI suite
passes. See `dev/notes/rich-litellm-textual-compat-2026-08-01.md` for the
package-metadata evidence and the eventual migration paths.

## Automation policy

`.github/dependabot.yml` opens weekly grouped updates for the root `uv` manifest
and lock (`core-proxy`, `optional-integrations`, and `developer-tooling`) plus a
weekly `github-actions` group. It does not configure auto-merge: every Dependabot
PR, including security and patch updates, requires human review and the relevant
CI group. Inline CI pins for ruff, mypy, pip-audit, and yamllint remain manually
managed in `scripts/tool-versions.sh`, rather than being added to runtime or
optional-extra metadata only to enable automation.

The uv updater uses `increase-if-necessary` plus a no-major policy. Explicit
ignores retain the remaining deferred migration boundaries even where zero-major
versioning would otherwise make Dependabot treat a breaking change as a minor
update. Textual 6.3+ is also deferred: it requires Rich 14 while the validated
LiteLLM 1.94.1 baseline requires Rich below 14. Compatible releases continue to
refresh `uv.lock` in the review groups.

## Validation

- `uv lock --check` verifies the manifest and records LiteLLM 1.94.1.
- A final compatible transitive refresh resolves cleanly, and `uv sync
  --all-extras` completes from the final 187-package lock. It retains all four
  explicit migration caps and the LiteLLM 1.94.1 baseline.
- `make sync` completes from the final 187-package lock with all extras, then
  restores the exact spaCy model wheel. `tests/test_dependency_contract.py` and
  `tests/test_docker_dependencies.py` guard the shared local/CI/Docker contract.
  The Docker image builds and its in-container smoke check imports Airlock and
  `en_core_web_lg` 3.8.0; it reports LiteLLM 1.94.1 and all six direct runtime
  dependencies satisfying `pyproject.toml`.
- A wheel built with `uv build --wheel` installs cleanly into an isolated virtual
  environment and reports `airlock-llm 0.5.6`.
- Ruff and formatting pass for Airlock and its tests. The repository-wide Ruff
  run has four pre-existing violations in `scripts/benchmark_fathomdb.py`; the
  current mypy configuration reports pre-existing missing-stub and source typing
  errors, so neither is attributed to this lock-only update.
- Post-refresh focused non-live regressions pass: core proxy and Docker checks
  (110 passed), plus FathomDB/S3/SQL/metrics/tracing/Mistral/MCP/config/CLI
  integration coverage (265 passed, 2 skipped, 1 xpassed). Live provider tests
  remain opt-in.
- The complete non-live test suite, Ruff, formatting, fast-subsystem mypy, and
  `pip-audit` pass. The audit reports no known vulnerabilities; it explicitly
  skips the pinned GitHub spaCy wheel because it is not published on PyPI.
- The initial `pip-audit` found 24 advisories. Compatible transitive patches,
  including aiohttp 3.14.3 and Click 8.4.2, reduced the final audit to zero
  known vulnerabilities.
