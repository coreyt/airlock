# Note: vLLM batch parity via a **gateway-as-executor** backend

**Date:** 2026-06-15
**Status:** Revised design (probe- + research-confirmed). Supersedes the earlier
"delegating adapter" sketch, which is **not buildable** — see Findings.
**Scope:** `airlock/batch/` + `config.yaml`

> **Build spec:** `dev/plans/prompts/vllm-batch-executor.md` is the authoritative,
> design-reviewed implementation contract (it corrects several specifics this
> note got wrong — `upload` must *persist* the translated input because the core
> unlinks it before `create`; `list_jobs` returns `[]`; resume uses the
> executor's own results-file diff, not `staged_keys`; translation rewrites
> `body.model`; concurrency is process-global; a startup reconciler handles
> crash-resume). This note remains the rationale/background.

## Summary

Give local-vLLM batch the **same controls and OpenAI-batch UX** as the
`aistudio`/`mistral` gateway providers — keyword/PII scanning, idempotency,
durable/resumable job state, `GET /v1/batches/{id}` status — so the **Fathom
tester** (our LLM-driven synthesized-data / extraction harness) can submit
extraction jobs through Airlock to the local model and get guardrailed,
trackable batches.

The earlier version of this note assumed vLLM exposes the OpenAI async Batch
**server** API (`/v1/files` + `/v1/batches`) and that a thin adapter could
*delegate* to it like Mistral does. **It does not.** This revision changes the
shape: Airlock does not delegate to a vLLM job queue (there is none) — it
**becomes the executor**, running the scanned batch against vLLM's live sync
endpoint and owning the entire job lifecycle itself.

## Findings (probe 2026-06-15 + web research)

Probed the live host `http://192.168.1.45:8000` (vLLM **0.21.0**, the latest
release — May 15 2026) and cross-checked the docs:

- **No async Batch server API.** `POST /v1/files` → **404**, `POST /v1/batches`
  → **404**. Confirmed against the full OpenAPI route list and the latest
  online-serving docs. This is not a version-lag issue — vLLM has never shipped
  these as *server* routes, and 0.21.0 is current.
- **`/v1/chat/completions/batch` is synchronous**, not the OpenAI job protocol:
  it takes `messages` as a *list of conversations* and returns one
  `chat.completion` with N `choices` immediately. No file upload, no job id, no
  polling. (Also: it is a vLLM-native route Airlock's chat-path guards do **not**
  intercept, so calling it directly would *bypass* guardrails.)
- **vLLM's OpenAI-Batch-file support is an offline CLI**, not a server feature:
  `vllm run-batch` / `python -m vllm.entrypoints.openai.run_batch -i in.jsonl -o
  out.jsonl --model …`. It loads its **own** engine (offline inference, needs the
  GPU) and has **no status/polling** — a one-shot process that writes an output
  file when done.

**Conclusion:** there is nothing on the vLLM side to delegate to and nothing on
the vLLM side that reports batch status. Parity must be produced by Airlock.

## Why "drop a file and poke `run_batch`" is the wrong tool

It is technically possible (`run_batch -i` accepts remote URLs, and Airlock
already serves the scrubbed file at `GET /v1/files/{id}/content`), but it is a
poor fit here:

1. **GPU contention / model eviction.** `run_batch` is *offline* — it loads its
   own engine into the GPU. The serving container already owns that GPU with
   `qwen3.6-27b` loaded; on one GPU you'd have to stop the server → run the batch
   → restart, killing sync traffic for the batch's duration.
2. **No status.** One-shot CLI; "status" is only "is the process alive / did the
   output file appear." Airlock would have to babysit a subprocess.
3. **Cross-boundary triggering.** Airlock runs as a **systemd** service (native
   process); vLLM is a **separate Docker** (possibly another host). Invoking the
   CLI means `docker exec`/SSH into the vLLM host or a sidecar agent there — real
   new ops surface.
4. **Hand-off.** Needs a shared volume (hard across hosts) or remote URLs *plus*
   an agent on the vLLM side to launch the CLI.

## The design: Airlock as executor

