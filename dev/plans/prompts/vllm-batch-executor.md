# Pack: vLLM batch via gateway-as-executor (`VLLMBackend`)

**Design:** `dev/notes/note-add-vllm-batch-backend.md` (rationale) — corrected by
the 2026-06-15 design review (verdict SOUND-WITH-CHANGES). This prompt is the
**build-accurate** contract; where it differs from the note, this wins.
**Depends on:** the merged batch gateway + content-scan (Item 2). Reuses the
provider-agnostic core unchanged.
**Goal:** local vLLM batch flows through the same guardrailed gateway as
aistudio/mistral, so the Fathom extraction harness gets keyword/PII scanning +
OpenAI-batch UX against local `qwen3.6-27b`. vLLM has **no** async batch server
API, so Airlock is the **executor**: it streams the scanned rows at vLLM's live
`/v1/chat/completions` and owns the whole lifecycle/status.

## Hard design decisions (from the review — do not relitigate)

1. **`upload` must persist the translated input.** The core writes a temp
   translated file, calls `upload(src, idem)`, then **`_safe_unlink`s it before
   `create` runs** (`gateway.py:219-227`). So `upload` copies `src` →
   `{work_dir}/{idem}.provider.jsonl` (streamed) and returns that durable path as
   `file_ref`. `create` reads `file_ref` to drive execution.
2. **Translation rewrites `body.model`.** Each line carries `body.model = <alias>`;
   vLLM only knows the served name. `to_provider_request` sets
   `body["model"] = provider_model`. This runs inside the core's
   `_translate_input_to_file`, so the durable file already has the right model.
3. **`list_jobs(idem)` returns `[]`.** There is no adoptable provider job.
   Idempotency rests entirely on `store.claim` (the live path never calls
   `list_jobs`) + the executor's durable per-row resume.
4. **Resume uses the executor's own results-file diff, NOT `staged_keys`.**
   `batch_rows`/`staged_keys` only populate at `stage_results` (after the whole
   batch completes), so they give zero mid-flight resume signal. The executor
   maintains `{idem}.results.jsonl` (one native result line per `custom_id`) and,
   on (re)start, skips `custom_id`s already present. A `{idem}.results.done`
   marker signals full completion. These are TWO distinct diffs: the executor's
   results-file diff (execution resume) vs. the core's `staged_keys` (fetch→stage
   idempotency). Keep them separate.
