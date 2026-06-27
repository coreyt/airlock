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
4. **Budget awareness** — if the resolved provider is at or above
   `budget_warn_ratio` (default `0.8`, i.e. 80%) of its configured daily
   budget, proactively swap to an alternative provider that still has
   headroom. If every provider is near budget, the request stays put (and
   logs a warning). This is the same threshold the monitor warns at —
   unified in 0.5.1 (previously the swap point was a hardcoded 90%).

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

## Provider-explicit model names (0.5.2)

Every catalog entry has a **`provider/model`** alias whose prefix names the
provider that serves it. The prefix *is* the provider — there is one rule
everywhere:

| Prefix | Provider | Served-by token |
|---|---|---|
| `anthropic/` | Anthropic | `anthropic` |
| `openai/` | OpenAI | `openai` |
| `aistudio/` | Google AI Studio | `gemini` |
| `vertex/` | Google Vertex AI | `vertex_ai` |
| `mistral/` | Mistral | `mistral` |
| `perplexity/` | Perplexity | `perplexity` |
| `tavily/` | Tavily | `tavily` |
| `vllm/` | Local vLLM (OpenAI-compatible) | `openai` |

- `aistudio/` and `vertex/` are deliberately distinct from LiteLLM's native
  `gemini/` / `vertex_ai/` tokens, so the alias is **exact-matched** and never
  re-parsed as provider routing.
- The **bare** name (e.g. `gemini-3.5-flash`) is a documented, ops-repointable
  **default** (AI Studio for Gemini). The **prefixed** name is the stable client
  contract — pin it when you need a guaranteed provider.
- `smart` is a routing *directive*, not a provider model — it is never pinned and
  takes no prefix.

### Discover → pin → verify

The whole point is that "which provider, and did it serve?" is answerable from
**data**, not from guessing at an alias suffix.

1. **Discover.** `GET /v1/models` (or `GET /model/info`) → read each model's
   capability object: `airlock_provider`, `endpoints`, `region`, `underlying`,
   `deprecated`. On `/v1/models` it is an additive `airlock` sub-object (the list
   stays OpenAI-compatible); on `/model/info` the same fields are merged into
   `model_info`.

   ```bash
   curl -s http://localhost:4000/v1/models \
     -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
   | jq '.data[] | select(.id=="aistudio/gemini-3.5-flash") | .airlock'
   # -> {"airlock_provider":"gemini","endpoints":["chat","batch"],
   #     "underlying":"gemini/gemini-3.5-flash","region":null,"deprecated":false}
   ```

2. **Pin.** Send a concrete prefixed name as the model:

   ```json
   { "model": "aistudio/gemini-3.5-flash", "messages": [ … ] }
   ```

   A concrete prefixed name is **auto-pinned** — fallbacks and retries are off,
   so an overloaded provider returns a `429` rather than a silent swap to a
   different model.

