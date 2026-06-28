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

---

## UN-19: Transparent Request Mutations

**As a** developer (or operator) sending a request through Airlock,
**I need** Airlock to tell me every way it changed my request before it reached
the provider — which field, from what to what, and why,
**so that** observability is a *benefit* of routing through Airlock rather than a
black box I have to reverse-engineer, and so a surprising response (silently
lowered reasoning effort, a dropped parameter, a swapped model, a suppressed
fallback, an injected system prompt) is explainable from the response alone.

> Traces to `dev/notes/design-mutation-and-provider-transparency.md`.
> Motivated by the audit in that note: ~30 mutation sites across 7 categories,
> most silent to the client today (see the inventory table). The bar is "more
> observable than plain LiteLLM or any thin wrapper."

### Stakeholders

- Developers (debugging unexpected model behavior)
- Operators (auditing what the gateway does to traffic)
- Compliance (proving what left the building, e.g. PII redaction occurred)

### Acceptance Criteria

1. Every request-altering mutation is recorded in one canonical, ordered ledger
   (`metadata["airlock_mutations"]`): field, op (`set`/`drop`/`clamp`/`rewrite`/
   `inject`/`redact`/`suppress`), before→after, stage (`pre_call`/`during_call`/
   `post_call`), source component, and reason. The previously-silent mutations
   (reasoning-effort normalization, `drop_params`, alias resolution, fallback
   suppression, system-prompt injection, reasoning-strip) are all included.
2. The ledger is surfaced on **every** response by default: a compact
   `X-Airlock-Mutations` header naming the changed fields, the full ledger in the
   JSONL request record (`mutations: [...]`), and per-type counters in metrics/TUI.
3. Surfacing never leaks content: redaction mutations record the field and a
   **count/category**, never the redacted value (enforced at construction); and the
   header serializer surfaces an after-value only for an allowlist of scalar/enum
   fields (model, reasoning_effort, fallbacks, num_retries) — injected system
   prompts and rewritten message bodies render as `field=<op>` with no content. The
   header is size-bounded and degrades to a `…+N more` summary with full detail in
   the log.
4. A non-streaming response whose request opted in (`X-Airlock-Explain: 1`)
   additionally receives a structured `airlock.mutations` block in the response body
   envelope (non-breaking, additive). For streaming responses the envelope is
   omitted (the data is in the headers + the JSONL log), since SSE headers are
   already flushed and the chunk schema cannot carry a trailing envelope.
5. Transparency is observe-only: it changes no request behavior and is governed by
   `transparency.*` config that is backward compatible (config-free deploys behave
   as before, plus the new default-on headers/logs).

---

## UN-20: Truthful Serving-Backend Attribution

**As an** operator running models that are reachable through more than one
backend (Anthropic native vs AWS Bedrock vs GCP Vertex; OpenAI native vs Azure;
Google via Vertex AI vs AI Studio),
**I need** Airlock to report which backend *actually* served each request — not a
guess derived from the model name,
**so that** spend, rate-limit, and quarantine accounting are attributed to the
real provider, and so failovers or router deployment choices between backends are
visible rather than hidden.

> Traces to `dev/notes/design-mutation-and-provider-transparency.md`.
> Root cause: `airlock_provider` is computed by `infer_provider(model_name)`
> pre-call (`guardian.py:451`, `monitor.py:197`), while the ground truth already
> exists post-call in `response._hidden_params` (`custom_llm_provider`,
> `api_base`, `region_name`, `model_id`, `response_cost`) and is currently ignored.

### Stakeholders

- Operators (correct provider health/quarantine attribution)
- Finance (spend debited to the backend that actually billed)
- Developers (knowing whether they hit Vertex or AI Studio)

### Acceptance Criteria

1. After every successful call, Airlock extracts a truthful served-backend record
   from `response._hidden_params`: `provider` (`custom_llm_provider`),
   `api_base` host, `region`, served `model_id`, and `response_cost`. This is
   stored distinctly from the pre-call inferred provider, so the record carries
   both *requested/inferred* and *served* attribution.
2. Native vs gateway backends are distinguished for the same logical model:
   `anthropic` vs `bedrock` vs `vertex_ai`; `openai` vs `azure`; and the Google
   ambiguity (`vertex_ai` vs `gemini`/AI Studio) is resolved via provider + the
   `api_base` host (`*-aiplatform.googleapis.com` vs `generativelanguage.googleapis.com`).
