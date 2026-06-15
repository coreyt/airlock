# Findings: Mistral Batch through Airlock + batch discount

**Status: FINDINGS — investigation only, no code changed.**
**Date:** 2026-06-14
**Scope:** Answers (1) does Mistral have a Batch API, (2) is there a discount,
(3) can we reach it through Airlock via LiteLLM-managed batch today, (4)
conclusion + options. Grounded in the installed LiteLLM and Mistral's official docs.

---

## 1. Mistral DOES have a Batch API

Source: https://docs.mistral.ai/capabilities/batch/ (also
https://mistral.ai/news/batch-api/, announced Nov 2024).

- **SDK surface (`mistralai`):**
  - `client.files.upload(...)` — upload the JSONL input file (`purpose="batch"`).
  - `client.batch.jobs.create(...)` — create a job (`input_files=[file_id]`,
    `model`, `endpoint`, optional `metadata`).
  - `client.batch.jobs.get(job_id)` — poll a job.
  - `client.batch.jobs.list()` — list jobs.
  - `client.batch.jobs.cancel(job_id)` — cancel.
  - `client.files.download(file_id=...)` — download results (output/error files).
- **HTTP surface:** files upload + a batch jobs endpoint (`/v1/batch/jobs`).
  Upload can also be done in Studio (console.mistral.ai/build/files, purpose
  "Batch Processing").
- **Input format — JSONL**, one request per line, required fields:
  - `custom_id` — unique id per request, used to key results back.
  - `body` — the raw request you'd send to the underlying endpoint.
  - Example: `{"custom_id":"0","body":{"max_tokens":128,"messages":[{"role":"user","content":"Question?"}]}}`
- **Supported endpoints:** `/v1/embeddings`, `/v1/chat/completions`,
  `/v1/fim/completions`, `/v1/moderations`, `/v1/chat/moderations`, `/v1/ocr`,
  `/v1/classifications`, `/v1/conversations`, `/v1/audio/transcriptions`.
- **Two modes:** file batching (up to **1 million** requests via uploaded JSONL)
  and inline batching (<10,000 requests, data embedded in the create call).
- **Job statuses:** `QUEUED`, `RUNNING`, `SUCCESS`, `FAILED`,
  `TIMEOUT_EXCEEDED`, `CANCELLATION_REQUESTED`, `CANCELLED`.
- **Limits:** max **1,000,000** requests per batch; asynchronous processing
  (latency-tolerant). Results retrieved via the output file once `SUCCESS`.

The shape (files upload + jobs create/poll/download, JSONL with `custom_id` +
`body`) is essentially the OpenAI batch shape, but on Mistral's **own
`client.batch.jobs.*` namespace** — not the OpenAI `/v1/batches` object.

## 2. Discount: YES — 50%

Mistral batch runs at a **50% discount** vs. synchronous API pricing.

- Docs: "50% discount" on compute (https://docs.mistral.ai/capabilities/batch/).
- Announcement: "process high-volume requests ... at 50% lower cost than that of
  a synchronous API call" (https://mistral.ai/news/batch-api/).
- Pricing page: https://mistral.ai/pricing/ (e.g. Mistral Large $2/M in,
  $6/M out synchronous; batch is half).

## 3. Reachable through Airlock today (LiteLLM-managed batch)? NO

LiteLLM does **not** wire the Batch API for the `mistral` provider. Grounded
evidence from the installed LiteLLM (`.venv/lib/python3.12/site-packages/litellm`):

- **The OpenAI-compatible batch/files allowlist is tiny — Mistral is not in it.**
  `litellm/types/utils.py:3390`:
  ```python
  OPENAI_COMPATIBLE_BATCH_AND_FILES_PROVIDERS: set[str] = {
      LlmProviders.OPENAI.value,
      LlmProviders.HOSTED_VLLM.value,
  }
  ```
  Only `openai` and `hosted_vllm`. (Note: this is narrower than the
  "openai/azure/vertex_ai/anthropic/bedrock" recollection — azure, vertex_ai,
  anthropic, bedrock are handled by *separate explicit branches*, not this set.)
- **`create_batch` dispatch** (`litellm/batches/main.py`) handles only:
  `OPENAI_COMPATIBLE_BATCH_AND_FILES_PROVIDERS` (openai, hosted_vllm) at line 258,
  `azure` (line 290), `vertex_ai` (line 326), plus a `provider_batches_config`
  path (line 231). Anything else hits the else at line 354:
  *"LiteLLM doesn't support custom_llm_provider={} for 'create_batch'"*.
  `retrieve_batch` additionally special-cases `anthropic` (line 522).
- **`get_provider_batches_config`** (`litellm/utils.py:9099`) returns a config
  **only for `BEDROCK`**; returns `None` for everything else (including mistral).
