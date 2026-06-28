# Changelog

All notable changes to Airlock are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.3] — 2026-06-28

Internal-quality release: decouple from LiteLLM internals, take PII off the event
loop, and pay down structural debt surfaced by the 0.5.0 architecture audit
(`dev/notes/architecture-audit-0.5.0-2026-06.md`). No new client-facing features;
behavior-preserving except the documented concurrency win.

### Changed

- **Lower tail latency under concurrency (UN-27).** Presidio PII analysis now runs
  via `asyncio.to_thread` instead of synchronously on the event loop, so concurrent
  `AIRLOCK_PII_ENABLED` requests no longer serialize behind it. Redaction output,
  mapping, and counters are **byte-identical**; single-request latency is unchanged.
- **Request text is extracted once per request.** `extract_text` is now
  cache-aware (metadata-scoped, post-PII-redaction); the PII, keyword, and guardian
  guards reuse the cached text instead of each re-walking the messages.
- **Local-vLLM `/models` cache TTL widened 5s → 30s** with an opt-in-safe prewarm
  (a cold cache still resolves correctly on the first request; non-vLLM aliases are
  unaffected). Override via `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`.

### Internal / structural (no wire change)

- **LiteLLM Anti-Corruption Layer (`airlock/litellm_adapter.py`).** A single module
  now owns every read of LiteLLM internals (`_hidden_params`, wrapper attributes,
  `response_cost`, the proxy-app object and middleware install). **This is the one
  file to re-verify on a LiteLLM upgrade** — internal-coupling assumptions live here
  and nowhere else. Response headers, served-by attribution, and logs are
  byte-identical (parity-tested across the streaming and non-streaming paths).
- **`fast` ↔ `guardrails` import cycle broken**, and the proxy bootstrap extracted
  to `airlock/proxy_bootstrap.py` (the sole caller of the `install_*` hooks); an
  automated guard fails the build if the cycle returns. Middleware install order is
  asserted (batch → admin → header → LiteLLM).
- **`threat_score` race fixed** — the read-modify-write now happens atomically under
  `StateStore`'s lock; client-identity extraction is consolidated behind
  `airlock/client_identity.py` (golden-equivalent across the prior call sites).

## [0.5.2] — 2026-06-27

### Added

- **Provider-explicit `provider/model` aliases across the whole catalog** —
  every `model_list` entry gains a stable alias whose prefix names the serving
  provider (`anthropic/…`, `openai/…`, `aistudio/…` (served-by `gemini`),
  `vertex/…` (served-by `vertex_ai`), `mistral/…`, `perplexity/…`,
  `tavily/web-search`, `vllm/…`). The prefix is the provider, exact-matched and
  never re-parsed as routing; `aistudio/` and `vertex/` are deliberately distinct
  from LiteLLM's native `gemini/` / `vertex_ai/` tokens. The bare name (e.g.
  `gemini-3.5-flash`) remains a documented, ops-repointable **default**; the
  prefixed name is the stable client contract. A concrete prefixed name is
  auto-pinned (fallbacks/retries off → 429 on overload, never a silent swap).
- **Machine-discoverable per-model capability** — `GET /model/info` publishes a
  capability record (`airlock_provider`, `endpoints`, `underlying`, `region`,
  `deprecated`) and `GET /v1/models` folds the same fields under an additive
  `airlock` object on each model. `endpoints` is computed from the real wiring by
  one helper (`airlock/capability.py`), so a model advertises `batch` **iff** it
  is gateway-batch-marked (`airlock_batch`) **or** a regionally-located Vertex
  model — published capability cannot drift from routing. A config-consistency
  test enforces the rule. Capability-in-the-name is gone: the legacy `-batch` /
  `-aistudio` twins are consolidated onto the `provider/model` entry, which serves
  sync **and** advertises batch.
- **`X-Airlock-Served-By` as the verify surface** — pin a `provider/model` alias,
  then confirm from data that the discovered `airlock_provider` actually served:
  `X-Airlock-Served-By` equals it (`aistudio/…` → `gemini`, `vertex/…` →
  `vertex_ai`). `X-Airlock-Served-Region` appears for gateway/region backends.

### Deprecated

