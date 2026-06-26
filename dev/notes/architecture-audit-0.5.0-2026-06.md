# Architecture Audit — Airlock @ 0.5.0 (2026-06)

> Code-verified architectural audit taken at the close of the 0.5.0 train
> (branch `feat/0.5.0-resilience-admin`). Scope: (1) Airlock's stewardship of
> LiteLLM, (2) Airlock's own internal architecture against named patterns, and
> (3) hot-path latency for client→LLM interactions. Findings cite `file:line`.
> Companion roadmap: the tiered work below is distributed into
> `dev/plans/0.5.1-plan.md` (already-scoped), `dev/plans/0.5.3-plan.md`
> (correctness/latency + structural hygiene), and `dev/plans/0.5.4-plan.md`
> (bulkhead/isolation).

## Method

Four parallel read-only sweeps (LiteLLM integration, `fast/` resilience core,
request pipeline + latency, cross-cutting patterns), then direct verification of
the load-bearing claims (Presidio inline-sync, the `enterprise_logger`
monkey-patch, the `fast`↔`guardrails` import cycle, the budget triple-source).
Claims that an agent overstated are corrected inline below.

## TL;DR verdict

The architecture is **fundamentally sound for its role** — a thin, mostly
idiomatic layer over LiteLLM with an excellent control plane and a workable
facade. Three systemic weaknesses limit its evolution, plus one verified latency
hazard:

1. **It reinvents state/spend LiteLLM already provides** and keeps three
   disagreeing "budget" sources (two of them buggy).
2. **Module-global singletons + a `state.py` god-object + a `fast`↔`guardrails`
   import cycle** undercut testability and the documented layering.
3. **Observability is multi-sink but not event-driven** (4× duplicated record
   builders).
4. **Presidio PII runs synchronously on the event loop** (`pii_guard.py:212`),
   serializing concurrent requests when `AIRLOCK_PII_ENABLED`.

None of these are blockers; the post-call/streaming hot path is well engineered
and the resilience/admin work shipped in 0.5.0 is solid.

---

## Part 1 — LiteLLM stewardship: considerate, but coupled to internals

**Overall: a good citizen on the extension surfaces, fragile on the internals.**

### Leverages LiteLLM correctly ✅

- **Idiomatic extension points.** Guards subclass `CustomGuardrail`; loggers
  subclass `CustomLogger`; the official hooks are used —
  `async_pre_call_hook`, `async_log_success_event`,
  `async_post_call_response_headers_hook`,
  `async_post_call_streaming_iterator_hook`. Custom providers
  (`providers/enhanced_passthrough.py`, `providers/tavily_provider.py`) use the
  provider plug-in surface.
- **Thin launcher.** `proxy.py` runs LiteLLM as a subprocess and lets it own HTTP
  serving, key validation, routing, and retries — Airlock does not reimplement the
  proxy.
- **Stable primitives imported directly** (`BudgetConfig`, `duration_in_seconds`)
  per the 0.5.1 plan.

### Reinvents / duplicates LiteLLM ⚠️

- **State/cache is hand-rolled.** Airlock keeps its own `fast/state.py` store
  (`deque`-based) rather than LiteLLM's `DualCache`. All ~12 `DualCache`
  references are *received* from LiteLLM in hooks, not used as Airlock's own
  store. The existing `ProviderSpend.spend_records = deque(maxlen=1000)`
  (`state.py`) duplicates LiteLLM's own `provider_spend:{provider}:{duration}`
  tally **and** undercounts past 1000 calls/day (register R5). 0.5.1 STORE-seam
  corrects both.
