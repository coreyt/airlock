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

---

## UN-10: Operator-Initiated Quarantine Clear

**As an** operator who has just topped up a provider's credits,
**I need** to clear or accelerate Airlock's provider-protection quarantine
instead of waiting out the full cooldown,
**so that** a recovered provider returns to service immediately rather than
draining a 300 s timer the operator knows is stale.

> Traces to `dev/notes/design-admin-api-capability-auth.md` (§7) and the live
> incident in `dev/notes/design-large-context-resilience-overview.md` (§1).

### Stakeholders

- Operations / on-call
- Developers blocked behind a stale quarantine

### Acceptance Criteria

1. An admin operation clears a provider (or client→provider) quarantine, and the
   provider is eligible for traffic again on the next request.
2. The default clear is **half-open**: it admits a single probe; a successful
   probe closes the breaker, a failed probe re-arms it using the configured
   cooldown — so a mistaken clear (credits not actually restored) self-corrects.
3. A blind **force** clear is available as a separate, higher-privilege
   operation.
4. Clearing a quarantine does not let the breaker's threshold counter re-arm off
   pre-clear 429s (the clear sets a "cleared floor"; see CC-6).
5. Clear operations are rate-limited per provider and every clear is audited.
6. The operation is reachable from the TUI (operator) and over HTTP (scripted).

---

## UN-11: Capability Auth Without New Infrastructure

**As an** operator,
**I need** to authorize privileged actions and per-client capabilities without
standing up a database, IdP, or PKI,
**so that** admin and capability features are usable in a single-node, no-extra-
infra deployment while remaining off by default.

> Traces to `dev/notes/design-admin-api-capability-auth.md` (§4–§5).

### Stakeholders

- Operations
- Information security
- Platform engineers

### Acceptance Criteria

1. A request arriving on the loopback interface is treated as an operator
   (network-position auth), governed by a `trust_loopback` setting.
2. Remote/programmatic callers authorize with a short-lived HS256 JWT carrying
   `sub`, `scope[]`, and `exp`, signed by a server-side secret
   (`AIRLOCK_JWT_SECRET`, falling back to an HKDF of `AIRLOCK_MASTER_KEY`).
3. The token `sub` is the **authenticated** key-derived identity `key:<last8>`
   (from the validated bearer key); a guardrail-skip token is honored only when
   `sub` matches that key-derived id — **never** the forgeable `X-Airlock-Client`
   attribution header, which carries zero authorization weight (prevents token
   replay).
4. The master key remains the root credential: break-glass admin plus the
   credential that mints tokens.
5. No database, external IdP, or client-cert PKI is required.
6. All of the above default to **off**; a config-free deploy exposes no admin
   surface and ignores capability headers.

---

## UN-12: Native TLS Termination

**As an** IT administrator,
**I need** Airlock to optionally terminate TLS itself,
**so that** I can serve HTTPS on a single node without deploying a separate
reverse proxy, while keeping the reverse-proxy option for fleets that need it.

> Traces to `dev/notes/design-admin-api-capability-auth.md` (§3).

### Stakeholders

- IT operations
- Information security

### Acceptance Criteria

1. When `AIRLOCK_SSL_CERTFILE` and `AIRLOCK_SSL_KEYFILE` are both set, the proxy
   serves HTTPS on `AIRLOCK_HOST:AIRLOCK_PORT` (litellm/uvicorn ssl passthrough).
2. When unset, the proxy serves plain HTTP exactly as today (no behavior change).
3. The existing "TLS at a reverse proxy" deployment remains supported and
   documented.
4. Token-based auth (UN-11) is documented as requiring TLS on any non-loopback
   bind (bearer credentials must not traverse plaintext).

---

## UN-13: Per-Request Guardrail Skip for Trusted Clients

**As a** platform engineer running a trusted internal workload (e.g., a
benchmark),
**I need** to downgrade specific guardrails for specific requests,
**so that** a known-safe client can run without a global guardrail change that
would weaken protection for everyone else.

> Traces to `dev/notes/design-admin-api-capability-auth.md` (§8).

### Stakeholders

- Platform engineers
- Information security (sets policy)

### Acceptance Criteria

1. A capability token scope (`guardrail:skip:<name>`) presented in the
   `X-Airlock-Capability` header downgrades that guardrail's effective mode for
   that request only.
2. "Skip" defaults to **downgrade-to-observe** (the guardrail still scans and
   logs) rather than full disable; full disable is a separately configured,
   higher-privilege capability.
3. PII redaction is **non-skippable** by default.
4. A skip can never disable provider-protection (the breaker) or re-enable
   fallbacks — those are operator-config, not client-grantable (see CC-10).
