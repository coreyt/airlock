# Batch Use Case — Guardrail & Subsystem Toggle Considerations

**Status: to-do / considerations only — no code changes, no behavior changes.**
**Date:** 2026-06-14
**Author:** design note (grounded in current `config.yaml`, `airlock/guardrails/*`, `airlock/fast/*`)

---

## Purpose

The user wants a switch to enable/disable guardrails "and other things," and
recognizes the **batch** path (`/v1/files` + `/v1/batches`, currently
`vertex_ai`) likely needs a *different default posture* than the interactive
chat path (`/v1/chat/completions`). This document enumerates every subsystem
that plausibly has an on/off/other setting for the batch use case, with
recommended defaults, the control mechanism (existing vs new), and the risk if
toggled wrong. It is a considerations / to-do list, **not** an implementation.

---

## Batch's different use case (and what NEW capability it implies)

Batch is structurally unlike chat, and that drives almost every recommendation
below:

| Dimension | Chat (`/v1/chat/completions`) | Batch (`/v1/files` + `/v1/batches`) |
|---|---|---|
| Body shape | top-level `model` + `messages` | `input_file_id` → GCS-staged JSONL; **no top-level `model`, no `messages`** |
| Latency / interactivity | synchronous, per-request, user-facing | asynchronous job, polled later, no interactivity |
| Volume | one prompt | thousands of rows in one file |
| Cost | full price | ~50% discount; cost-sensitive by design |
| Content location | in the request Airlock sees | **inside the uploaded file Airlock never parses** |
| call_type | `completion` / `acompletion` (and `call_mcp_tool` for MCP) | `acreate_batch` / file-create routes |

**The load-bearing consequence:** every content-inspecting guardrail extracts
text via `extract_text(data, call_type)` (`airlock/guardrails/extract.py`),
which reads `data["messages"]` (or MCP fields). A batch request has neither, so
`extract_text` returns `""` and the chat-path content guardrails (PII redaction,
keyword block, semantic, response scanner, enforcer signals) **silently no-op**.
This is already documented as a caveat in `dev/vertex-gemini-batch-setup.md`
("Guardrail caveat") and `airlock/fast/router.py` / `model_alias.py` already
have defensive comments that "Batch/file routes carry no top-level model."

So the real PII/keyword/injection exposure for batch is **not** on the chat
hook path at all — it is in the **uploaded JSONL**, which today is a pure
LiteLLM passthrough to GCS. That implies NEW capability rather than just a
toggle:

- **Scan-at-upload:** a `/v1/files`-time hook that downloads/streams the staged
  JSONL, extracts each row's prompt, and runs the existing
  `extract.py` + PII/keyword/regex logic over it before the file is accepted.
  This is the only place Airlock can actually see batch content.
- **A batch-specific guardrail profile** ("batch profile") — a named posture
  that differs from the chat default, selected by call_type, rather than
  per-guardrail env vars that apply globally.
- **Pre-redaction enforcement / attestation:** if scan-at-upload is too heavy,
  require that batch input be pre-redacted client-side and record an
  attestation, or reject un-attested batch uploads.
- **Size / row caps:** max rows, max file bytes per batch (bulk = bulk blast
  radius); a runaway batch is a cost and compliance event, not a single
  bad request.
- **Output-bucket retention / scanning:** batch *output* lands in
  `GCS_BUCKET_NAME`; consider retention limits and whether output is scanned
  (response_scanner equivalent) before downstream consumption.

---

## How toggling is done TODAY (baseline — what exists)

- **Per-guardrail `default_on: true`** in the `guardrails:` block of
  `config.yaml`. This is the coarse on/off; it is **global**, not route-aware.
- **Per-guardrail `mode:`** list (`pre_call`, `during_call`, `post_call`, plus
  `*_mcp_call` variants). Modes scope by *hook phase*, and the `_mcp_call`
  variants already scope by *route* — this is the precedent for route scoping.