3. **Verify.** Read [`X-Airlock-Served-By`](provider-observability.md#verifying-the-served-provider)
   on the response — `gemini` for AI Studio, `vertex_ai` for Vertex,
   `anthropic`/`openai`/… for the rest. It **equals** the `airlock_provider` you
   discovered in step 1. `X-Airlock-Served-Region` appears for gateway/region
   backends.

`endpoints` is computed from the real wiring by one helper
(`airlock/capability.py`), so a model advertises `batch` **iff** it is actually
batch-capable — see [Batch Processing](batch.md#which-aliases-batch) for the
capability map and the region-gated Vertex caveat.

## Provider inference and budgets

Routing maps each alias to a provider **catalog-first**: any alias in
your `model_list` is mapped from its `litellm_params.model` prefix
automatically, so adding a model to `config.yaml` wires it into routing
and metrics with no code change. A static family-prefix heuristic
(`claude→anthropic`, `gpt→openai`, `gemini→gemini`, `mistral`/`codestral`/`magistral→mistral`,
`gemma→vllm`, `sonar→perplexity`, `tavily→tavily`) is the fallback for
aliases not present in the cached config.

Per-provider daily budgets drive the budget-awareness step. As of 0.5.1
budgets are **purely config-driven** from
`router_settings.provider_budget_config` — there are **no hidden defaults**
(the old hardcoded `anthropic`/`openai` ≈ $50, `gemini`/`mistral`/`perplexity`
≈ $25 were removed). With no `provider_budget_config`, there is no proactive
swap and no budget warn; a `0` (or absent) budget means no enforcement. Set
caps in config, or override with `AIRLOCK_PROVIDER_BUDGETS` (JSON, takes
precedence):

```bash
AIRLOCK_PROVIDER_BUDGETS='{"anthropic":100,"openai":75,"gemini":40}'
```

## Fallbacks

When a request to a model fails, LiteLLM's `router_settings.fallbacks` can re-send
it to other models. For a large-context call each hop re-sends the full payload,
multiplying tokens and spend across providers — and can silently answer a request
from a different model than the client asked for. Airlock makes fan-out deliberate:

- **Large prompts.** When a request's estimated prompt size exceeds
  `AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS` (default `60000`), fallbacks are suppressed —
  Airlock fails fast with a descriptive [429](rate-limiting.md) instead of burning
  3× the tokens on retries.
- **Quarantined target.** When the resolved target provider is currently
  quarantined by the [circuit breaker](rate-limiting.md), fallbacks are suppressed
  rather than spreading the incident to another provider.
- **Pinned requests** keep today's behaviour (no fallback), as before.

When fallbacks are suppressed, Airlock records it in request metadata as
`airlock_fallback_suppressed`. When a fallback *is* used, the answering model is
surfaced via the `X-Airlock-Model-Override` header so the client knows a different
model responded.

`X-Airlock-Model-Override` tells you which *model alias* answered; its companion
[`X-Airlock-Served-By`](../reference/response-headers.md#x-airlock-served-by) tells
you which *backend* did (e.g. `anthropic` vs `bedrock` for the same model) — read
from the response rather than inferred. See
[Observability & Transparency](observability.md).

## Provider budgets

`router_settings.provider_budget_config` sets a per-provider daily spend cap (e.g.
$50/day for OpenAI and Anthropic). Once a provider hits its cap, LiteLLM stops
routing to it until the window rolls — a hard, deliberate stop. Airlock makes the
approach visible:

- At **`AIRLOCK_BUDGET_WARN_RATIO`** (default `0.8`, i.e. 80%) of a provider's
  `budget_limit`, Airlock logs a warning and tags responses with
  `X-Airlock-Budget-State: near_limit`, so clients and operators see the cliff
  coming.
- Spend against the cap is exported as the `airlock_provider_budget_used_usd` /
  `airlock_provider_budget_limit_usd` gauges — see
  [Provider Quota Observability](provider-observability.md).

This per-provider daily cap (a hard stop) is distinct from the budget-awareness
*routing swap* above, which proactively moves traffic at `budget_warn_ratio`
(default `0.8` / 80%, unified with the monitor warn) of a provider's configured
budget while headroom remains elsewhere.

## Configuration reference

| Variable | Description | Default |
|---|---|---|
| `AIRLOCK_COST_TIERS` | JSON tier→aliases map; overrides the `cost_tiers:` config block | shipped defaults |
| `AIRLOCK_SMART_THRESHOLDS` | JSON `[simple_max, complex_min]` band cutoffs for `model: smart` | `[0.30, 0.60]` |
| `AIRLOCK_SESSION_TTL` | Seconds a `session_id` stays pinned to its model | `3600` |
| `AIRLOCK_PROVIDER_BUDGETS` | JSON provider→daily-budget map for budget-aware swaps | see above |
| `AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS` | Prompt-token size above which fallbacks are suppressed | `60000` |
| `AIRLOCK_BUDGET_WARN_RATIO` | Fraction of a provider's `budget_limit` at which Airlock warns and tags `near_limit` | `0.8` |

Routing decisions are recorded on every request under
`metadata.airlock_routing` and surfaced in the JSONL logs, so you can
audit how `smart` classified prompts and which directives changed a
model.
