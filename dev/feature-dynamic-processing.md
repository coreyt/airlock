# Airlock — Dynamic Processing: Think Fast and Slow

This document describes the architecture of Airlock's dynamic processing
subsystem, which adds real-time adaptive behaviour ("fast") and offline
analytical intelligence ("slow") to the proxy.

---

## 1. Design Philosophy

The subsystem borrows its name from Daniel Kahneman's *Thinking, Fast and
Slow*.  The core insight is that a proxy needs **two fundamentally different
processing modes** operating in parallel:

| Mode | Operates on | Latency budget | Mechanism |
|------|-------------|---------------|-----------|
| **Fast** | Every request, in-band | < 1 ms | LiteLLM guardrail + callback |
| **Slow** | Accumulated logs, offline | Minutes–hours | CLI / cron analysis |

Fast reacts.  Slow reflects.  Together they form a closed loop: the slow
system discovers patterns and generates hypotheses; the fast system acts on
policies derived from those insights.

---

## 2. Fast Subsystem (`airlock/fast/`)

### 2.1 What It Does

On **every inbound request**, the fast subsystem performs three checks in a
single pre-call pass:

1. **Threat gate** — detect attacks or exploit attempts and apply
   exponential back-off.
2. **Circuit breaker** — detect unavailable models and transparently
   failover to a healthy alternative.
3. **Priority scoring** — identify clients that "need" a speed burst and
   tag the request with priority metadata.

On **every response** (success or failure), a callback feeds latency and
error metrics back into the in-memory state store, closing the feedback
loop.

### 2.2 Architecture

```
Inbound request
      │
      ▼
┌──────────────────────────────────────────────────┐
│  AirlockFastGuardian  (pre_call guardrail)       │
│                                                  │
│  1. Backoff check ─── client in backoff? REJECT  │
│  2. Threat assess ─── heuristic score > 0.7?     │
│     │                 → block + exponential wait  │
│  3. Circuit break ─── model circuit open?        │
│     │                 → swap to healthy fallback  │
│  4. Priority tag  ─── compute score, attach      │
│                       metadata for routing       │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
              Upstream LLM API
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  AirlockFastMonitor  (success/failure callback)  │
│                                                  │
│  • Update client latency + error tracking        │
│  • Update model health (circuit breaker state)   │
└──────────────────────────────────────────────────┘
```

### 2.3 Module Breakdown

```
airlock/fast/
├── __init__.py
├── state.py            Thread-safe in-memory state store (singleton)
│                       ClientState, ModelState, CircuitState, StateStore
├── priority.py         Priority scoring — defines "needs it"
├── circuit_breaker.py  Model health tracking + failover selection
├── threat_detector.py  Attack heuristics + exponential back-off
├── guardian.py         LiteLLM CustomGuardrail (pre_call) — the reactive mechanism
└── monitor.py          LiteLLM CustomLogger (callback) — the feedback loop
```

### 2.4 Defining "Needs It" — Priority Scoring

A client **needs** a speed burst when their composite priority score
crosses **0.6** (the `BOOST_THRESHOLD`).  The score is computed from four
real-time signals:

| Signal | Weight | Triggers when |
|--------|--------|---------------|
| **Interactive cadence** | 0.30 | avg inter-request gap ≤ 60 s across ≥ 3 recent requests (active coding session) |
| **Recovery need** | 0.35 | recent error rate > 30 % (client needs reliable next response) |
| **Latency spike** | 0.20 | current avg latency ≥ 2× the client's 30-min baseline |
| **Starvation** | 0.15 | > 5 requests with > 50 % errors (stuck, needs relief) |

The priority signal is attached as `metadata.airlock_priority` on the
request, making it available to downstream routing or queue-priority logic.

### 2.5 Circuit Breaker

Classic three-state circuit breaker per model:

```
           5 consecutive failures
  CLOSED ─────────────────────────→ OPEN
    ▲                                 │
    │  3 successful probes            │  30 s recovery timeout
    │                                 ▼
    └────────────────────────── HALF_OPEN
                                 (1 probe allowed)
```

