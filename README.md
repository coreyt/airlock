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
| **PII stripping** | Microsoft Presidio redacts configured entity types. The shipped pattern recognizers (cards, SSNs, emails, phones, bank numbers, IBANs) run without spaCy NER; configured semantic entities such as `PERSON` use spaCy. |
| **PII-safe tool calls** | Opaque, bounded reverse maps can rehydrate only non-streaming tool-call arguments; per-tool egress policy starts in observe mode and can be promoted to enforce. |
| **Operational storage** | Optional FathomDB search/analysis store with authenticated per-client erasure; JSONL retention remains a separate obligation. |
| **Keyword blocking** | Custom blocklist prevents restricted project names or terms from leaking |
| **Budget control** | Per-provider daily spend caps — near-limit warning, proactive reroute away from a provider approaching its cap, hard block at the limit. Per-tenant keys with per-key budgets are planned, not yet shipped |
| **Multi-tool support** | Works with Cursor, Claude Code, GitHub Copilot, and any OpenAI-compatible client |
| **Self-hosted models** | Route to local vLLM, Ollama, or any OpenAI-compatible endpoint alongside cloud providers |
| **Batch processing** | OpenAI-compatible Batch API (`/v1/files` + `/v1/batches`) for ~50% cheaper async jobs — OpenAI and Vertex AI Gemini (regional) work through the proxy today |
| **Interactive testing** | Built-in Basic Chat screen to test LLM connectivity and inspect full request/response cycles |
| **AI advisor** | Ask an LLM about operational data — diagnose errors, tune guardrails, get config recommendations (local models preferred) |

## Getting started

### Install from PyPI

```bash
pip install airlock-llm
pip install "https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl"  # only when configuring spaCy/NER entities, e.g. PERSON
airlock init
```

`airlock init` generates `config.yaml`, `.env`, and a `logs/` directory in the
current working directory.

### Install from source (quick setup)

```bash
git clone https://github.com/coreyt/airlock && cd airlock
./scripts/setup.sh
```

This installs Airlock and its dependencies, prepares the optional spaCy model for
semantic PII detection, and runs `airlock init`. The shipped deterministic PII
recognizers do not require that model. Pass `--pip` to use pip instead of uv.

### Developer setup

```bash
git clone https://github.com/coreyt/airlock && cd airlock
./scripts/setup-dev.sh
```

Everything in the standard setup, plus all optional extras (test, metrics,
tracing, search, s3, sql, vertex), install verification, and a test suite
run. Pass `--pip` to use pip instead of uv.

### Add your API keys

Edit the generated `.env` file and fill in your provider keys:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

You only need keys for the providers you plan to use. If you only use Anthropic models, you can leave `OPENAI_API_KEY` blank.

### Start the proxy

```bash
# Option A: TUI dashboard with built-in proxy (recommended)
uv run airlock tui --start

# Option B: proxy only (headless)
uv run airlock start
```

Airlock listens on `http://localhost:4000` by default. Change the port with `AIRLOCK_PORT` in `.env`.

Recommended local startup profile:

```bash
AIRLOCK_STARTUP_MODEL_DISCOVERY=0
AIRLOCK_MCP_STARTUP_MODE=lazy
uv run airlock start
```

### Test it

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

If `AIRLOCK_MASTER_KEY` is set, add `-H "Authorization: Bearer <your-master-key>"`.
If `AIRLOCK_MASTER_KEY` is unset or blank, Airlock strips the runtime proxy
`master_key` setting and accepts unauthenticated requests for local/dev use.

Or use the TUI's **Basic Chat** screen (press `5`) to interactively test any configured model and inspect the full request/response headers and body.

### Advisor

Ask an LLM about Airlock's operational data — diagnose errors, tune guardrails, understand trends:

```bash
# One-shot question
airlock advise "why does claude-sonnet have a high error rate?"

# Interactive session
airlock advise --interactive

# Force local model only (no data sent externally)
airlock advise --local-only "what should I tune?"
```

Or press `6` in the TUI for the Advisor screen. The advisor prefers local models (vLLM, Ollama) to avoid sending operational data to remote providers.

### Alternative: Docker

```bash
docker compose up --build
```

## Documentation and repository map

The [documentation site](docs/index.md) is the canonical guide for users and
operators. Start with installation, configuration, and deployment there; the
live proxy also exposes its OpenAPI schema at `/openapi.json` and interactive
Airlock API notes at `/airlock/docs`.

