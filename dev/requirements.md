# Airlock — Requirements

This document derives functional and non-functional requirements from the
[User Needs](user-needs.md). Each requirement is traceable to one or more user
needs.

---

## Functional Requirements

### FR-1: OpenAI-Compatible API Endpoint

**Traces to:** UN-1, UN-6

The proxy SHALL expose an HTTP endpoint at `/v1/chat/completions` that accepts
requests conforming to the OpenAI Chat Completions API schema and returns
responses in the same format, regardless of the upstream LLM provider.

### FR-2: Multi-Provider Model Routing

**Traces to:** UN-1

The proxy SHALL route requests to the correct upstream provider based on the
`model` field in the request body, as defined by the `model_list` entries in
`config.yaml`.

### FR-3: Declarative Model Configuration

**Traces to:** UN-1

New LLM provider models SHALL be configurable by adding entries to the
`model_list` section of `config.yaml` without modifying application code. Each
entry specifies the model name alias, upstream model identifier, and API
credentials.

### FR-4: PII Detection and Redaction

**Traces to:** UN-2

The proxy SHALL scan all outbound message content for PII entities using
Microsoft Presidio and replace detected entities with anonymized placeholder
tokens before the request is forwarded to the upstream provider.

### FR-5: Configurable PII Entity Types

**Traces to:** UN-2

The set of PII entity types to detect SHALL be configurable via the
`AIRLOCK_PII_ENTITIES` environment variable. The default set SHALL be:
`CREDIT_CARD`, `US_SSN`, `EMAIL_ADDRESS`, `PHONE_NUMBER`.

### FR-6: Keyword Blocklist Enforcement

**Traces to:** UN-3

The proxy SHALL reject any request whose message content contains a keyword
present in the blocklist defined by the `AIRLOCK_BLOCKED_KEYWORDS` environment
variable. Matching SHALL be case-insensitive.

### FR-7: Blocked Request Error Reporting

**Traces to:** UN-3

When a request is blocked by a guardrail, the proxy SHALL return an error
response to the caller that indicates the request was blocked by policy, without
echoing back the specific blocked content.

### FR-8: Structured JSONL Request Logging

**Traces to:** UN-4

The proxy SHALL log every successful and failed LLM request as a JSON object
appended to a daily log file named `airlock-YYYY-MM-DD.jsonl` in the configured
log directory.

### FR-9: Log Record Schema

**Traces to:** UN-4

Each log record SHALL contain the following fields:
- `timestamp` — ISO 8601 UTC timestamp
- `success` — boolean
- `model` — model name string
- `user` — user identifier (from virtual key metadata)
- `team` — team identifier (from virtual key metadata)
- `request_id` — LiteLLM call ID
- `messages` — the request messages array
- `response` — serialized response object
- `error` — error string (failures only)
- `start_time`, `end_time` — request timing
- `duration_ms` — request duration in milliseconds
- `prompt_tokens`, `completion_tokens`, `total_tokens` — token usage

### FR-10: Configurable Log Directory

**Traces to:** UN-4

The log output directory SHALL be configurable via the `AIRLOCK_LOG_DIR`
environment variable, defaulting to `./logs`.

### FR-11: Virtual Key Management

**Traces to:** UN-5

Administrators SHALL be able to create virtual API keys scoped to users or
teams via the `/key/generate` endpoint, protected by the master key.

### FR-12: Budget Enforcement

**Traces to:** UN-5

The proxy SHALL support configuring maximum spend budgets and rolling budget
windows per virtual key. Requests from keys that have exceeded their budget
SHALL be rejected.

### FR-13: Unsupported Parameter Handling

**Traces to:** UN-6

The proxy SHALL silently drop request parameters not supported by the upstream
provider (via LiteLLM `drop_params: true`) rather than returning errors.

### FR-14: Guardrail Registration

**Traces to:** UN-8

New guardrails SHALL be registerable in the `guardrails` section of
`config.yaml` by specifying a Python module path and execution mode, without
modifying core proxy code.

