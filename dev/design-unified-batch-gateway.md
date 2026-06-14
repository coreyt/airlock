# Design: Unified Airlock Batch Gateway (AI Studio Gemini + Mistral, one path)

**Status: PROPOSED — design only. Implementation NOT started.**
**Date:** 2026-06-14
**Supersedes (as the implementation spec):** `dev/design-aistudio-gemini-batch.md`
(Option A) and `dev/mistral-batch-findings.md` (§4). Those remain valid as the
per-provider investigations; this doc is the adversarially-reviewed, two-provider
design they both point to.

---

## 0. TL;DR

- **Adversarial review** of the two source docs found them directionally correct
  but with **six material holes** (Presidio-at-scale, PII-map-as-PII-at-rest,
  non-idempotent create race, lossy Gemini response mapping, route-shadowing vs
  SDK drop-in, logger entrypoint). One (logger) is already solved by existing
  code; the rest must be designed in.
- **Common vs separate: COMMON.** ~80% of the work is provider-agnostic
  (HTTP surface, OpenAI batch object, auth, JSONL parse, guard scan, PII
  lifecycle, status normalization, persistence, idempotency, caps, observability).
  Only a thin **`BatchBackend` adapter** differs per provider. Two separate
  wirings would duplicate the risky 80% and drift.
- **Design = an "Airlock Batch Gateway":** one injected route + one orchestration
  core + pluggable `BatchBackend` adapters (`MistralBackend`, `AIStudioBackend`,
  and later a `LiteLLMNativeBackend` so the *wired* providers also get uniform
  guarding). Build Mistral first (OpenAI-shaped, minimal translation), then
  AI Studio (adds the Gemini translation adapter).

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

### 🟠 A3 — AI Studio batch create is non-idempotent; the mitigation has a race
`design-aistudio-gemini-batch.md` §6 leans on Airlock-persisted id-mappings +
`display_name` to avoid double-submit, but a crash *between* `client.batches.create`
returning and Airlock persisting the mapping orphans a paid job and a retry
double-submits. AI Studio explicitly says create "is not idempotent."
**Resolution:** require/accept a client **idempotency key** (or content hash of
the input file); persist a `creating` intent record **before** calling the
provider; on retry, match the key and reconcile via `batches.list()` /
`batch.jobs.list()` by `display_name` before creating. Residual race is
documented, not hidden.

### 🟠 A4 — Gemini response translation is lossy
`design-aistudio-gemini-batch.md` §3.6 maps Gemini `candidates[].content.parts[]
.text` → `choices[].message.content`. That silently drops tool calls, thinking
blocks, finish/safety reasons, and multi-candidate output. Mistral is unaffected
(its batch results are already OpenAI-shaped).
**Resolution:** the OpenAI output line carries the **provider-native response
verbatim** in `response.body`, plus a best-effort `choices` projection. Never
lose the native payload. The translation lives entirely in the per-provider
adapter (see §3), so Mistral's adapter is near-identity and only Gemini's does work.

### 🟠 A5 — Namespaced route vs OpenAI-SDK drop-in is an unresolved either/or
`design-aistudio-gemini-batch.md` §3.2 recommends `/airlock/aistudio/batches` for
MVP but admits it breaks OpenAI/LiteLLM SDK clients (which call `/v1/batches`),
and defers the model-aliased `/v1/batches` dispatch that would be a true drop-in.
**Resolution (this design):** the gateway **owns `/v1/batches` + `/v1/files`** and
dispatches by resolved model alias: gateway-backed aliases (`*-aistudio`,
`*-mistral-batch`) are handled in-process; everything else is **delegated to
LiteLLM's native handler**. Route-ordering risk against LiteLLM's own
registration is the real cost — mitigated by registering the Airlock handler as a
thin front controller that calls the LiteLLM handler for non-gateway models.
Namespaced routes remain the **Phase-1 fallback** if front-controlling proves
fragile.

### 🟢 A6 — "Call the enterprise logger directly" — already solved
`design-aistudio-gemini-batch.md` §6 worries the logger is a completion callback.
Verified: `airlock/callbacks/enterprise_logger.py` exposes `_write_log(record)`
and the `write_precall_block_record(...)` pattern — a direct, callback-independent
record-write path. The gateway uses `_write_log` for submit/status/complete
events. No new logging infra needed. (Downgrade this from "risk" to "use the
existing function.")

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
create job → poll → download), 50% discount, **not wired in LiteLLM**, no
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
- **Phase 1 — gateway core + MistralBackend** (easiest, near-identity adapter):
  front controller on `/v1/batches`+`/v1/files`, state store, status
  normalization, OpenAI object shaping, `_write_log`. No guards yet. Proves the
  whole gateway with the cheapest translation.
- **Phase 2 — AIStudioBackend:** add the OpenAI↔Gemini translation (A4, native
  kept in `.body`). Same core, second adapter — validates the abstraction.
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

## 7. Open challenges (the independent red-team pass is verifying these)
1. Front-controlling `/v1/batches` vs LiteLLM's own handler registration order
   (A5) — confirm a clean delegate path exists, else fall back to namespaced
   routes for Phase 1.
2. Async scan back-pressure: how `validating` is surfaced while a 1M-row scan
   runs, and the failure UX when scan rejects after upload "succeeded".
3. Whether opt-in hydration is ever worth the encrypted-PII-store cost for batch,
   or whether terminal redaction should be the *only* supported mode.
4. `LiteLLMNativeBackend` double-handling risk (gateway scans, then litellm
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
