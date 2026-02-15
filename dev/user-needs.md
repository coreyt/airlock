# Airlock — User Needs

This document defines the primary user needs that Airlock addresses. Each need
includes a rationale, the stakeholders it serves, and measurable acceptance
criteria.

---

## UN-1: Unified LLM Access

**As a** developer using AI coding tools,
**I need** a single proxy endpoint that works with every LLM provider,
**so that** I can switch between models (Claude, GPT, etc.) without reconfiguring
each tool individually.

### Stakeholders

- Software developers
- Engineering managers

### Acceptance Criteria

1. Airlock exposes an OpenAI-compatible `/v1/chat/completions` endpoint.
2. Requests sent with different `model` values (e.g., `claude-sonnet`, `gpt-4o`)
   are routed to the correct upstream provider.
3. Adding a new provider model requires only a `config.yaml` change — no code
   modifications.
4. Existing AI tool configurations (Cursor, Claude Code, GitHub Copilot) connect
   to Airlock by changing only the base URL and API key.

---

## UN-2: Data Loss Prevention — PII Stripping

**As a** security engineer,
**I need** personally identifiable information to be automatically redacted from
prompts before they leave the corporate network,
**so that** sensitive data (credit card numbers, SSNs, emails, phone numbers) is
never transmitted to third-party LLM providers.

### Stakeholders

- Information security team
- Compliance / legal
- Developers (protected by default)

### Acceptance Criteria

1. All outbound messages are scanned for PII entities before the LLM API call
   is made (`pre_call` stage).
2. Detected PII is replaced with anonymized placeholders (e.g.,
   `<CREDIT_CARD>`) in the outbound request.
3. The set of entity types to redact is configurable via the
   `AIRLOCK_PII_ENTITIES` environment variable.
4. Multi-part messages (text + image content blocks) are handled; text parts are
   scrubbed while non-text parts pass through unchanged.
5. When PII is redacted, a structured log entry records the count and types of
   entities found.
6. If Presidio is not installed, the proxy still starts and processes requests
   (guardrail degrades gracefully).

---

## UN-3: Data Loss Prevention — Keyword Blocking

**As a** security engineer,
**I need** prompts containing restricted keywords (project codenames, classified
terms, internal product names) to be rejected outright,
**so that** confidential organizational terminology never reaches external
providers.

### Stakeholders

- Information security team
- Program / project managers
- Compliance / legal

### Acceptance Criteria

1. A configurable blocklist of keywords is read from the
   `AIRLOCK_BLOCKED_KEYWORDS` environment variable.
2. Matching is case-insensitive across all message content.
3. If a blocked keyword is detected, the entire request is rejected with a clear
   error message before any data leaves the network.
4. The rejected keyword is logged (but the full prompt content is not exposed in
   the error response to the user).
5. Multi-part messages are flattened and scanned identically to plain text
   messages.
6. When no keywords are configured, the guardrail passes all requests through
   without overhead.

---

## UN-4: Comprehensive Request/Response Logging

**As an** engineering manager or auditor,
**I need** every LLM interaction to be logged in a structured, queryable format,
**so that** I have full visibility into usage patterns, costs, errors, and
content for auditing and compliance.

### Stakeholders

- Engineering managers
- Finance (cost tracking)
- Compliance / auditors
- Security operations (incident investigation)

### Acceptance Criteria

1. Every successful and failed LLM request produces a structured JSON log
   record.
2. Each record contains: timestamp, success/failure flag, model name, user
   identity, team identity, request ID, message content, response content, error
   details (on failure), start/end time, duration in milliseconds, and token
   counts (prompt, completion, total).
3. Logs are written as JSONL (one JSON object per line) to daily rolling files
   named `airlock-YYYY-MM-DD.jsonl`.
4. The log directory is configurable via `AIRLOCK_LOG_DIR`.
5. Log files are append-only and safe for concurrent writes from async request
   handlers.
6. Objects that are not natively JSON-serializable (datetimes, bytes, Pydantic
   models) are serialized without data loss.

---

## UN-5: Budget and Cost Control

**As a** finance stakeholder or engineering leader,
**I need** per-user and per-team spending limits on LLM usage,
**so that** costs are predictable and no individual or team can incur
uncontrolled spend.

### Stakeholders

- Finance
- Engineering leadership
- Team leads

