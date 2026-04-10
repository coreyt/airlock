# Airlock Operations Guide

Production deployment, monitoring, and maintenance for Airlock.

## Deployment Options

### Docker Compose (single host)

```bash
# Build and start
docker compose up --build -d

# Verify
curl -f http://localhost:4000/health
```

The compose file mounts `config.yaml` read-only and persists logs to `./logs/`. Set `AIRLOCK_PORT` in `.env` to change the listen port.

### Kubernetes

Manifests are in `deploy/k8s/`. Apply in order:

```bash
kubectl apply -f deploy/k8s/secret.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/ingress.yaml
kubectl apply -f deploy/k8s/hpa.yaml
```

The deployment runs as non-root (UID 1000), sets resource limits (250m-1 CPU, 512Mi-1Gi RAM), and includes liveness/readiness probes on `/health`.

### Bare Metal / VM

```bash
python -m venv /opt/airlock/.venv
source /opt/airlock/.venv/bin/activate
pip install -e ".[metrics,tracing]"
pip install spacy && python -m spacy download en_core_web_lg

# Copy config
cp config.yaml /opt/airlock/
cp .env /opt/airlock/

# Start
cd /opt/airlock && airlock start
```

Use systemd or supervisord for process management. See the systemd unit example below.

## Configuration

### Required Files

| File | Purpose | Location |
|------|---------|----------|
| `config.yaml` | Model list, guardrails, router settings | Project root or `AIRLOCK_CONFIG` |
| `.env` | API keys, master key, ports | Project root |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AIRLOCK_MASTER_KEY` | Yes | — | Authentication key for all clients. Must be >=16 chars. |
| `ANTHROPIC_API_KEY` | Per provider | — | Anthropic API key |
| `OPENAI_API_KEY` | Per provider | — | OpenAI API key |
| `AIRLOCK_HOST` | No | `127.0.0.1` | Bind address. Set to `0.0.0.0` for Docker/Kubernetes or to expose externally. |
| `AIRLOCK_PORT` | No | `4000` | Listen port |
| `AIRLOCK_LOG_DIR` | No | `./logs` | JSONL log directory |
| `AIRLOCK_MAX_LOG_DAYS` | No | `30` | Days to retain log files |
| `AIRLOCK_MAX_LOG_SIZE_MB` | No | `500` | Max size per log file before rotation |
| `AIRLOCK_BLOCKED_KEYWORDS` | No | — | Comma-separated restricted phrases |
| `AIRLOCK_PII_ENTITIES` | No | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` | Presidio entity types to redact |
| `AIRLOCK_OTEL_SERVICE_NAME` | No | `airlock` | OpenTelemetry service name |

### Startup Validation

At startup, Airlock validates:

1. **Master key** — warns if default, short (<16 chars), or missing
2. **Config schema** — warns on missing model_list, malformed guardrails, bad MCP server entries
3. **MCP env refs** — exits if MCP servers reference unset environment variables

Warnings print to stderr but do not block startup. MCP env errors are fatal.

### Config Validation

Run `airlock post` to validate your configuration without starting the proxy:

```bash
airlock post                          # full check
airlock post --skip-llm               # skip provider connectivity
airlock post --skip-llm --skip-mcp    # config + guardrails only
airlock post --json                   # machine-readable output
```

## Health Checks

| Endpoint | Purpose | Use For |
|----------|---------|---------|
| `GET /health` | Full health check (may call providers) | Readiness probes, monitoring |
| `GET /health/liveliness` | Lightweight liveness check | Liveness probes, frequent polling |

Both return HTTP 200 when healthy. Use `/health/liveliness` for high-frequency checks (load balancer, TUI polling).

## Logging

### JSONL Logs

Every request/response is logged as structured JSONL to `AIRLOCK_LOG_DIR`:

```
logs/
  airlock-2026-04-01.jsonl
  airlock-2026-04-02.jsonl
  ...
```

Each line contains: timestamp, model, user, team, request_id, messages, response, tokens, duration, guardrail metadata, and error details (on failure).

### Log Rotation

- **Daily partitioning** — one file per day (`airlock-YYYY-MM-DD.jsonl`)
- **Size rotation** — files exceeding `AIRLOCK_MAX_LOG_SIZE_MB` are rotated to `.1.jsonl`, `.2.jsonl`, etc.
- **Age cleanup** — files older than `AIRLOCK_MAX_LOG_DAYS` are deleted at startup

### Log Shipping

For production, ship logs to your SIEM:

- **S3**: Install with `pip install airlock-llm[s3]` and add the S3 callback to `config.yaml`
- **SQL**: Install with `pip install airlock-llm[sql]` for database logging
- **Filebeat/Fluentd**: Point at the `logs/` directory for the JSONL files

### Offline Analysis

```bash
airlock analyze              # analyze recent logs
airlock analyze --days 7     # last 7 days
```

## Monitoring

### Prometheus Metrics

Install with `pip install airlock-llm[metrics]` and add the metrics callback:

```yaml
litellm_settings:
  success_callback: ["airlock.callbacks.metrics"]
  failure_callback: ["airlock.callbacks.metrics"]
```

Exposed metrics:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `airlock_requests_total` | Counter | model, user, success | Total proxied requests |
| `airlock_request_duration_seconds` | Histogram | model | Request latency |
| `airlock_pii_redactions_total` | Counter | entity_type | PII entities redacted |
| `airlock_keyword_blocks_total` | Counter | — | Keyword guard blocks |
| `airlock_threat_blocks_total` | Counter | — | Threat detector blocks |
| `airlock_circuit_breaker_state` | Gauge | model | 0=closed, 1=half_open, 2=open |

