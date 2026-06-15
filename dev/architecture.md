# Airlock — Architecture

This document describes the software architecture of Airlock, tracing design
decisions back to the [User Needs](user-needs.md) and
[Requirements](requirements.md).

---

## 1. System Context

Airlock is a reverse proxy that sits between AI coding tools and LLM provider
APIs. It intercepts every request, applies security guardrails, logs the
interaction, and forwards the (potentially modified) request to the appropriate
upstream provider.

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │  Cursor   │   │  Claude  │   │  Copilot  │   ... any OpenAI-compatible client
  │           │   │   Code   │   │           │
  └─────┬─────┘   └─────┬────┘   └─────┬─────┘
        │               │              │
        └───────────┬───┘──────────────┘
                    │
                    ▼
           ┌────────────────┐
           │    AIRLOCK      │   Port 4000 (configurable)
           │   ┌──────────┐ │
           │   │ LiteLLM  │ │   OpenAI-compatible API surface
           │   │  Proxy   │ │
           │   └────┬─────┘ │
           │        │       │
           │   ┌────▼─────┐ │
           │   │Guardrails│ │   pre_call: PII guard, keyword guard
           │   └────┬─────┘ │
           │        │       │
           │   ┌────▼─────┐ │
           │   │Callbacks │ │   success/failure: enterprise logger
           │   └──────────┘ │
           └────────┬───────┘
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
    ┌──────────┐ ┌────────┐ ┌─────────┐
    │Anthropic │ │ OpenAI │ │ Internal│   Upstream LLM providers
    │  API     │ │  API   │ │  RAG    │
    └──────────┘ └────────┘ └─────────┘
```

**Key design constraint:** Airlock must be invisible to end users. Developers
point their tools at Airlock instead of the provider directly, and everything
else works identically. This drives the choice of an OpenAI-compatible API
surface (FR-1) and silent parameter dropping (FR-13).

---

## 2. Technology Selection

| Layer | Technology | Rationale |
|---|---|---|
| **Proxy engine** | LiteLLM Proxy | Provides OpenAI-compatible API translation for 100+ providers, virtual key management, and a plugin system for callbacks and guardrails. Avoids building a proxy from scratch. |
| **PII detection** | Microsoft Presidio | Mature, open-source NLP-based entity recognition. Supports configurable entity types and runs locally (no external API calls). |
| **NLP model** | spaCy `en_core_web_lg` | Required by Presidio for named entity recognition. Large model chosen for accuracy over the small/medium variants. |
| **Configuration** | YAML + env vars | LiteLLM's native config format. Environment variables overlay for secrets and deployment-specific values. |
| **Logging format** | JSONL | One JSON object per line — trivially parseable, appendable, and ingestible by Splunk, Datadog, ELK, or S3-based analytics. |
| **Containerization** | Docker + Compose | Single-container deployment with health checks. No orchestrator required for basic setups. |
| **Language** | Python 3.10+ | LiteLLM and Presidio are both Python-native. Using the same runtime avoids FFI complexity. |

---

## 3. Component Architecture

### 3.1 Proxy Entry Point (`airlock/proxy.py`)

**Traces to:** FR-1, FR-2, FR-3, NFR-2, NFR-8

The entry point is intentionally thin. It:

1. Loads environment variables from `.env` via `python-dotenv`.
2. Locates `config.yaml` by searching a priority list of paths
   (`AIRLOCK_CONFIG` env var → project root → `/etc/airlock/`).
3. Launches the LiteLLM proxy as a subprocess with the resolved config, host,
   and port.

This delegation pattern means Airlock does not reimplement any proxy logic.
LiteLLM handles HTTP serving (via Uvicorn), request parsing, provider routing,
virtual key validation, and budget enforcement. Airlock's value is in the
configuration, callbacks, and guardrails it layers on top.

```
proxy.py
  │
  ├── load_dotenv()
  ├── _find_config() → config.yaml path
  └── subprocess.call(litellm --config ... --host ... --port ...)
```

### 3.1.1 CLI Framework (`airlock/cli/`)

**Traces to:** FR-17–FR-21, NFR-11

The CLI provides a unified `airlock` command that dispatches to subcommands:

```
airlock
  ├── init     → airlock.cli.init_cmd.run()     Generate config files
  ├── start    → airlock.proxy.main()            Launch the proxy
  ├── status   → airlock.cli.status_cmd.run()   Check proxy health
  ├── analyze  → airlock.slow.cli.main()         Offline log analysis
  └── advise   → airlock.cli.advise_cmd.run()   LLM-powered operational advisor