| Area | Purpose |
| --- | --- |
| [`docs/`](docs/index.md) | Public installation, configuration, operations, and API guidance |
| [`dev/`](dev/README.md) | Engineering requirements, architecture, designs, plans, and retained evidence |
| [`airlock/`](airlock/) | Application source, organized by runtime subsystem |
| [`tests/`](tests/) | Unit, configuration/contract, integration, harness, live, and stress coverage |
| [`deploy/`](deploy/) | systemd and Kubernetes deployment assets |
| [`scripts/`](scripts/) | Setup, verification, release, and local preflight utilities |

For contribution and release expectations, see [`AGENTS.md`](AGENTS.md), the
[developer documentation map](dev/README.md), and the
[release-plan guide](dev/plans/README.md).

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
- **`litellm_settings`** — callbacks, timeouts
- **`router_settings`** — routing strategy, fallbacks, provider budgets
- **`guardrails`** — PII and keyword guards
- **`mcp_servers`** — MCP tool servers (Armada, ADO, etc.) accessible via the proxy
- **`general_settings`** — master key, host/port

### Self-hosted / local models

Airlock supports any OpenAI-compatible endpoint (vLLM, Ollama, LocalAI, etc.) using the `openai/` prefix with a custom `api_base`:

```yaml
# config.yaml — add to model_list
- model_name: gemma-4
  litellm_params:
    model: openai/gemma4-31b          # model ID as reported by the server
    api_base: http://your-host:8000/v1
    api_key: os.environ/VLLM_API_KEY  # use "dummy-key" if server has no auth
```

```bash
# .env
VLLM_API_KEY=dummy-key
```

The model will appear in the TUI Basic Chat screen for interactive testing and can be used by any connected client via `model: "gemma-4"`.

### Enhanced model aliases

Airlock can expose logical aliases that inject prompt and parameter defaults while forwarding to a physical upstream model. Example: `gemini-coding` routes to Gemini tools mode through `enhanced/gemini-coding`.

- Clients send `model: "gemini-coding"`.
- Airlock injects the configured system prompt and normalizes Gemini reasoning settings.
- Provider auth is forwarded to the physical model automatically.
- Inner forwarded calls are marked `no_log=True`, so Airlock and Fathom log one row per logical request, not two.

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `GOOGLE_AISTUDIO_API_KEY` | Google AI Studio API key for Gemini models | — |
| `AIRLOCK_MASTER_KEY` | Optional proxy auth key. Leave unset for local/dev unauthenticated runs. | — |
| `AIRLOCK_HOST` | Bind address (set to `0.0.0.0` to expose externally) | `127.0.0.1` |
| `AIRLOCK_PORT` | Listen port | `4000` |
| `AIRLOCK_LOG_DIR` | Directory for JSONL log files | `./logs` |
| `AIRLOCK_STATE_DIR` | State directory for circuit-breaker state and optional FathomDB files | `./logs` |
| `AIRLOCK_MAX_LOG_DAYS` | Days to retain log files before cleanup | `30` |
| `AIRLOCK_MAX_LOG_SIZE_MB` | Max log file size before rotation | `500` |
| `AIRLOCK_BLOCKED_KEYWORDS` | Comma-separated restricted phrases | — |
| `AIRLOCK_PII_ENTITIES` | Presidio entity types to redact | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` |
| `AIRLOCK_STARTUP_MODEL_DISCOVERY` | Opt-in provider/model discovery at startup | `0` |
| `AIRLOCK_MCP_STARTUP_MODE` | MCP startup mode: `off`, `lazy`, or `eager` | `lazy` |
| `AIRLOCK_ENABLE_FATHOMDB` | Enable lazy FathomDB engine initialization | `0` |
| `AIRLOCK_ENABLE_FATHOM_LOGGER` | Append Fathom request logging at runtime | `0` |

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
├── advisor/              # LLM-powered operational advisor (agent loop, tools, proposals)
├── cli/                  # Unified CLI: init, start, status, tui, analyze, advise, hooks
└── tui/                  # Textual terminal dashboard (6 screens, proxy control)
scripts/
├── setup.sh              # Standard setup (install + init + spaCy model)
└── setup-dev.sh          # Developer setup (all extras + tests)
```

## Production deployment

See [docs/operations.md](docs/operations.md) for deployment guides (Docker, Kubernetes, bare metal), monitoring, security checklist, and upgrade procedures.

See [docs/troubleshooting.md](docs/troubleshooting.md) for common issues and debugging.

## License

Apache 2.0