3. The served backend is surfaced by default via response headers
   (`X-Airlock-Served-By`, and where applicable `X-Airlock-Served-Region`) and in
   the JSONL record and TUI.
4. Spend and provider rate-limit/quarantine state are keyed off the **served**
   provider wherever it diverges from the inferred one (e.g. a same-provider
   failover or a router deployment swap), closing the mis-attribution gap.
5. `infer_provider()` remains the basis for *pre-call* policy/routing decisions
   (it is a prediction) but is no longer treated as the authoritative record of
   what served the request. Where served truth is unavailable (errors before a
   response), the record is explicitly marked as inferred-only.

---

## UN-21: Discoverable Provider Selection

**As a** client/researcher choosing where a model runs (AI Studio vs Vertex;
native vs a gateway),
**I need** to enumerate the catalog and see, per model, the serving provider and
region without parsing the model id, pin a specific provider by a stable name,
and verify it actually served,
**so that** "which provider, and did it serve?" is answerable from data, not from
a confusing alias suffix.

> Traces to `dev/notes/design-provider-naming-and-capability-discovery.md`
> (as-built, shipped 0.5.2). Root cause: the catalog used three inconsistent
> naming conventions that conflated *provider/quota* (AI Studio vs Vertex) with
> *API surface* (sync vs batch) and encoded neither discoverably — a researcher
> pinned `gemini-3.5-flash-aistudio`, watched it behave differently from
> `gemini-3.5-flash`, and concluded it was "a separate broken deployment."

### Stakeholders

- Clients/researchers (pick and pin a provider by a stable name)
- Operators (a default that can be re-pointed without breaking the client contract)
- Finance/observability (served provider is answerable from data, not the id string)

### Acceptance Criteria

1. `GET /v1/models` and `GET /model/info` expose, per model, an airlock
   capability object (`airlock_provider`, `region`, `endpoints`, `underlying`,
   `deprecated`). On `/model/info` the record is merged into `model_info`; on
   `/v1/models` it is folded under an additive `airlock` object (OpenAI-compat
   preserved).
2. A `provider/model` alias (`aistudio/…`, `vertex/…`, `anthropic/…`, …) pins
   that provider and is auto-pinned (fallbacks/retries off → 429-on-overload,
   never a silent model swap).
3. The served provider is verifiable post-call via `X-Airlock-Served-By` (and
   `X-Airlock-Served-Region` for gateway/region backends); it equals the
   `airlock_provider` discovered in step 1 (`aistudio` → `gemini`, `vertex` →
   `vertex_ai`).
4. The bare name (e.g. `gemini-3.5-flash`) remains a documented, ops-repointable
   **default**; the prefixed name is the stable client contract.

---

## UN-22: Declared Capabilities

**As a** client selecting a batch-capable deployment,
**I need** per-model `endpoints` (chat/batch) published and **provably matching
the real routing**,
**so that** I pick a batch deployment from data, not by guessing from the id
string.

> Traces to `dev/notes/design-provider-naming-and-capability-discovery.md`
> (as-built, shipped 0.5.2). `endpoints` is computed from the real wiring by one
> helper (`airlock/capability.py:endpoints_for`), so published capability cannot
> drift from routing.

### Stakeholders

- Clients (pick a batch deployment from declared capability)
- Operators (capability cannot silently disagree with routing)

### Acceptance Criteria

1. `endpoints` is published on `/model/info` + `/v1/models`.
2. `batch ∈ endpoints` **iff** the entry is gateway-batch-marked
   (`airlock_batch`) OR a regionally-located Vertex model (`vertex_ai/` with a
   non-`global` `vertex_location`) — a config-consistency test enforces this so
   published capability cannot drift from routing. The shipped Vertex entries use
   `vertex_location: global`, so they are **chat-only** (no batch advertised).
3. Capability-in-the-name is gone: the `-batch`/`-aistudio` twins are
   consolidated (one `provider/model` entry serves sync and advertises batch);
   capability is read from `endpoints`, never the suffix. The legacy suffix twins
   carry `deprecated: true` and are removed in 0.6.0.

---

## UN-25: Unified Settings Precedence

**As an** operator configuring Airlock,
**I need** every `fast/` runtime setting to have one typed home and one uniform
`env > config > default` precedence rule, with no hidden hardcoded defaults that
silently override `config.yaml`,
**so that** what I write in `config.yaml` is what the proxy actually does — one
concept never reads from three disagreeing sources.