5. The mechanism is off by default (`allow_capability_skip: false`); normal
   clients are unaffected and send no new headers.
6. The future batch path consumes the same resolver; this need ships for the
   interactive path first.

---

## UN-14: No Self-Inflicted Quarantine Storms

**As a** developer running large or bursty workloads,
**I need** Airlock's breaker to quarantine a provider only after repeated genuine
rate-limit signals, not on a single 429,
**so that** a handful of upstream 429s do not amplify into a sustained,
self-inflicted outage.

> Traces to `dev/notes/design-circuit-breaker-per-client.md` (A1).

### Stakeholders

- Developers running large-context / batch jobs
- Operations

### Acceptance Criteria

1. A client→provider pair is quarantined only after `rate_limit_threshold` 429s
   within `rate_limit_window_seconds` (default threshold 1 preserves today's
   behavior).
2. A pre-call quarantine block does **not** feed the breaker's failure counter
   (the no-re-arm invariant), locked by a regression test.
3. The arming counter respects the "cleared floor" so an operator clear cannot be
   undone by pre-clear history (CC-6).
4. With no config, behavior is identical to today.

---

## UN-15: Per-Client Breaker Tuning

**As an** operator,
**I need** to tune breaker threshold, cooldown, and escalation per client key,
**so that** a trusted batch client can be granted a looser breaker without
loosening protection for everyone, and one client's 429s do not quarantine the
provider for all.

> Traces to `dev/notes/design-circuit-breaker-per-client.md` (E).

### Stakeholders

- Operations
- Platform engineers

### Acceptance Criteria

1. A per-client policy supplies `{rate_limit_threshold, cooldown_seconds,
   escalation_exempt, disabled}`, with precedence per-client → default →
   constant.
2. `escalation_exempt` clients do not count toward provider-wide escalation.
3. Policy is read once at startup from `airlock_settings.circuit_breaker` plus an
   `AIRLOCK_BREAKER_OVERRIDES` env override.
4. Client identity is the existing `client_id` (CC-1); no new identity concept.

---

## UN-16: Correct Client Backoff Signaling

**As a** developer whose client retries on rate limits,
**I need** Airlock to return a typed, OpenAI-compatible 429 with a `Retry-After`
header when it blocks a request,
**so that** my client backs off correctly instead of hammering a quarantined
provider or mis-recording empty responses.

> Traces to `dev/notes/design-rate-limit-client-errors.md` (B).

### Stakeholders

- Developers / client authors
- Operations

### Acceptance Criteria

1. Airlock breaker blocks raise a typed `AirlockProviderBlocked(RateLimitError)`
   distinguishable from a passthrough provider 429 without string-parsing.
2. Every rate-limit response carries `Retry-After` (breaker cooldown for Airlock
   blocks; provider reset/`Retry-After` for passthrough) and triage headers
   (`X-Airlock-Provider-State`, `X-Airlock-Block-Scope`).
3. The response body stays OpenAI-shaped, enriched (new `type`, `airlock`
   sub-object) without breaking existing parsers (CC-4).
4. Status code is 429 for both cases.

---

## UN-17: Provider Quota Observability

**As an** operator,
**I need** to see upstream rate-limit headroom and spend-vs-cap per provider,
**so that** 429s stop being surprises and I can act before a provider is
exhausted.

> Traces to `dev/notes/design-provider-quota-observability.md` (C).

### Stakeholders

- Operations
- Finance (spend visibility)

### Acceptance Criteria

1. `x-ratelimit-*` headers are captured on both success and 429 failure and
   tracked per provider in state.
2. Headroom and spend-vs-cap are surfaced via metrics gauges, structured logs,
   and the TUI.
3. The feature is observe-only — it changes no request behavior (CC-5).
4. Optional response passthrough of headroom headers is behind a default-off
   flag.

---

## UN-18: Bounded Fallback and Budget Blast-Radius

**As an** operator,
**I need** fallbacks suppressed for large or rate-limited requests and the daily
budget cap made visible,
**so that** a single incident does not fan a large payload across providers or
hit a silent spend cliff.

> Traces to `dev/notes/design-routing-fanout-guardrails.md` (A2 + A3).

### Stakeholders

- Operations
- Finance
- Developers

### Acceptance Criteria

1. Requests above a prompt-size threshold, or targeting a quarantined provider,
   have fallbacks suppressed and fail fast with the UN-16 typed 429.
2. Rate-limit/quota errors never fall back across providers (same-provider only).
3. When a fallback is used, the answering model is annotated and surfaced via
   `X-Airlock-Model-Override`.
4. The daily provider budget cap is observable and warns at ≥80% before the
   cliff. Defaults preserve today's behavior except the new warning (CC-3).
