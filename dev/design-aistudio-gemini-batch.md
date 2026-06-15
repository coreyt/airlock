# Design: Google AI Studio (Gemini Developer API) Batch Mode through Airlock

**Status: PROPOSED — design only. Implementation NOT started.**
**Date:** 2026-06-14
**Author:** design proposal (grounded in the files cited inline)
**Scope:** No behavior code is changed by this document. It specifies an
architecture for a future implementation.

---

## 1. Problem statement & constraints

We need a way to run **Gemini 3.x batch jobs** (`gemini-3.5-flash`,
`gemini-3.1-pro-preview`) through the Airlock proxy, integrated with the existing
LiteLLM + guardrails architecture. The established constraints (not re-litigated
here):

1. **LiteLLM does not wire the Batch API for the `gemini` (AI Studio) provider.**
   `litellm/batches/main.py` wires `/v1/files` + `/v1/batches` only for
   `openai`, `azure`, `vertex_ai`, `anthropic`, `bedrock`. A `create_batch` on a
   `gemini/…` model raises *"LiteLLM doesn't support
   custom_llm_provider=gemini for 'create_batch'"* (documented in
   `dev/vertex-gemini-batch-setup.md`).
2. **Vertex batch is regional; Gemini 3.x is global-only in this project.** The
   Vertex `global` endpoint does not support `BatchPredictionJob`, and the 3.x
   ids 404 in every region we probed (`config.yaml` lines 116–141 record the
   probe results: 3.x resolves only on `global`). So `vertex_ai` — the one
   LiteLLM-wired path that serves Gemini — cannot batch 3.x here.
3. **Google AI Studio has native Batch Mode and serves Gemini 3.x**, keyed by
   `GOOGLE_AISTUDIO_API_KEY` (already in `.env`, already used by every `gemini/`
   deployment in `config.yaml` lines 60–103). This is the **only viable path to
   Gemini 3.x batch.**

### AI Studio Batch API surface (verified against
https://ai.google.dev/gemini-api/docs/batch-api, `google-genai` SDK)

- **Create from file:** `client.files.upload(file=..., config=UploadFileConfig(
  mime_type='jsonl', display_name=...))` → `client.batches.create(
  model="gemini-3.5-flash", src=uploaded_file.name, config={'display_name': ...})`.
- **Create inline:** `client.batches.create(model=..., src=[{...}, ...])` for
  payloads under **20 MB**.
- **JSONL input line:** `{"key":"req-1","request":{"contents":[{"parts":[{"text":"…"}],
  "role":"user"}],"generation_config":{…},"system_instruction":…}}`.
- **Poll:** `client.batches.get(name=job_name)` → `batch_job.state.name`.
  States: `JOB_STATE_PENDING | RUNNING | SUCCEEDED | FAILED | CANCELLED |
  EXPIRED`. Jobs **expire after 48 h** pending/running.
- **Results:** file batches → `batch_job.dest.file_name`, then
  `client.files.download(file=...)`; inline → `batch_job.dest.inlined_responses`.
- **Other:** `client.batches.list()`, `.cancel(name=…)`, `.delete(name=…)`,
  webhooks via `client.webhooks.create(...)` with `["batch.succeeded",
  "batch.failed"]`. **50%** of interactive cost; **2 GB** input file limit;
  24 h turnaround target.

### The architectural friction

Airlock's content guardrails are built for the **chat** shape. Every
content-inspecting guard reads `data["messages"]` via
`extract_text(data, call_type)` (`airlock/guardrails/extract.py`). A batch
request carries **no top-level `model` and no `messages`** — the content lives
inside an uploaded JSONL file — so the chat-path guards **silently no-op** on
batch (`dev/batch-guardrail-toggles-considerations.md`, "load-bearing
consequence"). Any batch design must therefore bring its **own** content-scanning
seam; it cannot lean on the existing hook chain.

Additionally, Airlock's **custom providers** (`CustomLLM` subclasses:
`airlock/providers/tavily_provider.py`, `enhanced_passthrough.py`) only implement
`completion`/`acompletion`. They intercept **chat completions**, never the
`/v1/files` or `/v1/batches` routes. They cannot carry batch (see §2b).