> Traces to `dev/plans/0.5.1-plan.md` (register R1–R6) and
> `dev/notes/architecture-audit-0.5.0-2026-06.md` (Part 1, budget triple-source).
> Motivating incident (2026-06-24): `provider_budget_config: 0` silenced LiteLLM's
> hard block and the monitor warn, but the fast router kept swapping Gemini away at
> a hardcoded `$25/day` it read from `_DEFAULT_PROVIDER_BUDGETS` — three subsystems,
> one concept, three sources.

### Stakeholders

- Operators (config is authoritative and predictable)
- Finance (budget behavior matches the configured numbers)
- Developers (one precedence rule to reason about)

### Acceptance Criteria

1. A single typed settings object (modeled on `transparency.py` `TransparencyConfig`)
   reads each `fast/` setting in place with uniform `env > config > default`
   precedence, including malformed-input fallback.
2. The hardcoded `_DEFAULT_PROVIDER_BUDGETS`, `_DEFAULT_FAILOVER_MAP`, and the dual
   `0.8`/`0.9` warn-ratio constants are removed; budgets and failover derive from
   `config.yaml` (`router_settings.provider_budget_config` / `router_settings.fallbacks`).
3. The monitor reads budgets from the correct `router_settings` nesting (R6 fix);
   a budget set there is captured by `configure_budgets` (today it reads top-level
   and is always empty).
