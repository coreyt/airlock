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

## Quick start

### 1. Clone and configure

```bash
git clone <repo-url> && cd airlock
cp .env.example .env
# Edit .env with your API keys
```

### 2. Run with Docker (recommended)

```bash
docker compose up --build
```

### 3. Or run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Download the spaCy model for Presidio PII detection
python -m spacy download en_core_web_lg

airlock
# or: python -m airlock.proxy
```

Airlock will start on `http://0.0.0.0:4000`.

### 4. Test it

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-airlock-change-me" \
  -d '{
    "model": "claude-sonnet",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Connecting AI tools

### Cursor / Windsurf

In settings, set:
- **OpenAI Base URL**: `https://airlock.internal:4000/v1`
- **API Key**: your Airlock virtual key

### Claude Code

```bash
airlock start &
airlock hooks install
eval $(airlock dogfood)
```

Then launch Claude Code in this project — every request flows through PII redaction, keyword blocking, JSONL logging, and shows up in `airlock tui`.

See [dev/dogfooding.md](dev/dogfooding.md) for the full setup guide.

### GitHub Copilot

In VS Code `settings.json`:
```json
{
  "github.copilot.advanced": {
    "debug.overrideProxyUrl": "https://airlock.internal:4000/v1"
  }
}
```

## Configuration

### config.yaml

The main configuration file defines models, callbacks, and guardrails. See the inline comments in `config.yaml` for details.

Key sections:
- **`model_list`** — which LLM providers/models to expose
- **`litellm_settings`** — callbacks, timeouts, budgets
- **`guardrails`** — PII and keyword guards
- **`general_settings`** — master key, host/port

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `AIRLOCK_MASTER_KEY` | Master key for admin endpoints | — |
| `AIRLOCK_HOST` | Bind address | `0.0.0.0` |
| `AIRLOCK_PORT` | Listen port | `4000` |
| `AIRLOCK_LOG_DIR` | Directory for JSONL log files | `./logs` |
| `AIRLOCK_BLOCKED_KEYWORDS` | Comma-separated restricted phrases | — |
| `AIRLOCK_PII_ENTITIES` | Presidio entity types to redact | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` |

## Project structure

```
airlock/
├── airlock/
│   ├── __init__.py
│   ├── proxy.py                  # Entry point — launches LiteLLM proxy
│   ├── callbacks/
│   │   └── enterprise_logger.py  # Structured JSONL logging callback
│   └── guardrails/
│       ├── pii_guard.py          # Presidio-based PII stripping
│       └── keyword_guard.py      # Restricted keyword blocking
├── config.yaml                   # LiteLLM proxy configuration
├── .env.example                  # Template for environment variables
├── requirements.txt
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## Roadmap

- **Phase 1 — Passthrough**: Proxy with enterprise logging (this release)
- **Phase 2 — Bouncer**: PII stripping + budget management (this release)
- **Phase 3 — Library**: Internal RAG as a custom model provider

## License

Apache 2.0
