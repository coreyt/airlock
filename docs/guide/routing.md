# Intelligent Routing

Airlock can choose or adjust the target model for a request before it
reaches the provider. Routing runs in the fast path (inside the Fast
Guardian, between threat assessment and the circuit breaker), so it adds
no measurable latency — the complexity classifier is pure string math
with no ML and no extra network calls.

There are two ways to use it:

1. **`model: "smart"`** — let Airlock pick a cost tier from prompt complexity.
2. **Routing directives** — pass explicit hints in `metadata.airlock`.

Both resolve to a concrete model alias from your `config.yaml`
`model_list`, and both record what happened under
`metadata.airlock_routing` for the logs and offline analyzer.

## Smart complexity routing (`model: "smart"`)

Send `model: "smart"` and Airlock classifies the prompt, maps it to a
cost tier, and routes to the first model in that tier.

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "smart",
    "messages": [{"role": "user", "content": "Refactor this module and explain the trade-offs..."}]
  }'
```

### How classification works

The classifier scores the concatenated user text from 0.0–1.0 using six
weighted features:

| Feature | Weight | Signal |
|---------|--------|--------|
| Token count | 0.30 | Longer prompts skew complex (sigmoid centered at ~85 words) |
| Code blocks | 0.25 | Fenced ` ``` ` or inline backticks |
| Reasoning keywords | 0.20 | `analyze`, `debug`, `optimize`, `trade-off`, `root cause`, … (saturates at 3 hits) |
| Multi-step indicators | 0.10 | Numbered/bulleted lists, "first/then/next/finally" |
| Vocabulary richness | 0.10 | Unique-word ratio (needs ≥10 words) |
| Sentence length | 0.05 | Average words per sentence (weak tiebreaker) |

The composite score maps to a complexity band, and each band maps to a
cost tier:

| Score | Complexity | Cost tier |
|-------|-----------|-----------|
| `< 0.30` | simple | `low` |
| `0.30 – 0.60` | moderate | `medium` |
| `≥ 0.60` | complex | `high` |

Empty or whitespace-only prompts fail safe to **moderate / medium**.

The chosen tier is injected as a `cost_tier` directive, so the same
tier-resolution logic used by explicit directives selects the model. The
classification (`complexity`, `score`, per-feature breakdown) is stashed
in `metadata.airlock_routing.smart_classify` for observability.

Tune the band thresholds with `AIRLOCK_SMART_THRESHOLDS` (a JSON
`[simple_max, complex_min]` pair, default `[0.30, 0.60]`):

```bash
AIRLOCK_SMART_THRESHOLDS='[0.25, 0.55]'   # route more aggressively to higher tiers
```

## Routing directives

Clients can pass directives directly in `metadata.airlock` on any
request (not just `smart`):

```json
{
  "model": "claude-sonnet",
  "messages": [{"role": "user", "content": "..."}],
  "metadata": {
    "airlock": {
      "session_id": "abc123",
      "cost_tier": "low",
      "prefer_provider": "anthropic"
    }
  }
}
```

Directives are applied in this order:

1. **Session affinity** (`session_id`) — the first request for a session
   resolves a model and pins it; subsequent requests with the same
   `session_id` reuse that model until the session TTL expires
   (`AIRLOCK_SESSION_TTL`, default 3600s). This keeps a conversation on
   one model even if other directives would move it.
2. **Cost tier** (`cost_tier`) — restrict to models in the named tier
   (`low` / `medium` / `high`, or `any` to skip). If the requested model
   isn't already in the tier, Airlock swaps to the first model listed in
   that tier.
3. **Provider preference** (`prefer_provider`) — a soft tiebreaker: if a
   candidate from the preferred provider exists in the (tier-filtered)
   pool, prefer it. Never forces a switch when no candidate qualifies.
4. **Budget awareness** — if the resolved provider is at ≥90% of its
   configured daily budget, proactively swap to an alternative provider
   that still has headroom. If every provider is near budget, the
   request stays put (and logs a warning).

Every applied step is appended to `metadata.airlock_routing.reasons`
(e.g. `cost_tier(low→claude-haiku)`, `session_pin(claude-sonnet)`,
`budget(openai@47.0/50.0→claude-sonnet)`).

## Cost tiers

Tiers map a tier name to an ordered list of model aliases. The first
alias in a tier is the default chosen when a swap is needed, so order
matters. The shipped defaults:

| Tier | Default aliases (in order) |
|------|----------------------------|
| `low` | `claude-haiku`, `gemini-flash`, `gemini-flash-lite`, `gpt-5-nano`, `mistral-small` |
| `medium` | `claude-sonnet`, `gemini-pro`, `gpt-5-mini`, `mistral-medium`, `codestral` |
| `high` | `claude-opus`, `gpt-5`, `gpt-5-pro`, `mistral-large`, `magistral-medium` |

Every alias referenced by a tier must exist in your `config.yaml`
`model_list`. Override the tiers per-deployment with a `cost_tiers:`
block in `config.yaml`:

```yaml
cost_tiers:
  low: ["claude-haiku", "gemini-flash"]
  medium: ["claude-sonnet"]
  high: ["claude-opus"]
```

…or with the `AIRLOCK_COST_TIERS` environment variable (JSON, takes
precedence over `config.yaml`):

```bash
AIRLOCK_COST_TIERS='{"low":["claude-haiku"],"medium":["claude-sonnet"],"high":["claude-opus"]}'
```

## Provider inference and budgets

Routing maps each alias to a provider **catalog-first**: any alias in
your `model_list` is mapped from its `litellm_params.model` prefix
automatically, so adding a model to `config.yaml` wires it into routing
and metrics with no code change. A static family-prefix heuristic
(`claude→anthropic`, `gpt→openai`, `gemini→gemini`, `mistral`/`codestral`/`magistral→mistral`,
`gemma→vllm`, `sonar→perplexity`, `tavily→tavily`) is the fallback for
aliases not present in the cached config.

Per-provider daily budgets drive the budget-awareness step. Defaults are
`anthropic` $50, `openai` $50, `gemini` $25, `mistral` $25,
`perplexity` $25. Override with `AIRLOCK_PROVIDER_BUDGETS` (JSON):

```bash
AIRLOCK_PROVIDER_BUDGETS='{"anthropic":100,"openai":75,"gemini":40}'
```

## Configuration reference

| Variable | Description | Default |
|---|---|---|
| `AIRLOCK_COST_TIERS` | JSON tier→aliases map; overrides the `cost_tiers:` config block | shipped defaults |
| `AIRLOCK_SMART_THRESHOLDS` | JSON `[simple_max, complex_min]` band cutoffs for `model: smart` | `[0.30, 0.60]` |
| `AIRLOCK_SESSION_TTL` | Seconds a `session_id` stays pinned to its model | `3600` |
| `AIRLOCK_PROVIDER_BUDGETS` | JSON provider→daily-budget map for budget-aware swaps | see above |

Routing decisions are recorded on every request under
`metadata.airlock_routing` and surfaced in the JSONL logs, so you can
audit how `smart` classified prompts and which directives changed a
model.