- **Env-var flags / knobs** (grep of `os.getenv` / `_env_flag` / `AIRLOCK_*`):
  - `_env_flag` helper (`airlock/guardrails/__init__.py`) — defaults True.
  - `AIRLOCK_PII_ENABLED`, `AIRLOCK_PII_ENTITIES`, `AIRLOCK_PII_HYDRATION`
  - `AIRLOCK_KW_ENABLED`, `AIRLOCK_BLOCKED_KEYWORDS`
  - `AIRLOCK_ENFORCE_MODE` (observe/shadow/enforce)
  - `AIRLOCK_SEMANTIC_BLOCK_ON_FAIL`
  - `AIRLOCK_RESPONSE_SCAN_MODE` (observe/enforce), `AIRLOCK_RESPONSE_SCAN_THRESHOLD`
  - `AIRLOCK_REASONING_STRIP_MODELS`
  - `AIRLOCK_MCP_ALLOWED_TOOLS`, `AIRLOCK_MCP_BLOCKED_TOOLS`
  - Routing: `AIRLOCK_SMART_THRESHOLDS`, `AIRLOCK_COST_TIERS`,
    `AIRLOCK_SESSION_TTL`, `AIRLOCK_PROVIDER_BUDGETS`
  - Local vLLM: `AIRLOCK_LOCAL_VLLM_BASE_URL`, `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`,
    `AIRLOCK_LOCAL_VLLM_SWITCH_HINT`, `AIRLOCK_CONFIG`
  - **None of these env flags are route/call-type aware** — they flip a
    guardrail on for *all* traffic or *none*.
- **Per-request control via LiteLLM:** LiteLLM natively supports a `guardrails`
  field in the request body (and metadata) to enable/disable specific
  guardrails per request. This **already exists in LiteLLM** but is
  caller-driven; for batch it is the *batch submitter* who would set it, which
  is the wrong trust boundary for a "disable scanning" switch. Airlock does not
  currently read or constrain it.
- **Route discrimination precedent:** `is_mcp_call(data, call_type)` in
  `extract.py` keys off `call_type == "call_mcp_tool"` (or `mcp_tool_name` in
  data). **There is no `is_batch_call` analogue today.** `guardian.py`,
  `enterprise_logger.py`, `monitor.py`, and `state.py` all branch on the MCP
  call_type; a parallel `is_batch_call` (matching `acreate_batch` and the
  file-create call_types) would be the natural, idiomatic seam for batch
  scoping. **This would be NEW.**

---

## (a) Guardrails

Legend — *Applies to batch?* yes / no / partial.
*Control:* EXISTING mechanism, or NEW capability needed.

### PII redaction — pre_call (`pii_guard.AirlockPIIGuard`, `airlock-pii-guard`)
- **Applies to batch?** **No (on the chat path).** Reads `data["messages"]` /
  `mcp_arguments`; batch has neither, so it no-ops. The actual PII lives in the
  uploaded JSONL it never sees.
- **Suggested batch default:** **off on the hook path; ENFORCE at upload.** It
  cannot redact what it cannot see; pretending it runs is the dangerous part.
- **Control:** EXISTING global toggle is `AIRLOCK_PII_ENABLED` + `default_on`.
  NEW needed: scan/redact-at-`/v1/files` capability, or a required
  pre-redaction attestation gate.
- **Risk if wrong:** *highest.* Believing "PII guard is on" while bulk PII flows
  unredacted into a third-party batch is a real compliance failure (the bulk
  multiplies blast radius). False sense of security is worse than an honest
  "off."

### PII hydration — post_call (`pii_guard.AirlockPIIGuard`, `airlock-pii-hydrator`)
- **Applies to batch?** **No.** Hydrates tool-call args in a `ModelResponse`
  using the pre-call mapping; batch produces no such response object on this
  path and no mapping was created.
- **Suggested batch default:** **N-A.** No-op by construction.
- **Control:** EXISTING (`AIRLOCK_PII_HYDRATION`). No batch action needed unless
  upload-time redaction introduces a mapping that batch output must rehydrate
  (then NEW: output-side hydration keyed off the staged mapping).
- **Risk if wrong:** low (no-op), unless upload-time redaction is added without
  a matching output rehydration story.

### Keyword block — pre_call (`keyword_guard.AirlockKeywordGuard`)
- **Applies to batch?** **No (chat path).** `extract_text` → `""` → returns
  early; blocked keywords inside the JSONL are invisible.
- **Suggested batch default:** **off on hook path; enforce at upload** (same
  rationale as PII).
- **Control:** EXISTING global (`AIRLOCK_KW_ENABLED`, `AIRLOCK_BLOCKED_KEYWORDS`);
  NEW for upload-time scanning.
- **Risk if wrong:** medium/high — restricted codenames/terms exfiltrated in
  bulk with no block.

### Enhanced interceptor — pre_call (`enhanced_interceptor.EnhancedModelInterceptor`)
- **Applies to batch?** **No.** Only acts when `litellm_params.enhanced_profile`
  is present (the `enhanced/` custom provider); batch targets `vertex_ai`.