```

#### Dispatch Architecture

The entry point (`airlock.cli.main:main`) uses `argparse` with subparsers. Each
subcommand is handled by a dedicated function. Imports are lazy — the `start`
subcommand imports `airlock.proxy` only when invoked, so `pip install` users
who only run `airlock init` do not need LiteLLM or Presidio installed.

```
main(argv)
  │
  ├── argparse.ArgumentParser
  │     ├── subparser "init"    → --force, --dir
  │     ├── subparser "start"   → --host, --port, --config
  │     ├── subparser "status"  → --host, --port
  │     ├── subparser "analyze" → --days, --json, --output
  │     └── subparser "advise"  → --host, --port, --model, --local-only, --interactive
  │
  ├── No subcommand → print help, exit(0)
  └── Dispatch to handler
```

#### Start Pre-Flight Validation

Before launching the proxy, `airlock start` validates that `config.yaml` exists
at the resolved path (`--config` flag → `AIRLOCK_CONFIG` env → `./config.yaml`).
If missing, it prints an error suggesting `airlock init` and exits with code 1.
A missing `.env` file in the same directory triggers a warning on stderr but does
not prevent startup.

#### Status Health Check

`airlock status` probes the proxy's `/health` endpoint using stdlib
`urllib.request`. Resolution order for host/port: CLI flags → `AIRLOCK_HOST`/
`AIRLOCK_PORT` env vars → `localhost`:`4000`. Defaults to `localhost` (not
`0.0.0.0`) since this is a client-side probe. Exit 0 if healthy, exit 1 if not
reachable.

#### Template Storage

Init templates are stored as package data in `airlock/cli/templates/` and loaded
at runtime via `importlib.resources`:

| Template file | Written as | Contents |
|---|---|---|
| `config.yaml` | `config.yaml` | Copy of repo root `config.yaml` |
| `dot_env` | `.env` | Copy of repo root `.env.example` |

The `.env` template is named `dot_env` to avoid `.gitignore` matching in the
source tree. The init command writes it as `.env` in the target directory.

#### Backwards Compatibility

The `airlock-analyze` entry point in `pyproject.toml` continues to point
directly at `airlock.slow.cli:main`. The `airlock` entry point changes from
`airlock.proxy:main` to `airlock.cli.main:main`, with `airlock start` providing
the equivalent functionality.

### 3.2 Guardrails (`airlock/guardrails/`)

**Traces to:** FR-4–FR-7, FR-14–FR-16, NFR-6, NFR-9

Guardrails are LiteLLM `CustomGuardrail` subclasses registered in `config.yaml`.
They execute at the `pre_call` stage — after LiteLLM parses the request but
before it is forwarded to the upstream provider.

#### Request Processing Pipeline

```
Incoming HTTP Request
        │
        ▼
  ┌─────────────┐
  │  LiteLLM    │   Parse request, validate API key
  │  Core       │
  └──────┬──────┘
         │
         ▼
  ┌─────────────────┐
  │ PII Guard       │   Scan messages → redact entities → mutate request
  │ (pre_call)      │
  └──────┬──────────┘
         │
         ▼
  ┌─────────────────┐
  │ Keyword Guard   │   Scan messages → reject if match found
  │ (pre_call)      │
  └──────┬──────────┘
         │
         ▼
  ┌─────────────┐
  │  Upstream    │   Forward (modified) request to provider
  │  LLM API    │
  └──────┬──────┘
         │
         ▼
  ┌─────────────────┐
  │ Enterprise      │   Log request + response as JSONL
  │ Logger          │
  │ (callback)      │
  └─────────────────┘
         │
         ▼
  HTTP Response to Client
