# Design: resource permits and isolated inspection stages

**Status:** proposed design, informed by the 2026-08 G-9 native-memory incident.

## Decision in one sentence

An expensive, policy-required stage must be admitted by a small, bounded permit
at the request path, execute synchronously in a long-lived isolated worker, and
return its decision before the request can continue.  It must not be represented
as an unbounded queue or a chain of completion callbacks.

This is a general pattern, not a spaCy feature.  It applies to any stage whose
CPU, native memory, allocator behaviour, model state, or retained request data
could impair the router: semantic PII recognition, malware/DLP inspection,
embedding or retrieval enrichment, expensive schema/tool validation, and similar
policy checks.

## Problem and boundary

G-9 showed a dangerous mismatch: a request-path PII guard invoked full spaCy NER
before model routing.  It could consume native memory while the request still
eventually succeeded, so neither a model-error breaker nor a provider-429
quarantine was an appropriate protection.  A router cannot rely on post-hoc
failure history to protect itself from work that is still being admitted.

The required boundary is therefore:

```text
request -> cheap policy/routing decision -> stage permit -> isolated stage RPC
        -> allow | redact | block | explicit overload result -> next stage/provider
```

The router owns the policy decision, a few counters, a deadline, and the final
enforcement action.  The worker owns the expensive library/model and all of its
transient allocations.  The router never retains work merely because the worker
is busy.

This is a bulkhead/admission-control pattern.  It complements, rather than
replaces, provider/model circuit breaking; see
[circuit-breaker review](circuit-breaker-review-2026-08.md).

## Terminology

| Term | Meaning |
|---|---|
| **stage** | A named costly operation, such as `semantic_pii_v1`. |
| **stage route** | Policy mapping from request attributes to a stage and required outcome. |
| **permit** | A bounded, non-queued right to start one unit of stage work. |
| **cost** | Conservative resource estimate, initially request count and maximum text bytes; later tokens/bytes may be used. |
| **worker** | A separately supervised process providing one stage implementation. |
| **overload outcome** | A documented enforcement result for no permit, timeout, or unhealthy worker. |

## Stage-route specification

Each route must be declarative and auditable.  A conceptual configuration shape:

```yaml
resource_stages:
  semantic_pii_v1:
    required_for: [customer-strict-no-egress]
    max_in_flight: 1              # total permits across all router clients
    max_input_bytes: 65536
    acquire_timeout_ms: 0         # no implicit waiting/queue
    rpc_deadline_ms: 1500
    worker_pool: semantic-pii-local
    overload_action: block        # selected by policy, not by worker accident
    unhealthy_action: block
    timeout_action: block
    client_share:
      mode: weighted-fair
      max_in_flight: 1
```

The real configuration and API should be designed deliberately; this example
defines the contract, not a committed syntax.  A route specifies all of:

1. The exact policy condition that selects it.
2. Whether inspection is required before provider egress.
3. The input-size/cost limit and stage deadline.
4. Global and fair-share permit limits.
5. The outcome for saturation, unavailability, timeout, malformed worker result,
   and infrastructure error.  Security policy may require local `block`; a
   best-effort enhancement may return a clear 429/503.  It must never silently
   pass a request that policy says requires the stage.
6. What minimal, non-sensitive audit fields are recorded.

There may be many check-permit-routes.  They share the permit contract but not
their policy outcomes or capacity.  A stalled embedding stage must not consume
the permits reserved for semantic PII, for example.

## Request lifecycle and invariants

1. Run only cheap, deterministic selection work first.  It may choose a route
   or reject an oversized/invalid request, but it must not invoke the expensive
   engine.
2. If the chosen route requires inspection, attempt a *non-blocking* permit
   before sending input to the worker.  No permit means the route's explicit
   overload outcome.  There is no router-owned backlog.
3. Send one synchronous local RPC with a fixed deadline.  The worker returns only
   a compact, serializable result (for example entity spans/type/score, a verdict,
   and a version), never a library object or model document.
4. Apply the result, then route or block the request.  Release the permit in a
   local `finally` path for every RPC completion, cancellation, and timeout.
5. A local watchdog accounts for every outstanding permit and conservatively
   expires one whose owner has disappeared.  It is a correctness backstop, not
   a substitute for `finally`.

Required invariants:

- A permit is held from just before worker dispatch through result application;
  it is not released by an unrelated logging callback.
- The total number of worker requests plus queued worker input is bounded by
  configuration.  Queue length zero is the initial default.
- Router memory consumption remains bounded by request-size caps and the permit
  count even when the worker is unhealthy.
