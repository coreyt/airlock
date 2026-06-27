# Observability & Transparency

Plain LiteLLM (or any thin proxy) is a black box: it normalizes parameters, routes
between models, scrubs PII, and fails over between providers — but the client and
the operator can rarely tell *what changed* or *which backend actually answered*.
Airlock is a control plane, and a control plane you cannot see into is just a more
expensive black box. So Airlock makes two things first-class, default-on benefits:

1. **A mutation ledger** — every change Airlock makes to a request is recorded in
   one ordered list and surfaced (UN-19).
2. **Served-backend attribution** — the backend that *actually* served a response
   is read from the response itself, not guessed from the model name (UN-20).

Both ride the transparency layer (`airlock/transparency.py`). This is
**observe-only**: it changes what Airlock *reports*, never whether a request is
mutated or where it goes. (See architecture §3.7 for the internals.)

## The mutation ledger

Every site that mutates a request appends exactly one record to a single ordered
list, `metadata["airlock_mutations"]`. This is the **one-ledger principle**: rather
than scattering ad-hoc `airlock_*` keys (which is exactly why surfacing used to be
inconsistent), there is one normalized, authoritative view. The legacy keys
(`airlock_routing`, `airlock_alias`, `airlock_pii_map`, …) still exist for
back-compat, but the ledger is the canonical record.

Each record is a `Mutation`:

| Field | Meaning |
|---|---|
| `field` | what changed — `reasoning_effort`, `model`, `messages`, `fallbacks`, … |
| `op` | one of `set`, `drop`, `clamp`, `rewrite`, `inject`, `redact`, `suppress` |
| `before` / `after` | prior and new value (omitted for `inject`; **always absent** for `redact`) |
| `stage` | `pre_call`, `during_call`, or `post_call` |
| `source` | the recording site, e.g. `reasoning_effort.normalize`, `router.cost_tier`, `pii_guard` |
| `reason` | human-readable explanation, e.g. `openai has no 'off' enum; floored to minimal` |
| `count` / `category` | **redact-only**, value-free — e.g. `count: 3`, `category: pii` |

### What gets recorded

The audit covered roughly 30 mutation sites across these categories — all now land
in the ledger:

| Category | Example | `op` |
|---|---|---|
| `reasoning_effort` normalization | provider has no `off`; floored to `minimal` | `set` / `clamp` |
| Routing / cost-tier / budget swaps | `smart` or directive picks a different alias | `rewrite` |
| Model alias resolution | `claude-sonnet` → concrete deployment alias | `rewrite` |
| Failover / fallback suppression | large prompt or quarantined target ⇒ no fan-out | `suppress` |
| `drop_params` removal | client param the provider can't accept is dropped | `drop` |
| System-prompt injection | enhanced profile prepends a system prompt | `inject` |
| PII redaction | N spans scrubbed from messages / MCP args | `redact` |
| Reasoning-block strip | post-call reasoning content removed | `rewrite` |
| Gemini mode → `reasoning_effort` | Gemini thinking mode mapped to effort | `set` |

A note on `drop_params`: there is no Airlock call-site for it (the removal happens
inside LiteLLM), so it is **derived**. Pre-call, after the provider is resolved,
Airlock compares the client's params against
`litellm.get_supported_openai_params(model, custom_llm_provider)` and records each
unsupported one as an `op: drop` mutation with reason
`provider-unsupported (drop_params)`.

## Response headers

Three headers surface the transparency data by default. They are additive to the
existing `X-Airlock-*` catalog — see the
[response-header reference](../reference/response-headers.md) for the full list.

### `X-Airlock-Served-By` and `X-Airlock-Served-Region`

The provider that actually served the response, read from the response (not
inferred). Region is added only when the backend reports one (Bedrock / Vertex).

```
X-Airlock-Served-By: vertex_ai
X-Airlock-Served-Region: us-east5
```

When the served provider can't be determined (a pre-call block, a transport error,
or an unknown provider at header-flush time), the header is **omitted rather than
guessed**.

### `X-Airlock-Mutations`

A compact, allowlist-safe, byte-bounded summary of the ledger:

```
X-Airlock-Mutations: reasoning_effort=minimal;model=claude-sonnet;fallbacks=suppressed;messages=redacted(3)
```

Three rules make this safe to ship on by default:

- **Allowlist for values (CC-T2).** A token renders as `field=value` **only** for
  scalar/enum fields on the allowlist `HEADER_VALUE_FIELDS = {model,
  reasoning_effort, fallbacks, num_retries}`, and only for `set` / `clamp` /
  `rewrite` ops. Every other field or op renders as **`field=<op>`** —
  e.g. an injected system prompt is `system=inject`, a rewritten message body is
  `messages=rewrite`. **Content is never placed in a header.**
- **Value-free redaction.** Redactions render as `field=redacted(N)` (the count
  only), and suppression as `field=suppressed`. The `Mutation` constructor
  *rejects* a `before`/`after` on a `redact` record, so a matched secret can never
  reach the ledger, let alone a header.
- **Byte-bounded.** The serialized value is capped at
  `mutation_header_budget_bytes` (default 256). Past the budget it truncates to the
  leading tokens that fit plus a `…+N more` suffix; the **full** ledger is always
  in the JSONL log.

Set `transparency.mutation_headers: off` to suppress the header entirely, or
`full` to disable the compaction allowlist semantics described above (`compact` is
the default).

### `X-Airlock-Explain: 1` — opt-in body envelope

Send the request header `X-Airlock-Explain: 1` to additively attach the full
ledger to the response **body** under an `airlock.mutations` envelope. This is
**non-streaming only** — an SSE stream's headers are already flushed and the chunk
schema can't carry a trailing object without breaking clients (for streams, use the
headers + the JSONL log instead). **Without this header the response body is
byte-for-byte unchanged.** The request-header name is configurable via
`transparency.explain_body_optin_header`.

## Served vs. inferred (UN-20)

Airlock keeps two distinct provider facts, and the distinction matters:

- **`airlock_provider`** is an *inference* — `infer_provider(model_name)` runs
  **before** the call, from the requested alias. It is required for routing and
  policy (those must decide before a response exists), but it is a prediction.
- **`served`** is *observed truth* — `attribute_served_backend(response)` reads the
  response's `_hidden_params` after the fact: `custom_llm_provider`, `api_base`
  (host only), `region_name`, `model_id`, `response_cost`, and a derived
  `backend_kind` (`native` for anthropic/openai/gemini, `gateway` for
  bedrock/azure/vertex_ai, else `unknown`).

The same logical model can be served by different backends — Anthropic native vs
AWS Bedrock vs GCP Vertex; OpenAI native vs Azure; Google via Vertex AI vs AI
Studio. The inference cannot tell these apart; the served read can. Each request
record carries both, plus `attribution: "served" | "inferred"` to say which one is
authoritative for that request (`inferred` when the served read was unavailable).

### Accounting follows served

Because the served read is the truth, spend and quarantine accounting key off it.
On a **successful** response, spend is taken from `served.response_cost` and
attributed to `served.provider` — so a same-provider failover or a deployment swap
is billed to whoever actually answered, not to the inferred guess. On a
**failure** path (often no response, no `_hidden_params`), accounting falls back to
the provider parsed from the error / provider-429 metadata, else the inferred
provider — it never blocks on a served read that doesn't exist. Accounting
attributes to the **final** served response, not to intermediate fallback attempts.

This is gated behind `transparency.attribute_accounting_to_served` (default
**on**, documented as a bugfix). If you built dashboards on the old
inferred-provider keying, set it to `false` to restore the previous behavior.

## Where to see it