---

## 2. Options analysis

### Option A — Airlock-owned FastAPI route injected on the proxy app *(RECOMMENDED)*

Add an Airlock module that injects batch endpoints directly onto the LiteLLM
FastAPI app, exactly as `airlock/health.py` (`install_circuit_health_on_proxy_app`)
and `airlock/docs.py` (`install_airlock_docs_on_proxy_app`) already do. Both
reach `sys.modules["litellm.proxy.proxy_server"].app`, verify it is a `FastAPI`,
and register routes; both are bootstrapped at import time from
`airlock/callbacks/model_override_headers.py` lines 57–58. A new
`install_aistudio_batch_on_proxy_app()` would follow the same precedent.

The route translates an OpenAI-style batch request into `client.batches.create`
on the `google-genai` SDK, runs Airlock's guardrails over the JSONL **before**
submission, and maps the AI Studio job back to an OpenAI-shaped batch object.

- **Pros:**
  - Reuses an **existing, proven Airlock pattern** (route injection on the proxy
    app) — no fork of LiteLLM, no upstream dependency.
  - Airlock **owns the content-scan seam**: it parses the JSONL itself, so the
    reusable helpers (`_scrub_text_with_mapping`, `_scrub_messages`,
    `keyword_guard._blocked_keywords`, `extract.extract_text`) run on real batch
    content — the only place that is possible (§4).
  - Server-side trust boundary: the toggle is operator/config-controlled, not
    caller-controlled (addresses the trust-boundary concern in
    `dev/batch-guardrail-toggles-considerations.md` §(e)4).
  - Decoupled from LiteLLM's batch provider matrix — immune to future LiteLLM
    refactors of `batches/main.py`.
- **Cons:**
  - Airlock now owns a stateful, async job lifecycle (job-id mapping, polling,
    result retrieval) that LiteLLM otherwise abstracts.
  - Endpoint-shape divergence risk from native LiteLLM `/v1/batches` (mitigated
    by mirroring the OpenAI batch object — §3).
  - Depends on the `google-genai` SDK (new optional extra — §5).
- **Effort:** Medium. One injector module + one batch service module + config +
  an `is_batch_call`-style seam. No core guardrail rewrites.
- **Fit with litellm+guards:** High. It is the literal generalization of the
  docs/health injectors and reuses the guardrail text helpers directly.

### Option B — A LiteLLM custom batch/files provider for `gemini`

Try to register a custom handler that LiteLLM dispatches for `create_batch` /
`create_file` on a `gemini/…` model.

- **Reality check:** LiteLLM's `custom_provider_map` (config.yaml lines 260–264)
  resolves to `CustomLLM` subclasses whose contract is `completion`/
  `acompletion` (see `tavily_provider.py`, `enhanced_passthrough.py`). There is
  **no `CustomLLM.create_batch` / `create_file` extension point**;
  `litellm/batches/main.py` dispatches via a hardcoded provider `if/elif` +
  `get_provider_batches_config`, not via the custom-provider registry. So a
  `gemini` batch provider is **not pluggable without upstream changes**.
- **Pros:** If upstreamed, batch would flow through native `/v1/batches` with
  zero Airlock-specific surface; cleanest long-term ergonomics.
- **Cons:** Requires a **LiteLLM fork or contribution** (a new
  `gemini` batches transformation + provider wiring). Slow, externally gated, and
  still would **not** solve the guardrail-coverage gap (LiteLLM batch is a
  passthrough; content still never hits the chat hooks).
- **Effort:** High (upstream PR + review cycle) and out of our control.
- **Fit:** Low in the near term; see §7 for when this becomes preferable.

### Option C — A separate Airlock CLI / sidecar service

A standalone `airlock batch` CLI (or a small service) that talks to AI Studio
directly, outside the proxy HTTP surface.

