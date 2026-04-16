# Changelog

All notable changes to Airlock are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-04-15

### Added

- **Startup control flags** for low-noise local and production boot:
  `AIRLOCK_STARTUP_MODEL_DISCOVERY`, `AIRLOCK_MCP_STARTUP_MODE`,
  `AIRLOCK_ENABLE_FATHOMDB`, and `AIRLOCK_ENABLE_FATHOM_LOGGER`.
- **Enhanced Gemini alias execution path** via
  `airlock.providers.enhanced_passthrough`, allowing logical aliases
  such as `gemini-coding` to inject prompt and parameter defaults while
  forwarding to a physical Gemini deployment.

### Changed

- **Proxy runtime config rewriting** now strips
  `general_settings.master_key` when `AIRLOCK_MASTER_KEY` is unset or
  blank, so local and development runs do not accidentally trigger
  LiteLLM's database-backed virtual-key auth flow.
- **Liveness guidance** now consistently favors `GET /health/liveliness`
  for frequent probes, while leaving `GET /health` for slower,
  provider-touching readiness checks.

### Fixed

- **MCP startup behavior** now supports three explicit modes:
  `off` removes `mcp_servers` from runtime config, `lazy` preserves MCP
  configuration while suppressing LiteLLM's eager startup `list_tools()`
  sweep, and `eager` preserves LiteLLM's default probing behavior.
- **FathomDB bootstrap on fresh state dirs** now pre-creates the missing
  `vec_nodes_active` table stub used by current Fathom write paths,
  preventing fresh-database write noise.
- **FathomDB request logging** now:
  - self-registers on LiteLLM's async callback path for proxy traffic,
  - deduplicates repeated success/failure callback attempts by
    `litellm_call_id`,
  - skips inner forwarded enhanced-provider calls, and
  - uses a PID-bound, thread-safe lazy engine singleton to avoid
    same-process `Engine.open()` races under concurrent writes.
- **Enhanced Gemini alias forwarding** now preserves provider auth and
  transport context (`api_key`, `api_base`, `headers`, `client`) when
  delegating to the physical model, fixing `gemini-coding` so it reaches
  the same successful upstream path as `gemini-3.1-pro-tools`.

### Documentation

- Revised README, getting-started, operations, and developer design-note
  docs to describe low-noise startup defaults, optional FathomDB
  logging, Fathom's single-owner process model, and the implemented
  enhanced Gemini alias behavior.

## [0.2.0] — 2026-04-10

### Added

- **Admin Advisor** — LLM-powered operational assistant for diagnosing
  issues and recommending config changes. Includes:
  - `airlock advise` CLI command (one-shot, interactive, `--local-only`)
  - TUI Screen 6 ("Advisor") with model selector and chat interface
  - 9 data-gathering tools (state snapshot, errors, analysis, circuits,
    config, guard signals, client/model profiles, knobs)
  - Config proposal system with diff preview, risk classification
    (low/medium/high), `.bak` backup, and YAML validation
  - Local-first model selection to avoid sending operational data to
    remote providers
  - JSONL audit trail at `logs/advisor-audit.jsonl`
- **mkdocs documentation site** — organized user-facing docs with
  Getting Started, User Guide, Operations, and Architecture sections.
  Build with `uv run mkdocs serve`.
- **Release workflow** (`.github/workflows/release.yml`) — tag-triggered
  CD pipeline publishing to PyPI via trusted publishing (OIDC).

## [0.1.1] — 2026-04-09

First published release. Production-readiness pack on top of the `0.1.0`
internal baseline.

### Security

- **`AIRLOCK_HOST` now defaults to `127.0.0.1`** instead of `0.0.0.0`. The
  proxy no longer binds all interfaces out of the box. Deployments that
  need to accept off-host traffic (Docker, Kubernetes, reverse-proxied
  hosts) must set `AIRLOCK_HOST=0.0.0.0` explicitly. Documented in the
  README, `docs/operations.md`, the `.env` template, and the TUI config
  screen.

### Fixed

