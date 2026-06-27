# Batch Processing

Batch lets you submit many requests as one asynchronous job for **~50% lower
cost** (typical ~24h turnaround). Airlock exposes the OpenAI-compatible Batch API
(`/v1/files` + `/v1/batches`) on the proxy.

!!! info "Guardrails on batch content"
    The **Airlock Batch Gateway** (`aistudio`/`mistral`) scans uploaded batch
    content: each row is keyword-checked (a hit rejects the whole upload) and
    PII-redacted (terminal redaction — placeholders ship to the provider, no
    reverse map is stored) **before** the provider job is created. Scanning runs
    asynchronously after upload, so `POST /v1/files` returns `status: pending`;
    poll `GET /v1/files/{id}` until `processed` (or `error`), or just call
    `POST /v1/batches` — it waits for the scan and refuses a rejected file.
    Controlled by `batch_profile` (`scan_at_upload`, `keyword_block`,
    `pii_redact`). The **LiteLLM passthrough** providers (OpenAI/Vertex) still
    bypass these guards — their batch content never reaches the gateway scanner;
    pre-redact client-side or use the guarded chat path for sensitive data there.

## Support matrix

| Provider | Batch through Airlock? | Notes |
|---|---|---|
| **OpenAI** (`gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini/nano`, …) | ✅ **Working** | Needs `files_settings` (below) + a proxy restart |
| **Vertex AI (Gemini)** | ✅ **Working (regional models)** | See [Vertex AI Batch](vertex-batch.md). Batch needs a **regional** model; Gemini 3.x is `global`-only and **cannot batch** |
| **Anthropic / Azure / Bedrock** | ✅ Wired in LiteLLM | Not configured here by default |
| **Google AI Studio (Gemini)** | ✅ **Working (via Airlock Batch Gateway)** | LiteLLM doesn't wire the `gemini/` provider for batch, so Airlock's own gateway handles it. Needs the `aistudio` extra + an `airlock_batch` alias — see [AI Studio (Gemini) batch](#ai-studio-gemini-batch-via-the-airlock-batch-gateway) below |
| **Mistral** | ✅ **Working (via Airlock Batch Gateway)** | Same gateway/adapter as AI Studio; integration-tested **and live-verified**. Needs the `mistral` extra (pinned `<2`) + an `airlock_batch` alias — see [Mistral batch](#mistral-batch-via-the-airlock-batch-gateway) below |
| **Local vLLM** | ✅ **Working (via Airlock Batch Gateway, executor mode)** | Integration-tested **and live-verified** (`qwen3.6-27b`). vLLM has **no** async Batch server API, so Airlock *executes* the batch against the live `/v1/chat/completions` endpoint and owns the lifecycle/status. No extra needed (HTTP only) + an `airlock_batch` alias — see [Local vLLM batch](#local-vllm-batch-via-the-airlock-batch-gateway) below |

---

## Which aliases batch

Batch capability is **data, not a name suffix** (0.5.2). Each model publishes an
`endpoints` list on `GET /model/info` and `GET /v1/models` — a model advertises
`batch` **iff** it is actually batch-wired, computed from the real routing by one
helper (`airlock/capability.py`). Discover it instead of guessing:

```bash
curl -s http://localhost:4000/v1/models \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
| jq '.data[] | {id, endpoints: .airlock.endpoints}'
```

The shipped **batch-capable** stable aliases (`"batch" ∈ endpoints`):

| Stable alias | Provider (served-by) | Batch path |
|---|---|---|
| `aistudio/gemini-3.5-flash`, `aistudio/gemini-3.1-pro` | `gemini` | Airlock Batch Gateway (`?custom_llm_provider=aistudio`) |
| `mistral/mistral-large`, `mistral/mistral-small` | `mistral` | Airlock Batch Gateway (`?custom_llm_provider=mistral`) |
| `vllm/qwen3.6-27b` | `openai` (vLLM, OpenAI-compatible) | Airlock Batch Gateway, executor mode (`?custom_llm_provider=vllm`) |
| OpenAI models (e.g. `gpt-5.4-nano`) | `openai` | LiteLLM-native (`?custom_llm_provider=openai`) |

`vertex/gemini-3.5-flash` / `vertex/gemini-3.1-pro` are **chat-only** as shipped —
see the [region-gated Vertex caveat](#vertex-ai-gemini-batch) below.

### Old → new alias map (0.5.2)

The legacy capability-suffix names are **deprecated but still fully functional**
in 0.5.2 (dual-listed, same `litellm_params` + `airlock_batch` marker). They carry
`deprecated: true` in their capability record and are **removed in 0.6.0**. No
client breaks in 0.5.2 — migrate to the stable `provider/model` name:

| Legacy (deprecated → removed in 0.6.0) | New stable alias | Served-by | `endpoints` |
|---|---|---|---|
| `gemini-3.5-flash-aistudio` / `gemini-3.1-pro-aistudio` | `aistudio/gemini-3.5-flash` / `aistudio/gemini-3.1-pro` | `gemini` | `chat, batch` |
| `gemini-3.5-flash-vertex` / `gemini-3.1-pro-vertex` | `vertex/gemini-3.5-flash` / `vertex/gemini-3.1-pro` | `vertex_ai` | `chat` *(batch only when a **regional** `vertex_location` is set; the shipped entries use `global` → chat-only)* |
| `mistral-large-batch` / `mistral-small-batch` | `mistral/mistral-large` / `mistral/mistral-small` | `mistral` | `chat, batch` |
| `qwen36-27b-vllm-batch` | `vllm/qwen3.6-27b` | `openai` (vLLM) | `chat, batch` |

The `airlock_batch` marker now rides the consolidated `provider/model` entry, so
one alias serves **both** sync and batch — capability is read from `endpoints`,
never the suffix.

---

## Prerequisites (one-time)

### 1. `files_settings` in `config.yaml`
`/v1/files` needs a provider entry to accept the input-file upload (the `vertex_ai`
provider is special-cased and needs none; `openai` does need it):

```yaml
# top-level block in config.yaml
files_settings:
  - custom_llm_provider: openai
    api_key: os.environ/OPENAI_API_KEY
```

### 2. The API key in `.env`
The `os.environ/…` reference above reads the secret from the environment:

```bash
# .env
OPENAI_API_KEY=sk-...
```

### 3. Restart so the proxy loads it
LiteLLM reads `config.yaml` **once at startup** — a config edit needs a restart:

```bash
systemctl --user restart airlock
```

!!! danger "Don't probe `GET /health`"
    It fires live completions to every model. Use `GET /health/liveliness`.

---

## Run an OpenAI batch (working today)

### 1. Build a JSONL of requests
One request per line. Use the **upstream OpenAI model id** (e.g. `gpt-5.4-nano`),
not the Airlock alias — with `custom_llm_provider=openai` the file is uploaded
straight to OpenAI and bypasses the proxy's alias mapping.

```json
{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{"model":"gpt-5.4-nano","messages":[{"role":"user","content":"Say ok"}],"max_tokens":5}}
{"custom_id":"r2","method":"POST","url":"/v1/chat/completions","body":{"model":"gpt-5.4-nano","messages":[{"role":"user","content":"Say go"}],"max_tokens":5}}
```

### 2. Upload, create, poll
Pass `custom_llm_provider=openai` so the proxy resolves the provider.

```bash
# upload the input file (purpose=batch)
curl -s http://localhost:4000/v1/files \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -F purpose=batch -F custom_llm_provider=openai \
  -F file=@requests.jsonl
# -> {"id":"file-...", ...}

# create the batch
curl -s "http://localhost:4000/v1/batches?custom_llm_provider=openai" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"input_file_id":"file-...","endpoint":"/v1/chat/completions","completion_window":"24h"}'
# -> {"id":"batch-...","status":"validating", ...}

# poll
curl -s "http://localhost:4000/v1/batches/BATCH_ID?custom_llm_provider=openai" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"

# when completed, download the output file
curl -s http://localhost:4000/v1/files/OUTPUT_FILE_ID/content \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
```

The OpenAI Python SDK works too (point its `base_url` at the proxy); pass the
provider via the SDK's `extra_query={"custom_llm_provider":"openai"}` where needed.

---

## Vertex AI (Gemini) batch

Working for **regional** Gemini models via LiteLLM's native `vertex_ai` batch
(GCS-staged). Gemini 3.x is `global`-only in the current project and **cannot
batch** (the Vertex `global` endpoint doesn't support batch jobs). Full setup —
service account, GCS bucket, IAM — in **[Vertex AI Batch](vertex-batch.md)**.

---

## AI Studio (Gemini) batch — via the Airlock Batch Gateway

LiteLLM doesn't wire the AI Studio `gemini/` provider for `/v1/batches`, so Airlock
ships its **own** gateway for it. A request carrying `?custom_llm_provider=aistudio`
on `/v1/files` or `/v1/batches` is intercepted by the gateway middleware (everything
else falls through to LiteLLM untouched), translated OpenAI↔Gemini, and run against
Google's native Gemini batch API (`google-genai` `client.batches.*`). Verified
end-to-end against the live endpoint by `tests/test_aistudio_batch_e2e.py` (see
`dev/aistudio-batch-e2e-test-plan.md`).

### 1. Install the extra + set the key
The `google-genai` SDK is lazy-imported, so it ships only with the `aistudio` extra:

```bash
pip install 'airlock-llm[aistudio]'     # or: uv sync --extra aistudio
```
```bash
# .env
GOOGLE_AISTUDIO_API_KEY=AIza...
```

### 2. Declare an `airlock_batch` alias in `config.yaml`
The `airlock_batch` marker is a **sibling** of `litellm_params` (not nested inside
it, so it never leaks to the provider SDK on the sync path). `backend: aistudio`
selects the gateway; `provider_model` is the Gemini model the job runs:

```yaml
model_list:
  - model_name: gemini-3.5-flash-aistudio
    litellm_params:
      model: gemini/gemini-3.5-flash
      api_key: os.environ/GOOGLE_AISTUDIO_API_KEY
    airlock_batch:
      backend: aistudio
      provider_model: gemini-3.5-flash
```

Restart the proxy so it reloads `config.yaml`.

!!! tip "Thinking models need a generous `max_tokens`"
    Gemini 3.x flash/pro spend output tokens on internal reasoning. A tiny
    `max_tokens` can finish a row with `finish_reason: length` and **empty**
    content (the thinking budget starved the answer). Size `max_tokens` to cover
    thinking **and** the answer.

### 3. Build a JSONL, upload, create, poll
Unlike the OpenAI recipe, use the **Airlock alias** (`gemini-3.5-flash-aistudio`) as
the `model` — the gateway resolves it to the configured `provider_model`. Pass
`custom_llm_provider=aistudio` on each call:

```json
{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{"model":"gemini-3.5-flash-aistudio","messages":[{"role":"user","content":"Reply with one word: PONG"}],"max_tokens":512}}
```

```bash
# upload the input file
curl -s "http://localhost:4000/v1/files?custom_llm_provider=aistudio" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -F purpose=batch -F file=@requests.jsonl
# -> {"id":"file-...", ...}

# create the batch
curl -s "http://localhost:4000/v1/batches?custom_llm_provider=aistudio" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"input_file_id":"file-...","endpoint":"/v1/chat/completions","completion_window":"24h","model":"gemini-3.5-flash-aistudio"}'
# -> {"id":"batch-...","status":"validating", ...}

# poll
curl -s "http://localhost:4000/v1/batches/BATCH_ID?custom_llm_provider=aistudio" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"

# when completed, download the translated output (no provider param needed
# here — the gateway recognizes its own output file ids)
curl -s http://localhost:4000/v1/files/OUTPUT_FILE_ID/content \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
```

Output lines come back **OpenAI-shaped** (`choices[].message.content`), with the
native Gemini response preserved verbatim in `response.body` alongside the projected
`choices`. The gateway is idempotent on `(input_file_id, model, endpoint, params)`
and bounds duplicate provider jobs to ≤1.

## Mistral batch — via the Airlock Batch Gateway

Same gateway, second adapter. LiteLLM doesn't wire the `mistral` provider for
`/v1/batches`, so a request carrying `?custom_llm_provider=mistral` is intercepted
by the gateway and run against Mistral's native batch API
(`mistralai` `client.batch.jobs.*`) at the **50% batch discount**. Mistral's batch
input is already OpenAI-shaped and Mistral chat is OpenAI-compatible, so the
translation is near-passthrough. The no-network path is covered by
`tests/test_mistral_batch.py` + `tests/test_batch_gateway_integration.py`; a live
round-trip is covered by `tests/test_mistral_batch_e2e.py` (opt-in — see below).

### 1. Install the extra + set the key
The `mistralai` SDK is lazy-imported, so it ships only with the `mistral` extra
(pinned `<2`: 2.x moved the top-level client import; the adapter targets the v1
`client.batch.jobs` API):

```bash
pip install 'airlock-llm[mistral]'      # or: uv sync --extra mistral
```
```bash
# .env
MISTRAL_API_KEY=...
```

### 2. Declare an `airlock_batch` alias in `config.yaml`
Same shape as AI Studio — the `airlock_batch` marker is a **sibling** of
`litellm_params` (so it never leaks to the provider on the sync path), with
`backend: mistral`:

```yaml
model_list:
  - model_name: mistral-large-batch
    litellm_params:
      model: mistral/mistral-large-latest
      api_key: os.environ/MISTRAL_API_KEY
    airlock_batch:
      backend: mistral
      provider_model: mistral-large-latest
```

(`mistral-small-batch` ships too.) Restart the proxy so it reloads `config.yaml`.

### 3. Build a JSONL, upload, create, poll
Use the **Airlock alias** (`mistral-large-batch`) as the `model` and pass
`custom_llm_provider=mistral` on each call:

```json
{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{"model":"mistral-large-batch","messages":[{"role":"user","content":"Reply with one word: PONG"}],"max_tokens":32}}
```

```bash
# upload the input file
curl -s "http://localhost:4000/v1/files?custom_llm_provider=mistral" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -F purpose=batch -F file=@requests.jsonl

# create the batch
curl -s "http://localhost:4000/v1/batches?custom_llm_provider=mistral" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"input_file_id":"file-...","endpoint":"/v1/chat/completions","completion_window":"24h","model":"mistral-large-batch"}'

# poll, then download the translated output
curl -s "http://localhost:4000/v1/batches/BATCH_ID?custom_llm_provider=mistral" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
# content GET needs no provider param (gateway recognizes its own output ids)
curl -s http://localhost:4000/v1/files/OUTPUT_FILE_ID/content \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
```

As with AI Studio, output lines are **OpenAI-shaped** with the native Mistral
response preserved verbatim in `response.body`, and the gateway is idempotent on
`(input_file_id, model, endpoint, params)`, bounding duplicate provider jobs to ≤1.

!!! note "Live e2e is opt-in"
    `tests/test_mistral_batch_e2e.py` runs the real round-trip only when
    `AIRLOCK_LIVE_MISTRAL_E2E=1`, `MISTRAL_API_KEY` is set, and the `mistral`
    extra is installed (it's billable). The unit + integration suites need none of
    that. Verified live against Mistral's batch API on 2026-06-15
    (`mistral-small-latest`, completed ~60s).

## Local vLLM batch — via the Airlock Batch Gateway

Same gateway, **executor mode**. vLLM exposes no async Batch server API
(`/v1/files` + `/v1/batches` are 404; only synchronous `/v1/chat/completions`).
So a request carrying `?custom_llm_provider=vllm` is intercepted by the gateway
and Airlock **executes** the batch itself: after the content scan, it streams the
scrubbed rows at vLLM's live chat endpoint with bounded concurrency, stages the
results, and reports status from its own state. There is **no provider-side job
or discount** — you own the GPU. The no-network path is covered by
`tests/test_batch_vllm_*.py` + `tests/test_batch_gateway_integration.py`.

### 1. No extra needed — just reachability
vLLM is a plain OpenAI-compatible HTTP host; the gateway uses `httpx` (already a
dependency). The Airlock host must be able to reach the vLLM `api_base`.

```bash
# .env  (only if your vLLM host requires a key)
VLLM_API_KEY=...
```

### 2. Declare an `airlock_batch` alias in `config.yaml`
The `airlock_batch` marker is a **sibling** of `litellm_params`, with
`backend: vllm`. Unlike the hosted adapters, vLLM's `api_base`/`api_key` are read
**per-alias** from `litellm_params` (so each vLLM host gets its own alias). The
`api_base` must end in `/v1`; `provider_model` is the exact id vLLM serves at
`GET {api_base}/models`:

```yaml
model_list:
  - model_name: qwen36-27b-vllm-batch
    litellm_params:
      model: openai/qwen3.6-27b
      api_base: http://192.168.1.45:8000/v1
      api_key: os.environ/VLLM_API_KEY
    airlock_batch:
      backend: vllm
      provider_model: qwen3.6-27b
```

Restart the proxy so it reloads `config.yaml`.

### 3. Build a JSONL, upload, create, poll
Use the **Airlock alias** (`qwen36-27b-vllm-batch`) as the `model` and pass
`custom_llm_provider=vllm` on each call:

```json
{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{"model":"qwen36-27b-vllm-batch","messages":[{"role":"user","content":"Reply with one word: PONG"}],"max_tokens":32}}
```

```bash
# upload the input file (purpose=batch) -> returns {"id":"file-...","status":"pending"}
curl -s "http://localhost:4000/v1/files?custom_llm_provider=vllm" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -F purpose=batch -F file=@requests.jsonl

# create the batch (waits for the scan; refuses a rejected file)
curl -s "http://localhost:4000/v1/batches?custom_llm_provider=vllm" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"input_file_id":"file-...","endpoint":"/v1/chat/completions","completion_window":"24h","model":"qwen36-27b-vllm-batch"}'

# poll until status == completed, then download the output
curl -s "http://localhost:4000/v1/batches/BATCH_ID?custom_llm_provider=vllm" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
# content GET needs no provider param (gateway recognizes its own output ids)
curl -s http://localhost:4000/v1/files/OUTPUT_FILE_ID/content \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
```

Output lines are **OpenAI-shaped** with the native vLLM response preserved
verbatim in `response.body`. A row that exhausts retries comes back as an
**error line** (`response: null`, `error: {...}`) rather than failing the whole
batch. Tune concurrency against your single-GPU host with
`AIRLOCK_VLLM_BATCH_CONCURRENCY` (default 8); per-row timeout/retry via
`AIRLOCK_VLLM_BATCH_TIMEOUT` / `AIRLOCK_VLLM_BATCH_RETRIES`.

!!! note "Single-GPU operational note"
    The host loads one model at a time; a large batch saturates it for the run's
    duration. Don't swap the loaded model mid-batch.

!!! note "Live e2e is opt-in"
    `tests/test_vllm_batch_e2e.py` runs the real round-trip only when
    `AIRLOCK_LIVE_VLLM_E2E=1` and a vLLM host is reachable at
    `AIRLOCK_VLLM_E2E_API_BASE` (default the lab host) serving
    `AIRLOCK_VLLM_E2E_MODEL` (default `qwen3.6-27b`). No proxy restart or SDK
    needed — it drives the gateway functions against a real `VLLMBackend`. The
    unit + integration suites need none of that. Verified live against
    `qwen3.6-27b` on 2026-06-15 (both rows round-tripped; wall-clock depends on
    your host/GPU).

---

## Interface reference (for batch clients, e.g. Fathom)

This is the complete client contract for the Airlock Batch Gateway. It is a
**drop-in subset of the OpenAI Batch API**; the only Airlock-specific requirement
is the `?custom_llm_provider=<provider>` query parameter on `/v1/files` and
`/v1/batches`. The contract is **identical across providers** (`vllm`,
`aistudio`, `mistral`) — only the alias and provider value change.

### Auth
Every call sends `Authorization: Bearer $AIRLOCK_MASTER_KEY`. The gateway runs
ahead of LiteLLM's route auth and enforces the key itself (open only if
`AIRLOCK_MASTER_KEY` is unset on the server).

### Endpoints

| Method & path | Purpose |
|---|---|
| `POST /v1/files?custom_llm_provider=vllm` | Upload the JSONL (multipart `file=@…`, `purpose=batch`). Returns a file object. Scanning is async → initial `status: "pending"`. |
| `GET /v1/files/{file_id}?custom_llm_provider=vllm` | Poll file/scan status (see lifecycle). |
| `POST /v1/batches?custom_llm_provider=vllm` | Create a batch from `input_file_id` + `model` (the alias). Waits for the scan; **refuses** a rejected file. |
| `GET /v1/batches/{batch_id}?custom_llm_provider=vllm` | Poll batch status; transparently stages results when the run completes. |
| `POST /v1/batches/{batch_id}/cancel?custom_llm_provider=vllm` | Cancel. |
| `GET /v1/files/{output_file_id}/content` | Download the OpenAI-shaped output JSONL. **No provider param needed here** — the gateway recognizes its own output file ids, so a stock OpenAI SDK `files.content()` works. (`/v1/files` upload and `/v1/batches` create/poll still need the param.) |

### Input JSONL (one request per line)
```json
{"custom_id":"<your-id>","method":"POST","url":"/v1/chat/completions","body":{"model":"<alias>","messages":[…],"max_tokens":…}}
```
`custom_id` must be unique per row (it keys the output and the gateway's
idempotency/resume). `body` is a standard chat-completions request and is
**forwarded to the provider verbatim** — the gateway rewrites only `body.model`
(alias → served name) and drops nothing else, so any provider-specific params
(`temperature`, `chat_template_kwargs`, …) pass straight through.

**vLLM / Qwen3.6:** set `"chat_template_kwargs":{"enable_thinking":false}` in
**every** row's `body` to suppress reasoning tokens. The gateway injects no
default — a row that omits it gets thinking back on, which consumes `max_tokens`
on reasoning prose and breaks the parse. Example row:
```json
{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{"model":"qwen36-27b-vllm-batch","messages":[…],"max_tokens":1536,"chat_template_kwargs":{"enable_thinking":false}}}
```

### File status lifecycle (async scan)
`GET /v1/files/{id}` `status` is OpenAI's file enum:

| `status` | Meaning |
|---|---|
| `pending` | uploaded, scan in progress |
| `processed` | scan clean — safe to create the batch |
| `error` | scan rejected (keyword/PII/cap); `status_details` has the reason |

You may skip polling the file and call `POST /v1/batches` directly — it blocks
until the scan reaches a terminal state and then proceeds or returns an error.

### Batch status lifecycle
`GET /v1/batches/{id}` `status` is OpenAI's batch enum:
`validating` → `in_progress` → `completed` (or `failed` / `cancelled`). When
`completed`, read `output_file_id` and `request_counts.total`.

### Output JSONL (one result per line)
```json
{"id":"batch_req_<custom_id>","custom_id":"<your-id>","response":{"status_code":200,"request_id":"<your-id>","body":{<native chat.completion>}},"error":null}
```
On a per-row failure: `"response": null, "error": {"code":"…","message":"…"}`.
The provider-native response is always preserved verbatim in `response.body`.

### Error responses (OpenAI-style `{"error":{…}}`)

| HTTP | `code` | When |
|---|---|---|
| 401 | `invalid_api_key` | bad/missing bearer |
| 400 | `invalid_request_error` | unknown alias / bad `input_file_id` (`invalid_file_id`) |
| 400 | `content_scan_rejected` | scan blocked the upload (`message` has the reason) |
| 409 | `file_not_ready` | scan still running after the create wait — retry |

### Guarantees
- **Guardrails always run** before execution (keyword block + PII terminal
  redaction; config-controlled, not caller-controlled).
- **Idempotent** on `(input_file_id, model, endpoint, params)` — re-submitting the
  same create does not double-run; interrupted runs resume only the missing rows.

### End-to-end (OpenAI Python SDK)
The stock SDK can't set the query param on `batches.create`, so pass it via
`default_query` (or use the `/v1/files` + `/v1/batches` HTTP calls above):

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="$AIRLOCK_MASTER_KEY",
    default_query={"custom_llm_provider": "vllm"},
)

f = client.files.create(file=open("requests.jsonl", "rb"), purpose="batch")
batch = client.batches.create(
    input_file_id=f.id,
    endpoint="/v1/chat/completions",
    completion_window="24h",
    extra_body={"model": "qwen36-27b-vllm-batch"},
)
# poll client.batches.retrieve(batch.id) until status == "completed",
# then client.files.content(batch.output_file_id)
```

## Gateway auth & remaining work

Every gateway request (`?custom_llm_provider=aistudio|mistral|vllm` on `/v1/files`
and `/v1/batches`) is authenticated with the **`AIRLOCK_MASTER_KEY`** before any
upload/create/cancel/retrieve — the gateway runs ahead of LiteLLM's route-level
auth, so it enforces the master key itself (it mirrors the proxy's open-when-unset
behavior for parity).

Remaining gateway work:

- [x] Guardrail scanning of batch content (async, off the request path) so batch
  stops bypassing the guards — **done** (`scan_at_upload`; keyword reject + PII
  terminal redaction; gated at `create`). See `dev/design-batch-content-scan.md`.
- [ ] Output-side scanning (`output_scan_mode`) and opt-in PII hydration remain
  future seams; webhooks stay deferred in favor of polling.
