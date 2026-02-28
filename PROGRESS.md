# Airlock — Project Progress

## Status: Intelligent Routing Complete

Last updated: 2026-02-28

## Completed Work

### PR #1 — Initial Release
Core proxy on LiteLLM with JSONL logging, PII redaction (Presidio), and
keyword blocking. Docker and pip-installable deployment.

### PR #2 — Guardrails Documentation
`dev/` folder with guardrails feature definition and deterministic control
loop design.

### PR #3 — Architecture & Requirements
User needs (UN-1 through UN-9), functional requirements (FR-1 through FR-16),
non-functional requirements (NFR-1 through NFR-10), and architecture doc.

### PR #4 — Dynamic Processing (Fast/Slow)
Real-time threat detection, circuit breaker, priority scoring (fast path).
Offline log analysis, trend detection, hypothesis generation (slow path).

### PR #5 — Development Agents
Agent definitions for development workflow.

### PR #6 — Presidio Research
Investigation into Presidio configuration, stop-word suppression, and
performance tuning.

### Issues #7, #8, #9 — Bug Fixes
- #7: Volume spike heuristic threshold was mathematically unreachable
- #8: OOM kills from Presidio — fixed with shared session fixtures
- #9: Broken pyproject.toml build-backend

### TUI Dashboard (commit ce400c0)
Textual-based terminal dashboard with 6 screens: Dashboard, Models, Threats,
Logs, Analysis, Settings. Includes sidebar navigation and keyboard shortcuts.

### Unified CLI (commits fcfcc06, cd2206a)
`airlock` command with subcommands: init, start, status, tui, analyze.
Pre-flight validation on start, health-check via status.
Requirements FR-17 through FR-23 and NFR-11.

### PR #14 / Issue #12 — Claude Code Hooks
Client-side hooks for Claude Code: SessionStart (proxy health), PreSubmit
(keyword blocking), PreTool (config file protection), PostTool (audit logging).
`airlock hooks install` and `airlock hooks status` commands.

### Issue #13 — Dogfooding (commit 0a643b2)
- Crash-resilient SessionStart hook with recovery guidance
- `dev/dogfooding.md` setup guide
- README Claude Code section expanded with hooks and dogfood commands
- Auth passthrough documented (LiteLLM `os.environ/` syntax)

### Guardrails Exploration (commit df87267)
Research document exploring Gen 1 → Gen 2 guardrails evolution: classifier
guardrail models, embedding-based topic filters, prompt injection classifiers,
NLI hallucination detection, LLM-as-judge, speculative guardrailing, tool-call
sandboxing. Maps what is achievable at the proxy level vs. requires model weight
access. Evaluates Guardrails AI, NeMo Guardrails, LLM Guard frameworks.

### Semantic Guard Orchestrator (commit dd93d84)
`airlock/guardrails/semantic.py` — thin `during_call` orchestration layer that
runs pluggable ML classifiers concurrently via `asyncio.gather` while the LLM
call proceeds in parallel. Pluggable `Classifier` protocol, fail-open by
default, attaches classifier verdicts (scores, thresholds, labels, durations) to
request metadata for downstream logging. 41 tests.

### Semantic Analysis Dimension (commit 1c6ec25)
Fifth analysis dimension in the slow analyzer: `find_semantic_insights()` reads
`airlock_semantic` metadata from JSONL logs and computes per-classifier score
distributions (mean/p50/p95/p99), block/error rates, latency profiles, ambiguous
zone detection (scores within ±20% of threshold), and cross-classifier agreement
tracking. Enterprise logger updated to persist all `airlock_*` metadata to log
records. Hypothesis generation extended with four semantic-specific patterns:
high block rate (threshold tuning), high ambiguity (LLM-as-judge escalation),
classifier errors (reliability), high latency (bottleneck). 28 new tests across
3 files.

### Adaptive Guardrails: Collect, Orchestrate, Enforce (commit dbdc6a4)
Three-phase guardrail evolution: (1) `during_call` observer collects signals at
zero latency cost, (2) orchestrator reads tuned knobs and evaluates weighted
guardrail outputs, (3) enforcer in `pre_call` applies adaptive blocking with
observe/shadow/enforce modes. Slow analyzer tunes guardrail parameters offline,
evolving them from binary block/allow to weighted scoring. Closed feedback loop.

