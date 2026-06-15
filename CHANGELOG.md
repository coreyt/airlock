# Changelog

All notable changes to Airlock are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Airlock Batch Gateway + AI Studio (Gemini) batch adapter** — an
  Airlock-owned front controller on `/v1/files` + `/v1/batches` that
  intercepts requests carrying `?custom_llm_provider=aistudio` (everything
  else falls through to LiteLLM untouched) and runs them against Google's
  native Gemini batch API, which LiteLLM does not wire for the AI Studio
  `gemini/` provider. It translates OpenAI↔Gemini in both directions,
  maps `JOB_STATE_*` to OpenAI batch statuses, and returns OpenAI-shaped
  output with the native Gemini body preserved verbatim. The create path is
  **idempotent** on `(input_file_id, model, endpoint, params)` and bounds
  duplicate provider jobs to ≤1 under lease expiry (at-least-once with
  auto-cancel of duplicates). Aliases opt in via a `airlock_batch:
  {backend: aistudio, provider_model: …}` marker — a **sibling** of
  `litellm_params` so it never leaks to the provider SDK on the sync path.
  The gateway enforces `AIRLOCK_MASTER_KEY` on its own ingress (it dispatches
  before LiteLLM's route auth). Verified end-to-end against the live Gemini
  batch endpoint (`tests/test_aistudio_batch_e2e.py`, opt-in live gate). The
  Mistral adapter remains design-only. Batch-content guardrail scanning is a
  no-op stub for now, so batch still bypasses the guards.
- **`aistudio` optional extra** — pulls `google-genai` (lazy-imported), so
  the proxy boots without it. Install with `uv sync --extra aistudio` (or
  `make sync` for all extras).
- **Local vLLM router guardrail** (`airlock-local-vllm-router`, `pre_call`) —
  for single-GPU setups that serve one model at a time behind a shared
  vLLM endpoint. On first call it reads `config.yaml`, treats every
  `model_list` entry whose `litellm_params.api_base` matches
  `AIRLOCK_LOCAL_VLLM_BASE_URL` as a local alias (expected served-name =
  the `model` field with any `openai/` prefix stripped), and probes
  `{base_url}/models` (cached for `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`)
  to learn what vLLM is actually serving. If a requested local alias is
  not the loaded model, it **fails fast with an actionable message**
  ("Currently loaded: X. Stop the running container and start the one
  that serves Y") instead of letting an upstream 404 propagate. An
  optional `AIRLOCK_LOCAL_VLLM_SWITCH_HINT` format string (placeholders:
  `{requested}`, `{requested_served}`, `{loaded}`, `{loaded_aliases}`,
  `{base_url}`) is appended to the error.
- **Reasoning stripper guardrail** (`airlock-reasoning-stripper`,
  `post_call`) — removes non-standard `◁think▷ … ◁/think▷` reasoning
  blocks from responses. The reference case is **Kimi-Dev-72B**, whose
  delimiters are three separate tokens (`◁` + `think` + `▷`), so vLLM's
  native `--reasoning-parser` (which matches single token IDs) cannot
  handle them. Scoped per-model via `AIRLOCK_REASONING_STRIP_MODELS`
  (default: `kimi-dev`); matches bare and `openai/`-prefixed aliases,
  also strips an orphan trailing `◁/think▷`, and handles both streaming
  and non-streaming responses. Other models pass through untouched.
- **Three local vLLM model aliases** (`kimi-dev`, `qwen3-32b`,
  `qwen3.6-27b`) join the existing `gemma-4` entry in `config.yaml`, all
  pointing at the same vLLM endpoint since only one model is loaded at a
  time.
- **Four new environment variables**: `AIRLOCK_LOCAL_VLLM_BASE_URL`,
  `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`, `AIRLOCK_LOCAL_VLLM_SWITCH_HINT`,
  and `AIRLOCK_REASONING_STRIP_MODELS`.
- **Vertex AI Gemini deployments** (`gemini-3.5-flash-vertex`,
  `gemini-3.1-pro-vertex`, provider `vertex_ai/…`) in `config.yaml`, in
  addition to the AI Studio `gemini/` aliases. The reason to add them is
  **batch**: LiteLLM wires the Batch API (`/v1/files` + `/v1/batches`) for
  `vertex_ai` but not for the AI Studio `gemini/` provider. They read
  `VERTEX_PROJECT` / `VERTEX_CREDENTIALS` from the environment, pin
  `vertex_location: global`, and stage batch files in `GCS_BUCKET_NAME`.
- **Vertex AI Gemini batch (regional models)** — asynchronous, ~50%
  cheaper, ~24h turnaround through the proxy's `vertex_ai` Batch API.
  Batch requires a **regional** location; Gemini 3.x currently resolves
  only on the Vertex `global` endpoint and so cannot batch yet. Full GCP
  setup (service account, GCS bucket, IAM) is documented in the new Vertex
  AI Batch guide.
- **OpenAI batch through the proxy** via a `files_settings` block in
  `config.yaml`. `/v1/files` requires a provider entry for non-`vertex_ai`
  batch; without it the proxy returned `500 "files_settings is not set"`.
  With it, the OpenAI Batch API (`/v1/files` + `/v1/batches` with
  `custom_llm_provider=openai`) works end-to-end through Airlock
  (verified: file upload, batch create, and in-progress status).
- **`vertex` optional extra** — pulls `google-auth`, which LiteLLM's
  `vertex_ai` provider needs for token minting and GCS access but
  `litellm[proxy]` does not install. Install with `uv sync --extra vertex`
  (or `make sync` for all extras).

### Changed

- **CI hardening**: upgraded GitHub Actions to Node.js 24-compatible
  versions, pinned `astral-sh/setup-uv` to `v8.0.0`, and rewrote
  `preflight.sh` to mirror CI exactly.
- **`secrets/` is now gitignored** — Vertex AI batch needs a GCP
  service-account JSON key on the host (`VERTEX_CREDENTIALS`); keep it out
  of version control.

### Fixed

- **Fast subsystem and PII hook null-safety on batch/file routes** —
  `/v1/batches` and `/v1/files` carry no top-level `model` (the model
  lives inside the uploaded JSONL), which crashed several chat-shaped code
  paths and returned `500` on batch create:
  - `ModelAliasTable.resolve(None)` crashed on `.lower()`; the Fast
    Guardian now coerces a null requested model to `unknown`, and
    `resolve()` / `infer_provider()` bail safely on non-string/empty
    input.
  - the PII guard's `async_post_call_success_hook` is invoked with
    `data=None` on these routes; it now guards `data`/`metadata` being
    `None` instead of dereferencing them.
  - regression tests added for each.

### Documentation

- Documented both new guardrails in the guardrail-chain table with
  dedicated subsections (behavior + configuration), the
  multiple-aliases-on-one-vLLM-endpoint pattern in the configuration
  guide, and the four new environment variables.
- Added a **Batch Processing** guide (`docs/guide/batch.md`) covering the
  working OpenAI recipe (`files_settings` + restart, `/v1/files` +
  `/v1/batches`) and a **Vertex AI Batch** guide
  (`docs/guide/vertex-batch.md`) covering GCP setup; both added to the
  mkdocs nav. Both carry the standing caveat that batch bypasses Airlock's
  guardrails. The Batch guide now documents the **working AI Studio (Gemini)**
  recipe through the Airlock Batch Gateway (extra, `airlock_batch` alias,
  upload/create/poll with `custom_llm_provider=aistudio`); Mistral batch
  remains documented as in-progress.

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