### OpenTelemetry Tracing

Install with `pip install airlock-llm[tracing]` and add the tracing callback. Set `AIRLOCK_OTEL_SERVICE_NAME` to identify the service in your trace backend.

### TUI Dashboard

```bash
airlock tui --start    # start proxy + dashboard
airlock tui            # dashboard only (connect to running proxy)
```

The TUI provides real-time views of traffic, guardrail decisions, model status, and operational diagnostics across 6 screens:

| Key | Screen | Purpose |
|-----|--------|---------|
| `1` | Overview | Proxy health, guardrail status, model/client/provider overview |
| `2` | Guards | PII redaction stats, keyword blocking, guardrail signal details |
| `3` | Logs | JSONL log browsing with model/user/status filters |
| `4` | Config | Configuration viewer, MCP server management |
| `5` | Test | Interactive LLM connectivity testing (Basic Chat) |
| `6` | Advisor | LLM-powered operational diagnostics and config recommendations |

#### Basic Chat (Test screen)

The **Test** screen lets administrators test any configured model interactively. Select a provider and model from the dropdowns, compose a prompt, and send. The screen displays four quadrants:

- **Q2** (top-left): User query text
- **Q1** (top-right): Extracted response content with token usage
- **Q3** (bottom-left): Full outgoing request (URL, headers, JSON body)
- **Q4** (bottom-right): Full incoming response (HTTP status, headers, JSON body)

Use the **Parameter Builder** button to configure `temperature`, `max_tokens`, `top_p`, `top_k`, `stop` sequences, and `system` prompt without editing JSON directly. All requests route through the Airlock proxy with full guardrail coverage.

#### Advisor

The **Advisor** screen (key `6`) lets administrators ask natural-language questions about Airlock's operational state. The advisor uses an LLM (preferring local models) to query operational data and provide answers grounded in facts.

```bash
# CLI equivalent
airlock advise "why does claude-sonnet have a high error rate?"
airlock advise --interactive
airlock advise --local-only "what should I tune?"
airlock advise --host myproxy --port 8080 "check system health"
```

The advisor has access to 9 data-gathering tools: state snapshots, error logs, analysis reports, circuit health, config, guard signals, client/model profiles, and guardrail knobs. When it identifies actionable fixes, it proposes config changes with a diff preview and risk classification (low/medium/high).

**Privacy:** The advisor prefers local models (vLLM, Ollama — any model with a custom `api_base`) to avoid sending operational data to remote providers. When a remote model is used, a warning is displayed. Use `--local-only` to enforce this.

**Audit trail:** All advisor actions are logged to `logs/advisor-audit.jsonl`.

## Guardrails

### Enforcement Modes

Guardrails support progressive rollout:

| Mode | Behavior |
|------|----------|
| `observe` | Log signals only, never block |
| `shadow` | Log what would be blocked, but allow through |
| `enforce` | Block requests that exceed thresholds |

Start in `observe` mode, review logs, then promote to `enforce` when confident.

### PII Redaction

Uses Microsoft Presidio with the `en_core_web_lg` spaCy model. Default entities: credit cards, SSNs, emails, phone numbers. Customize with `AIRLOCK_PII_ENTITIES`.

### Keyword Blocking

Set `AIRLOCK_BLOCKED_KEYWORDS` to a comma-separated list. Case-insensitive matching against request content.

## Security Checklist

- [ ] Change `AIRLOCK_MASTER_KEY` from the default (`sk-airlock-change-me`)
- [ ] Use a key >= 16 characters
- [ ] Store API keys in environment variables or a secrets manager, not in config.yaml
- [ ] Run as non-root (Dockerfile and k8s manifests already enforce this)
- [ ] Place behind a reverse proxy (nginx/Caddy) with TLS for production
- [ ] Restrict network access to the proxy port
- [ ] Review `AIRLOCK_BLOCKED_KEYWORDS` for your organization
- [ ] Enable PII redaction for all client-facing deployments

## Shutdown

Airlock handles SIGTERM gracefully:

1. SIGTERM received by parent process
2. S3 logger buffers are flushed
3. `sys.exit(0)` triggers `atexit` handlers
4. LiteLLM subprocess receives the signal and shuts down

For Docker: `docker compose down` sends SIGTERM with a 10s grace period.
For Kubernetes: the default `terminationGracePeriodSeconds` (30s) is sufficient.

## Systemd Unit Example

```ini
[Unit]
Description=Airlock LLM Proxy
After=network.target

[Service]
Type=simple
User=airlock
WorkingDirectory=/opt/airlock
EnvironmentFile=/opt/airlock/.env
ExecStart=/opt/airlock/.venv/bin/airlock start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Upgrading

1. Back up `config.yaml` and `.env`
2. Pull the new version: `git pull && pip install -e .`
3. Run `airlock post` to validate configuration against the new version
4. Restart the proxy: `systemctl restart airlock` or `docker compose up --build -d`
5. Check `/health` endpoint and review startup warnings in stderr

### Breaking Changes

Check the commit log for changes to:
- `config.yaml` schema (new required fields, renamed keys)
- Environment variables (renamed or removed)
- Guardrail behavior (new defaults, changed thresholds)

The startup config validator will warn about schema issues after upgrade.
