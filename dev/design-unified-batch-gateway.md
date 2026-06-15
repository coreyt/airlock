# Design: Unified Airlock Batch Gateway (AI Studio Gemini + Mistral, one path)

**Status: PROPOSED — design only. Implementation NOT started.**
**Date:** 2026-06-14
**Supersedes (as the implementation spec):** `dev/design-aistudio-gemini-batch.md`
(Option A) and `dev/mistral-batch-findings.md` (§4). Those remain valid as the
per-provider investigations; this doc is the adversarially-reviewed, two-provider
design they both point to.

---

## 0. TL;DR

- **Adversarial review** (mine + an independent red-team that verified every claim
  against the code) found the two source docs directionally correct but with
  **six material holes**: Presidio-at-scale (A1), PII-map-as-PII-at-rest (A2),
  non-idempotent create race (A3), lossy Gemini response mapping (A4), route-shape
  vs SDK drop-in (A5), batch logging (A6).
- **Status after this pass:** **A5 RESOLVED** (✅ ASGI middleware — verified
  addable to the litellm app at import, query-param discriminator, `call_next`
  delegation; order-independent). **A3 & A4 RESOLVED** (✅ §3.7 — race-free claim,
  duplicates bounded to ≤1 job and auto-cancelled, result staging idempotent/keyed
  with reprocessing bounded to missing rows). A6 has a concrete fix
  (`write_batch_record`); A1/A2 have a committed approach (async process-pool scan;
  terminal-redaction default) with detail items still open (§7).
- **Common vs separate: COMMON.** ~80% of the work is provider-agnostic
  (HTTP surface, OpenAI batch object, auth, JSONL parse, guard scan, PII
  lifecycle, status normalization, persistence, idempotency, caps, observability).
  Only a thin **`BatchBackend` adapter** differs per provider. Two separate
  wirings would duplicate the risky 80% and drift.
- **Design = an "Airlock Batch Gateway":** one injected route + one orchestration
  core + pluggable `BatchBackend` adapters (`MistralBackend`, `AIStudioBackend`,
  and later a `LiteLLMNativeBackend` so the *wired* providers also get uniform
  guarding). Build the core + adapter interface first; **lead with the AI Studio
  adapter** (the actual Gemini-3.x need + hardest translation), then add Mistral as
  the cheap second adapter that validates the seam (see §6 for the trade-off).

---

## 1. Adversarial review of the two source docs

Severity: 🔴 must-fix before implementation, 🟠 design-affecting, 🟢 minor.

### 🔴 A1 — Scanning every JSONL row with Presidio *inline at upload* doesn't scale
`design-aistudio-gemini-batch.md` §3.4/§4.1 buffers the uploaded JSONL and runs
`_scrub_text_with_mapping` per row in the `POST /v1/files` handler. Mistral/AI
Studio batches go up to **1M rows / 2GB**. Presidio (spaCy NER) is a documented
latency problem (`dev/presidio-think-slow-investigation.md`) — per-row analysis of
1M rows synchronously will blow the request timeout and/or OOM the proxy, and
blocks an event-loop worker for the duration.
**Resolution (this design):** scanning is an **asynchronous phase**, not part of
the HTTP upload. The gateway accepts/persists the raw upload, returns immediately
with `status: validating`, and scans in the **think-slow** worker
(`airlock/slow/…`, the exact subsystem that note proposes for spaCy offload),
streaming the JSONL row-by-row (never fully in memory). The provider job is
created only after scan completes. Hard `max_rows`/`max_bytes` caps bound the
worst case.

### 🔴 A2 — The PII reverse-map is itself a large PII-at-rest store
Both docs assume the chat-path **redact→rehydrate** model: store the
placeholder→original mapping, restore originals in the response. For batch that
mapping (a) contains the **original PII**, (b) must persist for the **whole job
lifetime (up to 48h), across proxy restarts**, (c) scales to **1M rows**. That is
exactly the sensitive-data-at-rest that redaction exists to avoid — a self-inflicted
PII lake with its own encryption/retention/breach surface.
**Resolution:** make rehydration an **explicit, off-by-default, costed** option,
not the default. Default batch posture = **terminal redaction** (the third party
and the returned results both see placeholders; no original-PII store). If a
tenant needs hydration, it's opt-in per `batch_profile`, the map is stored
encrypted with TTL ≤ job expiry, and the row/byte caps bound its size. This
flips the source docs' implicit default.

