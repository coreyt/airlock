# Configuration

## config.yaml

The main configuration file defines models, callbacks, and guardrails. See the inline comments in `config.yaml` for details.

Key sections:

- **`model_list`** — which LLM providers/models to expose
- **`litellm_settings`** — callbacks, timeouts, budgets
- **`router_settings`** — routing strategy, fallbacks, provider budgets
- **`guardrails`** — PII and keyword guards
- **`mcp_servers`** — MCP tool servers accessible via the proxy
- **`general_settings`** — master key, host/port

## Self-hosted / local models

Airlock supports any OpenAI-compatible endpoint (vLLM, Ollama, LocalAI, etc.) using the `openai/` prefix with a custom `api_base`:

```yaml
# config.yaml — add to model_list
- model_name: gemma-4
  litellm_params:
    model: openai/gemma4-31b
    api_base: http://your-host:8000/v1
    api_key: os.environ/VLLM_API_KEY
```

```bash
# .env
VLLM_API_KEY=dummy-key
```

The model will appear in the TUI Basic Chat screen for interactive testing and can be used by any connected client via `model: "gemma-4"`.

### Multiple aliases on one vLLM endpoint

It is common to register several model aliases against the same vLLM endpoint when the host swaps between models (only one is loaded at a time):

```yaml
- model_name: kimi-dev
  litellm_params:
    model: openai/kimi-dev-72b
    api_base: http://192.168.1.45:8000/v1
    api_key: os.environ/VLLM_API_KEY

- model_name: qwen3-32b
  litellm_params:
    model: openai/qwen3-32b
    api_base: http://192.168.1.45:8000/v1
    api_key: os.environ/VLLM_API_KEY

- model_name: qwen3.6-27b
  litellm_params:
    model: openai/qwen3.6-27b
    api_base: http://192.168.1.45:8000/v1
    api_key: os.environ/VLLM_API_KEY
```