### Acceptance Criteria

1. Administrators can generate virtual API keys scoped to individual users or
   teams via the `/key/generate` endpoint.
2. Each virtual key can have a maximum budget (in USD) and a rolling budget
   window (e.g., 30 days).
3. When a user exceeds their budget, subsequent requests are rejected with a
   clear "budget exceeded" error.
4. Token usage is tracked per key and visible through admin API endpoints.
5. The master key is required to manage virtual keys, and is configured via
   `AIRLOCK_MASTER_KEY`.

---

## UN-6: Seamless AI Tool Integration

**As a** developer,
**I need** Airlock to work transparently with my preferred AI coding tools
without changing my workflow,
**so that** security and logging happen in the background with zero friction.

### Stakeholders

- Software developers
- DevOps / platform team

### Acceptance Criteria

1. Cursor / Windsurf connects by setting the OpenAI Base URL to Airlock's
   `/v1` endpoint and using an Airlock virtual key.
2. Claude Code connects by setting `ANTHROPIC_BASE_URL` to the Airlock host.
3. GitHub Copilot connects via the VS Code `debug.overrideProxyUrl` setting.
4. Any other OpenAI-compatible client (custom scripts, other IDE plugins)
   connects using standard OpenAI SDK configuration.
5. Unsupported parameters from any client are silently dropped (via
   `drop_params: true`) rather than causing errors.
6. Request timeout is configurable (default 300 seconds) to accommodate
   long-running completions.

---

## UN-7: Self-Hosted Deployment

**As an** IT administrator,
**I need** to deploy Airlock entirely within our own infrastructure,
**so that** no LLM traffic traverses networks outside our control and we
maintain full data sovereignty.

### Stakeholders

- IT operations
- Information security
- Compliance / legal

### Acceptance Criteria

1. Airlock runs as a single Docker container with no external service
   dependencies beyond the configured LLM APIs.
2. `docker compose up --build` produces a fully operational deployment from a
   clean checkout.
3. All configuration is driven by environment variables and `config.yaml` — no
   hard-coded external service URLs.
4. Health check endpoint (`/health`) returns status for monitoring systems.
5. The container restarts automatically on failure (`restart: unless-stopped`).
6. Bind address and port are configurable via `AIRLOCK_HOST` and `AIRLOCK_PORT`.
7. A non-Docker deployment path (`pip install` + `airlock` command) is also
   supported.

---

## UN-8: Extensible Guardrail Framework

**As a** platform engineer,
**I need** the ability to add custom guardrails beyond the built-in PII and
keyword guards,
**so that** I can enforce organization-specific policies (semantic filtering,
schema enforcement, content moderation) as requirements evolve.

### Stakeholders

- Platform / infrastructure engineers
- Information security
- AI/ML engineers

### Acceptance Criteria

1. New guardrails are added by implementing a `CustomGuardrail` subclass and
   registering it in `config.yaml` — no changes to core proxy code required.
2. Guardrails can run at the `pre_call` stage (before the request leaves the
   network) and support both sync and async execution.
3. Each guardrail receives the full request data (messages, metadata, call type)
   and can modify or reject the request.
4. Multiple guardrails execute in the order defined in `config.yaml`.
5. A guardrail that raises an exception blocks the request and returns the error
   to the caller.
6. Guardrail failures are logged through the same enterprise logging pipeline as
   normal request failures.

---

## UN-9: Guided Setup and Unified CLI

**As a** platform engineer deploying Airlock for the first time,
**I need** a single CLI with an `init` command that generates a working
configuration,
**so that** I can go from `pip install` to a running proxy in under two minutes.

### Stakeholders

- Platform / infrastructure engineers
- DevOps team
- Developers (first-time setup)

### Acceptance Criteria

1. A unified `airlock` command dispatches to `init`, `start`, and `analyze`
   subcommands.
2. `airlock init` generates `config.yaml`, `.env`, and `logs/` in the current
   directory (or a directory specified by `--dir`).
3. Existing files are never overwritten unless the `--force` flag is passed.
4. After initialization, a summary is printed showing which files were created,
   skipped, or overwritten, followed by next-step instructions.
5. The `airlock-analyze` entry point continues to work unchanged for backwards
   compatibility.
6. The CLI uses only Python standard library modules (argparse) — no additional
   dependencies.