```

#### PII Guard (`pii_guard.py`)

- Lazy-loads Presidio engines on first use (NFR-6: graceful degradation).
- Reads entity types from `AIRLOCK_PII_ENTITIES` on each call (hot-reloadable
  via env var change + restart).
- Processes each message independently: string content is scrubbed directly;
  list content (multi-part) has text blocks scrubbed while image blocks pass
  through (FR-16).
- Mutates `data["messages"]` in place and returns the modified request dict.

#### Keyword Guard (`keyword_guard.py`)

- Reads blocked keywords from `AIRLOCK_BLOCKED_KEYWORDS` on each call.
- Flattens all message content to a single lowercase string for scanning.
- On match: raises `ValueError` with a user-safe message (FR-7). LiteLLM
  translates this to an HTTP error response.
- On no match (or no keywords configured): returns data unchanged.

#### Execution Order

Guardrails run in the order listed in `config.yaml` (NFR-9). The current order
is:

1. **PII Guard** — redact sensitive data first
2. **Keyword Guard** — then check for restricted terms
3. **Enhanced Model Interceptor** — intercept and mutate enhanced profiles (see [Enhanced Provider Design Note](design-note-enhanced-provider.md))

This order is deliberate: PII redaction runs first so that even if a keyword
check fails and the error is logged, the log record contains redacted content
rather than raw PII.

### 3.3 Enterprise Logger (`airlock/callbacks/enterprise_logger.py`)

**Traces to:** FR-8–FR-10, NFR-7, NFR-10

The logger is a LiteLLM `CustomLogger` subclass registered as both a
`success_callback` and `failure_callback`.

#### Data Flow

```
LiteLLM fires callback
        │
        ▼
_build_record(kwargs, response, timing, success)
        │
        ├── Extract metadata (user, team, request_id)
        ├── Extract token usage from response.usage
        ├── Compute duration_ms from timing
        └── Assemble record dict
        │
        ▼
_write_log(record)
        │
        ├── _ensure_log_dir()   → mkdir -p LOG_DIR
        ├── Determine file: airlock-{today}.jsonl
        └── Append JSON line
```

#### Serialization Strategy

The `_serialize` helper handles edge cases that would otherwise cause
`json.dumps` to fail:

| Type | Serialization |
|---|---|
| `datetime.datetime` | `.isoformat()` |
| `bytes` | `.decode("utf-8", errors="replace")` |
| Pydantic v2 model | `.model_dump()` |
| Pydantic v1 model | `.dict()` |
| Everything else | `str()` |

This is passed as the `default` argument to `json.dumps`, ensuring no record
is ever lost to a serialization error (NFR-7).

### 3.4 Advisor (`airlock/advisor/`)

An LLM-powered operational assistant that lets administrators query
Airlock's state in natural language.  The advisor runs a bounded
tool-calling loop (max 5 iterations) against the proxy's own
`/v1/chat/completions` endpoint, using tools that read from the
StateStore, JSONL logs, config, and analysis pipeline.

```
airlock/advisor/
├── __init__.py
├── model_select.py   # Local-first model selection
├── tools.py          # 9 data-gathering tools + TOOL_REGISTRY
├── audit.py          # JSONL audit logger (advisor-audit.jsonl)
├── prompts.py        # System prompt + tool description builder
├── agent.py          # Agent loop with tool execution
└── proposals.py      # Config change proposals with risk classification
```

**Key design decisions:**

- **Local-first model selection:** The advisor prefers models with a
  custom `api_base` (vLLM, Ollama) to avoid sending operational data
  to remote providers.  Falls back to remote with a warning.
- **No new network listener:** The advisor runs in-process (TUI worker
  thread or CLI process).  It calls the proxy as a client, not as an
  internal endpoint, avoiding circular dependencies.
- **Tool-based context assembly:** Rather than dumping all data into the
  prompt, the LLM selectively requests data via function calling.  This
  keeps token usage bounded and works with smaller local models.
- **Guarded config writes:** Proposed changes generate a diff preview,
  require explicit approval, create `.bak` backups, and validate YAML
  before writing.
- **Audit trail:** All advisor actions are logged to
  `logs/advisor-audit.jsonl`.

**Surfaces:**

- `airlock advise "question"` — CLI one-shot query
- `airlock advise --interactive` — CLI REPL
- TUI Screen 6 ("Advisor") — key `6`

**Design document:** `dev/feature-admin-advisor.md`

---

### 3.5 Batch Gateway (`airlock/batch/`)

An Airlock-owned front controller for asynchronous batch jobs against providers
LiteLLM does not wire for the Batch API. It is installed as ASGI middleware ahead
of LiteLLM's routes: a request carrying `?custom_llm_provider=aistudio` on
`/v1/files` or `/v1/batches` is handled by the gateway; everything else falls
through to LiteLLM untouched.

```
airlock/batch/
├── middleware.py   # ASGI front controller; auth + route dispatch (/v1/files, /v1/batches)
├── gateway.py      # core: idempotent create/reconcile, poll, stage (no disk/SDK IO)
├── runtime.py      # config/alias resolution, file store, backend registry
├── store.py        # SQLite state store (claim/lease/stage; CAS idempotency §3.7)
├── backend.py      # BatchBackend protocol + NormalizedStatus
└── aistudio.py     # AI Studio (Gemini) adapter; lazy google-genai; OpenAI↔Gemini translation
```

**Key design decisions:**

- **Self-enforced auth:** the gateway dispatches *before* LiteLLM's route-level
  auth, so it checks `AIRLOCK_MASTER_KEY` itself (mirrors `proxy.py`'s open-when-unset
  behavior).
- **Idempotency (§3.7):** create is keyed on `(input_file_id, model, endpoint,
  params)` via a `BEGIN IMMEDIATE` CAS claim; an expired-lease reclaim reconciles
  against the provider (`list_jobs`) and cancels duplicates — an at-least-once bound
  with ≤1 surviving job, not exactly-once.
- **Streamed, bounded memory:** uploads stream to disk and translate line-by-line, so
  a ~2GB input is never rejoined in memory.
- **Marker isolation (§7.4):** the `airlock_batch` config marker is a sibling of
  `litellm_params`, stripped from the sync-path provider call so it never leaks to the
  SDK.
- **Lazy provider SDK:** `google-genai` is imported inside the adapter, so the proxy
  boots without the `aistudio` extra; a missing extra yields a clear error, not a boot
  failure.

**Surfaces:** `POST /v1/files`, `POST /v1/batches`, `GET /v1/batches/{id}`,
`POST /v1/batches/{id}/cancel`, `GET /v1/files/{id}/content` — all with
`?custom_llm_provider=aistudio`. Opt-in per alias via `airlock_batch:
{backend: aistudio, provider_model: …}`.

**Caveat:** batch-content guardrail scanning is a no-op stub today, so batch bypasses
the guards (the async scan hook plugs into `_handle_file_upload`).

**Design documents:** `dev/design-unified-batch-gateway.md`,
`dev/design-aistudio-gemini-batch.md`. **Live e2e gate:**
`dev/aistudio-batch-e2e-test-plan.md`.

---

## 4. Configuration Architecture

Airlock uses a layered configuration approach:

```
┌─────────────────────────────────────────────┐
│            Environment Variables             │   Secrets, deployment overrides
│  (.env file loaded at startup)               │
└──────────────────┬──────────────────────────┘
                   │ overlays