- **Pros:** Simplest to build; no proxy-app coupling; easy to iterate.
- **Cons:** Off the proxy's **auth, attribution, logging, and guardrail**
  machinery — bypasses `AIRLOCK_MASTER_KEY` auth, `X-Airlock-Client`
  attribution, and the enterprise logger. Clients would need a second integration
  path. Re-implements scanning the considerations doc says belongs at the proxy.
- **Effort:** Low–Medium, but creates a parallel, ungoverned data path —
  precisely the "false sense of security" failure mode warned about in
  `dev/batch-guardrail-toggles-considerations.md` §(e).
- **Fit:** Low. Acceptable only as a throwaway spike, not the product path.

### Recommendation: **Option A.**

It is the only option that (1) reuses an existing Airlock precedent, (2) keeps
batch inside the proxy's auth/logging/guardrail governance, and (3) actually lets
guardrails see batch content. Option C remains a useful **MVP spike vehicle**;
Option B is the **long-term upstream play** (§7) but cannot be the near-term
answer.

---

## 3. Recommended architecture

### 3.1 Request flow (client → Airlock → AI Studio → results)

```
client                  Airlock (on LiteLLM FastAPI app)            Google AI Studio
  |                                |                                       |
  | POST /v1/files (JSONL)         |                                       |
  |------------------------------->| auth (master key) + attribution       |
  |                                | parse JSONL rows                       |
  |                                | [guards] scan/redact each row (§4)     |
  |                                | client.files.upload(mime_type='jsonl')-+------>
  |<-- {id:"file-…", object:"file"}| store {file-id -> uploaded.name, pii map}      |
  |                                |                                       |
  | POST /v1/batches {input_file_id| resolve alias -> aistudio model        |
  |   , model:"gemini-3.5-flash-   | client.batches.create(model, src)------+------>
  |   aistudio"}                   | store {batch-id -> job.name}                   |
  |<-- {id:"batch-…",status:        |                                       |
  |     "validating"}              |                                       |
  |                                |                                       |
  | GET /v1/batches/{id}           | client.batches.get(name) -> state -----+------>
  |<-- {status:"in_progress"|...}  | map JOB_STATE_* -> OpenAI status                |
  |                                |                                       |
  | GET /v1/batches/{id} (done)    | dest.file_name -> client.files.download +------>
  |                                | [guards] scan/hydrate output (§4)      |
  |<-- {status:"completed",         | stage result, expose output_file_id            |
  |     output_file_id:"file-…"}   |                                       |
  | GET /v1/files/{output}/content | return staged result JSONL                     |
  |<-- result JSONL                |                                       |
```

### 3.2 Endpoint shape — reuse OpenAI `/v1/batches` semantics

**Decision: present the OpenAI batch surface, served by Airlock-injected routes.**
Mirror the OpenAI Batch object so existing OpenAI/LiteLLM SDK clients work
unchanged:

- `POST /v1/files` (purpose=`batch`) — accept multipart JSONL upload.
- `POST /v1/batches` — `{input_file_id, endpoint:"/v1/chat/completions",
  completion_window:"24h", model:"gemini-3.5-flash-aistudio", metadata:{…}}`.
- `GET /v1/batches/{id}`, `GET /v1/batches` (list), `POST /v1/batches/{id}/cancel`.
- `GET /v1/files/{id}/content` — download staged result.

Because LiteLLM **already owns** `/v1/files` and `/v1/batches` for the wired
providers, Airlock must **not** blindly shadow those paths. Two safe options,
in order of preference:

1. **Dedicated namespaced routes** `/airlock/aistudio/batches` and
   `/airlock/aistudio/files` (clean separation, zero collision risk, consistent
   with `/airlock/docs` and `/health/circuits`). The OpenAI **object shape** is
   still reused in the response bodies. *Recommended for MVP* — unambiguous and
   collision-free.