- Work is not retried or fanned out while its permit is held unless the route
  explicitly budgets another permit and attempt.
- The protected request never reaches a remote provider before a required local
  result exists.

## Worker contract and isolation

Start with a fixed, small process pool.  Each worker has a Unix-domain socket,
independent service account where applicable, bounded input framing, own health
endpoint, `MemoryMax`, CPU limits, restart policy, and logs/metrics.  A worker
crash is a normal `unhealthy` result at the router boundary, not a reason for the
router to take on its allocations.

Use a separate resource budget for each stage.  Capacity is measured at the
pool level, not as one worker per Airlock request.  Scale worker count only after
load testing shows it is needed; each additional NLP worker duplicates model and
allocator memory.

The first implementation should use a synchronous request/reply protocol, not a
callback mesh.  This gives one owner for the permit, one deadline, and one place
to make the policy decision.  A bounded micro-batch is a later optimization, not
part of correctness: it needs a maximum wait, request count, total characters,
and bytes.  `nlp.pipe()` may improve throughput, but it can increase peak memory
and does not remove the cost of NER inference.

## spaCy specialization

For a semantic spaCy stage, load the `Language` object exactly once in each
worker.  For spaCy 3.8+, process a request within `nlp.memory_zone()` and extract
only plain spans before leaving the zone:

```python
with nlp.memory_zone():
    doc = nlp(text)
    spans = [(ent.start_char, ent.end_char, ent.label_) for ent in doc.ents]
return spans
```

No `Doc`, `Span`, `Token`, or other object tied to the zone may cross that
boundary or be cached by Airlock.  The worker contract has to be tested against
the installed spaCy version and the selected model.  `memory_zone()` resets
spaCy cache/vocabulary memory between requests; it is valuable hygiene but not a
capacity control, memory limit, process isolation, or substitute for permits.

For deterministic Presidio entities (credit card, SSN, e-mail, phone, US bank,
IBAN), Airlock's current NoOp NLP engine avoids NER entirely while retaining
recognition/redaction.  A policy that explicitly requires person/location-like
semantic recognition selects the semantic stage instead.

## Observability and operation

Every stage needs the following low-cardinality metrics and structured events:

- permits configured, acquired, released, currently held, and expired;
- requested/admitted/shed counts and shed reason;
- input bytes/cost, RPC latency, deadline expiry, worker health, restart count;
- worker cgroup current/high/max events and process RSS; and
- verdict counts by route/outcome, with no prompt or PII values in telemetry.

Alerts fire before OOM: sustained permit saturation, rising worker `memory.high`,
any `memory.max`/OOM/restart, deadline spikes, leaked-permit watchdog action, and
nonzero `block` results caused by worker health.  Operators need a route/stage
status endpoint distinct from `/health/circuits` so upstream availability is not
mistaken for local inspection capacity.

The existing high-water instrumentation is documented in
[OOM instrumentation](../debugging/instrumentation/oom-high-water.md) and can be
used during initial sizing.  It must be enabled only with the stated privacy and
retention controls.

## Test and rollout requirements

Before enforcing a new route:

1. Verify semantic equivalence for the selected policy, including all intended
   non-egress examples and false-positive cases.
2. Unit-test every release path: success, block, RPC exception, timeout,
   cancellation, malformed result, worker death, and watchdog expiry.
3. Stress-test at and above permit capacity with realistic long text.  Assert no
   unbounded queue, no router memory growth, and the declared overload outcome.
4. Kill/restart the worker during traffic and confirm that required policy stays
   fail-closed without degrading Airlock liveness.
5. Observe first, then enforce only when policy permits observing.  A strict
   no-egress route is fail-closed from its first production use.

## Relationship to established patterns

This design follows the forwarding-path discipline used by routers and proxies:
cheap classification before expensive inspection, bounded active and pending
work, explicit shedding at saturation, and a separate overload controller for
the proxy's own resources.  It is also consistent with spaCy's documented
persistent-service memory-zone model.  Useful references:

- [spaCy memory management](https://spacy.io/usage/memory-management)
- [spaCy processing pipelines](https://spacy.io/usage/processing-pipelines/)
- [Envoy circuit breaking](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking.html)
- [Envoy overload manager](https://www.envoyproxy.io/docs/envoy/latest/configuration/operations/overload_manager/overload_manager)
- [Triton rate limiter](https://docs.nvidia.com/deeplearning/triton-inference-server/archives/triton-inference-server-2490/user-guide/docs/user_guide/rate_limiter.html)