5. **`create` is fire-and-forget + strong-ref'd.** It spawns the executor task
   (`asyncio.create_task`), registers it in an `idem → {task, cancel_event}`
   module registry (so the loop doesn't GC it and `cancel` can signal it), and
   returns `job_id = idem` immediately — never awaits execution.
6. **Concurrency is process-global.** A module-level `asyncio.Semaphore`
   (`AIRLOCK_VLLM_BATCH_CONCURRENCY`, default 8) bounds total in-flight vLLM
   requests across all batches (one GPU). Per-row `httpx` timeout + bounded
   retry; a row that exhausts retries becomes an **error result line** (reuse the
   per-line error shape), never fails the whole batch.
7. **Status is Airlock's.** `poll(idem)`: `completed` iff the `.done` marker
   exists, else `in_progress`. `fetch(idem)` reads `{idem}.results.jsonl` (raise
   `ResultUnavailableError` if absent). No live `request_counts.completed` (the
   shaper hard-codes it); parity with the other adapters is fine.
8. **Crash-resume reconciler.** An in-proxy task dies on restart and the core's
   reconcile branch only *adopts* provider jobs (useless here). Ship
   `reconcile_vllm_batches(store)` that re-spawns the executor for every
   non-terminal (`CREATED`/`RETRIEVING`) vLLM batch (idempotent via the
   results-file diff), invoked best-effort at gateway install. Document the
   liveness contract.
9. **HTTP is seamed.** `VLLMBackend(..., send_chat=...)` injects an async
   `send_chat(body) -> chat.completion`; default builds an `httpx.AsyncClient`
   POSTing `{api_base}/chat/completions` (api_base already ends in `/v1` — do not
   double-prefix). Tests inject a fake; no network in unit/integration tests.
10. **Wiring resolves per-alias `api_base`/`api_key`.** `load_batch_aliases` drops
    `litellm_params`, so `backend_for_alias` must re-read the `model_list` entry
    for the `vllm` branch to get `api_base` + resolve `api_key`
    (`os.environ/VLLM_API_KEY`). Generalize the create-error string (currently
    hard-codes "aistudio"). Add `"vllm"` to `_GATEWAY_PROVIDERS`.

## Native result-line shape (executor writes; `from_provider_result` reads)

Mirror Mistral's fetched shape so `vllm_result_to_openai` mirrors
`mistral_result_to_openai`:
```json
{"custom_id": "...", "response": {"status_code": 200, "body": <chat.completion>}}
{"custom_id": "...", "error": {"code": "...", "message": "..."}}
```

## Slice breakdown (RED test first per slice)

- **Slice 0 — pure translation.** `openai_line_to_vllm(line, provider_model)`
  (rewrites `body.model`, drops `method`/`url`) + `vllm_result_to_openai`
  (verbatim body in `response.body`, choices projection, per-row error shape).
  Pure, no network/SDK.
- **Slice 1 — executor core (highest risk).** `_execute_batch(idem, input_path,
  results_path, done_path, send_chat, semaphore, cancel_event)`: resume diff over
  `results_path`, bounded-concurrency fan-out, per-row timeout/retry→error line,
  atomic appended results (asyncio.Lock), `.done` on full completion, honors
  `cancel_event` between rows. Inject `send_chat`. Test: happy path, partial
  failure→error line, resume (pre-seed half a results file → only missing rows
  fire), cancel.
- **Slice 2 — `VLLMBackend`** satisfying the Protocol (upload-persists, create
  fire-and-forget+registry, poll via done-marker, fetch via results file,
  cancel via registry, list_jobs `[]`). Integration test through
  `dispatch_batch_gateway` with injected transport.
- **Slice 3 — wiring.** `_GATEWAY_PROVIDERS += "vllm"`; `backend_for_alias` vllm
  branch (resolve api_base/api_key); generalized error string; config alias.
- **Slice 4 — reconciler.** `reconcile_vllm_batches`; unit-test resume re-spawn;
  best-effort install hook.

## Tests plan (no-network; mirror existing batch tests)

- `tests/test_batch_vllm_translation.py` — Slice 0 pure functions.
- `tests/test_batch_vllm_executor.py` — Slice 1: happy/partial/resume/cancel with
  a fake `send_chat`; assert only-missing-rows fire on resume; assert global
  semaphore bounds concurrency (count concurrent calls); assert `.done` written.
- `tests/test_batch_vllm_backend.py` — Slice 2/4: `upload` persists + returns a
  durable path the core's unlink can't destroy; `create` returns immediately and
  registers; `poll` flips on the done marker; `fetch` raises
  `ResultUnavailableError` when absent; `list_jobs == []`; `cancel` signals;
  `reconcile_vllm_batches` re-spawns a `CREATED` row and completes it.
- Extend `tests/test_batch_gateway_integration.py` — add a `vllm` case driving
  the full ASGI lifecycle (upload→create→poll→content) with an injected
  send_chat, asserting the scan still runs (scrubbed input is what gets executed)
  and `request_counts.total` is correct after stage.
- `tests/test_batch_runtime.py` (or extend) — `backend_for_alias` resolves
  `api_base`/`api_key` from `litellm_params` for a `vllm` marker.

## Acceptance

- Full suite green; new tests cover every Slice-1/2/4 branch.
- ruff check + format clean.
- A `vllm`-marked alias round-trips through the real ASGI middleware with the
  content scan applied and no network.
- Live e2e against the real host (`192.168.1.45:8000`) is a **separate HITL gate**
  (mirror `dev/aistudio-batch-e2e-test-plan.md`), not in this pack.

## Out of scope (follow-ups)

- Generic "local executor" reuse for a future `LiteLLMNativeBackend` (Phase 5).
- Live `request_counts.completed` progress (needs a shaper change).
- `run_batch` CLI path (offline; GPU contention) — rejected, see the note.