2. **Model-aliased dispatch on the standard paths**: register Airlock handlers
   that inspect the request and only intercept when the model resolves to an
   AI-Studio-batch alias, delegating everything else to LiteLLM. Higher fidelity
   to the OpenAI surface but riskier (route-ordering and double-registration
   against LiteLLM's own handlers). *Defer to a later phase.*

### 3.3 Model alias selection

Add AI-Studio-batch aliases to `config.yaml` `model_list`, consistent with the
existing `gemini/` and `gemini-*-vertex` naming:

```yaml
  # --- Google AI Studio (Gemini 3.x, BATCH via Airlock route) ---
  - model_name: gemini-3.5-flash-aistudio
    litellm_params:
      model: gemini/gemini-3.5-flash          # AI Studio provider id
      api_key: os.environ/GOOGLE_AISTUDIO_API_KEY
      airlock_batch:                           # NEW marker block (Airlock-only)
        provider: aistudio
        aistudio_model: gemini-3.5-flash
  - model_name: gemini-3.1-pro-aistudio
    litellm_params:
      model: gemini/gemini-3.1-pro-preview
      api_key: os.environ/GOOGLE_AISTUDIO_API_KEY
      airlock_batch:
        provider: aistudio
        aistudio_model: gemini-3.1-pro-preview
```

The `-aistudio` suffix mirrors the `-vertex` suffix already used to distinguish
batch-capable Gemini deployments (config.yaml lines 129–141). The batch route
reads the `airlock_batch` marker (loaded from `config.yaml` the same way
`enhanced_passthrough._load_profile_cache()` reads `enhanced_profile`) to decide
which physical AI Studio model to pass to `client.batches.create`. Sync chat on
these aliases continues to flow through LiteLLM's native `gemini/` provider
untouched — only the batch route is Airlock-owned.

### 3.4 File staging — AI Studio Files API (NOT GCS)

Unlike the Vertex path (which stages through `GCS_BUCKET_NAME`), AI Studio batch
stages through the **AI Studio Files API**. On `POST /v1/files`, Airlock:

1. Buffers the uploaded JSONL (enforce row/byte caps — §6).
2. Runs guardrails over each row (§4).
3. Calls `client.files.upload(file=<scrubbed JSONL>, config=UploadFileConfig(
   mime_type='jsonl', display_name=<airlock-file-id>))`.
4. Returns an OpenAI-shaped `{id:"file-<uuid>", object:"file", purpose:"batch"}`
   and persists the mapping `airlock_file_id -> uploaded_file.name` plus the
   per-request PII reverse-mapping (§4) in a small state store.

Inline `src=[…]` is an optimization for payloads under 20 MB; MVP uses the file
path uniformly and may add inline later.

### 3.5 Job-id / status mapping to the OpenAI batch object

Airlock generates an OpenAI-style `batch-<uuid>` id and maps it to the AI Studio
`job.name`. State mapping:

| AI Studio `state.name` | OpenAI batch `status` |
|---|---|
| `JOB_STATE_PENDING` | `validating` then `in_progress` |
| `JOB_STATE_RUNNING` | `in_progress` |
| `JOB_STATE_SUCCEEDED` | `completed` |
| `JOB_STATE_FAILED` | `failed` |
| `JOB_STATE_CANCELLED` | `cancelled` |
| `JOB_STATE_EXPIRED` | `expired` |

The batch object also carries `input_file_id`, `output_file_id` (populated on
success), `request_counts`, `created_at`, `expires_at` (created_at + 48 h), and
`metadata` (echoed, including `X-Airlock-Client` attribution).

### 3.6 Result retrieval & format mapping

On `GET /v1/batches/{id}` once `SUCCEEDED`:

1. `batch_job = client.batches.get(name=job.name)`.
2. File path: `client.files.download(file=batch_job.dest.file_name)`; inline
   path: read `batch_job.dest.inlined_responses`.
3. Each AI Studio result line keys back to the input by `key` (`req-1`). Airlock
   maps each line into an **OpenAI batch output line**:
   `{"id":"…","custom_id":<key>,"response":{"status_code":200,"body":<chat-
   completion-shaped>},"error":null}`, translating Gemini `candidates[].content.
   parts[].text` into `choices[].message.content`.
4. Run **output-side guardrails** (PII hydration keyed by `key`, optional
   response scan — §4) before staging.
5. Stage the rewritten JSONL as a new file id; expose it as `output_file_id`
   and serve it from `GET /v1/files/{id}/content`.

---

## 4. Guardrail integration ("when enabled")

This is the crux. Chat-path hooks cannot see batch content; Airlock's batch route
must scan it explicitly, reusing the existing **text-level helpers** rather than
the hook objects.

### 4.1 Where guards run

**At `/v1/files` upload time (pre-submission), per JSONL row:**

1. Parse the row → `request.contents[].parts[].text` (and `system_instruction`).
   A small adapter normalizes the Gemini `contents` shape into the
   `messages`-like list that `extract.extract_text_from_messages` /
   `pii_guard._scrub_messages` already understand.
2. **Keyword block** — reuse `keyword_guard._blocked_keywords()` +
   `keyword_guard._normalize_text()` over each row's text; on a hit, **reject the
   whole upload** (bulk = bulk blast radius). Honors `AIRLOCK_KW_ENABLED` /
   `AIRLOCK_BLOCKED_KEYWORDS`.
3. **PII redaction with mapping** — reuse
   `pii_guard._scrub_text_with_mapping(text, mapping, counters)` /
   `_scrub_messages(...)`. Crucially, keep a **per-row mapping keyed by the
   request `key`**: `pii_maps[key] = mapping`. Honors `AIRLOCK_PII_ENABLED` /
   `AIRLOCK_PII_ENTITIES`. (`_get_presidio()` lazy-loads Presidio exactly as on
   the chat path.)
4. Rewrite the row's text with placeholders, re-serialize, and upload the
   **scrubbed** JSONL. Persist `pii_maps` alongside the file-id mapping.

**At result-retrieval time (post-submission):**

5. **PII hydration** — for each output line, look up `pii_maps[custom_id]` and
   run the existing `pii_guard._hydrate_value_recursive(...)` /
   `_hydrate_tool_calls(...)` logic over the response text and any tool-call
   arguments, restoring originals. Honors `AIRLOCK_PII_HYDRATION`.
6. **Output scan (optional)** — run a response-scanner-equivalent over each
   output row; mode `observe` initially, `enforce` later
   (`dev/batch-guardrail-toggles-considerations.md` §(a) response_scanner,
   §(d) output-bucket scanning).

This reuses, verbatim, the helpers the considerations doc names as reusable:
`_scrub_text_with_mapping`, `_scrub_text`, `_scrub_messages`, `_get_presidio`
(`pii_guard.py`); `_blocked_keywords`/`_normalize_text` (`keyword_guard.py`);
`extract_text`/`extract_text_from_messages` (`extract.py`).

### 4.2 The enable/disable switch — the "batch profile"

Adopt the **batch profile** from `dev/batch-guardrail-toggles-considerations.md`
§(e)2: a single named config block declaring the batch posture in one place,
env-overridable like `cost_tiers`:

```yaml
batch_profile:
  aistudio:
    scan_at_upload: true          # master switch for §4.1 steps 1–4
    pii_redact: true              # ties to AIRLOCK_PII_ENABLED
    keyword_block: true           # ties to AIRLOCK_KW_ENABLED
    pii_hydrate_output: true
    output_scan_mode: observe     # observe | enforce | off
    max_rows: 50000
    max_bytes: 2147483648         # 2 GB AI Studio cap
    max_concurrent_jobs: 5
```

A small `is_batch_call(...)`-style helper (the new seam proposed in the
considerations doc §(e)1, mirroring `extract.is_mcp_call`) tags traffic on the
batch route so logging/metrics can branch on it.

### 4.3 Guard-bypass risk if off & the trust boundary

- If `scan_at_upload: false`, **bulk un-redacted/un-scanned content flows to a
  third party.** This is the dominant failure mode the considerations doc calls
  out — a "false sense of security" where chat shows guards on but batch silently
  ships PII. Default **on**, and log loudly when disabled.
- **Trust boundary (mandatory):** the switch is **operator/config-controlled
  only** (`batch_profile` / env), never caller-controlled. LiteLLM's per-request
  `guardrails` field lets the *caller* disable guards; for batch the caller is the
  batch submitter — the wrong party. The batch route MUST **ignore
  caller-supplied `guardrails`/`metadata` disables** on the batch path
  (considerations doc §(e)4).

---

## 5. Auth & config

- **Auth to AI Studio:** `GOOGLE_AISTUDIO_API_KEY` (already in `.env`, already
  used by the `gemini/` deployments). The `google-genai` client is constructed
  with `genai.Client(api_key=os.environ["GOOGLE_AISTUDIO_API_KEY"])`.
- **Proxy auth/attribution:** the injected routes sit on the same FastAPI app and
  reuse the proxy's `AIRLOCK_MASTER_KEY` dependency and `X-Airlock-Client`
  attribution (same as `/health/circuits` and `/airlock/docs`).
- **New dependency — `google-genai` SDK as an optional extra**, mirroring the
  `vertex`/`search` pattern in `pyproject.toml` (lines 52–60):

  ```toml
  [project.optional-dependencies]
  aistudio = ["google-genai>=1.0.0"]   # AI Studio batch via Airlock route
  ```

  The batch module imports `google.genai` **lazily inside the handler** (as
  `pii_guard._get_presidio` and `tavily_provider._do_search` do), so the proxy
  still boots without the extra; the batch route returns a clear "install the
  `aistudio` extra" error if unavailable.
- **New config knobs:** the `airlock_batch` marker per alias (§3.3) and the
  `batch_profile` block (§4.2), both readable from `config.yaml` and overridable
  by `AIRLOCK_BATCH_*` env vars (consistent with `AIRLOCK_COST_TIERS`).

---

## 6. Failure modes, limits, idempotency, polling/webhooks, observability

**Failure modes & limits:**
- Missing `GOOGLE_AISTUDIO_API_KEY` or `google-genai` not installed → reject
  batch submission loudly (do not silently passthrough).
- File > 2 GB or rows > `max_rows` → reject at upload (caps from §4.2).
- AI Studio `JOB_STATE_FAILED` / `EXPIRED` (48 h) → surface as OpenAI `failed` /
  `expired` with the provider error attached.
- Malformed JSONL row → reject the upload with the offending `key`/line number.
- Keyword hit (§4.1.2) → reject whole upload; PII over-redaction is non-fatal.

**Idempotency:**
- Use the AI Studio `display_name` (set to the Airlock file-/batch-id) and the
  persisted id mappings so a retried `POST /v1/batches` for the same
  `input_file_id` does not double-submit; `client.batches.list()` can reconcile
  orphaned jobs after a proxy restart.

**Polling vs webhooks:**
- MVP: **client-driven polling** via `GET /v1/batches/{id}` → `client.batches.get`
  (no server-side loop, no extra state machine).
- Later: optional `client.webhooks.create(events=["batch.succeeded",
  "batch.failed"])` to a new Airlock route, which triggers the output-scan/
  hydrate step proactively. Keep polling as the fallback.

**Observability:**
- Log every batch lifecycle event through the **enterprise logger**
  (`airlock/callbacks/enterprise_logger.py`, `proxy_logger`) — but note that
  logger is a LiteLLM success/failure **callback** keyed off completions; the
  batch route is HTTP-level, so the batch service should call the logger's
  record-writing path directly (or emit an equivalent structured JSON record) for
  submit / status / complete events. Per the considerations doc §(c), batch
  **must always be logged** (audit/compliance) with: `airlock_file_id`,
  batch id, AI Studio `job.name`, model, row count, state, output id, and
  client attribution. Tag records with a batch call-type so the TUI
  (`tui/screens/logs.py`) and `fast/monitor.py` can show batch distinctly and
  not pollute interactive latency/health stats.

---

## 7. Alternatives & upstream

- **Contribute a `gemini` batches provider to LiteLLM (Option B).** This is the
  right move **if/when** Airlock wants batch to flow through native
  `/v1/batches` with no Airlock-specific surface, *and* the team is willing to
  own a transformation + provider wiring PR against `litellm/batches/main.py` +
  `get_provider_batches_config`. Prefer it once (a) the Airlock route has proven
  the request/result mapping in production, and (b) maintaining a parallel
  batch surface becomes a burden. Even then, upstream LiteLLM batch is a
  **passthrough** and would **not** scan content — so the §4 upload-scan seam
  remains an Airlock responsibility regardless.
- **Vertex (revisit later).** If Google later supports `BatchPredictionJob` for
  3.x on a region (or on `global`), the existing `gemini-*-vertex` deployments
  (config.yaml 129–141) + LiteLLM's wired `vertex_ai` batch become viable and
  this AI Studio route could be retired. Track via the open question in
  `dev/vertex-gemini-batch-setup.md`.

---

## 8. Phased implementation plan & test strategy

**Phase 0 — Spike (Option C throwaway).** A standalone script using
`google-genai` to upload a tiny JSONL, create a batch, poll, and download
results against the real `GOOGLE_AISTUDIO_API_KEY`. Validates the SDK
contract and result shape end-to-end. *Test:* `live`-marked manual run.

**Phase 1 — MVP route (no guards).** Add the `aistudio` extra; add the batch
service module; add `install_aistudio_batch_on_proxy_app()` and bootstrap it from
`model_override_headers.py` (next to the existing two installers). Implement
namespaced routes (§3.2 option 1), alias resolution (§3.3), file staging (§3.4),
job-id/status mapping (§3.5), and result mapping (§3.6). *Tests:* unit tests for
state mapping and OpenAI-object shaping (no network), mirroring the existing
guardrail/test style; one `live` end-to-end.

**Phase 2 — Guards.** Add the JSONL row adapter; wire upload-time keyword + PII
scan reusing the named helpers (§4.1), the `pii_maps`-by-key store, and
output-time hydration; add the `batch_profile` block and the
`is_batch_call`-style seam. Enforce the trust boundary (ignore caller disables).
*Tests:* a JSONL with seeded PII + a blocked keyword asserts redaction/rejection;
round-trip test asserts output hydration restores originals by `key`.

**Phase 3 — Polish.** Caps/idempotency/reconciliation; enterprise-logger batch
records + TUI/monitor batch tagging; optional webhooks; optional output scan
`enforce`; docs (`/airlock/docs` + a `docs/guide/aistudio-batch.md`). *Tests:*
cap-rejection, restart-reconciliation, and logging-presence tests.

---

## Appendix — Files this design is grounded in

- `litellm/batches/main.py` — provider matrix excluding `gemini` (via
  `dev/vertex-gemini-batch-setup.md`).
- `config.yaml` — custom_provider_map (260–264), guardrails block (363–435),
  `gemini/` deployments (60–103), `gemini-*-vertex` deployments + probe notes
  (116–141), `cost_tiers` (337–358).
- `airlock/health.py` (`install_circuit_health_on_proxy_app`) &
  `airlock/docs.py` (`install_airlock_docs_on_proxy_app`) — route-injection
  precedent; bootstrapped from `airlock/callbacks/model_override_headers.py`
  (57–58).
- `airlock/providers/tavily_provider.py`, `enhanced_passthrough.py` — `CustomLLM`
  contract is completion-only (cannot carry batch); `_load_profile_cache` config
  read pattern.
- `airlock/guardrails/extract.py` — `extract_text`, `extract_text_from_messages`,
  `is_mcp_call` (route-scoping precedent).
- `airlock/guardrails/pii_guard.py` — `_scrub_text_with_mapping`, `_scrub_text`,
  `_scrub_messages`, `_get_presidio`, `_hydrate_value_recursive`,
  `metadata["airlock_pii_map"]`.
- `airlock/guardrails/keyword_guard.py` — `_blocked_keywords`, `_normalize_text`.
- `airlock/callbacks/enterprise_logger.py` — `proxy_logger`, call_type tagging.
- `pyproject.toml` — optional-extras pattern (`vertex`, `search`).
- `dev/batch-guardrail-toggles-considerations.md` — batch profile, `is_batch_call`
  seam, trust boundary, upload-scan capability.
- `dev/vertex-gemini-batch-setup.md` — why Vertex was chosen before, dependency
  gap, "batch on global" open question.