┌──────────────────▼──────────────────────────┐
│              config.yaml                     │   Model list, guardrails,
│  (LiteLLM proxy configuration)               │   callbacks, proxy settings
└──────────────────┬──────────────────────────┘
                   │ read by
┌──────────────────▼──────────────────────────┐
│           LiteLLM Proxy Runtime              │
└─────────────────────────────────────────────┘
```

### Config Resolution Order

`config.yaml` is located by searching:

1. Path in `AIRLOCK_CONFIG` environment variable
2. `config.yaml` in the project root (relative to `proxy.py`)
3. `/etc/airlock/config.yaml` (for container deployments)

### Secrets Handling

API keys and the master key use LiteLLM's `os.environ/VAR_NAME` syntax in
`config.yaml`, which defers resolution to runtime environment variables. This
keeps secrets out of the config file and source control.

---

## 5. Deployment Architecture

### 5.1 Docker Deployment (Primary)

**Traces to:** NFR-1, NFR-3, NFR-4

```
┌─────────────────────────────────────────┐
│           Docker Host                    │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │  airlock container                 │  │
│  │                                    │  │
│  │  python:3.12-slim                  │  │
│  │  + spaCy en_core_web_lg           │  │
│  │  + pip install airlock-llm[all]   │  │
│  │                                    │  │
│  │  CMD: python -m airlock.proxy      │  │
│  │                                    │  │
│  │  Ports: 4000 (configurable)        │  │
│  │  Volumes:                          │  │
│  │    - config.yaml (read-only bind)  │  │
│  │    - ./logs (writable bind)        │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Health: GET /health (30s interval)      │
│  Restart: unless-stopped                 │
└─────────────────────────────────────────┘
```

### 5.2 Local Development Deployment

**Traces to:** NFR-8

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m spacy download en_core_web_lg
airlock  # or: python -m airlock.proxy
```

