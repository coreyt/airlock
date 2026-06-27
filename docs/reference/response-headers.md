# Response Headers Reference

Airlock adds `X-Airlock-*` headers to responses so clients and operators can see
what the control plane did — which backend served, what changed, and the state of
rate-limit/budget protections. This page is the catalog; see architecture §3.7 for
the internals.

All header values are CR/LF-stripped before emission, and value content is gated by
an allowlist (see [`X-Airlock-Mutations`](#x-airlock-mutations)) — request/response
**content is never placed in a header**.

## Transparency headers (default-on)

These come from the [transparency layer](../guide/observability.md) and ship on by
default; tune them with the `transparency:` config block.

### `X-Airlock-Served-By`

The provider that **actually** served the response, read from the response's
`_hidden_params` (`custom_llm_provider`), not inferred from the model name. Omitted
when the served provider can't be determined.

```
X-Airlock-Served-By: vertex_ai
```

This is the observed counterpart to the pre-call *inference* surfaced by
[`X-Airlock-Model-Override`](#protection-state-headers): `X-Airlock-Model-Override`
tells you the final *model alias* Airlock routed/failed-over to; `X-Airlock-Served-By`
tells you the *backend* that answered (e.g. `anthropic` vs `bedrock` vs `vertex_ai`
for the same logical model). See
[Routing → Fallbacks](../guide/routing.md#fallbacks) and
[Observability → Served vs. inferred](../guide/observability.md#served-vs-inferred-un-20).

### `X-Airlock-Served-Region`

The served region, added only when the backend reports one (Bedrock / Vertex).

```
X-Airlock-Served-Region: us-east5
```

### `X-Airlock-Mutations`

A compact, byte-bounded summary of the mutation ledger. Tokens are
`;`-separated `field=…`:

```
X-Airlock-Mutations: reasoning_effort=minimal;model=claude-sonnet;fallbacks=suppressed;messages=redacted(3)
```

Rendering rules:

- `field=value` — only for allowlisted scalar/enum fields (`model`,
  `reasoning_effort`, `fallbacks`, `num_retries`) on `set`/`clamp`/`rewrite`.
- `field=<op>` — every other field/op (e.g. `system=inject`, `messages=rewrite`).
  Content is never surfaced.
- `field=redacted(N)` — value-free redaction count.
- `field=suppressed` — suppression (e.g. `fallbacks=suppressed`).
- `…+N more` — appended when the value exceeds `mutation_header_budget_bytes`
  (default 256); the full ledger is in the JSONL log.

Controlled by `transparency.mutation_headers` (`off` | `compact` | `full`,
default `compact`). See
[Observability → Response headers](../guide/observability.md#response-headers).

## Protection-state headers

| Header | Meaning | Emitted when |
|---|---|---|
| `X-Airlock-Model-Override` | final model alias when Airlock routed or failed over (unpinned requests only) | a routing/failover swap occurred — incl. a proactive budget swap at `budget_warn_ratio` |
| `X-Airlock-Budget-State` | `near_limit` | a provider is at ≥ `budget_warn_ratio` (default `0.8`, env `AIRLOCK_BUDGET_WARN_RATIO` / `airlock_settings.budget_warn_ratio`) of its daily cap |
| `X-Airlock-Provider-State` | `quarantined` (breaker) or a Gemini output-shape marker | breaker block, or Gemini shape signalling |
| `X-Airlock-Block-Scope` | scope of a breaker block — `provider` or `client_provider` | a circuit-breaker 429 |
| `Retry-After` | client backoff seconds | a 429 (breaker cooldown or upstream provider reset) |

See [Rate Limiting & the Circuit Breaker](../guide/rate-limiting.md) for the full
429 contract and [Routing](../guide/routing.md) for the override/budget story.

## Request headers Airlock consumes

| Header | Effect |
|---|---|
| `X-Airlock-Explain: 1` | Opt into the additive `airlock.mutations` response-**body** envelope (non-streaming only; default body is unchanged). Header name configurable via `transparency.explain_body_optin_header`. |
| `X-Airlock-Capability` | Guardrail-skip JWT — downgrades the granted guard(s). See [Guardrails](../guide/guardrails.md). |
| `X-Airlock-Client` | Unauthenticated attribution only — **carries zero authorization**. |
