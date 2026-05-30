# Architecture Overview

Airlock is a reverse proxy that sits between AI coding tools and LLM provider APIs. It intercepts every request, applies security guardrails, logs the interaction, and forwards the (potentially modified) request to the appropriate upstream provider.

## Components

```
airlock/
├── proxy.py              # Entry point — launches LiteLLM subprocess
├── callbacks/            # JSONL logger, S3, SQL, Prometheus, OpenTelemetry
├── guardrails/           # PII redaction, keyword blocking, semantic, adaptive
├── fast/                 # Real-time: threat detection, circuit breaker, priority
├── slow/                 # Offline: log analysis, trend detection, tuning
├── advisor/              # LLM-powered operational advisor (agent loop, tools, proposals)
├── hooks/                # Claude Code client-side hooks (session, prompt, audit)
├── cli/                  # Unified CLI: init, start, status, tui, analyze, advise, hooks
└── tui/                  # Textual terminal dashboard (6 screens, proxy control)
```

### Proxy (`proxy.py`)

Launches the LiteLLM proxy subprocess, validates config, and installs health endpoints. All requests flow through the LiteLLM router which handles provider translation, retries, and load balancing.

### Guardrails (`guardrails/`)

Nine-stage pipeline applied to every request. See [Guardrails](../guide/guardrails.md).

### Fast Subsystem (`fast/`)

Real-time request-path logic running on every inbound request:

- **Threat Detector** -- scores clients 0-1 across four heuristics (volume spike, rapid-fire, payload anomaly, error probing). Blocks at >= 0.7.
- **Circuit Breaker** -- per-model state machine (CLOSED -> OPEN after 5 failures -> HALF_OPEN after 30s -> CLOSED after 3 successes). Transparent failover to healthy models.
- **Priority Scorer** -- boosts interactive sessions and clients with high error rates.
- **Smart Router** -- classifies prompt complexity and routes to cost-appropriate model tier, and applies client routing directives (session affinity, cost tier, provider preference, budget awareness). See [Routing](../guide/routing.md).
- **StateStore** -- thread-safe in-memory registry of all client, model, provider, and MCP state. Sliding 5-minute windows, capped at 1000 samples per metric.

### Slow Subsystem (`slow/`)

Offline analysis designed to run periodically:

- **Analyzer** -- reads JSONL logs and produces reports across 5 dimensions: optimizations, cache opportunities, trends, semantic insights, and hypotheses.
- **Tuner** -- analyzes guardrail signal distributions and computes auto-tuned weights and thresholds, written to `airlock-knobs.json`.

### Advisor (`advisor/`)

LLM-powered operational assistant. Runs a bounded tool-calling loop against the proxy, querying operational data via 9 tools and proposing config changes. See [Advisor](../guide/advisor.md).

### Callbacks (`callbacks/`)

Pluggable event handlers for every request:

- **Enterprise Logger** -- structured JSONL with daily rotation
- **Prometheus Metrics** -- counters, histograms, gauges
- **OpenTelemetry Tracing** -- distributed traces
- **S3 Logger** -- batched export to S3
- **SQL Logger** -- SQLAlchemy-based persistence

### TUI (`tui/`)

Textual-based terminal dashboard with 6 screens. See [TUI Dashboard](../guide/tui.md).

### CLI (`cli/`)

Unified `airlock` command with subcommands. See [CLI Reference](../guide/cli.md).