- **Alert engine leak (`airlock.tui.alert_engine`)** — active alerts
  accumulated forever with no expiry or resolution. The engine now
  auto-resolves alerts whose underlying condition no longer holds on the
  next evaluation cycle, drops alerts older than 24h, and caps the
  active list at 500 entries.
- **Guards stream burst drop (`airlock.tui.screens.guards`)** — a burst
  of log entries sharing the same timestamp was being dropped after the
  first one because the incremental-read filter used `<=` instead of
  `<`. Fixed, with a `request_id` dedupe for the rare re-seek path.
- **Overview p95 off-by-one (`airlock.tui.screens.overview`)** — the
  naive `int(n * 0.95)` index pinned p95 to the max for any sample size
  up to 20. Replaced with a nearest-rank helper (`_p95_index`).
- **`test_cli_status::test_status_defaults_to_localhost_4000`** — failed
  intermittently depending on whether a proxy was actually listening on
  `localhost:4000` in the test environment. Now mocks `urlopen`.
- **`test_proxy::test_main_default_host_port`** — flipped to assert the
  new `127.0.0.1` default and stubbed `load_dotenv` so a developer's
  local `.env` can't shadow the in-code default.
- **`test_cli_post::test_pass_when_key_and_sdk_available`** — stopped
  depending on the `[search]` extra actually being installed in the dev
  environment. Now stubs the SDK module.

### Changed

- **Distribution name is `airlock-llm`** on PyPI. The import name is
  unchanged (`import airlock`), and the CLI command remains `airlock`.
  The `airlock` name on PyPI was already taken by an unrelated package.
- **CI hardening** (`.github/workflows/ci.yml`):
  - `lint`, `docker`, and `security` jobs now `needs: [test]`, so they
    only run after tests pass. This stops lint/Docker from reporting
    green while the test suite is red.
  - Linter versions pinned: `ruff==0.15.9`, `mypy==1.20.0`,
    `pip-audit==2.7.3`. A new upstream release can no longer turn CI
    red on an unrelated PR.
- **Ruff per-file ignores** (`pyproject.toml`) for modules with
  legitimate post-gate imports (`callbacks/{metrics,s3_logger,sql_logger,tracing}.py`,
  `fast/monitor.py`, `guardrails/semantic.py`).

### Documentation

- Added PyPI metadata to `pyproject.toml`: `authors`, `keywords`,
  `classifiers`, and `[project.urls]` (Homepage, Repository, Issues,
  Changelog).
- README install instructions now lead with `pip install airlock-llm`
  and use the real repository URL for source installs.
- All `pip install airlock[extra]` snippets across docs and error
  messages updated to `pip install airlock-llm[extra]`.

## [0.1.0] — unreleased

Internal baseline. Never published to PyPI. Includes the full feature
set:

- LiteLLM-based unified proxy for OpenAI, Anthropic, and self-hosted
  (vLLM / Ollama / LocalAI) endpoints.
- Structured JSONL request/response logging with size + age rotation.
- PII redaction via Microsoft Presidio (CREDIT_CARD, US_SSN,
  EMAIL_ADDRESS, PHONE_NUMBER, US_BANK_NUMBER, IBAN_CODE).
- Keyword blocking (`AIRLOCK_BLOCKED_KEYWORDS`).
- Adaptive guardrail pipeline: semantic classifier, threat detector,
  circuit breaker, priority scoring.
- MCP tool server proxying with per-tool allow/block lists and argument
  sanitization.
- Textual TUI with overview, guards, threats, logs, config, test chat,
  and proxy control screens.
- Optional extras for S3 log archival, SQL logging, Prometheus metrics,
  OpenTelemetry tracing, and the `[tui]` / `[search]` integrations.
- Client-side Claude Code hooks (session, prompt, audit).
- Offline log analyzer (`airlock analyze`).

[0.3.0]: https://github.com/coreyt/airlock/releases/tag/v0.3.0
[0.2.0]: https://github.com/coreyt/airlock/releases/tag/v0.2.0
[0.1.1]: https://github.com/coreyt/airlock/releases/tag/v0.1.1
[0.1.0]: https://github.com/coreyt/airlock/releases/tag/v0.1.0
