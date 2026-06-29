# Airlock Bulkhead / Isolation — Phase 0 Decision Memo

_Scope: 0.5.5-EXPLORE deliverable. Closes the HITL gate before any IMPL pack is authored._
_Date: 2026-06-28. All numbers measured on a Linux 6.17 host, Python 3.12, airlock main (b068391)._

---

## Recommendation: **C1 — In-loop admission control**

> **HITL GATE** — the human must ratify or override this recommendation before Phase E begins.

Implement a **per-client token-bucket RPM gate + asyncio.Semaphore concurrency cap**,
inserted into `guardian.async_pre_call_hook` immediately after the circuit-breaker
check (`guardian.py:406`). Rate-limit counters live behind the existing 0.5.1
`SpendStore` / DualCache seam so a future Redis backend is a config flip.

---

## 1. Experimental setup

All measurements are in-process, no server required. Scripts are throwaway spikes
in the session scratchpad; they import airlock's `fast/` modules directly against
the real `StateStore`. Production safety preserved — no live service touched.

Four benchmark programs, run on airlock main (b068391):

| Script | What it measures |
|--------|-----------------|
| `bench_a_guard_chain.py` | p50/p99 of the full fast guard chain under N concurrent async tasks |
| `bench_b_cpu_gil.py` | CPU/GIL impact; noisy-neighbor in the event loop without a gate |
| `bench_c_dualcache.py` | SpendStore seam throughput + correctness under concurrent writes |
| `bench_d_c1_spike.py` | Raw C1 gate overhead; 429 fail-fast; noisy-neighbor with gate in place |

---

## 2. Axis (a): Event-loop contention through the guard chain

### Guard chain step costs (single-threaded, deque_size=1000 / worst case)

| Step | Mean µs |
|------|---------|
| `get_client` | 0.3 |
| `record_request` | 0.1 |
| `is_in_backoff` | 0.1 |
| `assess_threat` | 207 |
| `circuit_breaker` | 7 |
| `compute_priority` | 110 |
| **Total** | **~325** |

### Guard chain cost scales O(n) with deque size

Both `assess_threat` and `compute_priority` iterate over `ClientState.request_times`
(maxlen=1000). Cost is not constant — it grows with the client's request history:

| Deque size | `assess_threat` p50 | `compute_priority` p50 | Total guard µs |
|-----------|--------------------|-----------------------|---------------|
| 0 (new client) | 3 | 2 | ~12 |
| 10 (light user) | 6 | 4 | ~17 |
| 100 | 23 | 15 | ~45 |
| 500 | 103 | — | ~120 |
| 1000 (max) | 208 | 131 | ~340 |

**Interpretation:** The guard chain cost is dominated by O(n) deque iteration, not
lock contention. For a typical dev-proxy user (10–100 requests in the window),
guard chain p50 ≈ 17–45 µs. Worst case (1000-entry deque) is ~340 µs —
still irrelevant compared to LLM provider latency (500 ms–10 s per call).

### Lock contention (RLock) is negligible

RLock acquire/release costs p50=55 µs, p99=59 µs regardless of thread count
(1–16 threads). No serialization spike observed. The lock is never the bottleneck.

### Concurrency: inline guard chain under asyncio

| Concurrent tasks | p50 µs | p99 µs | Throughput RPS |
|-----------------|--------|--------|----------------|
| 1 | 179 | 337 | 5,443 |
| 4 | 161 | 352 | 5,801 |
| 16 | 53 | 343 | 10,707 |
| 32 | 37 | 341 | 15,169 |

The guard chain is **not event-loop contention bound**. p99 stays flat at ~340 µs
across all concurrency levels; throughput increases because asyncio interleaves
the tasks. The single event loop handles realistic dev-proxy traffic comfortably.

---

## 3. Axis (b): CPU/GIL impact

### Current guard chain (post-0.5.3, Presidio offloaded)

Benchmark B3 — victim task latency while N noisy async tasks run the guard chain:

| N noisy tasks | Victim p50 µs | Victim p99 µs |
|--------------|--------------|--------------|
| 0 (baseline) | 54 | 99 |
| 1 | 40 | 72 |
| 4 (50 iter) | 40 | 71 |
| 4 (200 iter) | 40 | 76 |

**The current guard chain does NOT degrade victim latency** — asyncio interleaving
actually amortizes context-switch overhead. No multiprocess isolation is needed
for the existing chain.

### GIL danger: CPU-bound work in threads

Benchmark B4 — CPU-heavy thread-pool workers alongside the victim:

| CPU threads | CPU iters | Victim p50 µs | Victim p99 µs |
|------------|-----------|--------------|--------------|
| 0 | 0 | 52 | 96 |
| 2 | 100 | 50 | 5,192 |
| 4 | 100 | 52 | 30,597 |
| 8 | 100 | 51 | 5,150 |

**Warning:** If any guard runs CPU-heavy work in a thread pool (not pure asyncio),
GIL contention spikes victim p99 by 32–318×. This confirms that 0.5.3's move
of Presidio out of the inline path was correct and critical. Any future CPU-bound
guard added back inline would require the same offload pattern — it must not run
in a thread pool alongside inference work.

### Heavy CPU guard (pre-0.5.3 Presidio analogue)

Running 500 regex iterations (simulating Presidio inline) costs p50=47,848 µs
per request. Thread-pooled at 4 concurrent: p99=789 ms. This is the failure
mode that motivated 0.5.3 — and it is entirely fixed for the current chain.

**Key finding:** The current fast/ guard chain has **no CPU/GIL problem**. The
problem only reappears if a new CPU-heavy guard is added inline. Recommendation:
any future guard with p50 > 1 ms must be offloaded to a subprocess or async
worker, not a thread pool.

---

## 4. Axis (c): DualCache / SpendStore seam for cross-worker accounting

### Write throughput and correctness

| Threads | p50 µs | p99 µs | Writes/s | Correctness error |
|---------|--------|--------|----------|-------------------|
| 1 | 3.6 | 15.6 | 202,104 | 0.00% |
| 4 | 2.8 | 5.2 | 299,528 | 0.00% |
| 8 | 2.8 | 6.6 | 284,009 | 0.00% |
| 16 | 2.8 | 5.2 | 305,296 | 0.00% |

**The seam is fast and correct.** Zero correctness errors at all concurrency
levels (the RLock compound read-modify-write provides atomicity). At ~3 µs/write
and 300 k writes/s, it is not the bottleneck for any realistic rate-counting use.

### Read latency under writes

| Concurrent writers | Read p50 µs | Read p99 µs |
|-------------------|-------------|-------------|
| 0 | 0.5 | 1.0 |
| 1 | 13 | 21 |
| 4 | 10 | 15 |
| 8 | 6 | 134 |

Reads are fast under low concurrency. Under 8 writers, p99 climbs to 134 µs —
still within budget for a pre-call admission check. If a Redis backend is
swapped in later (via the seam), read latency moves to network RTT (~0.5 ms
local) which is already priced into the <1 ms budget.

### DualCache vs plain dict counter

| Approach | p50 µs | p99 µs |
|----------|--------|--------|
| `dict` counter (single-process, no TTL) | 0.27 | 0.38 |
| DualCache (TTL, bucket, Redis-ready) | 6.4 | 7.2 |

DualCache is 23× slower than a raw dict. However, 6.4 µs is well within the
<1 ms hot-path budget. The dict approach loses time-window semantics, TTL, and
Redis readiness — not a fair trade for 6 µs.

**Conclusion:** C3 is not a standalone mechanism. It is the *accounting seam*
that C1 uses for its per-client RPM counters. Using the existing SpendStore
pattern for rate counters avoids creating a second disagreeing state store.

---

## 5. C1 spike: gate overhead measurement

### Raw asyncio.Semaphore cost

| Capacity | p50 µs | p99 µs |
|---------|--------|--------|
| 1 | 0.57 | 0.69 |
| 5 | 0.56 | 0.64 |
| 50 | 0.56 | 0.62 |

Semaphore acquire+release is effectively free (~0.6 µs). Not the bottleneck.

### Full admission gate (RPM token bucket + semaphore peek)

| Concurrent | p50 µs | p99 µs | Throughput RPS |
|-----------|--------|--------|----------------|
| 1 | 1.0 | 1.5 | 302,811 |
| 8 | 1.0 | 1.2 | 430,224 |
| 32 | 1.0 | 1.7 | 426,631 |

Gate p99 = **1.5–1.7 µs** at all concurrency levels. This is the total cost
of the admission decision (RPM check + concurrency check) before any LLM work.

### 429 fail-fast response time

| | p50 µs | p99 µs | p100 µs |
|--|--------|--------|---------|
| 429 shed | 0.58 | 0.65 | 1.01 |

Rejected requests return in under 1 µs. **No hang.** The gate fails fast —
the overloaded client gets an immediate 429 + retry_after, not a queued wait.

### Guard chain + C1 gate vs guard chain alone

| | p50 µs | p99 µs |
|--|--------|--------|
| Guard chain only | 22.5 | 38.7 |
| Guard chain + C1 gate | 56.3 | 73.3 |
| Gate overhead | +33.8 | +34.6 |