- **Suggested batch default:** **N-A.**
- **Control:** EXISTING (`default_on`); inert for batch.
- **Risk if wrong:** negligible.

### Fast Guardian — pre_call (`fast.guardian.AirlockFastGuardian`)
- **Applies to batch?** **Partial — and this is a sharp edge.** Threat/backoff
  and priority steps run on the *client* regardless of body shape. But the
  model-specific steps (`alias_table.resolve`, provider-protection,
  `apply_routing`, circuit breaker) operate on `data.get("model") or "unknown"`.
  For batch there is no top-level model, so `requested_model == "unknown"`:
  `_is_client_pinned("unknown", …)` returns True → `_lock_pinned_request` sets
  `disable_fallbacks/num_retries=0`; `infer_provider("unknown")` → None; the
  circuit breaker is consulted for a model named `"unknown"`. None of this is
  meaningful for a batch job and could mis-tag or mis-handle it.
- **Suggested batch default:** **threat/backoff/priority = on** (rate-abuse
  protection is still valuable for bulk submitters); **routing / circuit
  breaker / provider-protection / pinned-lock = off / skipped for batch** (just
  as they are already skipped for MCP via `if not mcp:`).
- **Control:** EXISTING pattern to copy — add an `is_batch_call` guard
  mirroring the existing `mcp` short-circuit so model-specific blocks are
  skipped. **NEW** (`is_batch_call`).
- **Risk if wrong:** medium — spurious "model unavailable / no healthy
  fallback" errors on batch submission, or rate-limit/priority metrics polluted
  by a model literally named `unknown`.

### Enforcer — pre_call (`enforcer.AirlockEnforcer`)
- **Applies to batch?** **No (effectively).** `collect_signals` ultimately reads
  `extract_text` → empty; composite score ≈ 0; default mode is `observe`
  (no-op).
- **Suggested batch default:** **off / observe.** It has no signal to act on for
  batch; enforcing would block on noise.
- **Control:** EXISTING (`AIRLOCK_ENFORCE_MODE`). Should remain observe for
  batch even if chat moves to `enforce`. NEW: per-route mode would let chat
  enforce while batch stays observe.
- **Risk if wrong:** medium — if a future global `enforce` is set, batch could
  be blocked (or passed) on a meaningless zero score.

### Semantic guard — during_call (`semantic.AirlockSemanticGuard`)
- **Applies to batch?** **No.** during_call runs concurrently with a synchronous
  provider round-trip that batch does not have; also reads `extract_text`
  (empty). Registry is currently empty anyway.
- **Suggested batch default:** **N-A / off.**
- **Control:** EXISTING `default_on` + `mode`. NEW: exclude batch from any
  future classifier registry; classifiers should instead run at upload-scan
  time if batch content is to be ML-screened.
- **Risk if wrong:** low today (no classifiers); medium later if ML screening is
  assumed to cover batch.

### Orchestrator — during_call (`orchestrator.AirlockOrchestrator`)
- **Applies to batch?** **No (observation-only, never raises).** Empty signals
  → composite 0.
- **Suggested batch default:** **N-A.** Harmless; leave as-is.
- **Control:** EXISTING. No action.
- **Risk if wrong:** negligible (never blocks).

### MCP tool guard — pre_mcp_call (`mcp_tool_guard.AirlockMCPToolGuard`)
- **Applies to batch?** **No.** MCP-only by mode and by `mcp_tool_name` gate.
- **Suggested batch default:** **N-A.**
- **Control:** EXISTING. No action.
- **Risk if wrong:** none.

### Response scanner — post_call (`response_scanner.AirlockResponseScanner`)
- **Applies to batch?** **No (on the sync path).** Scans a `ModelResponse` /
  stream / MCP result; batch output is a GCS file fetched out-of-band, not a
  hook-delivered response.
- **Suggested batch default:** **off on hook path; consider a NEW
  batch-output scan** of the result JSONL in `GCS_BUCKET_NAME` before downstream
  use.
- **Control:** EXISTING (`AIRLOCK_RESPONSE_SCAN_MODE/THRESHOLD`) is inert for
  batch. NEW: output-bucket scan job.
- **Risk if wrong:** medium — injection / exfil markers in bulk batch output go
  unscanned if a downstream agent later ingests them.