### ✅ A3 — RESOLVED: no race, double-submit detectable and bounded to ≤1 job
Provider create is non-idempotent and exposes no idempotency key, so we cannot get
exactly-once from the provider. We instead make the gateway's create path
**race-free, self-detecting, and bounded** so any duplicate is at most one job and
is auto-cancelled. Full mechanism in §3.7; in brief:

- **Deterministic idempotency key** `idem = sha256(input_file_id ∥ model ∥ endpoint
  ∥ canonical(params))` (a client `Idempotency-Key` header overrides). It is the
  state-store primary key **and** the provider `display_name`.
- **Write-ahead, atomic claim (no race):** `INSERT … ON CONFLICT(idem) DO NOTHING`
  **before** calling the backend. Exactly one concurrent caller wins the row
  (status `CREATING`, leased); losers read the row and return the in-flight
  `batch-id` — concurrent duplicate submits cannot both create.
- **Crash window is detectable and self-healing:** if a crash lands between
  `backend.create()` and recording `job_id`, retry sees `CREATING` with null
  `job_id` → **reconcile**: `backend.list()` filtered by `display_name == idem`;
  adopt the orphan if present; only (re)create if none **and** the lease expired.
- **Bounded + detectable:** because every job's `display_name == idem`, a duplicate
  is a *queryable* condition (`>1 job with the same display_name`). The reconciler
  adopts the earliest and **cancels the rest**. Worst case is **one** extra job in
  the narrow "lease expired before the provider list showed the orphan" window —
  detected and cancelled, never silent. The unit is one job (not per-row), so the
  wasted work is small and `max_concurrent_jobs` caps it further.

### 🟠 A4 — Gemini response translation is lossy
`design-aistudio-gemini-batch.md` §3.6 maps Gemini `candidates[].content.parts[]
.text` → `choices[].message.content`. That silently drops tool calls, thinking
blocks, finish/safety reasons, and multi-candidate output. Mistral is unaffected
(its batch results are already OpenAI-shaped).
**Resolution (correctness):** the OpenAI output line carries the
**provider-native response verbatim** in `response.body`, plus a best-effort
`choices` projection. Never lose the native payload. Translation lives entirely in
the per-provider adapter (see §3); Mistral's is near-identity, only Gemini's does work.

**Resolution (no double-processing — per your ask):** result fetch → translate →
hydrate → stage is **idempotent, keyed, and bounded** (full mechanism §3.7):
per-row keys (`key`/`custom_id`) so staging **upserts** by `(batch_id, row_key)`;
**deterministic** `from_provider_result` so a re-run is byte-identical and a
per-row hash detects drift; an atomic `RETRIEVING → STAGED` status gate so only one
worker stages a batch (a second caller sees `STAGED` and re-fetches nothing); and
on interrupted staging, retry diffs staged-keys vs result-keys and processes
**only the missing keys** — never the whole file again.

### ✅ A5 — RESOLVED: ASGI middleware front-controller (order-independent), verified
The red-team's first-match-wins blocker only bites the *route-registration*
approach. The resolution is **ASGI middleware**, which runs **before routing**
regardless of registration order or timing.

**Empirically verified against the installed litellm proxy app (2026-06-14):**
- A bare `import litellm.proxy.proxy_server` exposes **65 routes and NO
  `/v1/batches`/`/v1/files`** — litellm registers those later, during proxy
  startup. So route order is not even stable to reason about; middleware sidesteps
  it entirely.
- `app.middleware_stack` is **`None` (unbuilt)** at import, and
  `app.add_middleware(...)` **succeeds at import** (user_middleware 5 → 6). So
  Airlock can attach middleware from `model_override_headers` (where the existing
  `install_*_on_proxy_app` bootstraps already run) before the server starts.

