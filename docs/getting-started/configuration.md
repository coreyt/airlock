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

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | -- |
| `OPENAI_API_KEY` | OpenAI API key | -- |
| `GOOGLE_AISTUDIO_API_KEY` | Google AI Studio API key for Gemini models | -- |
| `AIRLOCK_MASTER_KEY` | Optional proxy auth key. Leave unset for local/dev unauthenticated runs; set it for protected deployments. | -- |
| `AIRLOCK_HOST` | Bind address | `127.0.0.1` |
| `AIRLOCK_PORT` | Listen port | `4000` |
| `AIRLOCK_LOG_DIR` | Directory for JSONL log files | `./logs` |
| `AIRLOCK_STATE_DIR` | State directory for circuit-breaker state and optional FathomDB files | `./logs` |
| `AIRLOCK_MAX_LOG_DAYS` | Days to retain log files | `30` |
| `AIRLOCK_MAX_LOG_SIZE_MB` | Max log file size before rotation | `500` |
| `AIRLOCK_BLOCKED_KEYWORDS` | Comma-separated restricted phrases | -- |
| `AIRLOCK_PII_ENTITIES` | Presidio entity types to redact | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` |
| `AIRLOCK_ENFORCE_MODE` | Guardrail mode: `observe` or `enforce` | `observe` |
| `AIRLOCK_ADVISOR_MODEL` | Override model for the advisor | -- |
| `AIRLOCK_STARTUP_MODEL_DISCOVERY` | Opt-in provider/model discovery on startup | `0` |
| `AIRLOCK_MCP_STARTUP_MODE` | MCP startup mode: `off`, `lazy`, or `eager` | `lazy` |
| `AIRLOCK_ENABLE_FATHOMDB` | Enable lazy FathomDB engine initialization | `0` |
| `AIRLOCK_ENABLE_FATHOM_LOGGER` | Append Fathom request logging at runtime | `0` |

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