- **No Mistral batch code exists:**
  - `grep -rni mistral litellm/batches/` → no matches.
  - `litellm/llms/mistral/` contains only `chat/`, `embedding.py`,
    `mistral_embedding_transformation.py`, `audio_transcription/`, `ocr/` —
    **no `batches/` subdir, no batches transformation.**
  - `grep -rni mistral litellm/files/` → no matches (Mistral not in the files
    dispatch either).

So the **effective LiteLLM batch provider set is**: `openai`, `hosted_vllm`,
`azure`, `vertex_ai`, `bedrock`, and `anthropic` (retrieve). **`mistral` is
absent from every one of these paths.** A `create_batch(custom_llm_provider=
"mistral", ...)` (or a `mistral/...` model) would raise the "doesn't support"
error above. Mistral chat is treated as OpenAI-compatible for *completions*
(`llms/mistral/embedding.py` even notes "mistral is an openai-compatible
endpoint"), but that compatibility is **not** registered for batch/files.

**Airlock config corroborates this.** `config.yaml` `files_settings` (line 286)
contains only `openai`; there is no `mistral` entry. (The `mistral:` at
config.yaml line 305 is under `router_settings.provider_budget_config` — a
budget cap, unrelated to files/batch.) Mistral models are plain sync chat
aliases: `mistral/<name>-latest` authed by `MISTRAL_API_KEY` (config.yaml
144-167).

## 4. Conclusion + options

**Mistral has a real, 50%-discounted Batch API, but it cannot be driven through
Airlock today** because LiteLLM does not wire `mistral` into its batch/files
dispatch. This is the **same situation as Google AI Studio (Gemini)** batch
(`dev/design-aistudio-gemini-batch.md` §1): the provider has native batch, but
LiteLLM's `batches/main.py` only dispatches a hardcoded provider set that
excludes it, and there is **no `CustomLLM` batch/files extension point** (custom
providers implement `completion`/`acompletion` only).

**To enable Mistral batch through Airlock, the remedy mirrors the AI Studio
design — do not duplicate it here; see `dev/design-aistudio-gemini-batch.md`:**

- **Option A (recommended there): Airlock-owned route injected on the proxy
  FastAPI app** (same precedent as `airlock/health.py` /`airlock/docs.py`,
  bootstrapped from `airlock/callbacks/model_override_headers.py`). The route
  would translate an OpenAI-style batch request into Mistral's
  `client.batch.jobs.create` / `client.files.upload` / `client.files.download`,
  run Airlock's guardrails over the JSONL **before submission** (the chat-path
  hooks never see batch content — `dev/batch-guardrail-toggles-considerations.md`),
  and map the Mistral job back to an OpenAI-shaped batch object. Mistral's JSONL
  (`custom_id` + `body`) is already close to OpenAI's, which simplifies the
  mapping vs. the Gemini `contents` adapter.
- **Option B (long-term upstream): contribute a `mistral` batches provider to
  LiteLLM** — a `litellm/llms/mistral/batches/transformation.py` + wiring in
  `get_provider_batches_config` and/or `OPENAI_COMPATIBLE_BATCH_AND_FILES_PROVIDERS`.
  Externally gated, slow, and still leaves the guardrail-coverage gap (LiteLLM
  batch is a passthrough; content never hits the chat hooks).

If LiteLLM later adds `mistral` to `OPENAI_COMPATIBLE_BATCH_AND_FILES_PROVIDERS`
(plausible, since Mistral is OpenAI-compatible), the call recipe would then be
the standard `/v1/files` (purpose `batch`) + `/v1/batches` with
`custom_llm_provider="mistral"`, plus a `files_settings` entry
`{custom_llm_provider: mistral, api_key: os.environ/MISTRAL_API_KEY}` added next
to the existing `openai` entry (config.yaml line 286). **That is not the case in
the currently installed LiteLLM**, so today the Airlock-owned route is the only
path.

---

### Sources
- Mistral Batch docs: https://docs.mistral.ai/capabilities/batch/
- Mistral Batch announcement (Nov 2024, "50% lower cost"): https://mistral.ai/news/batch-api/
- Mistral pricing: https://mistral.ai/pricing/
- Installed LiteLLM: `litellm/types/utils.py:3390`, `litellm/batches/main.py`
  (258/290/326/354/522), `litellm/utils.py:9099`, `litellm/llms/mistral/`,
  `litellm/files/`.
- Airlock: `config.yaml` (144-167 mistral aliases, 286 files_settings, 305
  budget), `dev/design-aistudio-gemini-batch.md`,
  `dev/batch-guardrail-toggles-considerations.md`.