**Mechanism.** A `BatchGatewayMiddleware` (added next to the existing injectors):
1. Acts only on `POST /v1/batches` and `POST /v1/files`.
2. Discriminates on the **`custom_llm_provider` query parameter** — read from
   `request.url.query`, so **no request body is buffered** (critical: `/v1/files`
   uploads can be 2 GB; buffering them in middleware would OOM). Gateway providers
   = `{aistudio, mistral}`.
3. Gateway provider → dispatch to the Airlock gateway handler. Otherwise
   `await call_next(request)` → litellm's native handler runs untouched. No route
   shadowing, no double-registration, no ordering dependency.

**Drop-in boundary (stated honestly).** Clients that can set
`?custom_llm_provider=aistudio|mistral` (raw HTTP, the litellm SDK, our own
clients) are drop-in on `/v1/batches`+`/v1/files`. The **stock OpenAI Python SDK**
sends no provider hint and no model on `batches.create` (the model lives in the
file), so it cannot select a gateway backend on the standard path — those callers
use the explicit query param or the namespaced `/airlock/batch/*` alias (kept as a
thin convenience, same handler). This boundary is inherent to the OpenAI batch
protocol, not a gateway shortcoming.

### 🟠 A6 — Batch logging is NEW work (corrected by the red-team pass)
My first pass called this "already solved" — **wrong.** The independent red-team
verified against the code: `_write_log(record)` is **private** and the only public
writers are chat/failure-shaped (`write_precall_block_record(...)` is hard-wired
`success=False` + `data.get("messages")`; `log_success_event` wants a LiteLLM
`kwargs`/`response_obj`). So: the **append primitive `_write_log` is reusable**
(it takes an arbitrary dict, with rotation/redaction/cleanup), but there is **no
batch-shaped record builder**, and `is_batch_call` + batch `call_type` +
TUI/monitor tagging are NEW (the considerations doc already labels them NEW).
**Resolution:** add a small public `write_batch_record(...)` next to
`write_precall_block_record` that builds the batch record and calls `_write_log`.
Budget it as new work, not reuse.

### 🟢 A7 — Minor over-claims
- "Reuses helpers verbatim": `_scrub_messages` expects a `messages` list, so the
  Gemini `contents` shape needs a normalizing adapter first (the docs say this,
  but "verbatim" oversells it). Mistral's `body.messages` *is* a messages list →
  genuinely verbatim there.
- Effort "Medium" for AI Studio undercounts A1 (async scan pipeline) and A2
  (encrypted PII store). With the common gateway, that cost is paid **once**.

---

## 2. Common vs separate — verdict: **COMMON gateway + thin adapters**

Both providers are the same shape of problem: a native batch API (JSONL upload →
create job → poll → download), 50% discount, **not wired in LiteLLM** (the
red-team confirmed the wired set is `openai`/`hosted_vllm`/`azure`/`vertex_ai`/
`bedrock` + `anthropic`-on-retrieve; neither `gemini` nor `mistral`), no
`CustomLLM` batch extension point. The remedy in both docs is the identical
"Airlock-owned route" (Option A). Implementing them separately duplicates every
hard part.

### What is genuinely common (build once)
- HTTP surface + OpenAI batch/file **object shapes** and status vocabulary.
- Proxy auth (`AIRLOCK_MASTER_KEY`), `X-Airlock-Client` attribution.
- JSONL ingest, **async guard-scan pipeline** (think-slow), `max_rows`/`max_bytes`
  caps (A1).
