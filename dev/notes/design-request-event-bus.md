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

### 2a. Related record producers held OUT OF SCOPE (explicit boundary)

Two other functions in `enterprise_logger.py` build request/log-shaped records but
are **not** on the LiteLLM success/failure callback fan-out path, so they are
**explicitly out of scope** for 0.5.4 — the same way `admin_action` records are
(codex design-review finding #3):

| Producer | Module | Why out of scope |
|---|---|---|
| `write_precall_block_record()` | `enterprise_logger.py:236` | Builds a **failure** record for requests blocked **before** LiteLLM callbacks fire (no `response_obj`/`start`/`end`; `request_id` from `metadata`, `duration_ms=0`). It is a *fourth* enterprise-shaped failure builder, but on the **pre-dispatch** path — there is no `RequestEvent` at that point. Folding it in would expand scope to the pre-call hook. **Left as-is**; a future release may converge it once the seam exists. |
| `write_batch_record()` | `enterprise_logger.py:287` | Batch/file-job lifecycle telemetry (`call_type="batch"`, `is_batch_call=True`), emitted **outside** the interactive success/failure callback path. Entirely different shape (no messages/response/usage). **Left as-is.** |

Both still write through `_write_log` (rotation + redaction) and are unchanged by
this release. The MIGRATE-enterprise golden tests assert these two paths' output is
**byte-identical** before vs after (they must not be perturbed by the refactor).

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
  request_headers: Any     # raw kwargs["headers"] — fathom headers_json (§3.8 / finding #2)
  # response
  response: Any            # objects; sql projection json-encodes; fathom response_text derives via _response_text
  usage: {prompt,completion,total}_tokens
  response_cost            # for fathom "cost"
  # failure — BOTH forms carried (finding #1)
  error / error_type / failure_category   # rich, _normalize_failure() — enterprise projects these
  bare_exception_error: str | None         # literal str(kwargs.get("exception")) — s3/sql project THIS as their "error"
  # enrichment (computed once, pre-fanout — §3.5)
  guardrail_meta: dict     # airlock_* (post Gemini enrichment)
  mcp_meta: dict           # call_type, mcp_tool_name, mcp_server_name
  mcp_arguments: Any       # resolved kwargs/litellm_params/metadata mcp_arguments — fathom mcp_arguments_json (§3.8 / finding #2)
  # transparency
  mutations: list          # from metadata["airlock_mutations"]
  served / attribution     # attribute_served_backend(...)
```

> **§3.8 — Fathom's env-flag-gated fields must all source from the event
> (finding #2).** `_fathom_properties` (fathom_logger.py:106-172) re-reads raw
> `kwargs`/`response_obj` for three optional fields gated by env flags:
> `response_text` = `_response_text(response_obj)` (`AIRLOCK_FATHOM_STORE_RESPONSE_TEXT`),
> `headers_json` = `_json_text(kwargs.get("headers"))` (`AIRLOCK_FATHOM_STORE_HEADERS`),
> and `mcp_arguments_json` = `_json_text(<resolved mcp_arguments>)`
> (`AIRLOCK_FATHOM_STORE_MCP_PAYLOADS`). For `project_fathom(event)` to be **pure**
> (no kwargs re-read — the seam's contract), the event must carry `request_headers`
> (raw `kwargs["headers"]`), `mcp_arguments` (the same resolution chain
> kwargs→litellm_params→metadata), and a response from which `_response_text` can
> derive `response_text` — `event.response` already holds the serialized response
> object, so fathom runs `_response_text` against it. `messages_json` derives from
> `event.messages`. The env-flag gating stays **in the fathom projection** (it is a
> fathom output concern, not an event concern).

> **§3.9 — Bare vs rich `error` (finding #1).** s3 (`s3_logger.py:93`) and sql
> (`sql_logger.py:121`) emit `"error": str(kwargs.get("exception")) if not success
> else None` — the **raw** exception string, including `None`/empty-string edge
> cases. Enterprise emits the **normalized** rich triple. The event carries **both**:
> the rich triple (enterprise projects it) and `bare_exception_error` (s3/sql
> project it verbatim as their `error`). Carrying only the rich triple would change
> s3/sql output — a BLOCK. fathom's `error`/`error_type` (under
> `AIRLOCK_FATHOM_STORE_ERROR_DETAILS`) project from the **rich** triple, matching
> today (it reads the enterprise builder's `record`).

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

### 5a. Registration cutover + ordering invariant (RESOLVED — finding #4)

The recorder is **one fan-out callback** that replaces the per-sink registrations.
Concretely, today's registration surface (verified at HEAD):

- **`config.yaml`** (`:547-549`): `callbacks` = `[model_override_headers...]`;
  `success_callback`/`failure_callback` =
  `[enterprise_logger.proxy_logger, fast.monitor.proxy_monitor]` (fathom is
  commented out — **not** registered via config today).
- **Module-level `_self_register()`** at import: enterprise (`:574-593`,
  adds `proxy_logger` to sync+async success/failure lists) — and the analogous
  self-registration in the s3 / sql / fathom / metrics modules.

**Cutover:** the recorder registers **once** (sync+async success+failure) and calls
the record sinks (enterprise, s3, sql, fathom) + the per-request side channels
(mutation ledger, `requests_total`/`request_duration`/`_record_mutations`) as plain
**projection functions**, not as independently-registered `CustomLogger`s. Each
migrated sink's `_self_register()` is removed and its `config.yaml` entry is dropped
in the **same** MIGRATE pack that moves it (so it is never both self-registered
*and* fan-out-dispatched — that would double-emit). The EVENT pack adds the recorder
registration; the MIGRATE packs each remove one old registration as they land.

**Ordering invariant — DO NOT regress (the subtle one):** `fast.monitor.proxy_monitor`
is **not a record sink** — it is the protection subsystem, and it **stays a separate
callback**. Crucially it **mutates `metadata`** during the success/failure callback
(`monitor.py:326-327` sets `metadata["airlock_provider"]` and
`metadata["airlock_provider_protection"]`), and enterprise's `guardrail_meta` is
`{k:v for k,v in metadata if k.startswith("airlock_")}`. **Today's callback order is
`[enterprise, monitor]` — enterprise's record is built BEFORE monitor mutates
metadata**, so `airlock_provider_protection` is *not* in today's
enterprise/fathom `guardrail_meta`. The recorder must therefore be registered in
**enterprise's slot — before `proxy_monitor`** — and source the event (snapshot
`guardrail_meta` from `metadata`) at that point, reproducing today's snapshot
exactly. A golden test pins a request that arms provider-protection and asserts
`airlock_provider_protection` is **absent** from the logged `guardrail_meta`
(byte-identical to today). Sourcing after monitor would silently *add* a field — a
BLOCK.

> The "memoized event off `kwargs`" alternative is rejected: it keeps N
> registrations and a second-source-of-truth risk, and does not resolve the
> ordering question above. One fan-out callback in enterprise's slot is the seam.

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
- 2026-06-28 (rev 2 — post codex BLOCK, re-gate) — addressed all four design-gate
  findings with HEAD-verified evidence:
  **#1** added `bare_exception_error` to the event so s3/sql reproduce their raw
  `str(exception)` (§3.9, §4); **#2** added `request_headers` + `mcp_arguments` and
  pinned `response_text` derivation so `project_fathom` is pure (§3.8, §4);
  **#3** explicitly scoped `write_precall_block_record()` + `write_batch_record()`
  OUT, like `admin_action` (§2a); **#4** pinned the registration cutover and the
  **monitor-ordering invariant** — recorder takes enterprise's slot *before*
  `proxy_monitor`, monitor stays a separate non-sink callback, each MIGRATE pack
  removes its own old registration (§5a). Verdict promoted to
  `0.5.4-EVENTBUS-design-review-20260628T153724Z.md`. Re-running the codex gate.
- 2026-06-28 — DRAFT seeded at kickoff from a verified HEAD read; resolved UN-28,
  parallel-safety, builder count, filename; surfaced the §3 divergences as the core
  risk; left seam-shape / redaction-placement / timestamp-convergence / branch for
  the gate/HITL.
</content>
</invoke>