### FR-15: Pre-Call Guardrail Execution

**Traces to:** UN-2, UN-3, UN-8

Guardrails configured with `mode: pre_call` SHALL execute before the request is
forwarded to the upstream provider. They SHALL receive the full request data and
MAY modify or reject the request.

### FR-16: Multi-Part Message Support

**Traces to:** UN-2, UN-3

Both PII redaction and keyword scanning SHALL handle multi-part message content
(arrays of text and image content blocks), processing text parts while passing
non-text parts through unchanged.

---

## Non-Functional Requirements

### NFR-1: Deployment as a Single Container

**Traces to:** UN-7

The system SHALL be deployable as a single Docker container with no external
service dependencies beyond the configured LLM provider APIs.

### NFR-2: Configuration via Environment Variables

**Traces to:** UN-7

All secrets (API keys, master key) and operational settings (host, port, log
directory, guardrail parameters) SHALL be configurable via environment variables,
with no secrets committed to source control.

### NFR-3: Health Check Endpoint

**Traces to:** UN-7

The system SHALL expose a `/health/liveliness` HTTP endpoint suitable for
container orchestrator liveness/readiness probes (no model calls). The deeper
`/health` endpoint may call providers and MUST NOT be used for automated probes.

### NFR-4: Automatic Restart on Failure

**Traces to:** UN-7

The Docker Compose deployment SHALL configure the container to restart
automatically on failure (`restart: unless-stopped`).

### NFR-5: Request Timeout

**Traces to:** UN-6

The proxy SHALL enforce a configurable request timeout (default 300 seconds) to
prevent hung upstream connections from blocking resources indefinitely.

### NFR-6: Graceful Degradation

**Traces to:** UN-2, UN-8

If an optional dependency (e.g., Presidio for PII detection) is not installed,
the proxy SHALL start and process requests normally with the affected guardrail
disabled, rather than failing to launch.

### NFR-7: Serialization Robustness

**Traces to:** UN-4

The logging subsystem SHALL serialize all Python objects (datetimes, bytes,
Pydantic v1/v2 models) to JSON without raising exceptions or losing data.

### NFR-8: Non-Docker Deployment Support

**Traces to:** UN-7

The system SHALL support a pip-installable deployment path (`pip install` +
`airlock` CLI command) for environments where Docker is not available.

### NFR-9: Guardrail Execution Order

**Traces to:** UN-8

Multiple guardrails SHALL execute in the order they are defined in
`config.yaml`, allowing administrators to control the evaluation sequence.

### NFR-10: Log Append Safety

**Traces to:** UN-4

Log writes SHALL be append-only to daily files, supporting concurrent async
request handlers without data corruption.

### FR-17: Unified CLI Entry Point

**Traces to:** UN-9

The system SHALL provide a single `airlock` command that dispatches to `init`,
`start`, and `analyze` subcommands. Invoking `airlock` with no subcommand SHALL
print help text and exit with code 0.

### FR-18: Project Initialization

**Traces to:** UN-9

`airlock init` SHALL generate `config.yaml`, `.env`, and a `logs/` directory in
the target directory (current directory by default, overridable with `--dir`)
from bundled templates.

### FR-19: Idempotent Initialization

**Traces to:** UN-9

`airlock init` SHALL skip existing files without modification unless the
`--force` flag is provided, in which case existing files SHALL be overwritten.
An existing `logs/` directory SHALL always be left untouched.

### FR-20: Initialization Summary

**Traces to:** UN-9

After initialization, the CLI SHALL print a summary showing the disposition of
each artifact (created, skipped, or overwritten) and next-step instructions
including how to start the proxy.

### FR-21: Backwards-Compatible Analyze

**Traces to:** UN-9

The existing `airlock-analyze` entry point SHALL remain unchanged and continue
to invoke `airlock.slow.cli:main` directly.

### FR-22: Start Pre-Flight Validation