- PII lifecycle + the (opt-in, encrypted, TTL'd) map store (A2).
- Idempotency/dedup + restart reconciliation (A3).
- Job-id mapping, status **normalization framework**, polling, optional webhooks.
- Observability via `_write_log` (A6); `is_batch_call` tagging.
- `batch_profile` config + trust-boundary enforcement (ignore caller-supplied
  guard disables).

### What is provider-specific (the adapter — the only thing that differs)
| Concern | AI Studio (`AIStudioBackend`) | Mistral (`MistralBackend`) |
|---|---|---|
| SDK | `google-genai`: `files.upload`, `batches.create(model, src)`, `batches.get`, `files.download` | `mistralai`: `files.upload`, `batch.jobs.create(input_files, model, endpoint)`, `batch.jobs.get`, `files.download` |
| Auth | `GOOGLE_AISTUDIO_API_KEY` | `MISTRAL_API_KEY` |
| Input JSONL line | Gemini-native `{key, request:{contents,…}}` — **needs translation** from OpenAI line | OpenAI-native `{custom_id, body:{messages,…}}` — **near-identity** |
| Result line | Gemini `candidates[…]` — **needs translation** (A4) | OpenAI-shaped — near-identity |
| Status enum | `JOB_STATE_PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED/EXPIRED` | `QUEUED/RUNNING/SUCCESS/FAILED/TIMEOUT_EXCEEDED/CANCELLATION_REQUESTED/CANCELLED` |
| Key field | `key` | `custom_id` |

The adapter is ~a few methods (below). Everything else is shared. **Verdict:
one gateway, pluggable backends.** Mistral's near-identity adapter also makes it
the ideal **first** backend to prove the gateway; AI Studio's translation adapter
slots in second.

---

## 3. Unified architecture — the Airlock Batch Gateway

```
                    Airlock Batch Gateway  (injected on the LiteLLM FastAPI app)
client → /v1/files ─┐   ┌───────────────────────────────────────────────┐
client → /v1/batches┼──►│ front controller: resolve model alias          │
                    │   │  • gateway alias?  → gateway core              │
                    │   │  • else            → delegate to LiteLLM native│
                    │   └───────────────┬───────────────────────────────┘
                    │                   ▼
                    │        ┌─ gateway core (provider-agnostic) ─────────┐
                    │        │ auth · attribution · JSONL ingest · caps    │
                    │        │ async guard-scan (think-slow) · PII store   │
                    │        │ idempotency · status normalize · _write_log │
                    │        └───────────────┬─────────────────────────────┘
                    │                        ▼   BatchBackend (adapter)
                    │            ┌───────────┼───────────────┐
                    │            ▼           ▼               ▼
                    │     MistralBackend  AIStudioBackend  LiteLLMNativeBackend
                    │     (mistralai)     (google-genai)   (delegates to litellm,
                    │                                       uniform guarding for
                    │                                       openai/vertex/anthropic)
```

### 3.1 `BatchBackend` protocol (the entire provider-specific surface)
```python
class BatchBackend(Protocol):
    name: str                                   # "aistudio" | "mistral" | "litellm"
    def to_provider_request(self, openai_line: dict) -> dict: ...   # translate IN
    def from_provider_result(self, native_line: dict) -> dict: ...  # translate OUT (keep native in .body)
    async def upload(self, jsonl: bytes, display_name: str) -> str: ...      # -> provider file ref
    async def create(self, model: str, file_ref: str, display_name: str) -> str: ...  # -> job id
    async def poll(self, job_id: str) -> NormalizedStatus: ...      # provider enum -> OpenAI status
    async def fetch(self, job_id: str) -> Iterable[dict]: ...       # native result lines
    async def cancel(self, job_id: str) -> None: ...
```
`MistralBackend.to_provider_request`/`from_provider_result` are ~identity;
`AIStudioBackend`'s do the OpenAI↔Gemini `contents`/`candidates` translation
(A4: always preserve native in `.body`).

### 3.2 Request flow (drop-in OpenAI surface)
1. `POST /v1/files` (purpose=batch) → store raw upload, assign `file-<uuid>`,
   return `validating`. **No inline scan** (A1).
2. Async: think-slow worker streams the JSONL, runs enabled guards per row
   (keyword → reject upload on hit; PII → redact, map only if hydration opt-in),
   writes the **scrubbed** JSONL, marks the file `ready` (or `rejected`).
3. `POST /v1/batches {input_file_id, model, …}` → front controller resolves alias
   → gateway backend. Persist `creating` intent (A3) → `backend.upload` (scrubbed
   bytes) → `backend.create` → persist `batch-<uuid> → (backend, job_id)`.
4. `GET /v1/batches/{id}` → `backend.poll` → normalized status. On terminal
   success → `backend.fetch` → `from_provider_result` per line (native kept in
   `.body`) → optional output scan / PII hydrate (if opted in) → stage
   `output_file_id`.
5. `GET /v1/files/{id}/content` → staged result JSONL.
Non-gateway models at steps 1/3 are delegated to LiteLLM's native handlers.

### 3.3 State store
A single small store keyed by Airlock id holds: file/job id mappings,
`creating`/`ready`/`rejected`/terminal state, idempotency key, row counts, client
attribution, and (only if hydration opted in) an **encrypted** PII map with
TTL ≤ job expiry. MVP: SQLite under the Airlock data dir (the proxy already
persists circuit-breaker/fast state to disk); the map column is encrypted at rest.

### 3.7 Idempotency & exactly-once-ish processing (A3 + A4)

Goal stated by the requirement: **no race conditions; any rework / data processed
more than once must be detectable and quite small.** We cannot get true
exactly-once (the providers' create is non-idempotent and there is no provider
idempotency key), so the design targets **at-least-once with a race-free claim, a
bounded duplicate window, and detection + auto-heal** at every stage.

**State machine** (one row per `batch-id`, plus per-row staging rows):
```
files:   UPLOADED → SCANNING → READY | REJECTED
batches: CLAIMED(idem) → CREATING → CREATED(job_id) → RETRIEVING → STAGED | FAILED
rows:    (batch_id, row_key) → STAGED(content_sha)        # per output row
```

**Keys.** `idem = sha256(input_file_id ∥ model ∥ endpoint ∥ canonical(params))`
(client `Idempotency-Key` header overrides). `idem` is the **state-store PK** and
the **provider `display_name`**. Per-row key = the request `key`/`custom_id`.

**1) Create — race-free claim (A3).** All store mutations go through one
single-writer path (SQLite `BEGIN IMMEDIATE`, or an in-proc async lock keyed by
`idem`):
- `INSERT INTO batches(idem,…) VALUES(…) ON CONFLICT(idem) DO NOTHING`. Exactly one
  caller inserts → that caller owns creation; every concurrent duplicate reads the
  existing row and returns its `batch-id`. **No two callers can both create.**