### TUI Flow Screen (commit a494478)
Real-time guardrail pipeline monitor. Reads `airlock_observation` metadata from
JSONL logs, renders per-guardrail signal breakdown (scores, weights, contribution),
enforcement verdict (block/shadow/observe), and pipeline stage visualization.
Incremental log polling, pause/resume, keyboard navigation.

### TUI Proxy Launch & Control (commit bd1bc41)
`ProxyManager` class owns a LiteLLM subprocess on behalf of the TUI. Dashboard
gains Start/Stop button and collapsible console log streaming proxy stdout in
real time. `airlock tui --start` auto-launches the proxy on TUI startup.
Graceful cleanup on TUI exit (SIGTERM → wait → SIGKILL). 22 new tests.

### Power-On Self-Test (commit 5eea760)
`airlock post` command validates every external dependency before sending real
traffic. 12 checks across 4 groups (config, providers, storage, guardrails).
Per-check timeout, colored/JSON output, `--skip-*` flags. Exit 0 if all pass,
1 if any fail. 72 new tests.

### Proxy Console Ring Log (commits ec92dbe, 605a669)
ProxyManager now persists subprocess output to `logs/proxy-console.log` via a
reader thread that tees lines to both an output queue (for TUI) and a
`deque(maxlen=1000)` ring buffer. Lines flush to disk immediately. File compacts
to 1000 lines on stop and periodically during operation.

### End-to-End Trial Fixes (commits 567bf4d, 38e1c31, 5aae4b0, 852c68c)
Four issues found and fixed during first real-traffic trial:
1. **Health checks missing auth** — all 4 probe sites now send
   `Authorization: Bearer` when `AIRLOCK_MASTER_KEY` is set.
2. **Callbacks registered as classes** — LiteLLM `get_instance_fn` returns the
   class, not an instance. `isinstance(Class, CustomLogger)` is False, so
   callbacks silently never fired. Fixed with module-level instances.
3. **Callbacks in wrong list** — LiteLLM config `success_callback` only
   populates the sync list, but the proxy runs async. Self-register into all 4
   callback lists on module import.
4. **Guardrails opt-in by default** — LiteLLM guardrails require
   `default_on: true` in `litellm_params` to fire on every request. Added to
   all 6 guardrails.

### Intelligent Routing
Seven routing features — 3 config-only leveraging LiteLLM built-ins, 4 requiring
new Airlock code — giving clients composable routing directives.

**Config-only (Tier 1):**
1. ~~**Smart complexity router**~~ — commented out; LiteLLM auto-router requires
   `semantic_router` package + embedding model, not a lightweight classifier
2. **Cost-based routing** — `router_settings.routing_strategy: cost-based-routing`
3. **Provider budget caps + fallbacks** — daily budget limits per provider plus
   cross-provider fallback chains for all 14 models

**Airlock code (Tier 2):**
4. **Session affinity** — pin requests with the same `session_id` to a consistent
   model for the session TTL (default 1 hour)
5. **Cost tiers** — restrict model selection to low/medium/high cost tier via
   `metadata.airlock.cost_tier`
6. **Provider preference** — soft tiebreaker among viable models via
   `metadata.airlock.prefer_provider`
7. **Budget awareness** — proactively swap away from providers at >90% of daily
   budget to avoid 429s

Directive priority: session affinity > cost tier > provider preference > budget
awareness. Router runs in guardian between threat check and circuit breaker so
the routed model gets circuit-checked. 43 new tests (612 total).

## Readiness

All subsystems are real, wired into LiteLLM, and tested. The end-to-end flow
has been **confirmed working with real API traffic**:

```bash
airlock init --dir ~/trial    # scaffold config, .env, logs/
# edit .env with real API keys
airlock post                  # validate config, keys, storage, guardrails
airlock tui --start           # launch proxy + TUI in one command
# send requests to localhost:4000
```

Requests flow through PII redaction → keyword blocking → threat scoring →
intelligent routing → circuit breaker → upstream LLM → observation guardrails →
JSONL logging → TUI dashboard.