**Traces to:** UN-9

`airlock start` SHALL validate that `config.yaml` exists at the resolved path
before launching. Missing config → error + suggest `airlock init`, exit 1.
Missing `.env` → warning on stderr, proceed with startup.

### FR-23: Proxy Status Check

**Traces to:** UN-9

`airlock status` SHALL probe `/health/liveliness` using only stdlib (urllib). Default
target: `http://localhost:4000/health/liveliness`, configurable via `--host`/`--port` flags
or `AIRLOCK_HOST`/`AIRLOCK_PORT` env vars. Exit 0 if healthy, exit 1 if not
reachable.

### NFR-11: Minimal CLI Dependencies

**Traces to:** UN-9

The CLI framework SHALL use only Python standard library modules (argparse) and
SHALL NOT introduce new third-party dependencies.

---

## 0.5.14 ratified requirements

### DFR-24 / DAC-24: Benchmark chat alias

Airlock SHALL expose `gpt-4o-mini` only as an explicit reviewed model-list
alias. Mocked normal and streaming tests SHALL retain authentication, policy,
and served-provider attribution; a non-sensitive funded smoke is recorded
separately.

### DFR-25 / DAC-25: Embedding alias boundary

Airlock SHALL serve `/v1/embeddings` only through explicit embedding-capable
aliases, preserving string/batch input and supported options through ordinary
policy and observability. Unconfigured aliases and unsupported options SHALL
fail clearly before dispatch; embedding requests SHALL not be rerouted or
failed-over.

### DFR-26 / DAC-26: Benchmark-safe logging

Operators SHALL have a logging profile that redacts request/response content in
enterprise JSONL and disables unredacted SQL/Fathom retention paths. Sentinel
tests SHALL prove redaction, and the profile SHALL prescribe
`/health/liveliness` as the no-model-call probe.

### DFR-27 through DFR-29 / DAC-27 through DAC-29: Optional providers

Provider configuration SHALL be explicit; discovery is informational,
same-origin and redirect-free; and provider errors are bounded before any
artifact boundary. OpenRouter is an operator-configured gateway: client routing
overrides are rejected and Airlock never claims downstream-provider control.
DeepSeek uses its stable explicit base and supports function tools only. Neither
provider is auto-enabled by an environment key or default alias.

### DFR-30 / DAC-30: Deterministic TUI verification

Ordinary TUI tests SHALL compose the production widget tree without unrelated
background workers. Named normal-mode tests SHALL retain lifecycle, cancellation,
shutdown, stale-callback, JSONL, and MCP coverage.

### DFR-31 / DAC-31: Bounded operator diagnostics

Authenticated operators SHALL receive bounded, source-labelled routing,
session-affinity, QoS-priority, and telemetry-health views without a UI
dependency on the inference path. Session IDs, key material, credentials,
prompt content, raw exporter errors, and raw endpoints SHALL not appear in these
views. A session-pin break SHALL be authenticated and audited; unavailable or
stale state SHALL be stated rather than inferred.

### DFR-33 / DAC-33: Optional FathomDB operational reads

Operators MAY select FathomDB as the operational-read backend only through an
explicit setting. The default SHALL remain bounded JSONL reads. TUI history and
Advisor error/search reads SHALL label their actual source, limit/truncation,
and any unavailable/invalid-backend JSONL fallback. FathomDB remains
single-owner and optional. Selected separate-process reads SHALL use a
loopback-only proxy-admin bridge (and require its local admin configuration),
never a second engine open. A FathomDB erasure receipt SHALL continue to state
that JSONL retention/deletion is a separate obligation.

---

## 0.5.15 ratified requirements

### DFR-36 / DAC-36: Unused configured-provider credential warning