When a model's circuit is OPEN, the guardian consults the **failover map**
(`AIRLOCK_FAILOVER_MAP` env var or built-in defaults) and transparently
rewrites `data["model"]` to the first healthy fallback.  The original
model and failover reason are attached as `metadata.airlock_failover`.

### 2.6 Threat Detection Heuristics

| Heuristic | Max contribution | Detection |
|-----------|-----------------|-----------|
| **Volume spike** | 0.40 | 30 s request rate > 10× the 5-min baseline |
| **Rapid-fire** | 0.35 | ≥ 10 requests with sub-100 ms inter-request gaps |
| **Payload anomaly** | 0.20 | Prompt text > 100 k characters |
| **Error probing** | 0.30 | > 80 % error rate over ≥ 10 recent requests |

Scores are blended with a decaying accumulated threat score (factor 0.95).
When the combined score exceeds **0.7**, the client is blocked and placed
in exponential back-off: 2 s → 4 s → 8 s → … → 1 hour max.

### 2.7 State Store

All fast-subsystem state lives in a **thread-safe, in-memory singleton**
(`airlock.fast.state.store`).  Per-client and per-model metrics use
bounded `deque` collections (max 1000 samples) with a 5-minute default
sliding window.  This means:

- Zero external dependencies (no Redis, no database).
- Bounded memory regardless of traffic volume.
- State resets on proxy restart (acceptable for a reactive system).

---

## 3. Slow Subsystem (`airlock/slow/`)

### 3.1 What It Does

The slow subsystem reads the JSONL logs produced by the enterprise logger
and performs **offline analysis** across four dimensions:

1. **Optimizations** — patterns that can be improved (high error-rate
   models, slow p95 latency, outlier token usage).
2. **Cache opportunities** — repeated identical prompts that would benefit
   from local or provider-side caching.
3. **Trends** — directional shifts in volume, model share, error rate,
   latency, and user concentration.
4. **Hypotheses** — testable statements derived from the data, each with
   a confidence score and a concrete test proposal.

### 3.2 Architecture

```
                JSONL log files
              (enterprise logger)
                      │
                      ▼
┌──────────────────────────────────────────────────┐
│         airlock.slow.analyzer                    │
│                                                  │
│  _load_logs(days)                                │
│       │                                          │
│       ├─→ find_optimizations()   → Optimization  │
│       ├─→ find_cache_opportunities() → CacheOpp  │
│       ├─→ find_trends()          → Trend         │
│       └─→ generate_hypotheses()  → Hypothesis    │
│                                                  │
│  Output: AnalysisReport                          │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│         airlock.slow.cli                         │
│                                                  │
│  airlock-analyze [--days N] [--json] [-o file]   │
│                                                  │
│  Human-readable text or machine-readable JSON    │
└──────────────────────────────────────────────────┘
```

### 3.3 The Data Access Mechanism

The slow system's access to data is the **JSONL log archive** — the same
structured logs the enterprise logger writes on every request.  Each
record contains timestamp, model, user, team, messages, response, token
counts, latency, and error details.

The analyzer loads records from `$AIRLOCK_LOG_DIR/airlock-YYYY-MM-DD.jsonl`
for the requested number of days and performs in-process analysis using
Python's standard library (no external analytics stack required).

### 3.4 Asking Internal Questions

The analysis functions answer specific internal questions:

- "Which models have high error rates?" → `find_optimizations()`
- "Are there repeated prompts we could cache?" → `find_cache_opportunities()`
- "Is usage shifting toward a particular provider?" → `find_trends()`
- "What would happen if we enabled caching?" → `generate_hypotheses()`

### 3.5 Hypothesis Generation

Hypotheses are the slow system's primary output.  Each hypothesis has:

| Field | Purpose |
|-------|---------|
| `statement` | A testable claim (e.g., "Enabling prompt caching could reduce token usage by ~12%") |
| `evidence` | The data points supporting the claim |
| `confidence` | 0.0–1.0 score reflecting data strength |
| `test_proposal` | A concrete experiment to validate the hypothesis |

Example hypotheses the system generates:

