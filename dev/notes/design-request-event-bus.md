# Design — `RequestEvent` + single recorder/dispatcher (0.5.4)

> **STATUS: READY FOR CODEX GATE.** Authored at 0.5.4 kickoff as the Phase-B design
> seed; kickoff HITL answered (branch `feat/0.5.4-eventbus` off `main`, UN-28,
> sequential/small-batch MIGRATE — §6/§7). The canonical-shape and dispatch-semantics
> sections are **proposals with a recommended resolution** for the design gate,
> grounded in a verified read of HEAD (see "Verified inventory").
> No Phase-E pack may start until this note passes the **codex design-review gate**
> (DoD item 1). Items marked **[OPEN — HITL]** still need a human call; items marked
> **[RESOLVED]** are settled with code evidence and recorded here so they don't
> re-litigate at every transition.
>
> Plan: `dev/plans/0.5.4-plan.md` · Board: `dev/plans/runs/STATUS-0.5.4.md` ·
> Audit source: `dev/notes/architecture-audit-0.5.0-2026-06.md` (Part 2 telemetry
> row ★★ "Weakest"; Tier 3 #8).

---

## 1. Problem (what we're collapsing)

The same per-request record is derived **independently** in multiple telemetry
consumers. A new field or a fix must be applied in each, and they have already
drifted (see §3). The fix: source the record **once** into a canonical
`RequestEvent`, then let each sink **project** its historical subset out of that
one event. "Build once, dispatch many."

## 2. Verified inventory (corrects the audit's "4× `_build_record()`")

Read against HEAD (`airlock/callbacks/`). The audit's "four duplicated
`_build_record()`s" is imprecise — there are **three distinct builders**, plus a
fathom projection layer, plus two side channels:

| Consumer | Module | How it builds today |
|---|---|---|
| enterprise | `enterprise_logger.py:407` | `AirlockLogger._build_record` — the **richest** builder (staticmethod). The de-facto canonical source. |
| fathom | `fathom_logger.py:97` | **No own builder.** Calls `AirlockLogger._build_record` (`_base_record`), then `_fathom_properties` projects a **subset** with per-field env-flag gating (`AIRLOCK_FATHOM_STORE_*`). |
| s3 | `s3_logger.py:64` | `AirlockS3Logger._build_record` — own, **narrower** builder; applies `_redact_record`. |
| sql | `sql_logger.py:90` | `AirlockSQLLogger._build_record` — own, **narrower** builder; messages/response stored as **JSON strings**, not objects. |
| mutation ledger | `transparency.py:105` (source) → consumed by enterprise (`record["mutations"]`), metrics (`_record_mutations`), `model_override_headers.py` | Sourced once into `metadata["airlock_mutations"]`; **each consumer re-reads it.** |
| metrics | `metrics.py:172` `AirlockMetricsCallback` | Reads `kwargs`/`metadata` independently for `requests_total{model,user,success}`, `request_duration{model}`, and `_record_mutations`. |

**Implication for the migration story:** enterprise is already the canonical
builder and fathom already consumes it. So the real work is (a) lift enterprise's
builder into a standalone `RequestEvent` producer, (b) re-express fathom's
projection against the event (near-trivial — it already does this), (c) migrate the
two genuinely-independent builders (s3, sql) onto event projections, (d) feed the
side channels from the same event. This is **less duplication than "4×" implies**
and changes ordering/parallelism (§8).

## 3. Critical divergences — "behavior-preserving" is NOT "make sinks identical"

The sinks **do not emit the same record today.** The canonical event is a
**superset**; each sink keeps a **projection function** that reproduces its
*current* fields byte-for-byte. Naively unifying onto one shape would change wire
output for s3/sql — a **BLOCK** unless registered. The known divergences the golden
tests must pin:

1. **`error` value differs.** enterprise uses `_normalize_failure()` (rich
   `error`/`error_type`/`failure_category`); s3 & sql use bare
   `str(kwargs.get("exception"))`. The event should carry the **rich** triple;
   s3/sql projections must keep emitting their **bare** `error` string (no
   `error_type`/`failure_category`) unless we deliberately register an upgrade.
2. **Field set differs.** `airlock_provider`, `guardrail_meta` (`airlock_*`),
   `mcp_meta`, `mutations`, `served`, `attribution`, `record_type`,
   `start_time`/`end_time` exist in **enterprise only** (fathom projects some). s3
   & sql carry only the narrow core + `usage`. Projections must not leak superset
   fields into s3/sql.
3. **Encoding differs.** enterprise/s3 keep `messages`/`response` as objects
   (serialized at write via `default=_serialize`); **sql stores JSON strings**
   (`json.dumps(..., default=_serialize)`). Projection must preserve per-sink
   encoding.
4. **Three timestamps today → one tomorrow.** Each builder calls
   `datetime.now(timezone.utc)` independently, so the same request currently gets
   **three slightly different `timestamp`s**. Sourcing once means **one** timestamp
   for all sinks. Values *converge* (strictly more correct), but it is technically
   a value change. **[OPEN — HITL]** confirm this is acceptable as an unregistered
   "internal — values converge" change, or register it. Recommendation: acceptable;
   note in the behavior-change register.
5. **Side-effecting builder.** `AirlockLogger._build_record` **mutates `metadata`
   in place** for Gemini (sets `airlock_gemini`, `airlock_gemini_response`,
   `airlock_response_headers`, then recomputes `guardrail_meta`). fathom inherits
   this only because it calls the same builder. The event producer must run this
   enrichment **once, before fan-out**, so fathom still sees enriched
   `guardrail_meta`. Order matters: enrichment is part of *sourcing*, not *sinking*.
6. **`user`/`team` defaults differ.** metrics defaults `user` to `"unknown"`;
   loggers leave `None`. Projection-level, not event-level — keep per-consumer.
7. **`_redact_record` (env `AIRLOCK_LOG_REDACT_FIELDS`)** applies to
   enterprise/s3 at record level. Decide whether redaction is an event-producer
   step or a per-sink projection step. Recommendation: **per-sink** (sql does not
   redact today; making it global would change sql output).

## 4. Canonical `RequestEvent` (PROPOSAL for the gate)

A frozen dataclass (not pydantic — matches `transparency.py` `Mutation`/`Served`
dataclasses; cheaper on the hot path, no validation cost). Sourced once from
`kwargs`/`response_obj`/`start_time`/`end_time` at the success/failure callback
boundary, **through the 0.5.3 ACL** (`litellm_adapter.py`) for LiteLLM internals —
do **not** re-read LiteLLM internals directly (STATUS §7 decision).

```text
RequestEvent (superset; each sink projects its subset)
  # identity / lifecycle
  timestamp: str           # ONE iso timestamp, sourced once (§3.4)
  record_type: str         # "request" (admin_action stays its own path)
  success: bool
  start_time / end_time    # raw datetimes (enterprise keeps; s3/sql derive duration_ms)
  duration_ms: int | None
  # request
  model: str
  messages: Any            # objects; sql projection json-encodes
  request_id: str          # litellm_call_id
  user / team / airlock_client
  airlock_provider: str
  # response
  response: Any            # objects; sql projection json-encodes
  usage: {prompt,completion,total}_tokens
  response_cost            # for fathom "cost"
  # failure (rich; s3/sql project to bare error str)
  error / error_type / failure_category
  # enrichment (computed once, pre-fanout — §3.5)
  guardrail_meta: dict     # airlock_* (post Gemini enrichment)
  mcp_meta: dict           # call_type, mcp_tool_name, mcp_server_name
  # transparency
  mutations: list          # from metadata["airlock_mutations"]
  served / attribution     # attribute_served_backend(...)
```

Each sink/side-channel gets a **pure projection function** `project_<sink>(event)
-> dict` (or metric increments). The four-plus inlined builders are deleted; their
exact current output is reproduced by their projection and proven by golden tests.

## 5. Dispatch seam (PROPOSAL for the gate)

A single recorder that builds the event once and fans out:

- **Synchronous, in-process.** No async bus, no queue, no broker — explicitly
  deferred by the plan. Sinks already do their own `asyncio.to_thread` /
  buffering; the seam just builds once and calls each.
- **Registration:** sinks register with the recorder (mirror the existing
  `_self_register()` pattern); the recorder holds an ordered list.
- **Ordering:** deterministic, registration order. Document it; a golden/seam test
  pins it. (Today order is incidental across separate callbacks — pinning it is an
  improvement, not a regression, since sinks are independent.)
- **Per-sink failure isolation [AC-SEAM]:** each projection+emit is wrapped; a
  raising sink is caught, logged, and **never propagates** to the request path or
  other sinks — mirrors the perimeter's "never raise from telemetry" posture
  (`_write_log` swallows `OSError`; served attribution already wrapped). A
  dedicated test asserts a deliberately-raising sink doesn't break the others.
- **Hot-path cost:** one event build + N projections must be ≤ today's N
  independent builds. Enrichment (Gemini, served attribution) runs **once** instead
  of per-builder → strictly cheaper.

**Open seam question [OPEN — HITL/gate]:** does the recorder *replace* the N
LiteLLM `CustomLogger` registrations with one callback that fans out, or does each
sink stay a `CustomLogger` that pulls a memoized event off `kwargs`? Recommendation:
**one fan-out callback** (true single seam; matches the audit intent). Pulling a
memoized event keeps N registrations and a second source-of-truth risk. Settle at
the gate.

## 6. Resolved open items (settled with evidence)

- **[RESOLVED] UN number = UN-28.** The plan's `UN-27` **collides** — UN-27 is
  already allocated to 0.5.3 and present in `dev/user-needs.md`. Next-free is
  **UN-28** (UN-23/24 reserved by 0.5.5). Phase A allocates UN-28; fix the `UN-27`
  reference in `0.5.4-plan.md`.
- **[RESOLVED] MIGRATE parallel-safety = safe in disjoint per-file batches.** Each
  sink lives in its **own module** and the MIGRATE packs touch disjoint files; the
  only shared artifact is the new event/recorder module, which is **read-only** to
  sinks once `EVENT` merges. Therefore the MIGRATE-* packs are **file-parallel-safe
  after EVENT lands**, within the runbook's 3-worktree cap. Caveat: enterprise and
  fathom are **coupled** (fathom calls `AirlockLogger._build_record`) — migrate
  them **together or enterprise-first** to avoid a transient broken fathom. Suggested
  batches: **[enterprise+fathom]**, **[s3]**, **[sql]**, then **[sidechannels]**.
- **[RESOLVED] Builder count.** Three distinct builders + fathom projection + two
  side channels, not "four" (§2). Equivalence baselines are captured per *consumer*,
  not per "builder."
- **[RESOLVED] Design-note filename.** This file is `design-request-event-bus.md`
  per the plan's DoD item 7 (not the `0.5.4-<PACK>-design.md` pack convention) —
  intentional; DoD references it by this name.

## 7. Open items — resolved at kickoff / proposed to the gate

- **[RESOLVED — HITL] Working branch = `feat/0.5.4-eventbus` off `main`.** Confirmed
  at the 0.5.4 kickoff gate (2026-06-28). All packs branch off this; release merges
  to `main` at sign-off.
- **[RESOLVED — HITL] Unified-timestamp convergence (§3.4) = accept + register.**
  Confirmed: the three independently-sampled per-builder timestamps collapse to one
  sourced-once value (strictly more correct). Recorded as the single accepted
  internal value change in UN-28 AC-5 and the behavior-change register; not a wire
  shape change.
- **[PROPOSED — gate] Seam shape (§5) = one fan-out callback.** True single seam,
  matches the audit intent; avoids the N-registration + memoized-event second-source
  risk. Codex to confirm.
- **[PROPOSED — gate] Redaction placement (§3.7) = per-sink.** Preserves sql's
  current non-redaction; event-level would change sql output. Codex to confirm.
- **[PROPOSED — gate/scope] Metrics standalone helpers OUT OF SCOPE.**
  `record_pii_redaction`, `record_keyword_block`, `record_threat_block`,
  `record_response_scan_detection`, `record_provider_ratelimit_headroom`,
  `set_circuit_breaker_state` are **event-specific**, called by guardrails — **not**
  per-request-record derived. Only `requests_total`, `request_duration`, and
  `_record_mutations` (the per-request metrics) move to the seam. Codex to confirm.

## 8. Migration order (derived from §6)

```
DESIGN (this note, codex PASS)
  → EVENT            # RequestEvent + recorder/dispatcher seam, sinks not yet migrated
    → MIGRATE-enterprise + MIGRATE-fathom   (one batch — coupled)
    → MIGRATE-s3                            (parallel-eligible)
    → MIGRATE-sql                           (parallel-eligible)
    → MIGRATE-sidechannels                  (mutation ledger + per-request metrics)
  → VERIFY           # cross-sink equivalence + isolated-instance parity run
  → DOCS             # UN-28, as-built note, changelog + behavior-change register
```

## 9. Equivalence / test strategy

- **Golden, per consumer.** Capture each consumer's **current** output for a fixed
  representative request set (success; provider failure; pre-call failure; MCP call;
  Gemini; with mutations/redactions) **before** migration. After each MIGRATE pack,
  assert the projection reproduces it **field-for-field** (sql: string-encoded;
  s3: redacted; fathom: env-flag-gated subset). Any diff that isn't registered is a
  review BLOCK (plan guardrail).
- **Seam test [AC-SEAM]:** a deliberately-raising sink is contained; request and
  other sinks unaffected; dispatch order pinned.
- **Parity oracle [AC-SMOKE]:** isolated-instance `dev/smoketest/` run before/after
  on a **separate dir+port** — live `:4000`/`:8090` never touched
  ([[airlock-production-safety]]). Expected outcome: **no** served/logged field
  change; extend the harness only if a field shape changes.
- **Hot-path check:** event-build + fan-out ≤ current cost (§5).

---

### Changelog of this note
- 2026-06-28 — DRAFT seeded at kickoff from a verified HEAD read; resolved UN-28,
  parallel-safety, builder count, filename; surfaced the §3 divergences as the core
  risk; left seam-shape / redaction-placement / timestamp-convergence / branch for
  the gate/HITL.
</content>
</invoke>
