# Airlock Operations Guide

Production deployment, monitoring, and maintenance for Airlock.

## Deployment Options

### Docker Compose (single host)

```bash
# Build and start
docker compose up --build -d

# Verify
curl -f http://localhost:4000/livez
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

The deployment runs as non-root (UID 1000), sets resource limits (250m-1 CPU, 512Mi-1Gi RAM), and uses `/livez` for liveness and `/readyz` for readiness. No health endpoint makes model calls, so any of them is safe to probe at any interval.

### Bare Metal / VM

```bash
python -m venv /opt/airlock/.venv
source /opt/airlock/.venv/bin/activate
pip install -e ".[metrics,tracing]"
pip install "https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl"

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
| `config.local.yaml` | Empty tracked fallback plus uncommitted machine-specific overrides (MCP servers with absolute host paths), pulled in via `include:` in `config.yaml`. See [Configuration → Machine-specific overrides](getting-started/configuration.md#machine-specific-overrides-configlocalyaml). | Project root |

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
| `LITELLM_MCP_STDIO_EXTRA_COMMANDS` | No | — | Comma-separated extra command basenames allowed to launch stdio MCP servers, beyond the built-in `deno,docker,node,npx,python,python3,uvx`. Custom launcher binaries 403 at tool discovery without this. See [MCP Servers](guide/mcp-servers.md). |
| `AIRLOCK_ENABLE_FATHOMDB` | No | `0` | Enable lazy FathomDB engine initialization |
| `AIRLOCK_ENABLE_FATHOM_LOGGER` | No | `0` | Append the Fathom request logger at runtime without editing `config.yaml` |
| `AIRLOCK_BLOCKED_KEYWORDS` | No | — | Comma-separated restricted phrases |
| `AIRLOCK_PII_ENTITIES` | No | `CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` | Presidio entity types to redact |
| `AIRLOCK_PII_FAIL_MODE` | No | `open` | `open` stamps a value-free unavailable marker; `closed` blocks if redaction cannot run |
| `AIRLOCK_PII_EGRESS_MODE` | No | `observe` | Rehydration egress policy: `observe`, `shadow`, or `enforce` |
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

**No Airlock health endpoint makes a model call.** Probe any of them at any
frequency.

| Endpoint | Answers | Use for |
|----------|---------|---------|
| `GET /livez` | Is the process responsive? | Liveness probes, container restart decisions |
| `GET /readyz` | Can it serve traffic now? | Readiness probes, load-balancer membership |
| `GET /healthz`, `GET /health` | Aggregate status | Uptime checkers, dashboards, humans |
| `GET /health/live`, `GET /health/ready` | Same as `/livez` / `/readyz` | MicroProfile-style tooling |
| `GET /health/circuits` | Per-model circuit-breaker state | Diagnosing routing/circuit issues |
| `GET /health/latest` | Cached deep per-model results | Deep checking, when `background_health_checks` is on |
| `GET /health/liveliness`, `/health/liveness`, `/health/readiness` | Legacy LiteLLM paths, still supported | Existing manifests — prefer the canonical names above |

Probe endpoints are **unauthenticated** and respond as `application/health+json`
per the [IETF health check draft](https://datatracker.ietf.org/doc/html/draft-inadarei-api-health-check-06):

```json
{"status": "pass", "serviceId": "airlock", "version": "0.5.10",
 "checks": {"models:available": [{"status": "pass", "observedValue": 79}]}}
```

`status` is `pass`, `warn`, or `fail`. HTTP 200 for pass and warn, **503** for
fail. Readiness reports `warn` (200) when some model circuits are open but at
least one can still serve — withdrawing an instance because one provider is
rate-limited would turn a partial outage into a total one. It reports `fail`
(503) only when nothing can serve.

!!! note "`GET /health` changed in 0.5.9"
    It previously fired a live completion to **every configured model**, making
    the most-probed path in the ecosystem the most expensive one. It is now the
    cheap aggregate, with no option to restore the old behavior. For deep
    per-model results, enable `general_settings.background_health_checks: true`
    and read the cached results from `GET /health/latest` — that loop's rate is
    controlled by you rather than by whoever last pointed a probe at the proxy.

### Probe configuration

```yaml
# Kubernetes
livenessProbe:
  httpGet: {path: /livez, port: http}
readinessProbe:
  httpGet: {path: /readyz, port: http}
```

```yaml
# Docker Compose
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:4000/livez"]
```

```bash
# Uptime checker / load balancer
GET /healthz    # expect 200 and {"status": "pass"}
```

The `/health/circuits` endpoint is installed by the `model_override_headers`
callback (see [Callbacks](#callbacks)).

## Startup Modes

Airlock now keeps expensive startup work opt-in:

- `AIRLOCK_STARTUP_MODEL_DISCOVERY=0` skips provider/model discovery during startup. Set `1` only when you explicitly want an informational discovery pass.
- `AIRLOCK_MCP_STARTUP_MODE=off` removes `mcp_servers` from the runtime config.
- `AIRLOCK_MCP_STARTUP_MODE=lazy` keeps MCP configured but suppresses LiteLLM's startup-wide `list_tools()` sweep.
- `AIRLOCK_MCP_STARTUP_MODE=eager` keeps LiteLLM's default eager MCP probing behavior.

Lazy mode is implemented in `sitecustomize.py`, which replaces LiteLLM's
`initialize_tool_name_to_mcp_server_name_mapping` with a no-op when the proxy
starts. Python imports `sitecustomize` automatically, so the patch applies to
Airlock-owned subprocesses without wrapping the entrypoint.

Recommended low-noise startup profile:

```bash
AIRLOCK_STARTUP_MODEL_DISCOVERY=0
AIRLOCK_MCP_STARTUP_MODE=lazy
```

### Slow MCP servers at startup

In `eager` mode, LiteLLM lists tools from each configured MCP server with a
deadline of **30 seconds**, configurable through `LITELLM_MCP_TOOL_LISTING_TIMEOUT`:

```bash
LITELLM_MCP_TOOL_LISTING_TIMEOUT=60
```

Airlock deliberately does **not** add a competing setting of its own — two knobs
for one deadline is how they drift apart.

A server that exceeds the deadline does not silently produce an empty tool list.
LiteLLM raises a classified error naming the server, distinguishing `timeout`
(the server is slow) from `unreachable` (the server is down) — different
problems calling for different responses. `tests/test_mcp_startup_timeout.py`
pins that contract, because Airlock's operator guidance depends on it and a
future dependency bump that reintroduced silent-empty listing would otherwise
only surface as tools mysteriously missing in production.

If MCP tools appear to be missing, check the proxy log for a listing warning
naming the server before assuming a configuration problem. In the default
`lazy` mode there is no startup sweep at all, so a slow server cannot delay
startup — tools are discovered on first use instead.

### Verifying MCP servers after a restart

A restart re-reads config from disk **and** re-applies the current LiteLLM
behavior — including the stdio command allowlist and `include`/`config.local.yaml`
merge. A long-running process that predates a LiteLLM upgrade may serve MCP
servers that a fresh restart then rejects, so always re-verify MCP after
restarting. Because servers are launched lazily, a clean startup log does **not**
prove they work; tool discovery happens on first client request.

```bash
# 1. Liveness
curl -s http://localhost:4000/livez

# 2. Models are served
curl -s -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  http://localhost:4000/v1/models | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["data"]),"models")'

# 3. MCP tool discovery actually succeeds (REST helper at /mcp-rest; the bare
#    /mcp path is the SSE streaming transport, not JSON)
curl -s -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  http://localhost:4000/mcp-rest/tools/list | python3 -m json.tool

# 4. Scan stderr for per-server failures (basename allowlist 403, literal ${HOME}
#    in a path, ModuleNotFoundError / "Connection closed" at init)
grep -iE "allowlist|no such file|connection closed|modulenotfound" service-stderr.log | tail
```

See [MCP Servers](guide/mcp-servers.md) for the underlying constraints and fixes.

## FathomDB

FathomDB is optional and disabled by default.

- Set `AIRLOCK_ENABLE_FATHOMDB=1` to enable the lazy engine path.
- Set `AIRLOCK_ENABLE_FATHOM_LOGGER=1` to append the Fathom request logger at runtime.
- Put fresh databases under `AIRLOCK_STATE_DIR` while debugging. The database file is `airlock-fathom.db`; a `logs/airlock.db` left behind by FathomDB 0.3.x is abandoned — Airlock refuses to open it, and its records remain in the JSONL logs.

Current write-path guarantees:

- Airlock initializes the Fathom engine lazily.
- The in-process engine singleton is PID-bound and thread-safe, which avoids same-process `Engine.open()` races during concurrent callback writes.
- Forwarded inner `enhanced/*` provider calls do not emit duplicate Fathom rows.

### Per-client erasure

An operator can erase every FathomDB row a client produced, keyed on the
authenticated client id (`key:<last8>`, or `no_client` for unauthenticated
traffic):

```bash
airlock admin erase-client key:90abcdef --confirm key:90abcdef
# or, against the loopback admin API directly:
curl -X POST http://127.0.0.1:4000/airlock/admin/clients/key%3A90abcdef/erase \
  -H 'Content-Type: application/json' -d '{"confirm": "key:90abcdef"}'
```

- The operation is **loopback-only** and audited: the `admin_action` record
  carries the full `EraseReport` (nodes/edges excised, projections
  invalidated), not a bare "ok".
- An **incomplete** erasure is answered with HTTP 409 and never reported as
  done — the obligation is outstanding. Retrying is safe; erasure is
  idempotent.
- **Scope — read this before promising anything to a user.** This erases the
  client from the **search and analysis store** (FathomDB) only. The same
  records exist in the JSONL logs, which this operation does not touch; JSONL
  retention is governed separately by `AIRLOCK_MAX_LOG_DAYS`. A user-facing
  deletion obligation requires both, and the JSONL half is not automated in
  0.5.11.

Operational constraint:

- FathomDB remains single-owner at process level. Do not point multiple live processes at same `AIRLOCK_STATE_DIR/airlock-fathom.db`.
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
| `airlock_process_resident_memory_bytes` | Gauge | — | Latest LiteLLM worker RSS at request callback completion |
| `airlock_process_resident_memory_peak_bytes` | Gauge | — | LiteLLM worker RSS high-water mark since process start |
| `airlock_cgroup_memory_current_bytes` | Gauge | — | Current Airlock service cgroup memory use |
| `airlock_cgroup_memory_peak_bytes` | Gauge | — | Airlock service cgroup high-water mark |
| `airlock_cgroup_memory_high_bytes` / `airlock_cgroup_memory_max_bytes` | Gauge | — | Soft-pressure and hard-kill cgroup thresholds |
| `airlock_cgroup_memory_events` | Gauge | event | Current cgroup `high`, `max`, `oom`, and `oom_kill` counters |

The rate-limit and budget gauges are **observe-only** — they capture what providers
report without changing routing or what reaches the client. See
[Provider Quota Observability](guide/provider-observability.md). Alert when
`airlock_provider_ratelimit_remaining_tokens` falls below a fraction of its observed
ceiling, or when `airlock_provider_budget_used_usd` approaches
`airlock_provider_budget_limit_usd`.

For the bundled user service, `MemoryHigh=3G` starts cgroup reclaim and throttling
before the `MemoryMax=4G` hard limit. Alert on a rising
`airlock_cgroup_memory_events{event="high"}` and immediately investigate any
non-zero `oom` or `oom_kill`. These metrics are sampled after each LiteLLM
success/failure callback; they are lightweight kernel counters, not heap profiling.

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

Uses Microsoft Presidio. Airlock's shipped card, SSN, email, phone, US-bank, and
IBAN recognizers use Presidio's no-NLP engine; adding semantic entities such as
`PERSON` uses the spaCy model. Customize with `AIRLOCK_PII_ENTITIES`.

Reverse-redaction maps are bounded, request-scoped process memory addressed by
opaque handles; they are never metadata, telemetry, or log fields. Non-streaming
tool-call hydration returns a private response copy, keeping the telemetry object
redacted. Rehydration egress policy starts in `observe`: it emits value-free
tool/path/entity-class decisions but does not alter a response. In `shadow` and
`enforce`, unknown/exfil-capable paths keep placeholders unless explicitly
authorized. See `dev/notes/design-pii-rehydration-primary.md`.

### Keyword Blocking

Set `AIRLOCK_BLOCKED_KEYWORDS` to a comma-separated list. Case-insensitive matching against request content.

## Admission Control

Per-client rate limiting and concurrency caps. Off by default — opt in per deployment.

### Enabling

```yaml
# config.yaml (or config.local.yaml)
airlock_settings:
  admission:
    enabled: true
    rpm: 60              # requests per minute per client (default: 60)
    concurrency: 10      # max concurrent in-flight per client (default: 10)
    boost_multiplier: 1.5  # RPM multiplier for priority-boosted clients (default: 1.5)
```

Or via environment: `AIRLOCK_ADMISSION='{"enabled": true, "rpm": 30}'`

### Behavior

| Condition | Response |
|-----------|----------|
| RPM cap exceeded | HTTP 429 with `Retry-After` (seconds until token bucket refills) |
| Concurrency cap full | HTTP 429 with `Retry-After: 1` |
| Gate internal error | Request passes through (fail-open, warning logged) |

Clients with `PrioritySignal.boost=True` receive `rpm × boost_multiplier` allowance. All other clients share the base `rpm` cap equally.

The gate runs in the Fast guardian and applies to MCP and batch calls as well as
chat calls.  Content guards configured before the Fast guardian (such as the PII
guard) have already run by this point, so this gate is not a capacity boundary
for an earlier expensive guard.

### Known limitations (0.5.5)

- `X-Airlock-Admission` response header is not yet propagated (metadata is stamped internally; header wiring is a follow-up).
- The concurrency counter is process-local and is released by LiteLLM
  success/failure callbacks.  A delayed or missing callback can leave a stale
  slot until the process restarts; it has no request-lifetime watchdog.  It is
  therefore provider-request admission, not an exact permit mechanism for an
  expensive policy stage.  Airlock's internal resource-permit design records
  the required boundary for a future isolated expensive stage.

Full ops guide: `dev/notes/ops-admission-gate.md`.

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
StartLimitIntervalSec=5min
StartLimitBurst=3

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
2. Pull the new version: `git pull && ./scripts/setup.sh`
3. Run `airlock post` to validate configuration against the new version
4. If you use the bundled user unit, run `airlock install-service` to refresh
   it, reload systemd, and start the service. Otherwise, install the equivalent
   unit changes and restart the proxy: `systemctl restart airlock` or
   `docker compose up --build -d`.
5. Check `/livez` and review startup warnings in stderr

### Breaking Changes

Check the commit log for changes to:
- `config.yaml` schema (new required fields, renamed keys)
- Environment variables (renamed or removed)
- Guardrail behavior (new defaults, changed thresholds)

The startup config validator will warn about schema issues after upgrade.