- The winner sets `CREATING` + a short **lease** (`lease_until = now + 60s`), then
  calls `backend.create(display_name=idem)`, then atomically records `job_id` +
  `CREATED`.

**2) Crash window — detect & adopt, never blind-resubmit (A3).** If the winner
dies between `backend.create` and recording `job_id`, the row is stuck `CREATING`.
Any retry/another worker:
- If lease not expired → treat as in-flight, return the `batch-id` (back off).
- If lease expired → **reconcile before creating**: `backend.list()` filtered by
  `display_name == idem`. If a job exists → adopt it (`job_id`, `CREATED`). Only if
  **none** exists → create. Worst case (lease expired *and* provider listing hadn't
  yet surfaced the orphan) is **one** duplicate job — and because its
  `display_name == idem`, it is **detectable** (group jobs by `display_name`; count
  > 1) and **auto-cancelled** by the reconciler (keep earliest, `cancel()` the
  rest). Bounded to one job, never silent.

**3) Result processing — idempotent & bounded (A4).**
- `RETRIEVING → STAGED` is an atomic compare-and-set: one worker stages a batch; a
  second observing `STAGED` re-fetches **nothing**.
- Output rows **upsert** by `(batch_id, row_key)` with a `content_sha`. Translation
  is **deterministic**, so a re-run is byte-identical; a differing `content_sha`
  for an existing key is a **detected** non-determinism anomaly (logged, not
  silently overwritten with drift).
- Interrupted staging resumes by **diffing staged row-keys vs result row-keys** and
  processing only the **missing** keys — reprocessing is bounded to the unstaged
  remainder, never the whole (up to 1M-row) file, and the diff *is* the evidence of
  what got reprocessed.