No Docker required. The `airlock` CLI entry point (defined in `pyproject.toml`)
invokes `airlock.proxy:main`.

---

## 6. Data Flow Summary

### Successful Request

```
Client → POST /v1/chat/completions
  → LiteLLM parses request, validates virtual key
  → PII Guard: scrub messages (mutate in place)
  → Keyword Guard: scan messages (pass or reject)
  → LiteLLM routes to upstream provider (Anthropic/OpenAI)
  → Provider returns response
  → Enterprise Logger: write JSONL record (success)
  → LiteLLM returns response to client
```

### Blocked Request (Keyword)

```
Client → POST /v1/chat/completions
  → LiteLLM parses request, validates virtual key
  → PII Guard: scrub messages
  → Keyword Guard: blocked keyword detected → raise ValueError
  → Enterprise Logger: write JSONL record (failure)
  → LiteLLM returns error response to client
```

### Failed Request (Upstream Error)

```
Client → POST /v1/chat/completions
  → LiteLLM parses request, validates virtual key
  → PII Guard: scrub messages
  → Keyword Guard: pass
  → LiteLLM routes to upstream provider → provider returns error
  → Enterprise Logger: write JSONL record (failure, with error details)
  → LiteLLM returns error response to client
```

---

## 7. Module Dependency Graph

```
airlock/
├── proxy.py                      ← depends on: dotenv, litellm (subprocess)
├── callbacks/
│   └── enterprise_logger.py      ← depends on: litellm.integrations.custom_logger
└── guardrails/
    ├── pii_guard.py              ← depends on: litellm.integrations.custom_guardrail,
    │                                            presidio_analyzer (lazy),
    │                                            presidio_anonymizer (lazy)
    └── keyword_guard.py          ← depends on: litellm.integrations.custom_guardrail
```

Key observations:

- **No internal cross-dependencies.** The proxy, callbacks, and guardrails are
  independent modules connected only through LiteLLM's plugin registration in
  `config.yaml`. This allows any component to be added, removed, or replaced
  without affecting the others.
- **Presidio is lazy-loaded.** The PII guard imports Presidio only on first use,
  so the proxy starts even if Presidio is not installed.
- **LiteLLM is the integration backbone.** All components extend LiteLLM base
  classes (`CustomLogger`, `CustomGuardrail`) and are discovered via
  `config.yaml` at startup.

---

## 8. Extension Points

| Extension | Mechanism | Example |
|---|---|---|
| New LLM provider | Add entry to `model_list` in `config.yaml` | Internal RAG service, Azure OpenAI |
| New guardrail | Implement `CustomGuardrail` subclass, register in `config.yaml` | Semantic embedding filter, regex validator |
| New logging backend | Implement `CustomLogger` subclass, add to callbacks | S3 shipper, Datadog integration, SQL writer |
| New deployment target | Use `pip install` entry point or extend Dockerfile | Kubernetes, AWS ECS, systemd service |

---

## 9. Security Considerations

| Concern | Mitigation |
|---|---|
| API key exposure | Keys stored in env vars, never in `config.yaml` or source control. `.env` is gitignored. |
| PII in transit | PII guard runs `pre_call` — data is redacted before leaving the proxy process. |
| Keyword leakage | Keyword guard runs `pre_call` — blocked requests never reach the provider. |
| Unauthorized admin access | `/key/generate` and admin endpoints protected by `AIRLOCK_MASTER_KEY`. |
| Log confidentiality | Logs contain full request/response content. Log directory access must be restricted at the OS/infrastructure level. |
| Guardrail bypass | Guardrails are enforced server-side in the proxy. Clients cannot opt out. |

---

## 10. Future Architecture (Roadmap)

### Phase 3: Internal RAG Provider

The `model_list` in `config.yaml` already contains a commented-out entry for
`internal-docs`, pointing to an internal RAG service. When enabled, this allows
developers to query internal documentation through the same Airlock endpoint
they use for Claude and GPT — with the same guardrails and logging applied.

### Potential Extensions

- **S3 log archival** — Rotate JSONL files to S3 for long-term retention
  (optional `boto3` dependency already declared).
- **SQL log backend** — Write logs to a relational database for structured
  queries (optional `sqlalchemy` dependency already declared).
- **Deterministic control loops** — Advanced guardrail patterns (auditor loops,
  tool-call sandboxing, semantic alignment) as described in
  `dev/feature-guardrails-deterministic-control-loops.md`.