The batch gateway already owns **everything except a vLLM backend**: the OpenAI
batch HTTP surface (upload→create→poll→fetch→content), the file state machine +
**content-scan pipeline** (`airlock/batch/scan.py`, `worker.py`, the
`batch_files` state machine in `store.py` — to-do #2, merged), idempotency
(§3.7), per-row staging (`batch_rows`), and OpenAI batch-object shaping
(`gateway.py:123` `to_openai_batch_object`). The provider surface is the single
`BatchBackend` protocol (`airlock/batch/backend.py:39-82`).

A `VLLMBackend` satisfies that protocol but, instead of calling a provider job
queue, **executes the batch itself** against vLLM's live `/v1/chat/completions`:

| `BatchBackend` method | vLLM-as-executor behavior |
|---|---|
| `to_provider_request` / `from_provider_result` | ~identity — vLLM is OpenAI-shaped (mirror `mistral.py`). |
| `upload(src, display_name)` | no remote upload; the **scrubbed** file already sits on Airlock's disk (the scan wrote it). Return a local ref. |
| `create(model, file_ref, display_name)` | start an Airlock-side **execution job** (a background task, reusing the `worker.py` pattern) that streams the scrubbed JSONL and fires rows at the live sync endpoint with **bounded concurrency** (vLLM continuous-batches them internally), writing native result lines to a local results file keyed by `custom_id`. Return an Airlock-internal `job_id` (e.g. the `idem`). |
| `poll(job_id)` | derive `NormalizedStatus` from **Airlock's own progress** (rows done / total): `in_progress` until all rows are written, then `completed`. **This is the parity status** — Airlock is the source of truth because vLLM has none. |
| `fetch(job_id)` | stream the native result lines Airlock produced; raise `ResultUnavailableError` if the results artifact is missing (parity with §7.3). The existing `stage_results` then translates + stages them into `batch_rows`. |
| `cancel(job_id)` | signal the execution job to stop. |
| `list_jobs(display_name)` | return the in-flight Airlock job for this `idem` so a duplicate `create` **adopts** it (idempotency: never execute the same batch twice). |

The conceptual shift: `job_id` is an Airlock handle, and `poll`/`fetch` read
**Airlock-produced** artifacts rather than provider ones. To the gateway core and
to clients, it is indistinguishable from a real provider batch.

### Wiring (three dispatch points, same as any provider)

1. **Adapter** — new `airlock/batch/vllm.py` (`VLLMBackend`) + a small executor
   (bounded-concurrency async loop, httpx to `{api_base}/v1/chat/completions`,
   incremental results-file writer). Read `api_base`/`api_key` (env
   `VLLM_API_KEY`) from the marker.
2. **Gateway provider set** — add `"vllm"` to `_GATEWAY_PROVIDERS`
   (`airlock/batch/middleware.py:27`) so `?custom_llm_provider=vllm` on
   `/v1/files`+`/v1/batches` is captured by the gateway.
3. **Backend dispatch** — add a `backend == "vllm"` branch in `backend_for_alias`
   (`airlock/batch/runtime.py:84-100`):
   ```python
   if backend == "vllm":
       return VLLMBackend(provider_model=provider_model, api_base=…, api_key=…)
   ```

### Config alias (`airlock_batch` SIBLING of `litellm_params`, never nested — §7.4)

```yaml
- model_name: qwen36-27b-vllm-batch
  litellm_params:
    model: openai/qwen3.6-27b
    api_base: http://192.168.1.45:8000/v1
    api_key: os.environ/VLLM_API_KEY
  airlock_batch:
    backend: vllm
    provider_model: qwen3.6-27b
```

## Guardrails come for free

Once vLLM is a gateway provider, the content scan runs in the provider-agnostic
core **before** `create`/execution — same `batch_profile` controls
(`scan_at_upload`, `keyword_block`, `pii_redact`) as aistudio/mistral, resolved
in `scan.py`. No per-provider guardrail wiring. This is the "same types of
controls" parity goal.

## Status, durability, concurrency

- **Status** is Airlock's, from per-row progress — and can be *richer* than the
  hosted providers (live `request_counts.completed`, not just terminal).
- **Durability / resume** — write the results file incrementally (or stage rows
  as they complete); on restart, re-run skips `custom_id`s already present
  (reuses the §3.7 `staged_keys` diff idea), so a crash reprocesses only the
  missing rows, never the whole file.
- **Concurrency** — a semaphore bounds in-flight requests so a large extraction
  batch doesn't overwhelm vLLM; tune to the host. Surface failures per row
  (an errored row → an error result line, not a whole-batch failure).

## Fathom tester fit

Fathom submits an extraction job as a **drop-in OpenAI batch** (upload JSONL →
create batch with `?custom_llm_provider=vllm` → poll → download) — identical to
how it targets aistudio/mistral — and gets keyword/PII scanning against local
`qwen3.6-27b`, with durable/resumable job state and real status.

## Single-GPU caveats

- A big batch **saturates** the one loaded model for its duration (true of any
  heavy local use); bounded concurrency keeps vLLM healthy.
- The host serves **one model at a time** (`local_vllm_router` guards the sync
  path). Don't swap the loaded model mid-batch; document the operational
  expectation.

## Interim (zero code, available today)

The **sync** chat path is already guardrailed, so Fathom can point at Airlock's
`/v1/chat/completions` with the Note-1 `qwen36-27b-vllm` alias and run its own
concurrency now — it gets the guard controls immediately, just without the
batch-job UX / durability. This buys time while `VLLMBackend` is built.
See [note-add-qwen36-27b-vllm-alias](note-add-qwen36-27b-vllm-alias.md).

## Open questions

- **Executor home** — does the execution loop live in `vllm.py`, or as a generic
  "local executor" in `worker.py` that a future `LiteLLMNativeBackend` could also
  use to guard openai/vertex batch? (The latter generalizes design Phase 5.)
- **Results persistence** — incremental results file on disk vs. staging into
  `batch_rows` directly as rows complete. The latter unifies with the existing
  staging/`fetch` path but changes when `from_provider_result` runs.
- **Backpressure** — concurrency limit + retry/timeout policy per row against a
  single-GPU host under load.
- **Test parity** — mirror the Mistral integration tests
  (`test_batch_gateway_integration.py`, faithful fake backend) and add an e2e
  plan akin to `dev/aistudio-batch-e2e-test-plan.md`, driving the executor
  against a fake sync endpoint (no network in unit tests).

## Related

- [note-add-qwen36-27b-vllm-alias](note-add-qwen36-27b-vllm-alias.md) — the sync
  alias + the zero-code interim.
- `dev/design-unified-batch-gateway.md` (gateway core, §3.7 idempotency, §7.4),
  `dev/design-batch-content-scan.md` (the scan pipeline this reuses),
  `airlock/batch/backend.py` (the `BatchBackend` protocol).