- *"Configuring automatic failover for 'gpt-4o' would reduce user-visible
  errors by ~25%"*  (confidence: 0.7, test: enable circuit breaker for
  48 hours and compare)
- *"Median latency increased 40% over the last 7 days — suggesting
  provider degradation"*  (confidence: 0.6, test: compare prompt length
  distributions)
- *"Usage of 'claude-sonnet' is increasing (15 pp shift) — consider
  volume pricing"*  (confidence: 0.5, test: monitor for 2 weeks)

### 3.6 Usage

```bash
# Default: analyze last 7 days, human-readable output
airlock-analyze

# Analyze 30 days, JSON output to file
airlock-analyze --days 30 --json -o report.json

# Pipe JSON to another tool
airlock-analyze --json | jq '.hypotheses[] | select(.confidence > 0.6)'
```

---

## 4. How Fast and Slow Work Together

The two subsystems form a **closed feedback loop**:

```
       ┌────────────────────────────────────────────┐
       │                                            │
       │  SLOW discovers:                           │
       │  "Model X has 30% error rate"              │
       │  "Prompt Y is repeated 50 times/day"       │
       │                                            │
       │  SLOW recommends:                          │
       │  "Enable circuit breaker for Model X"      │
       │  "Enable caching for Prompt Y"             │
       │                                            │
       └─────────────┬──────────────────────────────┘
                     │
           operator acts on recommendation
                     │
                     ▼
       ┌────────────────────────────────────────────┐
       │                                            │
       │  FAST enforces:                            │
       │  Circuit breaker auto-failovers Model X    │
       │  Priority boosts clients recovering from   │
       │    Model X failures                        │
       │  Threat detector blocks abuse patterns     │
       │                                            │
       │  FAST produces:                            │
       │  JSONL logs with latency, errors, usage    │
       │                                            │
       └─────────────┬──────────────────────────────┘
                     │
            logs feed back into slow
                     │
                     ▼
              (cycle repeats)
```

---

## 5. Configuration

### 5.1 Fast Subsystem

Registered in `config.yaml`:

```yaml
# Guardrail (pre_call)
guardrails:
  - guardrail_name: airlock-fast-guardian
    litellm_params:
      guardrail: airlock.fast.guardian
      mode: pre_call

# Callback (success/failure)
litellm_settings:
  success_callback: [..., "airlock.fast.monitor"]
  failure_callback: [..., "airlock.fast.monitor"]
```

Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AIRLOCK_FAILOVER_MAP` | built-in map | JSON object mapping model → fallback list |

### 5.2 Slow Subsystem

CLI entry point registered in `pyproject.toml`:

```toml
[project.scripts]
airlock-analyze = "airlock.slow.cli:main"
```

Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AIRLOCK_LOG_DIR` | `./logs` | Where to read JSONL log files |

---

## 6. Extension Points

| Extension | How |
|-----------|-----|
| New threat heuristic | Add to `threat_detector.assess_threat()` |
| Custom priority signal | Add to `priority.compute_priority()` |
| New failover strategy | Modify `circuit_breaker._load_failover_map()` |
| New analysis dimension | Add function to `analyzer.py`, call from `analyze()` |
| Scheduled slow runs | Call `airlock-analyze` from cron or a CI pipeline |
| Dashboard integration | Use `airlock-analyze --json` and pipe to your tooling |

---

## 7. Module Dependency Graph

```
airlock/fast/
├── state.py              ← no internal deps (standalone)
├── priority.py           ← depends on: state
├── circuit_breaker.py    ← depends on: state
├── threat_detector.py    ← depends on: state
├── guardian.py           ← depends on: state, priority, circuit_breaker, threat_detector
│                            + litellm.integrations.custom_guardrail
└── monitor.py            ← depends on: state
                             + litellm.integrations.custom_logger

airlock/slow/
├── analyzer.py           ← no internal deps (reads JSONL files)
└── cli.py                ← depends on: analyzer
```

No cross-dependencies between fast and slow.  No dependencies on the
existing guardrails or callbacks.  Each subsystem can be enabled or
disabled independently by editing `config.yaml`.
