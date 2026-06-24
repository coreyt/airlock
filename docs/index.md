# Airlock

Enterprise LLM proxy built on [LiteLLM](https://github.com/BerriAI/litellm) — unified access, logging, and guardrails for AI coding tools.

Airlock sits between your developers and LLM providers, giving you visibility and control without slowing anyone down.

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │  Cursor   │   │  Claude  │   │  Copilot  │
  │           │   │   Code   │   │           │
  └─────┬─────┘   └─────┬────┘   └─────┬─────┘
        │               │              │
        └───────────┬───┘──────────────┘
                    │
              ┌─────▼──────┐
              │   AIRLOCK   │  ← logging, PII guard, keyword guard
              │  (LiteLLM)  │
              └──────┬──────┘
                     │
           ┌─────────┼──────────┐
           │         │          │
      ┌────▼───┐ ┌───▼────┐ ┌──▼──────┐
      │Anthropic│ │ OpenAI │ │ Internal│
      │  API    │ │  API   │ │  RAG    │
      └────────┘ └────────┘ └─────────┘
```

## What it does

| Concern | How Airlock handles it |
|---|---|
| **Unified access** | Single OpenAI-compatible endpoint for all providers |
| **Logging** | Every request/response logged as structured JSONL |
| **PII stripping** | Microsoft Presidio scrubs credit cards, SSNs, emails, etc. before they leave the network |
| **Keyword blocking** | Custom blocklist prevents restricted project names or terms from leaking |
| **Budget control** | Per-user/per-team spend limits via LiteLLM virtual keys |
| **Multi-tool support** | Works with Cursor, Claude Code, GitHub Copilot, and any OpenAI-compatible client |
| **Self-hosted models** | Route to local vLLM, Ollama, or any OpenAI-compatible endpoint alongside cloud providers |
| **Interactive testing** | Built-in Basic Chat screen to test LLM connectivity and inspect full request/response cycles |
| **AI advisor** | Ask an LLM about operational data — diagnose errors, tune guardrails, get config recommendations (local models preferred) |

## Quick start

```bash
pip install airlock-llm
python -m spacy download en_core_web_lg
airlock init
# Edit .env with your API keys
airlock tui --start
```

See [Installation](getting-started/installation.md) for detailed setup instructions.

## Resilience & admin (new in 0.5.0)

- [Admin API](guide/admin-api.md) — the control plane for live protection state:
  clear a quarantine, reset a circuit, mint scoped tokens, audit log.
- [Rate Limiting & the Circuit Breaker](guide/rate-limiting.md) — the 429 contract
  (`Retry-After`, headers, body) and the tunable per-client circuit breaker.
- [Provider Quota Observability](guide/provider-observability.md) — observe-only
  rate-limit headroom and budget gauges captured from upstream providers.
