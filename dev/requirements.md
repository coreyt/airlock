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

`airlock status` SHALL probe `/health` using only stdlib (urllib). Default
target: `http://localhost:4000/health`, configurable via `--host`/`--port` flags
or `AIRLOCK_HOST`/`AIRLOCK_PORT` env vars. Exit 0 if healthy, exit 1 if not
reachable.

### NFR-11: Minimal CLI Dependencies

**Traces to:** UN-9

The CLI framework SHALL use only Python standard library modules (argparse) and
SHALL NOT introduce new third-party dependencies.