### Reasoning stripper — post_call (`reasoning_stripper.AirlockReasoningStripper`)
- **Applies to batch?** **No.** Model-scoped to `AIRLOCK_REASONING_STRIP_MODELS`
  (default `kimi-dev`, a local vLLM model). Batch is Vertex Gemini; also no hook
  response object.
- **Suggested batch default:** **N-A.** If Gemini batch ever emits reasoning in
  the output file, stripping would belong in the (NEW) output-scan stage, not
  this hook.
- **Control:** EXISTING (`AIRLOCK_REASONING_STRIP_MODELS`).
- **Risk if wrong:** negligible.

### Local vLLM router — pre_call (`local_vllm_router.AirlockLocalVLLMRouter`)
- **Applies to batch?** **No.** Only fires for aliases whose `api_base` matches
  the local vLLM host; reads `data["model"]` (absent for batch) and returns
  early.
- **Suggested batch default:** **N-A.**
- **Control:** EXISTING. No action.
- **Risk if wrong:** none.

### Observer — during_call (`observer.AirlockObserver`) *(not currently wired; superseded by orchestrator)*
- **Applies to batch?** **No** (same empty-text reason). Listed for completeness.
- **Suggested batch default:** **N-A.**

---

## (b) Fast subsystem / routing

### Smart / cost-tier routing (`fast/router.py: apply_routing`, `classify_complexity`, `cost_tiers`)
- **Applies to batch?** **No.** Driven by `model == "smart"` and
  `metadata.airlock` directives plus `_extract_text` over `messages`; batch has
  neither. Cost optimization for batch is the provider's ~50% discount and model
  *selection inside the JSONL rows*, not Airlock's per-request tier swap.
- **Suggested batch default:** **off / N-A.** Do not let smart-routing rewrite a
  batch (there is nothing coherent to rewrite).
- **Control:** EXISTING (`AIRLOCK_COST_TIERS`, `AIRLOCK_SMART_THRESHOLDS`,
  `cost_tiers:`). NEW: explicit batch skip via `is_batch_call` in guardian.
- **Risk if wrong:** medium — a batch mis-tagged as `model="unknown"`/`"smart"`
  could be mutated unexpectedly.

### Model-alias resolution (`fast/model_alias.py`)
- **Applies to batch?** **No.** `resolve()` already bails on non-str/empty model
  (explicit batch comment at line ~296).
- **Suggested batch default:** **N-A** (already safe).
- **Control:** EXISTING (defensive). No action.
- **Risk if wrong:** low (already guarded).

### Rate-limit / threat backoff (`fast/threat_detector.py`, `state.py`, guardian Steps 1–2)
- **Applies to batch?** **Yes (and should).** Per-client request recording,
  backoff, and threat heuristics are body-shape-independent and protect against
  bulk abuse / submission floods.
- **Suggested batch default:** **on.** Bulk submitters are exactly who you want
  rate-limited; arguably *stricter* caps for batch (rows × jobs).
- **Control:** EXISTING (runs unconditionally in guardian). NEW (optional):
  batch-specific thresholds (jobs/day, rows/job) once `is_batch_call` exists.
- **Risk if wrong:** medium — disabling lets a client flood expensive bulk jobs;
  over-tightening blocks legitimate large jobs.

### Provider protection / smart routing / failover (guardian Step 2.5b–3, `circuit_breaker.py`)
- **Applies to batch?** **No / harmful.** Operates on `model="unknown"` for
  batch; `infer_provider` returns None so most blocks are skipped, but the
  circuit-breaker check and pinned-lock still execute against a meaningless
  model name.
- **Suggested batch default:** **off / skipped for batch** (mirror the existing
  `if not mcp:` skip).
- **Control:** EXISTING skip pattern; NEW `is_batch_call` to trigger it.
- **Risk if wrong:** medium — false "model unavailable" rejections of batch
  submissions; corrupted circuit-breaker stats keyed on `"unknown"`.

### Circuit breaker (`fast/circuit_breaker.py`)
- **Applies to batch?** **No.** See above — no real per-batch model to break on;
  batch async failures are reported by the provider job status, not a sync 5xx.
- **Suggested batch default:** **off for batch submission.** (Provider-level
  Vertex health could be tracked separately, but not via this sync breaker.)
- **Control:** EXISTING; NEW skip via `is_batch_call`.
- **Risk if wrong:** medium — same `"unknown"`-model pollution.

### Priority scoring (`fast/priority.py`, guardian Step 4)
- **Applies to batch?** **Partial / low value.** Computes a priority score for
  speed-bursts; batch is async and non-interactive, so priority is largely
  meaningless for it.