- **JSONL request records** carry three new fields alongside the existing
  `airlock_provider`:
  - `mutations` — the full ledger (untruncated, including post-call entries).
  - `served` — the `ServedBackend` block (`provider`, `api_base_host`, `region`,
    `model_id`, `response_cost`, `backend_kind`).
  - `attribution` — `"served"` or `"inferred"`.

  ```jsonc
  {
    "record_type": "request",
    "airlock_provider": "anthropic",        // inferred, kept
    "served": {
      "provider": "bedrock",
      "api_base_host": "bedrock-runtime.us-east-1.amazonaws.com",
      "region": "us-east-1",
      "model_id": "anthropic.claude-...",
      "response_cost": 0.0123,
      "backend_kind": "gateway"
    },
    "attribution": "served",
    "mutations": [
      {"field": "reasoning_effort", "op": "set", "before": "off", "after": "minimal",
       "stage": "pre_call", "source": "reasoning_effort.normalize",
       "reason": "openai has no 'off' enum; floored to minimal"},
      {"field": "messages", "op": "redact", "count": 3, "category": "pii",
       "stage": "pre_call", "source": "pii_guard"}
    ]
  }
  ```

- **Prometheus** — install the metrics extra and add the callback (see
  [Operations → Monitoring](../operations.md#monitoring)). Airlock exports:

  | Metric | Type | Labels | Meaning |
  |---|---|---|---|
  | `airlock_requests_total` | counter | `model`, `status` | requests handled |
  | `airlock_request_duration_seconds` | histogram | `model` | end-to-end latency |
  | `airlock_pii_redactions_total` | counter | `entity_type` | PII entities redacted |
  | `airlock_keyword_blocks_total` | counter | — | keyword-guard blocks |
  | `airlock_threat_blocks_total` | counter | — | threat-detector blocks |
  | `airlock_response_scan_detections_total` | counter | — | response-scanner detections |
  | `airlock_mutations_total` | counter | `field`, `op` | mutations by field and op |
  | `airlock_circuit_breaker_state` | gauge | `model` | breaker state (0 closed / 1 open / 2 half-open) |
  | `airlock_provider_ratelimit_remaining_tokens` | gauge | `provider` | upstream token headroom |
  | `airlock_provider_ratelimit_remaining_requests` | gauge | `provider` | upstream request headroom |
- **TUI** — the Overview screen gains a **"Served via"** column, so the
  native-vs-gateway split (and same-provider failover) is visible at a glance. See
  [TUI Dashboard](tui.md).

## Streaming behavior

Streaming forces a split surface, because HTTP headers flush *before* the SSE body:

- **In the headers (pre-call):** identity (`X-Airlock-Served-By` / `-Region`) and
  the pre/during-call mutations (`X-Airlock-Mutations`) are emitted in the
  response-headers hook, which fires for streams too. On a stream the served
  provider is read from the wrapper attribute (`response.custom_llm_provider`),
  since it isn't in `_hidden_params` yet at flush time; if it's still unknown, the
  header is omitted.
- **In the log (post-call):** `response_cost` and post-call mutations (e.g. the
  reasoning-strip) are only final at stream end, so the **full** `served` block
  (with cost) and the complete ledger are finalized into the JSONL record on the
  assembled response.
- The `X-Airlock-Explain` body envelope is **not** produced for streams.

## Configuration reference

The `transparency:` block in `config.yaml` (all keys are backward compatible —
omit the block to take the defaults):

| Key | Default | Effect | Opt-out |
|---|---|---|---|
| `mutation_headers` | `compact` | `off` \| `compact` \| `full` — whether/how `X-Airlock-Mutations` is emitted | `off` to suppress the header |
| `served_headers` | `true` | Emit `X-Airlock-Served-By` / `-Region` | `false` to suppress |
| `explain_body_optin_header` | `X-Airlock-Explain` | Request header that opts into the body envelope | rename, or simply don't send the header |
| `attribute_accounting_to_served` | `true` | Key spend/quarantine off the served provider (bugfix) | `false` restores inferred-provider keying |
| `mutation_header_budget_bytes` | `256` | Max byte length of `X-Airlock-Mutations` before `…+N more` truncation | raise/lower as needed |

```yaml
transparency:
  mutation_headers: compact          # off | compact | full
  served_headers: true               # X-Airlock-Served-By / -Region
  explain_body_optin_header: X-Airlock-Explain
  attribute_accounting_to_served: true
  mutation_header_budget_bytes: 256
```

Invalid values fall back to the defaults with a logged warning, and a config-free
deploy behaves exactly as the table above.