- **"Budget" has three disagreeing owners** — the standout coherence smell:
  1. LiteLLM hard block (`router_settings.provider_budget_config`),
  2. monitor warn (`monitor.py` — reads the **wrong nesting**, top-level instead
     of `router_settings`, so it is effectively dead unless `AIRLOCK_PROVIDER_BUDGETS`
     is set — register R6),
  3. router proactive swap (`router.py` — env-only + hardcoded
     `_DEFAULT_PROVIDER_BUDGETS`, **never consults config.yaml** — register R1),
     at a different warn ratio (0.9 vs the monitor's 0.8 — register R3).
  One concept, three sources, two buggy. **0.5.1 is scoped exactly here.**
- **Pre-call failover swap vs LiteLLM post-call `fallbacks`** — two mechanisms for
  one concern; the Airlock side hardcodes `gpt-4o` targets absent from
  `model_list` (register R2). 0.5.1 derives the breaker map from
  `router_settings.fallbacks`.

### Fragile internal coupling 🔴

- **Reaches into LiteLLM internals:** `response._hidden_params`,
  `response.custom_llm_provider`, and middleware injection via
  `sys.modules.get("litellm.proxy.proxy_server")` poking `app.middleware_stack` /
  `app.state` (`batch/middleware.py`, `admin/http.py`). These work but are
  undocumented surfaces; the version range is wide (`litellm[proxy]>=1.83.4,<2`,
  `pyproject.toml:44`) while behavior is pinned to **1.89.0** assumptions.
- **Defensive monkey-patch** of `LowestCostLoggingHandler.async_log_success_event`
  (`enterprise_logger.py:549`, `_patch_lowest_cost_none_guard`) — *corrected
  reading:* this is a None-crash guard around a LiteLLM bug, **not** a reinvention,
  but it is brittle to any refactor of `litellm.router_strategy.lowest_cost`.
- **No Anti-Corruption Layer.** Every internal access is scattered inline. For a
  project whose entire value is wrapping LiteLLM, the absence of a single adapter
  isolating internal reads is the biggest threat to upgrade survivability.

**Recommendation:** a `litellm_adapter.py` ACL that is the *only* place that
touches `_hidden_params`, wrapper attributes, and the proxy-app object
(→ 0.5.3); land the 0.5.1 budget unification; adopt `DualCache` behind the
STORE-seam (→ 0.5.1) so the spend tally stops shadowing LiteLLM's.

---

## Part 2 — Airlock's own architecture

### Patterns scorecard

| Concern / Pattern | Verdict | Evidence |
|---|---|---|
| **Control plane (PDP/PEP)** | ★★★★★ Excellent | `admin/policy.py` `decide()` is a clean PDP; `admin/http.py` is the enforcement perimeter; tokens scoped + audited. Best subsystem. |
| **Circuit breaker** | ★★★★ Good | `fast/circuit_breaker.py` thin, delegates to store; half-open clear semantics well thought-out. |
| **Strategy (backends)** | ★★★★ Good | `batch/backend.py` `BatchBackend` protocol with swappable adapters. |
| **BFF / context-aware facade** | ★★★ Present but scattered | Facade role is real (model-alias resolution, `/v1/models` augmentation, served-by headers, OpenAI-compat) but split across `models_catalog.py`, `fast/model_alias.py`, `transparency.py`, `model_override_headers.py` — no single coherent adapter. |
| **Separation of concerns / layering** | ★★★ Partial | Documented layering violated by a **`fast`↔`guardrails` import cycle** (`fast/guardian.py:41` ↔ `guardrails/observer.py:34`) and by `model_override_headers.py` acting as a hidden bootstrap installing 5 subsystems. |
| **Dependency injection** | ★★★ Mixed | Good `configure_*`/`set_*` wiring at startup, but they write **module-global mutable singletons** (`store = StateStore()`, `_breaker_default`, `_configured_budgets`) imported by ~38 files. Hard to test; fragments under multi-worker. |
| **Chain of responsibility (pipeline)** | ★★ Partial | Guards are a config-ordered *sequence*, not a composable chain — no programmatic composition, no conditional short-circuit, results not shared (message text extracted 3×). |
| **Telemetry / event-driven** | ★★ Weakest | **4 duplicated `_build_record()`** (enterprise/fathom/s3/sql loggers) + a separate mutation ledger + metrics counters + served-backend attribution. Multiple sinks, no shared event model. The one genuinely event-sourced path is JSONL→`tail_jsonl`/`ingest_jsonl_record` (the TUI replica, CC-9) — good, but it is the exception. |
| **Cohesion** | ★★ god-object | `fast/state.py` (~1400 LOC) owns client metrics, circuit state, provider quarantine, spend, rate-limit, MCP health, JSONL ingest, checkpoint/restore, and admin mutators. 7+ reasons to change. |
| **Anti-Corruption Layer** | ★ Missing | (see Part 1) |
| **Bulkhead / isolation** | ◐ Fault-isolation only | Per-client breaker + bounded fallback contain *failures* (UN-14/15/18). *Resource/throughput* isolation (admission control, fair queueing, process isolation) is absent; the one concrete proposal is parked out-of-scope (`design-circuit-breaker-per-client.md:189`). → 0.5.4. |

### Recurring root causes

Three things explain most findings:

1. **Global singletons instead of injected services** → testing friction, the
   import cycle, and the multi-worker hazard 0.5.1 already flags.
2. **The `state.py` god-object** → durability, spend, and event concerns are
   entangled (this is *why* the FIX-1 checkpoint-in-wrong-process and R5
   undercount bugs hide there).
3. **Sink-per-logger instead of one event** → observability duplication and
   divergence risk.

### Correctness issues surfaced (verified or plan-confirmed)

- **FIX-1 (critical):** `checkpoint_state`/`restore_state` run in the **launcher**
  process, but state mutates only in the **child** → checkpoints persist an empty
  store; breaker recovery across restart is a silent no-op today. (0.5.1
  STORE-seam fixes this.)
- **`threat_score` write outside the lock** (`threat_detector.py`) — a real but
  low-frequency race on concurrent same-client requests. → 0.5.3.
- **Duplication of client-identity extraction** in 3 places
  (`fast/state.normalize_client_id`, `enterprise_logger`, `client_identity.py`)
  and config loading in 4+ places. → 0.5.3 (consolidate).

---

## Part 3 — Latency & throughput on the client→LLM hot path

Mostly disciplined, with **one verified hazard**.

| Inline work | Sync/async | When | On hot path? | Status |
|---|---|---|---|---|
| **Presidio PII (`analyzer.analyze`)** | **SYNC inside `async`** (`pii_guard.py:212`, no `to_thread`) | pre_call | **Yes** (when `AIRLOCK_PII_ENABLED`) | 🔴 **Blocks the event loop ~50–200ms/req** — serializes concurrency |
| Local vLLM `/models` probe | async (awaited) | pre_call | vLLM aliases only | 🟠 5s cache; network on misses — widen TTL / prewarm |
| Keyword scan | sync, fast | pre_call | Yes | 🟡 O(n·m); fine for small keyword sets |
| Guardian threat / priority | sync, fast | pre_call | Yes | 🟡 O(n) deque scans, repeated across guards (no shared extraction) |
| Circuit / alias / enhanced | sync, <2ms | pre_call | Yes | ✅ cheap |
| Response headers + transparency | sync, <1ms | post-response | No | ✅ off TTFT |
| Streaming scan | chunks yielded first | streaming | No | ✅ no TTFT impact |
| Enterprise / fathom loggers | `asyncio.to_thread` | post-call | No | ✅ correctly offloaded |

**Good news:** the post-call and streaming paths are well engineered — chunks are
forwarded before scanning, logging is thread-offloaded, header serialization is
byte-bounded and post-response. **TTFT is protected.**

**The one real fix:** wrap the Presidio call in `await asyncio.to_thread(...)` —
the single highest-ROI latency change (recovers concurrency under load, ~1h).
Secondary: cache extracted message text once in `data["metadata"]` so
PII/keyword/guardian don't each re-walk the messages. Both → 0.5.3.

The **middleware stack is cheap** — batch/admin/header middlewares fall through
quickly for non-matching routes with no per-request body buffering.

---

## Prioritized recommendations → release mapping

**Tier 1 — correctness/latency, small:**
1. Offload Presidio to a thread (`pii_guard.py`). — **0.5.3** *(new)*
2. Budget unification (R1/R3/R6). — **0.5.1** *(already scoped: SET-loader/unify/warnratio)*
3. FIX-1 checkpoint in the child process. — **0.5.1** *(already scoped: STORE-seam)*

**Tier 2 — structural, medium:**
4. LiteLLM Anti-Corruption Layer (one module owns `_hidden_params`/app-object). — **0.5.3** *(new)*
5. Adopt `DualCache` + rolling-window spend (fix R5). — **0.5.1** *(already scoped: STORE-seam)*
6. Break the `fast`↔`guardrails` import cycle; extract a `proxy_bootstrap.py`. — **0.5.3** *(new)*

**Tier 3 — larger, deliberate:**
7. Replace the global `store` singleton with an injected `StateProvider` (also
   unblocks multi-worker). — **0.5.4** *(enabler for isolation; see plan)*
8. Unify observability behind one `RequestEvent` + recorder; collapse the 4
   `_build_record()`s. — **0.5.3** *(candidate/stretch)*
9. Split `fast/state.py` into core/spend/persistence/mcp. — **0.5.4** *(pairs with #7)*

**Bulkhead / isolation (resource, not fault):** exploration + trade-off + impl. —
**0.5.4** *(new)*

---

## Closing assessment

Airlock achieves its goal of being an invisible, OpenAI-compatible shim that adds
security, resilience, and observability without reimplementing the proxy. The
control plane is exemplary; the resilience and transparency work shipped in 0.5.0
is well-reasoned. The debt that matters is **coupling-shaped, not feature-shaped**:
isolate the LiteLLM internals (ACL), stop shadowing LiteLLM's state (DualCache),
de-globalize the store (StateProvider), and unify the event model. Doing so makes
every future LiteLLM bump cheaper and unblocks multi-worker — which is itself the
gateway to true bulkhead isolation. The roadmap (0.5.1 → 0.5.3 → 0.5.4) sequences
these in dependency order.
