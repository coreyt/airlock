# Airlock Operations Guide

Production deployment, monitoring, and maintenance for Airlock.

## Deployment Options

### Docker Compose (single host)

```bash
# Build and start
docker compose up --build -d

# Verify
curl -f http://localhost:4000/health/liveliness
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

The deployment runs as non-root (UID 1000), sets resource limits (250m-1 CPU, 512Mi-1Gi RAM), and uses `/health/liveliness` for **both liveness and readiness** probes (it makes no model calls). Never point an automated probe at `/health` — it can trigger real provider work on every poll; reserve `/health` for on-demand/manual deep checks only.

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

### Native TLS

Airlock can terminate TLS itself instead of relying only on a reverse proxy. Set
**both** of these and Airlock serves HTTPS on the same `AIRLOCK_HOST:AIRLOCK_PORT`:

```bash
AIRLOCK_SSL_CERTFILE=/etc/airlock/tls/fullchain.pem
AIRLOCK_SSL_KEYFILE=/etc/airlock/tls/privkey.pem
```

Leave either unset to serve plain HTTP (the default). Clients only change the URL
scheme (`http://` → `https://`).

- **Certificates load at startup only** — renewal means a (rolling) restart. A
  front proxy is still preferable when you need hot cert rotation, an LB, or an
  HTTP→HTTPS redirect.