**Total added latency: ~35 µs.** Budget requirement is <1,000 µs. Gate overhead
is **0.003×** the budget — effectively free on the hot path.

### Noisy-neighbor protection WITH C1 gate

| N noisy clients | Victim p50 µs | Victim p99 µs | Noisy rejection rate |
|----------------|--------------|--------------|---------------------|
| 0 (baseline) | 3.5 | 15 | — |
| 2 | 3.9 | 14 | 61% |
| 4 | 3.9 | 10 | 61% |
| 8 | 3.9 | 15 | 61% |

**Victim latency is fully protected.** Noisy clients are rejected at 61% (their
RPM budget is exhausted; they receive 429 + Retry-After). Victim experiences
no degradation regardless of how many noisy clients are hammering.

---

## 6. Candidate verdicts

### C1 — In-loop admission control (asyncio.Semaphore + token bucket) ✓ CHOSEN

**Evidence:** Gate adds 35 µs overhead (3.5% of 1 ms budget). 429 fail-fast in
<1 µs. Victim protected completely. No deployment change; no state fragmentation.
Naturally consumes the existing `PrioritySignal` for tier-aware RPM limits.
DualCache seam provides correct cross-request accounting and Redis-readiness.

**Kept:** This is the right mechanism for Airlock's current deployment shape —
single-process, single-tenant dev proxy, I/O bound on provider latency (not
CPU bound). The problem we are solving is **unfair resource consumption**, not
**CPU saturation**; C1 addresses it precisely.

---

### C2 — Process-level isolation (`--num_workers` + shared store) ✗ REJECTED

**Evidence:** The guard chain benchmarks (A, B) show the event loop is NOT
CPU/GIL saturated. The bottleneck is provider I/O latency (500 ms–10 s), not
in-process compute. Adding workers would:

1. Fragment `StateStore` across processes — requires Redis (0.5.1 STORE-seam
   makes this possible but it is a non-trivial operational dependency).
2. Introduce `cb_state.json` checkpoint/restore races in child processes
   (0.5.1 FIX-1 already a known landmine).
3. Add no benefit: provider throughput is the ceiling, not CPU throughput.
4. Contradict the 0.5.1 deferral decision (same reasoning applies now).

**Rejected.** Re-evaluate if a CPU-heavy guard is added inline in a future
release, or if horizontal-scale multi-tenant SLAs become a real requirement.

---

### C3 — DualCache-coordinated counters ✓ ADOPTED AS SEAM (not a mechanism)

**Evidence:** The DualCache seam is correct (0% accounting error), fast (3 µs
write), and Redis-ready. It is the right backing store for C1's rate counters.
It is NOT an isolation mechanism by itself — it requires C1 (or C2) as the
actuator. C3 = accounting; C1 = enforcement.

**Disposition:** Adopted as the counter storage layer for C1. C1's per-client
RPM bucket uses `SpendStore`'s pattern (DualCache-backed, TTL-tracked, atomic
read-modify-write under the existing `StateStore._lock`). No new parallel
state store — counters live behind the 0.5.1 seam.

---

### C4 — Multi-process orchestration (bulkhead worker pools) ✗ REJECTED

**Evidence:** C4 is C2 with added routing/affinity complexity. Everything that
argues against C2 argues more strongly against C4. Per-tenant worker pools
require a front-end router, make crash isolation harder, and add significant
operational surface area. The measurements show zero CPU saturation under
realistic single-tenant load; this level of isolation is pure over-engineering
for Airlock's current deployment shape.

**Rejected.** Would only be revisited if a production multi-tenant deployment
(multiple teams, SLA guarantees between them) becomes a real requirement.

---

### C5 — Hybrid (C1 in-loop + C2 for CPU/parallelism) ✗ REJECTED

**Evidence:** C2 was rejected because the event loop is not CPU-saturated.
The hybrid adds C2's complexity without C2's benefit. If CPU saturation
ever becomes real, start with a proper CPU-offload worker (subprocess, not
multiprocess server) rather than the full `--num_workers` path.

**Rejected.** C1 alone is sufficient.

---

## 7. Horizontal-scale question (closes 0.5.1 open-Q #2)

> **Is horizontal scaling / multi-tenant SLA actually a requirement?**

**Answer (HITL confirmed 2026-06-28):** Not yet. Single-process is correct for
now. Multi-tenant horizontal scale is a future requirement — acknowledged but
not current. The DualCache seam + Redis-ready counter design means the
transition, when it comes, is a config flip, not a rewrite.