- **Legacy capability-suffix aliases** — `-aistudio` / `-vertex` / `-batch` twins
  (`gemini-3.5-flash-aistudio`, `gemini-3.1-pro-aistudio`, `gemini-3.5-flash-vertex`,
  `gemini-3.1-pro-vertex`, `mistral-large-batch`, `mistral-small-batch`,
  `qwen36-27b-vllm-batch`) are **deprecated in 0.5.2, removed in 0.6.0**. They are
  **dual-listed and fully functional** in 0.5.2 (same `litellm_params` + marker)
  and carry `deprecated: true` in their capability record. No client breaks in
  0.5.2 — migrate to the `provider/model` names (see
  [Batch → old → new alias map](https://github.com/coreyt/airlock/blob/main/docs/guide/batch.md)).

### Unchanged

- **Request-path behavior** — pinning, fallbacks, the circuit breaker, and
  served-by attribution are unchanged; this work is additive naming + metadata.
- **`/v1/models` stays OpenAI-compatible** — the `airlock` object is purely
  additive; absent-aware clients are unaffected.
- **Vertex remains chat-only as shipped** — `vertex/…` entries use
  `vertex_location: global` and advertise `endpoints: ["chat"]`; Vertex batch is
  region-gated and is **not** advertised at `global`.

## [0.5.1] — 2026-06-26

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
  batch endpoint (`tests/test_aistudio_batch_e2e.py`, opt-in live gate).
  Batch-content guardrail scanning is a no-op stub for now, so batch still
  bypasses the guards.
- **Mistral batch adapter** — second gateway backend
  (`?custom_llm_provider=mistral`), opting in via `airlock_batch:
  {backend: mistral, provider_model: …}`. Translation is near-passthrough
  (Mistral batch input is OpenAI-shaped and Mistral chat is OpenAI-compatible);
  it reuses the gateway's idempotency/reconcile core, keying provider jobs by
  metadata `display_name` so reconcile-by-idem works. Verified end-to-end
  against the live Mistral batch API (`tests/test_mistral_batch_e2e.py`,
  opt-in live gate). Ships `mistral-large-batch` + `mistral-small-batch`
  aliases.
- **`aistudio` + `mistral` optional extras** — pull `google-genai` /
  `mistralai` (both lazy-imported), so the proxy boots without them. Install
  with `uv sync --extra aistudio` / `--extra mistral` (or `make sync` for all
  extras). The `mistral` extra is pinned `<2`: `mistralai` 2.x restructured the
  package (the top-level `from mistralai import Mistral` moved to
  `mistralai.client.sdk`) and the adapter targets the v1 `client.batch.jobs` API.
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

- **Unified settings precedence + no hidden budget defaults (UN-25)** — every `fast/`
  setting now reads through one typed `AirlockSettings` loader (`airlock/fast/settings.py`)
  with uniform `env > config > default` precedence and malformed-input fallback. **Behavior
  change:** the hidden hardcoded provider-budget defaults (`anthropic`/`openai` ≈ $50,
  `gemini`/`mistral`/`perplexity` ≈ $25) the fast router previously read regardless of
  `config.yaml` are **removed**. With no explicit `router_settings.provider_budget_config`,
  there is now **no proactive cost-swap and no monitor budget warn** — budgets are purely
  config-driven (`0`/absent ⇒ no enforcement, identically across LiteLLM's hard block, the
  monitor warn, and the router swap; documented in `config.yaml`). The monitor also now reads
  budgets from the correct `router_settings.provider_budget_config` nesting (R6 fix — it
  previously read a top-level key that was always empty).
- **Failover derives from `router_settings.fallbacks` and is constrained to `model_list`
  (R2)** — the circuit-breaker pre-call failover map is now built from the same `fallbacks`
  config LiteLLM uses (no separate hardcoded map). **Behavior change:** the stale default
  targets (`gpt-4o`/`gpt-4o-mini`, which were not served `model_name`s) are gone — a
  circuit-open now routes to a real served alias. Failover candidates are additionally
  filtered against the loaded `model_list` catalog (an unknown/typo'd target, incl. via
  `AIRLOCK_FAILOVER_MAP`, is skipped), with a safe fallback that disables filtering when the
  catalog is not loaded.
- **Provider spend is accurate at any volume and survives restart (UN-26)** — per-provider
  spend is now a rolling, time-windowed integer-µ$ accumulator behind a `DualCache`-backed
  store seam, replacing the `deque(maxlen=1000)` that **undercounted** providers doing
  >1000 billed calls/day (R5). **Behavior change:** a proxy restart **no longer zeroes
  captured provider spend** — it is checkpointed to disk (versioned, atomic, prune-before-
  checkpoint, idempotent age-bounded restore) and rehydrated on startup, now correctly run
  in the litellm **child** process where spend is recorded (FIX-1). `cb_state.json` circuit-
  breaker recovery rides the same (now correctly-located) path. (In-memory/single-process;
  Redis backend + multi-worker remain deferred.)
- **Single configurable budget warn ratio** — the "near budget" threshold is now one
  value, `airlock_settings.budget_warn_ratio` (env `AIRLOCK_BUDGET_WARN_RATIO`,
  default `0.8`), read from `AirlockSettings` by **both** the monitor near-limit warn
  (`X-Airlock-Budget-State: near_limit`) and the router proactive swap. Previously these
  diverged: the monitor warned at `0.8` while the router only swapped at a hardcoded,
  non-overridable `0.9`. **Behavior change:** the router's proactive-swap point moves from
  `0.9` to the configured ratio (default `0.8`), so `X-Airlock-Model-Override` and
  `near_limit` now fire at the same, tunable point. A `0`/absent provider budget still
  short-circuits to no-warn/no-swap (unchanged).
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
  and **Mistral** recipes through the Airlock Batch Gateway (extra,
  `airlock_batch` alias, upload/create/poll with
  `custom_llm_provider=aistudio|mistral`), both live-verified end-to-end.

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