- Native TLS is what protects the admin/capability bearer tokens. If the
  [admin API](#admin-api) or capability skips are enabled on a non-loopback bind
  with TLS off, Airlock **refuses to start** unless you set `AIRLOCK_SSL_*`,
  `admin.behind_tls_proxy: true`, or `admin.allow_insecure_tokens: true`.

## Configuration

### Required Files

| File | Purpose | Location |
|------|---------|----------|
| `config.yaml` | Model list, guardrails, router settings | Project root or `AIRLOCK_CONFIG` |
| `.env` | API keys, master key, ports | Project root |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AIRLOCK_MASTER_KEY` | No | — | Optional proxy auth key. When unset or blank, Airlock strips the runtime `master_key` setting and accepts unauthenticated requests. |
| `ANTHROPIC_API_KEY` | Per provider | — | Anthropic API key |
| `OPENAI_API_KEY` | Per provider | — | OpenAI API key |
| `GOOGLE_AISTUDIO_API_KEY` | Per provider | — | Google AI Studio API key for Gemini models |
| `AIRLOCK_HOST` | No | `127.0.0.1` | Bind address. Set to `0.0.0.0` for Docker/Kubernetes or to expose externally. |
| `AIRLOCK_PORT` | No | `4000` | Listen port |
| `AIRLOCK_LOG_DIR` | No | `./logs` | JSONL log directory |
| `AIRLOCK_STATE_DIR` | No | `./logs` | State directory for the circuit-breaker checkpoint (`cb_state.json`), the provider-spend checkpoint (`spend_state.json`), and optional FathomDB files |
| `AIRLOCK_SPEND_CHECKPOINT_INTERVAL` | No | `60` | Seconds between provider-spend checkpoints to disk (the litellm child also checkpoints on shutdown) |
| `AIRLOCK_MAX_LOG_DAYS` | No | `30` | Days to retain log files |
| `AIRLOCK_MAX_LOG_SIZE_MB` | No | `500` | Max size per log file before rotation |
| `AIRLOCK_STARTUP_MODEL_DISCOVERY` | No | `0` | Opt-in provider/model discovery at startup |
| `AIRLOCK_MCP_STARTUP_MODE` | No | `lazy` | MCP startup behavior: `off`, `lazy`, or `eager` |
| `AIRLOCK_ENABLE_FATHOMDB` | No | `0` | Enable lazy FathomDB engine initialization |
| `AIRLOCK_ENABLE_FATHOM_LOGGER` | No | `0` | Append the Fathom request logger at runtime without editing `config.yaml` |
| `AIRLOCK_BLOCKED_KEYWORDS` | No | — | Comma-separated restricted phrases |
| `AIRLOCK_PII_ENTITIES` | No | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` | Presidio entity types to redact |
| `AIRLOCK_OTEL_SERVICE_NAME` | No | `airlock` | OpenTelemetry service name |

### Provider-spend durability across restart

As of 0.5.1, provider spend (used for budget warns and proactive cost-swaps) is a
rolling, time-windowed accumulator that is **checkpointed to disk and restored on
startup**, so a restart no longer zeroes accumulated spend. The checkpoint
(`spend_state.json` in `AIRLOCK_STATE_DIR`) is written by the litellm **child** process
every `AIRLOCK_SPEND_CHECKPOINT_INTERVAL` (default 60s) and on shutdown, and rehydrated
when the child restarts; `cb_state.json` circuit-breaker recovery rides the same path.
For durability, point `AIRLOCK_STATE_DIR` at a persistent volume (not an ephemeral
container layer). The accumulator is integer-micro-dollar and volume-independent, so it
no longer undercounts high-traffic (>1000 call/day) providers. *(In-memory / single
process — multi-worker durability via a shared backend is a future release.)*

### Startup Validation

At startup, Airlock validates:

1. **Master key** — warns if default, short (<16 chars), or missing. Missing/blank means runtime auth is removed for local/dev use.
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
| `GET /health` | Full health check (**calls every provider** when `background_health_checks` is off) | On-demand deep check / dashboards only — **never automated probes** |
| `GET /health/liveliness` | Lightweight liveness check (no model calls) | Liveness **and readiness** probes, load balancers, frequent polling |
| `GET /health/circuits` | Per-model circuit-breaker state (JSON) | Diagnosing routing/circuit issues |

> **Hard constraint:** liveness/readiness probes and any high-frequency poller MUST
> use `GET /health/liveliness`. `GET /health` fires live completions to every model
> when `background_health_checks` is off — running it on a 10–30 s probe interval
> hammers (and bills) every provider.

`/health` and `/health/liveliness` return HTTP 200 when healthy. The
`/health/circuits` endpoint is installed by the `model_override_headers` callback
(see [Callbacks](#callbacks)).

## Startup Modes

Airlock now keeps expensive startup work opt-in:

- `AIRLOCK_STARTUP_MODEL_DISCOVERY=0` skips provider/model discovery during startup. Set `1` only when you explicitly want an informational discovery pass.
- `AIRLOCK_MCP_STARTUP_MODE=off` removes `mcp_servers` from the runtime config.
- `AIRLOCK_MCP_STARTUP_MODE=lazy` keeps MCP configured but suppresses LiteLLM's startup-wide `list_tools()` sweep.
- `AIRLOCK_MCP_STARTUP_MODE=eager` keeps LiteLLM's default eager MCP probing behavior.

Recommended low-noise startup profile:

```bash
AIRLOCK_STARTUP_MODEL_DISCOVERY=0
AIRLOCK_MCP_STARTUP_MODE=lazy
```

## FathomDB

FathomDB is optional and disabled by default.

- Set `AIRLOCK_ENABLE_FATHOMDB=1` to enable the lazy engine path.
- Set `AIRLOCK_ENABLE_FATHOM_LOGGER=1` to append the Fathom request logger at runtime.
- Put fresh databases under `AIRLOCK_STATE_DIR` while debugging. Airlock treats old `logs/airlock.db` files as suspect until proven clean.

Current write-path guarantees:

- Airlock initializes the Fathom engine lazily.
- The in-process engine singleton is PID-bound and thread-safe, which avoids same-process `Engine.open()` races during concurrent callback writes.
- Forwarded inner `enhanced/*` provider calls do not emit duplicate Fathom rows.

Operational constraint:

- FathomDB remains single-owner at process level. Do not point multiple live processes at same `AIRLOCK_STATE_DIR/airlock.db`.
- Airlock's safeguards cover same-process callback concurrency and inherited PID mismatches, not intentional multi-process shared-writer access.

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

## Callbacks

Airlock registers LiteLLM callbacks via `config.yaml`. The default
`config.yaml` registers **one** telemetry callback —
`airlock.callbacks.recorder.recorder_callback` — plus the fast-path monitor and the
model-override-headers callback.

> **Since 0.5.4 — one event, one recorder.** Every per-request telemetry sink
> (enterprise/fathom/s3/sql loggers + Prometheus metrics) is now fed from a single
> canonical `RequestEvent` built once per request and fanned out by
> `recorder_callback`. You no longer register each sink in `config.yaml`. The recorder
> entry **must** stay **before** `airlock.fast.monitor.proxy_monitor` in
> `success_callback`/`failure_callback` (the recorder snapshots guardrail metadata
> before the monitor mutates it). The enterprise logger and metrics are **always-on**
> via the recorder; the optional sinks are gated by env flags (see below). Emitted
> records and counters are unchanged — only the opt-in mechanism moved from a
> `config.yaml` callback entry to an env flag.

```yaml
litellm_settings:
  callbacks: ["airlock.callbacks.model_override_headers.proxy_model_override_headers"]
  success_callback: ["airlock.callbacks.recorder.recorder_callback", "airlock.fast.monitor.proxy_monitor"]
  failure_callback: ["airlock.callbacks.recorder.recorder_callback", "airlock.fast.monitor.proxy_monitor"]
```

| Sink (fed by the recorder) | Module | Enabled by | Role |
|----------------------------|--------|------------|------|
| Enterprise logger | `airlock.callbacks.enterprise_logger` | always-on | Structured JSONL request/response logging (default) |
| Prometheus metrics | `airlock.callbacks.metrics` | always-on (`[metrics]` extra for the exporter) | Prometheus counters/histograms |
| Fathom logger | `airlock.callbacks.fathom_logger` | `AIRLOCK_ENABLE_FATHOM_LOGGER=1` | Optional FathomDB request logging |
| S3 logger | `airlock.callbacks.s3_logger` | `AIRLOCK_ENABLE_S3_LOGGER=1` (+ `AIRLOCK_S3_BUCKET`, `[s3]` extra) | Ship JSONL logs to S3 |
| SQL logger | `airlock.callbacks.sql_logger` | `AIRLOCK_ENABLE_SQL_LOGGER=1` (+ `AIRLOCK_SQL_URL`, `[sql]` extra) | Database logging |

Separately registered (not recorder sinks):

| Callback | Module | Role |
|----------|--------|------|
| Fast monitor | `airlock.fast.monitor.proxy_monitor` | Feeds circuit breaker / threat / priority state (default; registered after the recorder) |
| Model override headers | `airlock.callbacks.model_override_headers.proxy_model_override_headers` | Adds Airlock/Gemini response headers; installs the `/health/circuits` endpoint and enriched API docs (default) |
| OpenTelemetry tracing | `airlock.callbacks.tracing` | Trace export (`[tracing]` extra) |

## Monitoring

### Prometheus Metrics

Install with `pip install airlock-llm[metrics]` for the Prometheus exporter. Since
0.5.4 the per-request metrics are **always-on** via the recorder — you do **not** add
a metrics callback to `config.yaml` (the `recorder_callback` already fans the request
event out to the metrics sink). Just having the recorder registered (the default
`config.yaml`) emits the counters below.

Exposed metrics:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `airlock_requests_total` | Counter | model, user, success | Total proxied requests |
| `airlock_request_duration_seconds` | Histogram | model | Request latency |
| `airlock_pii_redactions_total` | Counter | entity_type | PII entities redacted |
| `airlock_keyword_blocks_total` | Counter | — | Keyword guard blocks |
| `airlock_threat_blocks_total` | Counter | — | Threat detector blocks |
| `airlock_circuit_breaker_state` | Gauge | model | 0=closed, 1=half_open, 2=open |
| `airlock_provider_ratelimit_remaining_tokens` | Gauge | provider | Tokens remaining against the provider's rate-limit window (from upstream `x-ratelimit-*`) |
| `airlock_provider_ratelimit_remaining_requests` | Gauge | provider | Requests remaining against the provider's rate-limit window |
| `airlock_provider_budget_used_usd` | Gauge | provider | USD spent against the provider's daily budget cap |
| `airlock_provider_budget_limit_usd` | Gauge | provider | Configured daily budget cap for the provider |

The rate-limit and budget gauges are **observe-only** — they capture what providers
report without changing routing or what reaches the client. See
[Provider Quota Observability](guide/provider-observability.md). Alert when
`airlock_provider_ratelimit_remaining_tokens` falls below a fraction of its observed
ceiling, or when `airlock_provider_budget_used_usd` approaches
`airlock_provider_budget_limit_usd`.

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

## Admin API

The admin control plane lets an operator mutate live protection state — clear a
provider quarantine after a verified credit top-up, reset a model circuit, clear a
client backoff — without a restart. It is **off by default**; when disabled,
`/airlock/admin/*` returns `404`. Enable it in `config.yaml`:

```yaml
admin:
  enabled: true
  trust_loopback: true
```

Authentication is either **loopback** (a connection from `127.0.0.1`/`::1` is the
operator, no credential) or a **Bearer token** — the master key or a scoped
capability JWT. Mint tokens locally with the CLI:

```bash
airlock admin mint-token --sub lme-ops --scope admin:clear_quarantine --ttl 15m
```

```bash
# Clear a draining quarantine after a credit top-up (probe = self-correcting half-open):
curl -X POST http://localhost:4000/airlock/admin/providers/openai/clear-quarantine \
     -d '{"mode":"probe"}'
```

Every mutation emits an `admin_action` record into the JSONL log as the audit trail.
The TUI's `c` clear-quarantine keybinding is a loopback client of this API. Full
reference, scopes, and the fail-closed TLS requirement: [Admin API](guide/admin-api.md).

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