- **Suggested batch default:** **off / neutral for batch.** Batch should not
  consume interactive priority boosts.
- **Control:** EXISTING; NEW skip/neutralize via `is_batch_call`.
- **Risk if wrong:** low — batch jobs taking interactive priority budget.

### Monitor / metrics (`fast/monitor.py`)
- **Applies to batch?** **Partial.** Runs on success/failure callbacks; already
  branches on MCP call_type (`record_call_type`). Batch jobs would be recorded,
  but model health / latency semantics differ (async).
- **Suggested batch default:** **on, but tagged separately.** Record batch as
  its own category so it doesn't distort interactive latency/health stats.
- **Control:** EXISTING callback; NEW: a batch call_type branch alongside the
  existing MCP branch (`monitor.py:127/236`, `state.py:785`).
- **Risk if wrong:** low/medium — batch latencies (minutes) skewing circuit
  breaker / priority if folded into interactive health.

---

## (c) Logging & observability

### Enterprise logger (`callbacks/enterprise_logger.py`, success/failure_callback)
- **Applies to batch?** **Yes.** It already inspects `call_type` and tags MCP
  (`enterprise_logger.py:398–401`). Batch jobs should be logged too —
  audit/compliance arguably *needs* batch logged even more (bulk, third-party).
- **Suggested batch default:** **on, always.** Plus a batch tag and the
  `input_file_id` / job id / row count / output URI for traceability.
- **Control:** EXISTING callback; NEW: a batch call_type branch and batch
  metadata fields (analogous to the MCP branch).
- **Risk if wrong:** high (audit gap) if batch is *excluded* from logging; this
  is the one place batch logging should never be toggled off.

### Fast monitor metrics — see (b) above.

### Fathom logger (`callbacks/fathom_logger.py`, currently commented out)
- **Applies to batch?** **Yes if enabled.** Reads `call_type`
  (`fathom_logger.py:135`).
- **Suggested batch default:** **on when fathom is on**, with batch tagging.
- **Control:** EXISTING (commented in `config.yaml` success/failure_callback).
- **Risk if wrong:** low (currently disabled).

### TUI guard/log views (`airlock/tui/screens/guards.py`, `logs.py`)
- **Applies to batch?** **Partial.** They filter/label by `call_type` (MCP vs
  LLM today). Batch records would show as neither/"unknown".
- **Suggested batch default:** **add a batch label/filter** so operators can see
  batch traffic distinctly.
- **Control:** NEW (a batch call_type case in the existing LLM/MCP switch).
- **Risk if wrong:** low — observability clarity only.

---

## (d) Vertex / batch-specific config

These are genuinely batch-only knobs (chat has no equivalent). Most are NEW or
currently only in `.env` / `config.yaml` literals.

### Vertex batch location override (`vertex_location`)
- **Applies to batch?** **Yes — critical.** `config.yaml` pins
  `vertex_location: global` literally on the `gemini-*-vertex` deployments, and
  `dev/vertex-gemini-batch-setup.md` flags that `BatchPredictionJob` generally
  needs a **regional** location while 3.x models only resolve on `global`.
- **Suggested batch default:** **a regional location (e.g. `us-central1`) for
  batch**, distinct from `global` used for sync — pending the "batch on global"
  open question in the setup note.
- **Control:** EXISTING config field, but NEW need: a sync-vs-batch location
  split (today `VERTEX_LOCATION` env isn't even consulted because the literal
  `global` wins).
- **Risk if wrong:** high — batch jobs rejected outright, or silently routed to
  a region where the model 404s.

### GCS bucket / staging (`GCS_BUCKET_NAME`, `secrets/airlock-vertex.json`)
- **Applies to batch?** **Yes — mandatory.** Batch input/output stage through
  the bucket; SA + IAM are batch-only.
- **Suggested batch default:** **on / required;** fail batch submission loudly if
  unset rather than passing through.
- **Control:** EXISTING `.env` (`GCS_BUCKET_NAME`, `VERTEX_CREDENTIALS`). NEW:
  a preflight/validation gate.
- **Risk if wrong:** high — opaque failures; output written to an unexpected /
  unscanned bucket.

### completion_window / job parameters
- **Applies to batch?** **Yes (batch-only concept).** No chat analogue.
- **Suggested batch default:** **a sane default window**, configurable; document
  it.