4. A circuit-open failover lands on a model that actually exists in `model_list`
   (R2 fix; today the stale `gpt-4o` default targets a model the proxy doesn't serve).
5. `0 ⇒ no enforcement` is preserved and documented identically across all three
   layers (LiteLLM hard block, monitor warn, router proactive swap).
6. An empty `airlock_settings:` reproduces today's behavior for the **non-budget**
   settings (`session_ttl`, `smart_thresholds`, `cost_tiers`, warn-ratio); provider
   budget auto-swap defaults are intentionally **not** preserved (documented behavior
   change — auto-swap now requires an explicit `provider_budget_config`).

---

## UN-26: Accurate, Durable Provider-Spend Accounting

**As an** operator relying on budget warnings and proactive cost-swaps,
**I need** provider spend accounted accurately regardless of call volume and
preserved across a proxy restart,
**so that** the busy providers budgets exist to protect are not silently
undercounted, and a restart does not zero an accumulated daily spend total.

> Traces to `dev/plans/0.5.1-plan.md` (register R5, STORE-seam FIX-1…FIX-7) and
> the architecture audit. The pre-0.5.1 `deque(maxlen=1000)` was a true sliding
> 24h window but count-capped — a provider doing >1000 billed calls/day dropped
> in-window records and undercounted exactly where budgets matter — and it zeroed
> on restart.

### Stakeholders

- Operators (accurate spend visibility and durable counters)
- Finance (no undercount on high-volume providers)

### Acceptance Criteria

1. Spend is tracked in a rolling, time-windowed accumulator (timestamped buckets
   pruned by age), so trailing-24h spend is accurate regardless of call volume —
   a >1000-call/day provider no longer undercounts (R5 fix).
2. Spend survives a proxy restart: it is checkpointed to disk and rehydrated on
   start, proven by an **end-to-end subprocess restart test** (start → record spend
   → stop → start → assert restored), not only an in-process unit test.
3. Checkpoint/restore runs in the **litellm child process** where the store is
   actually mutated (FIX-1), not in the launcher process (which checkpoints an
   empty store today).
4. The checkpoint has defined semantics: versioned schema, atomic write
   (temp + `os.replace`), prune-before-checkpoint, and idempotent replace-not-append
   restore bounded by record age (only in-window records rehydrated).
5. The store sits behind an Airlock-owned interface backed by an in-memory
   `DualCache` (single-process; spend stored as integer µ$ for `INCR`/Redis-flip
   compatibility; explicit per-key TTL ≥ the rolling window; `_lock` held around
   read-modify-write). Redis backend and `--num_workers` are deliberately deferred.
6. `cb_state.json` circuit-breaker recovery still round-trips on the same
   (now correctly process-located) checkpoint path.

## UN-27: Predictable Latency Under Concurrency

**As an** operator running Airlock under concurrent load with PII redaction
enabled,
**I need** the request pipeline not to serialize behind synchronous PII analysis,
**so that** N concurrent requests do not pay N× the single-request latency and
tail latency stays bounded when `AIRLOCK_PII_ENABLED`.

> Traces to `dev/plans/0.5.3-plan.md` and the architecture audit
> (`dev/notes/architecture-audit-0.5.0-2026-06.md`, Part 3). Presidio's
> `analyzer.analyze` (`pii_guard.py`) runs **synchronously inside an `async def`**
> pre-call hook, blocking the event loop ~50–200 ms/request and serializing
> concurrency. This is the single verified hot-path latency hazard; the fix is
> behavior-preserving (redaction output unchanged) — only the threading changes.

### Stakeholders

- Operators (predictable tail latency under load with PII enabled)
- End users (lower latency when the proxy is busy)

### Acceptance Criteria

1. Presidio analysis is offloaded off the event loop (e.g. `asyncio.to_thread`),
   so N concurrent `AIRLOCK_PII_ENABLED` requests **no longer serialize** —
   wall-clock ≪ N × single-request latency (or an event-loop-not-blocked
   interleaving probe demonstrates the same).
2. Redaction output is **byte-identical** to the pre-change behavior for the same
   input (the offload is purely a threading change, not a semantic one).
3. Single-request latency is unchanged (no regression for the uncontended path).
4. Request text is extracted **once per request** and reused by the keyword and
   guardian guards; the cache reflects the **post-PII-redaction** text (order
   subtlety pinned in tests).
5. The local-vLLM `/models` capability probe widens its cache TTL and prewarms on
   startup without regressing first-request correctness for non-vLLM aliases
   (which never hit that path).

---

## UN-28: One Canonical Request Event Behind Every Telemetry Sink

**As an** operator and maintainer of Airlock's observability,
**I need** each per-request telemetry record to be built **once** into a single
canonical event and fanned out to every sink, rather than re-derived independently
in each logger,
**so that** a new field or a fix lands in one place instead of four-plus, the sinks
cannot silently drift, and one failing sink cannot break the request or the others.

> Traces to `dev/plans/0.5.4-plan.md` and the architecture audit
> (`dev/notes/architecture-audit-0.5.0-2026-06.md`, Part 2 telemetry row ★★
> "Weakest", and Tier 3 #8). Today the same record is derived independently across
> three distinct `_build_record()` builders (enterprise/`AirlockLogger`, s3, sql),
> a fathom projection that reuses the enterprise builder, plus a separate mutation
> ledger and metrics counters — the fields agree only by convention and have already
> drifted. The fix is a **behavior-preserving structural refactor**: source the
> record once into a canonical `RequestEvent` and let each sink **project** its
> historical subset; the wire/log/metrics output is unchanged. Design:
> `dev/notes/design-request-event-bus.md`.

### Stakeholders

- Operators (telemetry stays consistent; a failing sink is contained, not fatal)
- Maintainers (one typed contract to extend instead of four-plus to keep in sync)

### Acceptance Criteria

1. A single canonical `RequestEvent` is built **once per request** and dispatched
   to every telemetry sink (enterprise, fathom, s3, sql) plus the mutation ledger
   and per-request metrics; no sink derives its own record (the three
   `_build_record()`s and the fathom builder-reuse are deleted).
2. **Behavior-preserving:** every sink emits the **same fields and values** it emits
   today, proven by a per-consumer golden/field-for-field equivalence test
   (enterprise/fathom/s3/sql + mutation ledger + metrics, before vs after). s3 keeps
   its redaction + narrow set; sql keeps its JSON-string encoding; fathom keeps its
   env-flag-gated subset; s3/sql keep their bare `error` string.
3. **One dispatch seam with sink-failure isolation:** a sink that raises is caught,
   logged, and does **not** propagate to the request path or the other sinks;
   dispatch order is deterministic and test-pinned.
4. **No second source of truth and no measurable hot-path latency added:** the event
   is sourced once from existing request state (LiteLLM internals through the 0.5.3
   ACL), and one build + N projections is ≤ today's N independent builds.
5. The intended logged/served surface is **unchanged** — `dev/smoketest/` serves as
   the parity oracle (isolated-instance run before/after on a separate dir+port,
   live `:4000` untouched). The one accepted internal value change is **timestamp
   convergence** (the three independently-sampled per-builder timestamps collapse to
   one sourced-once value); it is recorded in the behavior-change register. Any other
   field-shape change must extend the smoke harness and be registered.