**4) Detectability surface.** Duplicate jobs → `display_name` grouping; reprocessed
rows → per-row `content_sha` + the resume diff; every transition is written via
`write_batch_record` (A6) tagged `is_batch_call`, so an operator can audit "was
anything done twice?" from the logs. **Net:** races eliminated at the claim/stage
gates; the only residual duplication is ≤1 job in a narrow window, detected and
auto-cancelled; row-level rework is bounded to missing rows and hash-detectable.

---

## 4. Guardrails (when enabled) — corrected for batch reality
- **Async, streamed, capped** (A1): scanning runs in think-slow, row-streamed,
  bounded by `max_rows`/`max_bytes`; the job is created only after a clean scan.
- **Keyword**: reuse `keyword_guard._blocked_keywords()`/`_normalize_text()`; a
  hit **rejects the whole upload** (bulk blast radius).
- **PII**: reuse `pii_guard._scrub_text_with_mapping` (after normalizing the
  provider line to a `messages` list — A7). Default **terminal redaction**;
  hydration is opt-in and pays the encrypted-map cost (A2).
- **Trust boundary**: posture is operator/config-controlled (`batch_profile`);
  the gateway **ignores caller-supplied `guardrails`/`metadata` disables** on the
  batch path.
- **Observability**: every lifecycle event via `_write_log` (A6), tagged
  `is_batch_call`, with backend, job id, model, row count, state, client.

---

## 5. Config & deps
```yaml
# per-alias marker (read like enhanced_passthrough's profile cache)
- model_name: gemini-3.1-pro-aistudio
  litellm_params: { model: gemini/gemini-3.1-pro-preview, api_key: os.environ/GOOGLE_AISTUDIO_API_KEY }
  airlock_batch: { backend: aistudio, provider_model: gemini-3.1-pro-preview }
- model_name: mistral-large-batch
  litellm_params: { model: mistral/mistral-large-latest, api_key: os.environ/MISTRAL_API_KEY }
  airlock_batch: { backend: mistral, provider_model: mistral-large-latest }

batch_profile:                 # one place, env-overridable like cost_tiers
  default: { scan_at_upload: true, keyword_block: true, pii_redact: true,
            pii_hydrate_output: false,            # A2: off by default
            output_scan_mode: observe,
            max_rows: 50000, max_bytes: 2147483648, max_concurrent_jobs: 5 }
```
Optional extras (mirror `vertex`/`search`): `aistudio = ["google-genai>=1.0.0"]`,
`mistral-batch = ["mistralai>=1.0.0"]`. Imported lazily in the adapter; the route
returns a clear "install the X extra" error if missing.

---

## 6. Phased plan
- **Phase 0 — spikes:** standalone scripts hitting Mistral `client.batch.jobs.*`
  and AI Studio `client.batches.*` end-to-end (`live`-marked). Confirms both SDK
  contracts + result shapes.