**Decision d-031:** Single-process now; Redis-flip later via the seam.

---

## 8. UN-23/UN-24 criteria (FINALIZED at DECIDE 2026-06-28)

### UN-23 — Predictable latency under load / noisy-neighbor protection

> A victim client's p99 is protected while a noisy client is throttled.

Acceptance criteria (mechanism-final for C1):
- Load test (separate dir+port, `dev/smoketest/`): victim p99 protected while
  noisy client is throttled.
- Gate **fails open** on internal error — never blocks a request if the limiter
  crashes; logs a warning and lets the request through.
- Throttled client receives **`429 + Retry-After`** header — never a silent hang.
- Admission check adds **<1 ms** baseline on the hot path (measured: ~35 µs ≪ 1 ms).
- `X-Airlock-Admission` response header reports the gate decision.

### UN-24 — Per-client resource fairness

> Per-client RPM/concurrency quota enforced per the configured policy.

Acceptance criteria:
- Per-client RPM counter correct under concurrent requests (0% error in bench C2).
- Concurrency cap (asyncio.Semaphore) prevents a single client from holding more
  than N in-flight slots.
- Counters live **behind the 0.5.1 store seam** (no new parallel state store).
- No double-counting against the circuit breaker's rate-limit accounting.
- Per-client quota configurable; off-by-default (generous default so existing
  dev-proxy users are unaffected before opt-in tuning).

---

## 9. Phase E pack shape (FINALIZED at DECIDE 2026-06-28)

| Pack | Goal |
|------|------|
| `ENABLE-stateprovider` | De-globalize `store = StateStore()` → injected `StateProvider` (audit Tier 3 #7). Required for clean testing of the gate in isolation. |
| `ENABLE-statesplit` | Split `state.py` god-object into core/spend/persistence/mcp modules (audit Tier 3 #9). Gives the admission gate a clean surface. |
| `IMPL-admission` | Per-client `asyncio.Semaphore` concurrency cap + PrioritySignal-tiered token-bucket RPM gate at `guardian.py:406`. Counters in new `AdmissionStore` (SpendStore pattern, integer request counts). `429 + Retry-After` shed (Retry-After = precise bucket refill time). `X-Airlock-Admission` header. `airlock_admission_*` metrics. Fails open on error. Off-by-default (`admission.enabled: false`). |
| `DOCS` | UN-23/UN-24 in `dev/user-needs.md`; as-built design note; ops guide (knobs, what is and is not isolated); changelog + behavior-change register. |

---

## 10. DECIDE answers (HITL confirmed 2026-06-28)

1. **Fairness policy (d-027):** PrioritySignal-tiered RPM caps. Default is
   equal-share across all clients; `boost=True` clients (interactive cadence)
   get a higher cap (e.g. 1.5×). Configurable in `AirlockSettings`.

2. **Shed vs queue (d-028):** Shed with `429 + Retry-After`. Retry-After is
   derived precisely from the token bucket: `refill_period - (now - last_refill)`.
   This gives the client an exact wait time, not a guess.

3. **Default behavior (d-029):** Off-by-default (`admission.enabled: false`).
   Explicit opt-in; existing dev-proxy users see no change on upgrade.

4. **Counter storage (d-030):** New `AdmissionStore` class following the
   `SpendStore` pattern, tracking integer request counts (not µ$ spend).
   Semantically clean; independently testable; Redis-ready via same seam.

---

## 11. Summary

The measured data is unambiguous:

- **The bottleneck is provider I/O latency**, not event-loop CPU.
- **The guard chain is fast** (12–340 µs depending on deque size; 17 µs for
  typical users). Not a bottleneck.
- **The event loop does not degrade under concurrent async tasks** — asyncio
  interleaving is sufficient for the current light chain.
- **GIL is dangerous only for CPU-heavy thread-pool work** — not present in the
  current chain post-0.5.3.
- **C1 gate overhead is ~35 µs** — 3.5% of the 1 ms budget.
- **C1 fail-fast 429 is <1 µs** — no hang, ever.
- **DualCache seam is correct and fast** — 0% error rate, 3 µs/write, Redis-ready.
- **Noisy-neighbor IS protected with C1** — victim p99 stable regardless of
  noisy client count or RPM.

**Chosen: C1. Rejected: C2, C4, C5. C3 adopted as seam.**

**DECIDE gate closed 2026-06-28.** All four implementation questions answered
(d-027 – d-030). Horizontal-scale question closed (d-031): single-process now,
Redis-flip later. Phase E packs (`ENABLE-stateprovider`, `ENABLE-statesplit`,
`IMPL-admission`, `DOCS`) may now be authored.
