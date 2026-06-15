# Airlock — Project Progress

> **Narrative changelog of what landed.** Live state for in-flight orchestrated
> work lives on the board (`dev/plans/runs/STATUS-<release>.md`), not here — see
> `dev/plans/README.md`. Do not duplicate live pack state into this file.

## Status: End-to-End Trial Ready + MCP Gateway

Last updated: 2026-04-08

## Planned — v0.4.0 (To-Do)

The batch follow-ups carried over from the Vertex/OpenAI batch work (PR #40) have
moved to **`dev/plans/0.4.0-plan.md`** (the AI Studio/Mistral batch gateway, the
`is_batch_call` seam, batch observability, and the §7 design decisions),
re-planned as orchestrated harness packs A→B→C with a live board at
`dev/plans/runs/STATUS-0.4.0.md`.

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
Textual-based terminal dashboard with 10 screens: Dashboard, Models, Threats,
Clients, Logs, Analysis, Settings, Flow, MCP Servers, Basic Chat. Includes
sidebar navigation and keyboard shortcuts (`1`–`9`, `0`).

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
traffic. 14 checks across 5 groups (config, providers, storage, guardrails,
MCP). Per-check timeout, colored/JSON output, `--skip-*` flags. Exit 0 if all
pass, 1 if any fail.

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
the routed model gets circuit-checked. 43 new tests.

### Native Complexity Routing (`model: smart`)
Clients send `model: "smart"` and Airlock auto-classifies prompt complexity to
route to the appropriate cost tier. Native heuristic classifier in `router.py`
— no ML dependencies, ~50μs latency.

Six weighted text features (sum to 1.0):
- Token count (0.30) — sigmoid mapping 20–150 words
- Code blocks (0.25) — fenced ``` or inline backticks
- Reasoning keywords (0.20) — 20-word frozenset, saturates at 3 hits
- Multi-step indicators (0.10) — compiled regex for numbered lists + sequencing words
- Vocabulary richness (0.10) — unique/total word ratio
- Sentence length (0.05) — weak tiebreaker

Composite score 0–1 maps to: `<0.30` → simple (low tier), `0.30–0.60` →
moderate (medium tier), `≥0.60` → complex (high tier). Thresholds configurable
via `AIRLOCK_SMART_THRESHOLDS` env var. 25 new tests.

### MCP Server Gateway
Dual-registration of all 7 guardrails for both LLM and MCP hooks. Unified text
extraction in `airlock/guardrails/extract.py` dispatches by `call_type`. MCP
tool guard (`mcp_tool_guard.py`) provides allowlist/blocklist + argument
sanitization (path traversal, shell metacharacters). Guardian skips
routing/circuit breaker for MCP calls but still applies threat detection and
priority scoring. Enterprise logger includes `call_type`, `mcp_tool_name`,
`mcp_server_name` in JSONL. TUI flow screen shows tool name for MCP calls.
POST checks include MCP config validation + guardrail hook registration.

### MCP Server Management
Three server types: remote (health-check only), local/managed (Airlock
starts/stops), stdio (LiteLLM per-call). `McpServerManager` follows
`ProxyManager` pattern — subprocess lifecycle, ring buffers, reader threads.
TUI screen 8 (MCP Servers) with DataTable, detail tabs (Info/Console/Tools),
Start/Stop/Restart/Probe buttons. Health probes: HTTP GET for URL-based,
`shutil.which()` for stdio binaries.

### MCP Visibility Across TUI (commit d062c97)
MCP indicators on all 5 existing TUI screens. `McpToolState` in state layer
with deque sliding windows for success/failure/latency tracking (modeled after
`ModelState` but without circuit breaker). Monitor callbacks track MCP tool
calls via `mcp_tool_name` metadata.

### Local vLLM Provider (Gemma 4)
Added support for a local vLLM-hosted Gemma 4 31B (AWQ quantized) model.
Configured as `gemma-4` in `config.yaml` via OpenAI-compatible API format
(`openai/gemma4-31b` with `api_base`). Provider prefix `gemma` → `vllm` added
to both `model_alias.py` and `router.py`. Fallback chain: `gemma-4` →
`claude-haiku` → `gemini-flash` → `mistral-small`.

### TUI Basic Chat Screen
Interactive LLM connectivity test screen (key `0`). Top control bar with
provider/model selects, inline params JSON field, and slide-out Parameter
Builder panel (temperature, max_tokens, top_p, top_k, stop, system prompt).
Four-quadrant layout: Q2 (user query text area), Q1 (response content with
token usage), Q3 (formatted request — URL, headers, JSON body), Q4 (raw
response — HTTP status, headers, JSON body). Requests route through the
Airlock proxy with full guardrail coverage.

### Code Review Fixes (commit e01d0eb)
8 issues fixed: memory (McpToolState deque limits), security (PII guard
recursive scrubbing, `_collect_strings` depth limit), deduplication
(`_check_arguments` reuses `_collect_strings`), streaming response scanner
accumulates text only, response scan metadata only on detection.

### Response Scanner (commit 08033c5)
`airlock/guardrails/response_scanner.py` — regex-only detection with 4 weighted
categories (injection 1.0, exfiltration 0.9, override 0.8, tool_call 0.7) and
composite scoring. Three separate hook methods for non-streaming, streaming, and
MCP response paths. Runs in critical path — microsecond-fast.

### Application-Level File Logging (commit 23ee6bd)
`configure_logging()` in `airlock/cli/main.py` sets up file handler (DEBUG+ to
`logs/airlock-YYYYMMDD-HHMMSS.log`) and stderr handler (WARNING+) on the
`airlock` root logger. All 20 child loggers inherit. Idempotent guard prevents
duplicate handlers. Only activates for real subcommands, not `--help`.

### MCP Server Config Validation (commit 4c1b95f)
Pre-validate `os.environ/` references in `mcp_servers` config before LiteLLM
startup. Clear error messages naming the missing variable and where to set it.
Validation runs in both `proxy.py` and `ProxyManager.preflight()`.

## Readiness

All subsystems are real, wired into LiteLLM, and tested. The end-to-end flow
has been **confirmed working with real API traffic**:

```bash
airlock init --dir ~/trial    # scaffold config, .env, logs/
# edit .env with real API keys
airlock post                  # validate config, keys, storage, guardrails, MCP
airlock tui --start           # launch proxy + TUI in one command
# send requests to localhost:4000
```

Requests flow through PII redaction → keyword blocking → threat scoring →
intelligent routing → circuit breaker → upstream LLM → response scanning →
observation guardrails → JSONL logging → TUI dashboard.

MCP tool calls flow through the same guardrail pipeline (except routing and
circuit breaker which are model-specific).

### Not yet present (does not block trial)
- TUI log search (issue #10)
- Hybrid sparse+dense search (issue #11, depends on #10)
- Semantic ML classifiers (orchestrator is wired, no models plugged in)
- Per-server MCP startup timeout (issue #20)
- Default enforce mode is `observe` (collects data, doesn't block via weighted system)

## Open Issues

| # | Title | Status |
|---|-------|--------|
| 10 | Basic keyword search for TUI logs | Open |
| 11 | Hybrid sparse+dense search backend (depends on #10) | Open |
| 16 | Code-as-tool-call detection (security guardrail) | Open |
| 20 | Per-server MCP startup timeout | Open |

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

- **876 tests** across 32 test files
- **876 passing**, 0 failing
- Presidio engines shared via session fixture to avoid OOM
- TUI tests use async `app.run_test()` pattern
- ProxyManager tests cover subprocess lifecycle, ring log, and output queue
- POST tests cover all 14 checks, rendering, JSON output, skip flags, timeouts

## Architecture Summary

| Subsystem | Location | Status |
|-----------|----------|--------|
| Proxy | `airlock/proxy.py` | Complete |
| Guardrails | `airlock/guardrails/` | Complete (8 guardrails wired, incl. response scanner) |
| Semantic Guard | `airlock/guardrails/semantic.py` | Orchestrator complete — awaiting classifiers |
| Callbacks | `airlock/callbacks/` | Complete — JSONL confirmed working with real traffic |
| Fast (real-time) | `airlock/fast/` | Complete — intelligent routing, circuit breaker, threat detection |
| Slow (offline) | `airlock/slow/` | Complete — 5 dimensions (incl. semantic) |
| Hooks | `airlock/hooks/` | Complete |
| CLI | `airlock/cli/` | Complete (init, start, status, post, tui, analyze, hooks, dogfood) |
| TUI | `airlock/tui/` | Complete — 10 screens, proxy launch, MCP management, basic chat |
| MCP Gateway | `airlock/guardrails/extract.py`, `mcp_tool_guard.py` | Complete — dual LLM+MCP guardrail protection |
