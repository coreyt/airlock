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

Define an `enhanced_profile` in `litellm_params`. Airlock will intercept requests to this model, inject the prompt/parameters, and seamlessly rewrite the routing target to the physical model:

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

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | -- |
| `OPENAI_API_KEY` | OpenAI API key | -- |
| `AIRLOCK_MASTER_KEY` | Master key for admin endpoints | -- |
| `AIRLOCK_HOST` | Bind address | `127.0.0.1` |
| `AIRLOCK_PORT` | Listen port | `4000` |
| `AIRLOCK_LOG_DIR` | Directory for JSONL log files | `./logs` |
| `AIRLOCK_MAX_LOG_DAYS` | Days to retain log files | `30` |
| `AIRLOCK_MAX_LOG_SIZE_MB` | Max log file size before rotation | `500` |
| `AIRLOCK_BLOCKED_KEYWORDS` | Comma-separated restricted phrases | -- |
| `AIRLOCK_PII_ENTITIES` | Presidio entity types to redact | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` |
| `AIRLOCK_ENFORCE_MODE` | Guardrail mode: `observe` or `enforce` | `observe` |
| `AIRLOCK_ADVISOR_MODEL` | Override model for the advisor | -- |