- **Control:** NEW (not currently surfaced in Airlock config).
- **Risk if wrong:** low/medium — jobs expiring or queued longer than expected.

### Batch size / row caps & output retention
- **Applies to batch?** **Yes (batch-only).**
- **Suggested batch default:** **caps ON** (max rows, max bytes, max concurrent
  jobs); **output retention** policy on `GCS_BUCKET_NAME`.
- **Control:** NEW.
- **Risk if wrong:** high — unbounded bulk cost / data sprawl; stale sensitive
  output lingering in GCS.

### Output-bucket scanning (see response_scanner in (a))
- **Applies to batch?** **Yes (NEW capability).**
- **Suggested batch default:** **observe initially**, enforce later.
- **Control:** NEW.
- **Risk if wrong:** medium — unscanned bulk output.

---

## (e) Cross-cutting — a global "batch profile" / per-route scoping

This is the heart of the user's "switch to enable/disable guardrails and other
things." Recommended building blocks, in priority order:

1. **`is_batch_call(data, call_type)` helper** (NEW) in `extract.py`, mirroring
   `is_mcp_call`. Matches the batch/file call_types (`acreate_batch` and the
   file-create routes). This is the single seam every other batch decision keys
   off — exactly how `is_mcp_call` is reused across `guardian.py`,
   `enterprise_logger.py`, `monitor.py`, `state.py`. **Highest-leverage item.**

2. **A named "batch profile"** (NEW) — a config block (e.g. `batch_profile:` in
   `config.yaml`, env-overridable like `cost_tiers`) that declares the batch
   posture in one place: which guardrails are on/off/observe for batch, caps,
   location, scan-at-upload on/off. Far better than scattering route logic into
   every guardrail or relying on global env flags that can't distinguish routes.

3. **Per-guardrail route scoping via `mode`** (EXTENDS existing): the `_mcp_call`
   mode variants already prove modes can scope by route. A `*_batch_call` mode
   family (or a `routes:`/`exclude_routes:` key per guardrail) would let each
   guardrail opt in/out of batch declaratively in `config.yaml` — consistent
   with how MCP is handled today. **NEW but idiomatic.**

4. **Trust-boundary note on LiteLLM's per-request `guardrails` field**
   (EXISTING in LiteLLM): it lets the *caller* disable guardrails per request.
   For batch, the caller is the batch submitter — the wrong party to hold a
   "disable scanning" switch. Any batch toggle should be *operator/config*
   controlled (profile), and Airlock should consider *ignoring or constraining*
   client-supplied `guardrails`/`metadata` disables on the batch path.

5. **Scan-at-upload hook** (NEW) — the only mechanism that gives batch the
   content coverage chat gets. Without it, "guardrails on for batch" is
   aspirational. Reuses `extract.py` + PII/keyword/regex logic over each JSONL
   row at `/v1/files` time.

**Cross-cutting risk if wrong:** the dominant failure mode is a *false sense of
security* — a global "guardrails: on" switch that visibly applies to chat but
silently no-ops on batch, letting bulk unredacted/unscanned data flow to a
third-party provider. The fix is to make the batch posture *explicit and
separate*, not implicitly inherited from chat.

---

## Top-priority items (summary)

| # | Item | Suggested batch default | Mechanism |
|---|---|---|---|
| 1 | PII redaction coverage for batch content | **off on hook path; ENFORCE via scan/redact-at-`/v1/files` or required pre-redaction** | NEW upload scan + EXISTING `AIRLOCK_PII_ENABLED` |
| 2 | `is_batch_call` seam + skip model-specific Fast Guardian steps (routing, circuit breaker, provider-protection, pinned-lock) for batch | **skipped for batch** (mirror `if not mcp:`) | NEW helper, EXISTING skip pattern |
| 3 | Vertex batch **location** (regional vs `global`) + GCS bucket/SA validation | **regional for batch (e.g. `us-central1`), distinct from sync `global`; fail loudly if GCS unset** | EXISTING config, NEW sync/batch split |
| 4 | Enterprise logging of batch jobs (audit) | **on, always, with batch tag + input_file_id/job id/row count/output URI** | EXISTING callback, NEW batch branch |
| 5 | Batch caps (rows/bytes/concurrent jobs) + output-bucket retention/scan | **caps ON; output scan observe→enforce** | NEW |

Honorable mention: a single **batch profile** config block (item (e)2) is the
cleanest home for items 1–5 and the literal answer to "a switch to enable/disable
guardrails and other things" for batch.
