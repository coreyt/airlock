# Circuit-breaker review: upstream health versus local resource protection

**Date:** 2026-08-10
**Status:** design review; no circuit-breaker behaviour changed by this note.

## Finding

Airlock has three related but materially different mechanisms:

| Mechanism | Current scope | Signal | Placement | Why it did not stop G-9 |
|---|---|---|---|---|
| Model circuit breaker | `fast/circuit_breaker.py`, `ModelState` | five consecutive provider-call failures | Fast guardian pre-call | G-9 PII work ran before the guardian and requests normally succeeded. Native allocator growth is not a provider-call failure. |
| Provider/client quarantine | `fast/monitor.py`, provider state | provider 429/quota signals | monitor failure callback, enforced in guardian | There was no upstream 429/quota signal. It protects provider standing, not Airlock memory. |
| Admission gate | `fast/admission.py` | per-client RPM and in-flight slots | guardian near return to LiteLLM | It is not enabled by the supplied config, occurs after PII, is per client rather than per costly stage, and releases only through callbacks. |

The OOM incident was not a missed model-breaker threshold.  It was a local
resource-exhaustion mode without the breaker’s input signal, occurring before
the breaker’s enforcement point.  A circuit breaker is historical failure
control; it cannot be the only control for a stage that can damage the router
while calls are still successful.

## Current implementation facts

- `ModelState` opens after five consecutive recorded failures, waits 30 seconds,
  admits one half-open probe, and closes after three successes.  The fast monitor
  records model failures only when LiteLLM supplies an exception for a provider
  call; pre-call rejections deliberately do not feed it.
- The model breaker can choose a configured healthy fallback for unpinned
  traffic.  Pinned traffic is rejected when no permitted route remains.
- Provider/client quarantine is specifically driven by `RateLimitError`, HTTP
  429, or matching quota/rate-limit text.  It has its own policy and persistence.
- The `airlock_settings.admission` feature is off by default.  It uses an
  in-process, per-client RPM store and nonblocking semaphore manipulation.  Its
  release depends on LiteLLM success/failure callbacks; under the incident's
  callback timeouts, that is an especially unsuitable ownership boundary for a
  new resource permit.

## What the breaker should and should not mean

Keep the current breaker for model/provider health.  It should continue to
provide failover or clear, typed rejection when an upstream is repeatedly
failing.  Do not overload it with local memory, CPU, or stage saturation:

- An **open upstream circuit** means a known-bad provider/model should receive
  no new traffic for its recovery interval.
- A **stage permit denial** means no safe local capacity is available *now*.
- A **local overload state** means Airlock or an isolated worker is near a
  resource limit and must shed or use the route's policy action.

They use different keys, time scales, recovery actions, observability, and
security semantics.  Conflating them would cause an OOM in one semantic stage
to mark a healthy provider unavailable, or a provider 429 to consume semantic
inspection capacity.

## Holistic target model

```text
                  local overload / worker health
                             |
client -> policy -> stage permit -> stage worker -> upstream model breaker -> provider
                    |               |                    |                  |
                 429/403/etc.   bounded admission    cgroup/restart     failover/quarantine
```

The evaluation order follows causality: reject a policy-required request before
egress if inspection cannot happen; do not invoke expensive inspection until its
capacity is reserved; do not call an upstream already known to be unhealthy.
Every control has a bounded and observable action.

## Required review work before relying on breakers for new stages

1. **Inventory every costly request-path stage.** For each, document its
   placement, resource budget, success/failure signal, whether it can affect
   router liveness, and whether policy allows fail-open.
2. **Move capacity control before costly work.** The existing admission gate
   cannot protect a PII guard that precedes it.  New stage permits follow the
   contract in [resource permits and isolated inspection stages](design-resource-permits-and-isolation.md).
3. **Separate state machines.** Give model/provider circuits, worker health,
   local overload, and stage permits distinct state, metrics, endpoints, and
   operator actions.  A circuit `clear` must never silently override a strict
   PII `block` policy.
4. **Make saturation outcomes contractual.** Every route declares its action
   for no permit, timeout, worker restart, malformed result, and cgroup pressure.
   Retry headers and client guidance apply only where retry is safe.
5. **Use exact permit ownership.** Acquire and release in the request/RPC scope
   with `try/finally`; do not depend on asynchronous logging callbacks.  Provide
   a leak watchdog and tests for cancellation/process death.
6. **Bound all queues and retries.** Count active, pending, and retry work by
   stage and resource cost.  No unbounded backlog, implicit fallback fan-out, or
   retry storm may bypass a permit.
7. **Decide failure posture explicitly.** Provider availability protections may
   return 429 or fail over.  A strict no-egress PII route must block locally on
   uncertainty; a best-effort feature may shed.  The default is never inferred
   from a generic breaker setting.
8. **Prepare multi-process semantics.** Existing admission state is process
   local.  When Airlock uses multiple proxy workers, a global stage cap must be
   enforced by a shared permit authority or by partitioning fixed worker capacity
   per proxy, with the resulting effective cap documented.
9. **Test under degraded callbacks and memory pressure.** Tests must simulate
   callback timeout, provider success during local pressure, worker death,
   half-open concurrency, retry/fallback interaction, and cgroup high/max events.
10. **Expose operator truth.** `/health/circuits` remains upstream health only.
    Add a stage/overload surface with current permits, queue, worker health,
    cgroup events, deadline rates, and selected enforcement outcome.

## Acceptance criteria for the next implementation

- A G-9-scale stream requiring an expensive policy stage cannot grow Airlock's
  unbounded native memory or make its liveness endpoint stall.
- At capacity, the test observes the configured route result, not a timeout,
  hidden callback backlog, or kernel OOM.
- An upstream 429 opens the provider protection without changing stage permits.
- A worker failure blocks a strict protected request before egress while unrelated
  routes and Airlock liveness continue to work.
- Model breaker half-open behaviour remains deterministic under concurrency and
  no new permit can be leaked through cancellation or callback failure.

## Follow-on implementation sequence

1. Publish the stage-route configuration and policy outcomes.
2. Implement one isolated semantic PII worker with exact permits and no queue.
3. Add stage/overload metrics and an operator endpoint; retain the current OOM
   instrumentation for sizing.
4. Run load and failure tests, then enable a strict route for a selected policy.
5. Generalize the stage registry only after the first implementation has proved
   the simple lifecycle.  Do not introduce a callback framework as the first step.
