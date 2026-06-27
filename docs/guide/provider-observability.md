# Provider Quota Observability

Airlock captures upstream **rate-limit headroom** from every provider response and
exposes it so you can see how close a provider is to its token/request ceiling
*before* it starts returning 429s — and how close it was afterward.

This is **observe-only**. Capturing headroom never changes routing, the circuit
breaker, or what reaches the client; it only surfaces what the provider already
told us.

## What's captured

Providers return per-call rate-limit headers (`x-ratelimit-*`, normalized across
providers by LiteLLM). On a successful response Airlock reads them from the response;
on a 429 it reads the same headers off the error — which gives the valuable
*exhausted* snapshot. Airlock keeps the latest values per provider in memory.

The captured values include remaining tokens and requests, their limits, and reset
hints.

## Prometheus gauges

Install the metrics extra (`pip install airlock-llm[metrics]`) and add the metrics
callback (see [Operations → Monitoring](../operations.md#monitoring)). Airlock then
exports:

| Gauge | Labels | Meaning |
|---|---|---|
| `airlock_provider_ratelimit_remaining_tokens` | `provider` | Tokens remaining against the provider's current rate-limit window. |
| `airlock_provider_ratelimit_remaining_requests` | `provider` | Requests remaining against the provider's current rate-limit window. |

These are set from the captured upstream `x-ratelimit-*` headers and updated on
every call.

Alerting suggestion: alert when `airlock_provider_ratelimit_remaining_tokens` drops
below a fraction of its observed ceiling — that's your early warning before the
provider starts rejecting calls.

## Budget headroom

Provider **daily-budget** spend is exposed alongside the rate-limit headroom as
companion gauges, so you can watch spend against the configured per-provider cap:

| Gauge | Labels | Meaning |
|---|---|---|
| `airlock_provider_budget_used_usd` | `provider` | USD spent against the provider's daily cap in the current window. |
| `airlock_provider_budget_limit_usd` | `provider` | The configured daily cap for the provider. |

These gauges and the near-limit warning are **opt-in**: they exist only for providers
with an explicit `router_settings.provider_budget_config` cap (or an
`AIRLOCK_PROVIDER_BUDGETS` override). As of 0.5.1 there are no hidden default budgets, so
with no budget configured there are no budget gauges, no warn, and no proactive swap.

The cap and the near-limit warning are described in
[Routing → Provider budgets](routing.md#provider-budgets). At `budget_warn_ratio`
(default `0.8`, env `AIRLOCK_BUDGET_WARN_RATIO` / `airlock_settings.budget_warn_ratio`) of
a provider's daily cap, Airlock logs a warning and tags near-limit responses with
`X-Airlock-Budget-State: near_limit` (the same threshold at which the router proactively
swaps providers).

## Where else to see it

- **TUI** — the Overview/Guards screens show a per-provider headroom line
  (remaining tokens/requests, % of limit, spend vs cap). See
  [TUI Dashboard](tui.md).
- **JSONL logs** — request records carry a compact `provider_ratelimit` block
  (`remaining_tokens`, `remaining_requests`, `reset_tokens`) so you can correlate
  headroom with specific requests after the fact.