PII redaction confirmed: email, credit card, and phone numbers scrubbed by
Presidio before reaching upstream providers. JSONL logs capture full structured
records with request/response, token counts, and timing.

### Not yet present (does not block trial)
- TUI log search (issue #10)
- Hybrid sparse+dense search (issue #11, depends on #10)
- Semantic ML classifiers (orchestrator is wired, no models plugged in)
- Default enforce mode is `observe` (collects data, doesn't block via weighted system)

## Open Issues

### Issue #10 — Basic Keyword Search for TUI Logs
Add free-text keyword search to the Logs screen. Case-insensitive substring
matching against request messages and response content. Wire up `/` keybinding
per TUI design doc. Client-side filtering over loaded records.

### Issue #11 — Hybrid Sparse+Dense Search Backend
Combine BM25 keyword search with embedding-based semantic search for log
retrieval. Reciprocal rank fusion scoring. Local index storage
(SQLite + faiss). Optional `pip install airlock[search]` dependencies.
Depends on #10 completing first.

## Performance Benchmark — 2026-02-22

Live test: 7 sequential queries through a running Airlock proxy (5× claude-haiku, 2× gpt-4o-mini).

### Latency

| Metric | Value |
|--------|-------|
| Avg proxy time (Airlock-measured) | 1113ms |
| Avg client wall clock | 1137ms |
| **Avg proxy overhead** | **~23ms** |
| Fastest request | gpt-4o-mini math (482ms proxy) |
| Slowest request | gpt-4o-mini story (2010ms proxy) |

The 23ms overhead is local loopback + LiteLLM routing. Calling Airlock vs calling the provider directly is effectively zero-cost.

### Guardrails

Each request ran 3 during_call guardrails (pii_scan, keyword_scan, threat_read) concurrently with the LLM call:

- Guardrail wall time: **~0.03ms per request** (runs inside `asyncio.gather` alongside the API call — adds zero latency)
- Composite threat score: 0.000 on all requests
- Blocks triggered: 0

### Raw data (proxy-measured `duration_ms` vs client wall clock)

| Query | Model | Proxy ms | Client ms | Overhead |
|---|---|---|---|---|
| simple-math | claude-haiku | 948 | 1004 | +56ms |
| short-story | claude-haiku | 853 | 870 | +17ms |
| code-snippet | claude-haiku | 1842 | 1859 | +17ms |
| explain-concept | claude-haiku | 980 | 996 | +16ms |
| haiku | claude-haiku | 679 | 696 | +17ms |
| gpt-simple | gpt-4o-mini | 482 | 512 | +30ms |
| gpt-story | gpt-4o-mini | 2010 | 2019 | +9ms |

All 7 succeeded. JSONL logs confirmed written to `logs/airlock-2026-02-22.jsonl`.

## Test Suite

- **612 tests** across 28 test files
- **612 passing**, 0 failing
- Presidio engines shared via session fixture to avoid OOM
- TUI tests use async `app.run_test()` pattern
- ProxyManager tests cover subprocess lifecycle, ring log, and output queue
- POST tests cover all 12 checks, rendering, JSON output, skip flags, timeouts

## Architecture Summary

| Subsystem | Location | Status |
|-----------|----------|--------|
| Proxy | `airlock/proxy.py` | Complete |
| Guardrails | `airlock/guardrails/` | Complete (7 guardrails wired) |
| Semantic Guard | `airlock/guardrails/semantic.py` | Orchestrator complete — awaiting classifiers |
| Callbacks | `airlock/callbacks/` | Complete — JSONL confirmed working with real traffic |
| Fast (real-time) | `airlock/fast/` | Complete — intelligent routing, circuit breaker, threat detection |
| Slow (offline) | `airlock/slow/` | Complete — 5 dimensions (incl. semantic) |
| Hooks | `airlock/hooks/` | Complete |
| CLI | `airlock/cli/` | Complete (init, start, status, post, tui, analyze, hooks, dogfood) |
| TUI | `airlock/tui/` | Complete — 7 screens, proxy launch, search pending (#10) |
