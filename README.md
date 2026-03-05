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

## Getting started

### 1. Install

```bash
git clone <repo-url> && cd airlock
python -m venv .venv && source .venv/bin/activate
pip install -e ".[tui]"

# Install spaCy and download the model for Presidio PII detection
pip install spacy && python -m spacy download en_core_web_lg
```

### 2. Initialize

```bash
airlock init
```

This creates `config.yaml`, `.env`, and a `logs/` directory. If these files already exist they are left untouched (use `--force` to overwrite).

### 3. Add your API keys

Edit the generated `.env` file and fill in your provider keys:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

You only need keys for the providers you plan to use. If you only use Anthropic models, you can leave `OPENAI_API_KEY` blank.

### 4. Start the proxy

```bash
# Option A: TUI dashboard with built-in proxy (recommended)
airlock tui --start

# Option B: proxy only (headless)
airlock start
```

Airlock listens on `http://localhost:4000` by default. Change the port with `AIRLOCK_PORT` in `.env`.

### 5. Test it

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-airlock-change-me" \
  -d '{
    "model": "claude-sonnet",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Alternative: Docker

```bash
docker compose up --build
```

## Connecting AI tools

Point any OpenAI-compatible client at `http://localhost:4000` (or your deployed Airlock URL).

### Claude Code

```bash
# Install client-side hooks and route traffic through the proxy
airlock hooks install
eval $(airlock dogfood)
claude
```

Every request now flows through PII redaction, keyword blocking, and JSONL logging. Open `airlock tui` in another terminal to watch traffic in real time.

See [dev/dogfooding.md](dev/dogfooding.md) for the full setup guide.

### Cursor / Windsurf

In settings, set:
- **OpenAI Base URL**: `http://localhost:4000/v1`
- **API Key**: your Airlock master key (from `.env`)

### GitHub Copilot

In VS Code `settings.json`:
```json
{
  "github.copilot.advanced": {
    "debug.overrideProxyUrl": "http://localhost:4000/v1"
  }
}
```

## Configuration

### config.yaml

The main configuration file defines models, callbacks, and guardrails. See the inline comments in `config.yaml` for details.

Key sections:
- **`model_list`** — which LLM providers/models to expose
- **`litellm_settings`** — callbacks, timeouts, budgets
- **`router_settings`** — routing strategy, fallbacks, provider budgets
- **`guardrails`** — PII and keyword guards
- **`mcp_servers`** — MCP tool servers (Armada, ADO, etc.) accessible via the proxy
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

## Adding MCP servers

Airlock can proxy MCP tool servers alongside LLM providers. Add entries to `mcp_servers` in `config.yaml`. LiteLLM spawns stdio servers from the proxy's working directory, so command resolution matters.

### Command resolution patterns

**Module via `python -m`** — cwd-independent, requires package installed in the proxy's venv:
```yaml
mcp_servers:
  ado_mcp:
    command: uv
    args: ["run", "python", "-m", "ado_mcp.mcp.server"]
    env:
      ADO_ORG_URL: os.environ/ADO_ORG_URL
      ADO_PAT: os.environ/ADO_PAT
```

**Installed script via `uv run`** — cwd-independent, resolves from PATH/venv:
```yaml
  armada:
    command: uv
    args: ["run", "armada-mcp"]
    env:
      ARMADA_PROFILE: essential
```

**Script file** — must use an absolute path (relative paths resolve against the proxy's cwd, not the server's project directory):
```yaml
  mono_tui:
    command: python3
    args: ["/home/user/projects/my-mcp-server/server.py"]
```

**Other runtimes:**
```yaml
  # Node.js
  my_node_server:
    command: node
    args: ["/path/to/server.js"]

  # npx (installed package)
  my_npx_server:
    command: npx
    args: ["my-mcp-server"]

  # Bun
  my_bun_server:
    command: bun
    args: ["run", "/path/to/server.ts"]

  # Poetry
  my_poetry_server:
    command: poetry
    args: ["run", "python", "-m", "my_server"]
```

### Environment variables

Use `os.environ/VAR_NAME` to pass environment variables from Airlock's `.env` to the MCP server. Airlock validates these references at startup and gives clear error messages for missing values.

### Guardrail coverage

All MCP tool calls flow through the same guardrail pipeline as LLM requests (PII redaction, keyword blocking, threat detection). MCP-specific guards add tool allowlist/blocklist and argument sanitization. No extra configuration needed — guardrails apply automatically.

## Project structure

```
airlock/
├── proxy.py              # Entry point — launches LiteLLM subprocess
├── callbacks/            # JSONL logger, S3, SQL, Prometheus, OpenTelemetry
├── guardrails/           # PII redaction, keyword blocking, semantic, adaptive
├── fast/                 # Real-time: threat detection, circuit breaker, priority
├── slow/                 # Offline: log analysis, trend detection, tuning
├── hooks/                # Claude Code client-side hooks (session, prompt, audit)
├── cli/                  # Unified CLI: init, start, status, tui, analyze, hooks
└── tui/                  # Textual terminal dashboard (8 screens, proxy control)
```

## License

Apache 2.0