After normal environment loading and before optional provider discovery or
LiteLLM launch, Airlock SHALL emit one redacted, local, advisory startup warning
per recognised provider that has a nonblank recognised credential and zero
explicit aliases in LiteLLM's effective direct-include model list. The warning
SHALL use the stable event
`airlock.startup.provider_credential_without_alias` and expose only the
canonical provider, `credential_configured: true`,
`configured_alias_count: 0`, and `source: startup_validation`. It SHALL not
scan arbitrary environment variables, disclose values or variable names, make a
network/provider call, alter configuration/routing/discovery/startup status, or
create an Admin/TUI surface. Effective aliases SHALL use
`airlock_provider_for`; include semantics SHALL match installed LiteLLM's
active include-list expansion, including descendants reached through an
included `include:` list.

### DFR-34 / DAC-34: Typed Fast Guardian threat-backoff response

Fast Guardian SHALL raise an Airlock-owned typed rate-limit exception for both
the request that creates a client threat backoff and a request rejected while
that backoff remains active. The proxy SHALL render the exception as an
OpenAI-shaped HTTP 429 with a whole-second, minimum-one `Retry-After`, stable
`type` and `code`, and `error.airlock.source: threat_backoff`. The response
SHALL not expose client identity, threat score, heuristic reason, request
content, provider identity, or provider-circuit-breaker state. Provider and
admission 429 contracts SHALL remain distinguishable and unchanged.

### DFR-35 / DAC-35: Secret-scan delivery control

Airlock's repository SHALL use a dedicated, non-deploying Gitleaks control at
scanner version `8.30.0`: a staged pre-commit hook and an isolated GitHub
Actions `gitleaks / scan` job for pull requests to `main`, pushes to `main`,
manual dispatch, and scheduled reachable-history scans. The workflow SHALL use
full-SHA-pinned actions, a full checkout, and only `contents: read` and
`pull-requests: read` permissions. It SHALL not use `pull_request_target`,
write/OIDC permissions, repository/deployment secrets, comments, artifact or
SARIF upload, or result summaries.

The baseline SHALL contain only individually reviewed exact fingerprints;
broad path/rule exclusions and inline allow comments are prohibited. Scanner
configuration files SHALL have designated code ownership, and `main` SHALL
require both that review and the stable `gitleaks / scan` check once a GitHub
administrator configures the external rule. Directory and reachable-history
scans must be clean for the reviewed baseline; a synthetic non-usable detector
fixture must fail redacted and pass after removal. The control SHALL not alter
Airlock runtime behavior, configuration, image contents, or provider/deployment
credential access.

### DFR-37 / DAC-37: Read-only provider configuration projection

When the existing Admin control plane is explicitly enabled, Airlock SHALL
provide `GET /airlock/admin/config/providers` as a bounded, immutable,
startup-effective provider-configuration projection. It SHALL require
`admin:read_config` for a capability token; the existing master-key and trusted
loopback operator paths retain full authority, while `admin:read` alone SHALL
receive `403` and a disabled Admin plane SHALL receive `404`. The projection
SHALL report only aliases, `capability_record()` provider/endpoints/underlying/
region/deprecation truth, hostname-only API bases, opaque credential kind plus
presence, source, timestamp, schema version, credential-blind redacted canonical fingerprint,
restart-required status, and deterministic truncation within 64 providers, 200
aliases, and 256-character metadata fields. It SHALL set `Cache-Control:
no-store`.

The launcher, LiteLLM child, Admin policy, snapshot, and capability seam SHALL
use the same pinned LiteLLM include-list expansion semantics and the same
materialized runtime config path. The child SHALL receive that path via
`AIRLOCK_CONFIG` before installing the Admin perimeter. Neither literal or
reference credential names/values/lengths, arbitrary environment names, include
paths, raw API-base paths/query/userinfo, provider calls/errors, CRUD, reload,
discovery activation, nor a second configuration owner are permitted. The TUI
SHALL read this view only over its Admin HTTP client in a bounded background
refresh, visibly label unavailable/stale startup state, and never fall back to
another process's files. YAML plus deployment workflow and restart remain the
configuration authority.