- **Phase 1 — gateway core + the `BatchBackend` interface + first adapter.**
  Front controller, state store, status normalization, OpenAI object shaping,
  `write_batch_record`. No guards yet.
  **Which adapter first is a genuine tension** (the red-team and my first pass
  disagreed):
  - *Mistral-first* (my pick): near-identity translation → fastest working
    end-to-end batch → de-risks the gateway core independently of hard
    translation.
  - *Gemini-first* (red-team's pick): it's the **actual business need** (only
    viable Gemini-3.x batch path) **and** the hardest translation, so building it
    first proves the abstraction handles the hard case.
  **Resolution:** the core + the adapter *interface* are built first regardless;
  pick the first adapter by priority. Since Gemini 3.x is the reason this exists,
  **lead with `AIStudioBackend` behind the interface**, then add `MistralBackend`
  as the cheap second adapter that *validates* the boundary (its near-identity
  translation is the proof the seam is right). If gateway-core risk dominates over
  shipping Gemini, swap the order — the interface makes it cheap either way.
- **Phase 2 — second adapter** (`MistralBackend` if Gemini led, else AI Studio):
  same core, validates the abstraction. Note Mistral batch is **multi-endpoint**
  (`/v1/chat/completions`, `/v1/embeddings`, `/v1/ocr`, …), so the adapter must
  carry an `endpoint` field — AI Studio batch is generateContent-only.
- **Phase 3 — guards:** async think-slow scan pipeline (A1), keyword reject, PII
  redact (terminal default), `batch_profile`, trust-boundary enforcement, caps.
- **Phase 4 — hardening:** idempotency/reconciliation (A3), opt-in encrypted
  hydration store (A2), optional webhooks, output-scan `enforce`, TUI/monitor
  batch tagging, `docs/guide/batch.md`.
- **Phase 5 (optional) — `LiteLLMNativeBackend`:** front the *wired* providers
  (openai/vertex/anthropic) through the same gateway so they get the same
  guard-scan the chat path can't give batch. Closes the guardrail gap for ALL
  batch, not just the two unwired providers.

**Tests:** unit (status-enum normalization per backend; OpenAI object shaping;
Gemini⇄OpenAI translation incl. tool-call/lossless `.body`; null/odd lines),
guard round-trip (seeded PII + blocked keyword on a JSONL → redaction/rejection;
hydration restores by key when opted in), idempotency (duplicate create → one
job), and `live`-marked end-to-end per backend.

---

## 7. Status of the holes + remaining open items
**Resolved with evidence/spec:** A5 (✅ §1/A5 — ASGI middleware, verified
addable at import; query-param discriminator; `call_next` delegation),
A3 (✅ §3.7 — race-free claim, ≤1-job detectable+auto-cancelled duplicate),
A4 (✅ §3.7 — idempotent keyed staging, deterministic, bounded to missing rows),
A6 (✅ §1/A6 — `write_batch_record` → `_write_log`). A1/A2 have a committed
approach (async process-pool scan; terminal-redaction default).

**Still genuinely open (decide during build, not blockers to starting):**
1. **Async scan UX (A1 detail).** ~50–150 ms/row × up to 1M = 14–40 h
   single-threaded; runs in a process pool (think-slow), upload returns
   "accepted, scanning" — define the failure UX when a scan rejects *after*
   upload already returned 200.
2. **Hydration may not be worth it for batch at all.** Terminal redaction might be
   the *only* supported mode; opt-in hydration must justify the encrypted,
   TTL'd, ≤48 h bulk-PII store (A2).
3. **Result-file retention ≠ job expiry.** 48 h is the pending/running expiry; the
   provider result file has its own retention and may expire independently —
   staging/download must not assume the result is fetchable for the full window.
4. **Verify `airlock_batch` placement.** Under `litellm_params` it may be
   forwarded to the provider SDK on the *sync* path for these aliases (the
   `enhanced_profile` precedent suggests survivable — verify, don't assume).
5. **Webhooks need a public HTTPS callback + signature verification** — not
   "near-free later"; defer explicitly. Polling is the MVP.
6. `LiteLLMNativeBackend` double-handling risk (gateway scans, then litellm
   re-processes) — scope carefully in Phase 5.

---

## Appendix — grounding
- `dev/design-aistudio-gemini-batch.md`, `dev/mistral-batch-findings.md`,
  `dev/batch-guardrail-toggles-considerations.md`.
- `airlock/callbacks/enterprise_logger.py` — `_write_log`, `write_precall_block_record` (A6).
- `dev/presidio-think-slow-investigation.md` — Presidio latency + think-slow offload (A1).
- `airlock/guardrails/pii_guard.py` — `_scrub_text_with_mapping`, `_scrub_messages`,
  `_hydrate_tool_calls`, `_hydrate_value_recursive`, `airlock_pii_map` (A7).
- `airlock/health.py`/`airlock/docs.py` — proxy-route injection precedent;
  bootstrap in `airlock/callbacks/model_override_headers.py`.
- `litellm/batches/main.py`, `litellm/types/utils.py` (`OPENAI_COMPATIBLE_BATCH_AND_FILES_PROVIDERS`)
  — neither `gemini` nor `mistral` wired.
