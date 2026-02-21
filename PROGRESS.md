# Airlock — Project Progress

## Status: Ready for End-to-End Trial

Last updated: 2026-02-20

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

## Readiness

All subsystems are real, wired into LiteLLM, and tested. No mocks in production
code. The end-to-end flow works:

```bash
airlock init --dir ~/trial    # scaffold config, .env, logs/
# edit .env with real API keys
airlock tui --start           # launch proxy + TUI in one command
# send requests to localhost:4000
```

Requests flow through PII redaction → keyword blocking → threat scoring →
upstream LLM → observation guardrails → JSONL logging → TUI dashboard.

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

## Test Suite

- **476 tests** across 25 test files
- **476 passing**, 0 failing
- Presidio engines shared via session fixture to avoid OOM
- TUI tests use async `app.run_test()` pattern
- ProxyManager tests cover subprocess lifecycle with mocked Popen

## Architecture Summary

| Subsystem | Location | Status |
|-----------|----------|--------|
| Proxy | `airlock/proxy.py` | Complete |
| Guardrails | `airlock/guardrails/` | Complete (7 guardrails wired) |
| Semantic Guard | `airlock/guardrails/semantic.py` | Orchestrator complete — awaiting classifiers |
| Callbacks | `airlock/callbacks/` | Complete (JSONL, S3, SQL, Prometheus, OTel) |
| Fast (real-time) | `airlock/fast/` | Complete |
| Slow (offline) | `airlock/slow/` | Complete — 5 dimensions (incl. semantic) |
| Hooks | `airlock/hooks/` | Complete |
| CLI | `airlock/cli/` | Complete (init, start, status, tui, analyze, hooks, dogfood) |
| TUI | `airlock/tui/` | Complete — 7 screens, proxy launch, search pending (#10) |
