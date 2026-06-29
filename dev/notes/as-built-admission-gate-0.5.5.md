# As-Built: C1 Admission Gate — 0.5.5

_Date: 2026-06-28. Implements per-client RPM token-bucket + concurrency cap in the
guardian pre-call hook. Off-by-default. Closes UN-23, partially closes UN-24._

---

## What Was Built and Why

The admission gate (C1) was selected over four alternatives (C2 process isolation,
C4 multi-process orchestration, C5 hybrid) based on Phase 0 benchmark evidence — see
`dev/notes/design-bulkhead-isolation.md` for the full candidate analysis. The short
version: the event loop is not CPU-saturated, so the noisy-neighbor problem is
queueing contention in the pre-call hook, not GIL contention. C1 resolves it with a
synchronous O(1) gate at hook entry: requests that exceed the client's cap are shed
immediately with 429 + Retry-After rather than queued.

C3 (DualCache-coordinated counters) was adopted as an accounting seam — not an
isolation mechanism — so a future Redis backend for distributed rate-counting is a
config flip rather than a rewrite.

## Module Layout

```
airlock/fast/
    admission.py        # AdmissionStore, AdmissionGate, configure_admission()
    settings.py         # AdmissionConfig dataclass; _load_admission(); loaded into AirlockSettings
    guardian.py         # Step 2.5 gate inserted between assess_threat and routing
    proxy.py            # configure_admission(config) called at startup
```

**`admission.py`** owns the runtime objects. `AdmissionStore` is a plain-dict-of-deques
rolling counter (no DualCache dependency; no litellm import at module load). Each
client maps to a `deque[float]` of request timestamps; `recent_count()` prunes stale
entries as a side-effect, keeping memory O(requests within the window). `AdmissionGate`
wraps the store with per-client `asyncio.Semaphore` objects and exposes a single
`check(client_id, boost, now) -> (allowed, retry_after_s)` method. The module-level
`_admission_gate: AdmissionGate | None` is set to `None` when disabled (the default),
so the guardian hot path is a single cheap None-check.

**`settings.py`** defines `AdmissionConfig` as a frozen dataclass with four fields
(`enabled`, `rpm`, `concurrency`, `boost_multiplier`) and `_load_admission()` which
implements `AIRLOCK_ADMISSION` (JSON env) > `airlock_settings.admission` (config block)
> defaults precedence, consistent with other AirlockSettings loaders.

**`guardian.py`** inserts Step 2.5 between threat assessment (Step 2) and routing
(Step 3). When `_admission_gate` is not None and `check()` returns `(False, retry)`,
it raises `ValueError(f"Too many requests — Retry-After: {retry:.1f}s")`, which the
LiteLLM proxy surfaces as HTTP 429. It also stamps `metadata["airlock_admission"]`
with the shed reason for downstream inspection.

**`proxy.py`** calls `configure_admission(config)` once at startup, after
`configure_settings`, so the gate is ready before the first request.

## Key Design Choices

**Fail-open.** Any exception inside `AdmissionGate.check()` is caught, logged at
`WARNING`, and the request is admitted. The gate never blocks traffic due to its own
error.

**Off-by-default.** `admission.enabled` defaults to `False`. Operators opt in
explicitly. Existing deployments are unaffected.

**Shed, not queue.** Excess requests receive an immediate 429 + Retry-After. There
is no waiting queue. This gives clients an actionable signal and keeps proxy memory
bounded.

**Token bucket vs DualCache.** The initial `AdmissionStore` is a plain
threading-locked dict-of-deques rather than a DualCache-backed SpendStore. The
DualCache seam is the planned Redis-flip path (d-031), but the immediate need
(single-process correctness) does not require it. The interfaces are compatible.

**Precise Retry-After.** `max(0.1, window_s - (now % window_s))` gives the time
until the current 60-second window resets. Client SDKs with exponential backoff can
use this directly rather than guessing.

## Measured Overhead (Phase 0)

| Path | p50 µs | p99 µs |
|------|--------|--------|
| Guard chain only | 22.5 | 38.7 |
| Guard chain + C1 gate | 56.3 | 73.3 |
| Gate overhead | +33.8 | **+34.6** |
| 429 shed path (gate only) | 0.58 | 0.65 |

Gate overhead is ~35 µs at p99. The 1 ms budget is not approached. The 429 fast path
at 0.58 µs p50 means rejected clients never hang.

Noisy-neighbor validation: with 4 noisy clients at 10× RPM, victim p99 stayed at
10–15 µs (vs 15 µs baseline) — noisy-neighbor contention is eliminated.

## Follow-Ups

1. **`X-Airlock-Admission` response header.** The metadata key
   `data["metadata"]["airlock_admission"]` is stamped on shed. Propagating it to the
   HTTP response `X-Airlock-Admission` header requires wiring through `transparency.py`
   — not yet done.

2. **True semaphore acquire/release.** The current concurrency check reads
   `sem._value` (non-blocking peek). This correctly detects a full semaphore for burst
   detection but does not truly reserve a slot — two concurrent requests could both see
   `_value > 0` and both be admitted if the check and the LLM dispatch are not
   atomically paired. Full `asyncio.Semaphore` acquire/release requires the gate to
   operate in an async context with `async with sem`, which in turn requires the
   guardian hook to `await` the gate. Planned for a follow-up pack.