The **Local vLLM Router** guardrail (enabled by default, see [Guardrails](../guide/guardrails.md#local-vllm-router)) intercepts requests for unloaded aliases and returns an explanatory error naming the currently loaded model, instead of letting the upstream `model not found` propagate.

Set `AIRLOCK_LOCAL_VLLM_BASE_URL` to the endpoint Airlock should treat as local. Aliases pointing at other endpoints pass through untouched.

## Enhanced Models

Airlock supports "enhanced" model profiles to silently inject constraints (like forcing an LLM to retain its reasoning loops) or default parameters out-of-band. This is especially useful for agentic workflows using models like `gemini-3.1-pro-preview-customtools`.

Define an `enhanced_profile` in `litellm_params`. Airlock resolves the logical alias at execution time, injects the prompt/parameters, and forwards the request to the physical target model without double-logging the inner provider call:

```yaml
# config.yaml — add to model_list
- model_name: gemini-coding
  litellm_params:
    model: enhanced/gemini-coding  # The logical name clients request
    enhanced_profile:
      target_model: gemini/gemini-3.1-pro-preview-customtools
      system_prompt: "CRITICAL: You are operating in a multi-turn tool-calling loop. You must retain and finalize all reasoning pathways. Do not truncate internal thoughts."
      params:
        thinking: true
        thinking_level: "MEDIUM"
```

Notes:

- Clients request `model: "gemini-coding"`. They never need to know the physical Gemini model name.
- Gemini-specific `thinking` settings are normalized to the provider surface LiteLLM actually accepts.
- Provider auth and transport context are forwarded to the physical model call, so the alias uses the same `api_key` / `api_base` wiring as the underlying deployment.
- The forwarded inner provider call is marked `no_log=True` and skips the Airlock Fathom callback, so one logical request produces one Fathom row.

## Search providers

Airlock can expose web search as a regular chat model so any connected
client can search by sending a normal completion request.

### Tavily

The Tavily provider (`airlock.providers.tavily_provider`) is a LiteLLM
custom provider. Clients send `model: "tavily-search"` with their query
as the user message and get back a chat-style response whose content is
formatted results (title, URL, snippet), optionally prefixed with
Tavily's summary answer. The optional `max_results` parameter defaults
to 5.

```yaml
# config.yaml — add to model_list
- model_name: tavily-search
  litellm_params:
    model: tavily/web-search
```

```bash
# .env
TAVILY_API_KEY=tvly-...
```

Install the extra with `pip install airlock-llm[search]`. For news
search via MCP, see the NewsCatcher server in
[MCP Servers](../guide/mcp-servers.md).

## AI Studio (Gemini) batch aliases

To batch Gemini jobs through the Airlock Batch Gateway, a `model_list` entry opts
in with an `airlock_batch` marker that is a **sibling** of `litellm_params` (so it
never leaks to the provider SDK on the sync path):

```yaml
model_list:
  - model_name: gemini-3.5-flash-aistudio
    litellm_params:
      model: gemini/gemini-3.5-flash
      api_key: os.environ/GOOGLE_AISTUDIO_API_KEY
    airlock_batch:
      backend: aistudio          # selects the Airlock Batch Gateway
      provider_model: gemini-3.5-flash
```

Needs the `aistudio` extra (`pip install 'airlock-llm[aistudio]'`) and
`GOOGLE_AISTUDIO_API_KEY`. Full upload/create/poll recipe in
[Batch Processing → AI Studio (Gemini) batch](../guide/batch.md#ai-studio-gemini-batch-via-the-airlock-batch-gateway).
Batch files are staged under `AIRLOCK_STATE_DIR` (falls back to `AIRLOCK_LOG_DIR`).

## Mistral batch aliases

Same gateway, `backend: mistral`. Opt in a `model_list` entry the same way:

```yaml
model_list:
  - model_name: mistral-large-batch
    litellm_params:
      model: mistral/mistral-large-latest
      api_key: os.environ/MISTRAL_API_KEY
    airlock_batch:
      backend: mistral             # selects the Airlock Batch Gateway
      provider_model: mistral-large-latest
```

Needs the `mistral` extra (`pip install 'airlock-llm[mistral]'`, pinned `<2`) and
`MISTRAL_API_KEY`. `mistral-large-batch` + `mistral-small-batch` ship by default.
Full recipe in
[Batch Processing → Mistral batch](../guide/batch.md#mistral-batch-via-the-airlock-batch-gateway).

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | -- |
| `OPENAI_API_KEY` | OpenAI API key | -- |
| `GOOGLE_AISTUDIO_API_KEY` | Google AI Studio API key for Gemini models (+ AI Studio batch gateway) | -- |
| `MISTRAL_API_KEY` | Mistral API key for Mistral models (+ Mistral batch gateway) | -- |
| `AIRLOCK_MASTER_KEY` | Optional proxy auth key. Leave unset for local/dev unauthenticated runs; set it for protected deployments. | -- |
| `AIRLOCK_HOST` | Bind address | `127.0.0.1` |
| `AIRLOCK_PORT` | Listen port | `4000` |
| `AIRLOCK_LOG_DIR` | Directory for JSONL log files | `./logs` |
| `AIRLOCK_STATE_DIR` | State directory for circuit-breaker state and optional FathomDB files | `./logs` |
| `AIRLOCK_MAX_LOG_DAYS` | Days to retain log files | `30` |
| `AIRLOCK_MAX_LOG_SIZE_MB` | Max log file size before rotation | `500` |
| `AIRLOCK_BLOCKED_KEYWORDS` | Comma-separated restricted phrases | -- |
| `AIRLOCK_PII_ENTITIES` | Presidio entity types to redact | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` |
| `AIRLOCK_ENFORCE_MODE` | Guardrail mode: `observe`, `shadow`, or `enforce` | `observe` |
| `AIRLOCK_CLIENT` | Client identity label propagated as the `X-Airlock-Client` header and recorded on each request for per-tool attribution | -- |
| `AIRLOCK_ADVISOR_MODEL` | Override model for the advisor | -- |
| `AIRLOCK_STARTUP_MODEL_DISCOVERY` | Opt-in provider/model discovery on startup | `0` |
| `AIRLOCK_MCP_STARTUP_MODE` | MCP startup mode: `off`, `lazy`, or `eager` | `lazy` |
| `AIRLOCK_ENABLE_FATHOMDB` | Enable lazy FathomDB engine initialization | `0` |
| `AIRLOCK_ENABLE_FATHOM_LOGGER` | Append Fathom request logging at runtime | `0` |
| `AIRLOCK_LOCAL_VLLM_BASE_URL` | URL of the local vLLM endpoint the router guardrail watches | `http://192.168.1.45:8000/v1` |
| `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS` | Cache TTL for the `/models` probe used by the local vLLM router | `5` |
| `AIRLOCK_LOCAL_VLLM_SWITCH_HINT` | Optional format-string appended to the router's mismatch error (placeholders: `{requested}`, `{requested_served}`, `{loaded}`, `{loaded_aliases}`, `{base_url}`) | -- |
| `AIRLOCK_REASONING_STRIP_MODELS` | Comma-separated aliases for which `◁think▷ … ◁/think▷` blocks are stripped from responses | `kimi-dev` |
| `AIRLOCK_COST_TIERS` | JSON tier→model-alias map for routing; overrides the `cost_tiers:` config block (see [Routing](../guide/routing.md)) | shipped defaults |
| `AIRLOCK_SMART_THRESHOLDS` | JSON `[simple_max, complex_min]` complexity cutoffs for `model: smart` | `[0.30, 0.60]` |
| `AIRLOCK_SESSION_TTL` | Seconds a routing `session_id` stays pinned to its model | `3600` |
| `AIRLOCK_PROVIDER_BUDGETS` | JSON provider→daily-budget map for budget-aware routing swaps | per-provider defaults |
| `AIRLOCK_SSL_CERTFILE` | TLS certificate file for native HTTPS. **Both** cert and key must be set, or Airlock serves plain HTTP. | -- |
| `AIRLOCK_SSL_KEYFILE` | TLS private-key file for native HTTPS (see `AIRLOCK_SSL_CERTFILE`) | -- |
| `AIRLOCK_BREAKER_OVERRIDES` | JSON per-client circuit-breaker overrides: `{"defaults":{…},"clients":{"key:<last8>":{…}}}`. See [Rate Limiting](../guide/rate-limiting.md). | -- |
| `AIRLOCK_BUDGET_WARN_RATIO` | Fraction of a provider's daily budget cap at which Airlock warns and tags responses `X-Airlock-Budget-State: near_limit` | `0.8` |
| `AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS` | Prompt-token size above which fallbacks are suppressed (fail fast instead of fanning out a large payload) | `60000` |
| `AIRLOCK_JWT_SECRET` | Secret for signing/verifying admin & capability tokens. Falls back to an HMAC derivation from `AIRLOCK_MASTER_KEY` when unset. | -- |
| `AIRLOCK_JWT_SECRET_PREV` | Previous JWT secret, accepted for verification during a rolling secret rotation | -- |

## Resilience & admin settings

Airlock 0.5.0 adds three optional `config.yaml` blocks for the circuit breaker, the
admin API, and per-request guardrail skips. All are **off by default / behaviour-
preserving** — a config-free deploy behaves exactly as before.

### `airlock_settings.circuit_breaker`

Per-client rate-limit circuit breaker. The defaults reproduce the historical
one-strike behaviour. Full reference in [Rate Limiting](../guide/rate-limiting.md).

```yaml
airlock_settings:
  circuit_breaker:
    rate_limit_threshold: 1                 # 429s within the window before quarantine
    rate_limit_window_seconds: 300
    client_cooldown_seconds: 300
    provider_cooldown_seconds: 300
    provider_escalation_client_threshold: 2 # distinct clients before provider-wide quarantine
    clients:
      "key:b35cf679":                       # per-client-key override
        rate_limit_threshold: 8
        client_cooldown_seconds: 30
        escalation_exempt: true             # don't trip the provider for everyone else
        disabled: false                     # true = skip the breaker for this client
```

The env override `AIRLOCK_BREAKER_OVERRIDES` (JSON, same shape under
`defaults`/`clients`) takes precedence.

### `admin`

Enables the admin control plane. When `enabled` is `false`, `/airlock/admin/*`
returns `404`. Full reference in [Admin API](../guide/admin-api.md).

```yaml
admin:
  enabled: false            # off → /airlock/admin/* returns 404
  trust_loopback: true      # loopback connections are the operator (Path A)
  allow_insecure_tokens: false   # permit token auth over plaintext on a non-loopback bind
  behind_tls_proxy: false   # assert TLS is terminated upstream
```

Tokens are signed with `AIRLOCK_JWT_SECRET` (falling back to a derivation from
`AIRLOCK_MASTER_KEY`); mint them with `airlock admin mint-token`. If the admin API
or capability skips are active on a non-loopback bind without TLS, Airlock refuses
to start unless one of `AIRLOCK_SSL_*`, `behind_tls_proxy`, or
`allow_insecure_tokens` is set.

### `guardrail_overrides`

Allows trusted clients to downgrade specific content guards per request via an
`X-Airlock-Capability` token. Off until `allow_capability_skip: true`. Full
reference in [Guardrails → Per-request guardrail skips](../guide/guardrails.md#per-request-guardrail-skips).

```yaml
guardrail_overrides:
  allow_capability_skip: false              # master flag
  capability_header: X-Airlock-Capability
  skippable:
    pii_redact:      { skippable: false }                  # never, by default
    keyword:         { skippable: true, downgrade_to: observe }
    response_scan:   { skippable: true, downgrade_to: observe }
    reasoning_strip: { skippable: true, downgrade_to: off }
```

### `provider_budget_config`

The existing per-provider daily budget caps (under `router_settings`) now warn
before the cliff. At `AIRLOCK_BUDGET_WARN_RATIO` (default `0.8`) of a provider's
`budget_limit`, Airlock logs a warning and emits `X-Airlock-Budget-State:
near_limit`. See [Routing → Provider budgets](../guide/routing.md#provider-budgets).

## Native HTTPS

Set **both** `AIRLOCK_SSL_CERTFILE` and `AIRLOCK_SSL_KEYFILE` to serve HTTPS
natively on the same `AIRLOCK_HOST:AIRLOCK_PORT`. Leave either unset to serve plain
HTTP (the default), e.g. when TLS is terminated by a reverse proxy. Certificates
load at startup only, so renewal means a restart. See
[Operations → Native TLS](../operations.md#native-tls).

## Startup Defaults

Airlock defaults to low-noise startup:

- `AIRLOCK_STARTUP_MODEL_DISCOVERY=0`
- `AIRLOCK_MCP_STARTUP_MODE=lazy`
- `/health/liveliness` for liveness probes and frequent polling

If `AIRLOCK_MASTER_KEY` is unset or blank, Airlock strips the runtime `general_settings.master_key` entry before launching LiteLLM. That keeps local/dev runs usable without requiring LiteLLM's database-backed virtual-key flow.

## Optional FathomDB Logging

Enable FathomDB only when you want it:

```bash
AIRLOCK_ENABLE_FATHOMDB=1
AIRLOCK_ENABLE_FATHOM_LOGGER=1
AIRLOCK_STATE_DIR=/tmp/airlock-fathom-fresh
```

Operational note: FathomDB is still a single-owner database. Airlock now avoids same-process engine-open races and inherited cross-process reuse, but separate processes should not open the same `airlock.db` simultaneously.

Recommended debug profile:

```bash
AIRLOCK_STARTUP_MODEL_DISCOVERY=0
AIRLOCK_MCP_STARTUP_MODE=lazy
AIRLOCK_ENABLE_FATHOMDB=1
AIRLOCK_ENABLE_FATHOM_LOGGER=1
AIRLOCK_STATE_DIR=/tmp/airlock-fathom-fresh
```
