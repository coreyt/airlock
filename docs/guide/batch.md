# Batch Processing

Batch lets you submit many requests as one asynchronous job for **~50% lower
cost** (typical ~24h turnaround). Airlock exposes the OpenAI-compatible Batch API
(`/v1/files` + `/v1/batches`) on the proxy.

!!! warning "Batch bypasses Airlock guardrails today"
    Airlock's guardrails (PII redaction, keyword, etc.) run on
    `/v1/chat/completions`, **not** on batch jobs — the request content lives
    inside the uploaded file, which the chat-path guards never see. This holds for
    both the LiteLLM passthrough providers **and** the Airlock Batch Gateway: the
    gateway's async content-scan hook is currently a **no-op stub**, so it does not
    yet enforce guards on batch content. Pre-redact client-side, or use the guarded
    chat path, for sensitive data.

## Support matrix

| Provider | Batch through Airlock? | Notes |
|---|---|---|
| **OpenAI** (`gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini/nano`, …) | ✅ **Working** | Needs `files_settings` (below) + a proxy restart |
| **Vertex AI (Gemini)** | ✅ **Working (regional models)** | See [Vertex AI Batch](vertex-batch.md). Batch needs a **regional** model; Gemini 3.x is `global`-only and **cannot batch** |
| **Anthropic / Azure / Bedrock** | ✅ Wired in LiteLLM | Not configured here by default |
| **Google AI Studio (Gemini)** | ✅ **Working (via Airlock Batch Gateway)** | LiteLLM doesn't wire the `gemini/` provider for batch, so Airlock's own gateway handles it. Needs the `aistudio` extra + an `airlock_batch` alias — see [AI Studio (Gemini) batch](#ai-studio-gemini-batch-via-the-airlock-batch-gateway) below |
| **Mistral** | ✅ **Working (via Airlock Batch Gateway)** | Same gateway/adapter as AI Studio; integration-tested **and live-verified**. Needs the `mistral` extra (pinned `<2`) + an `airlock_batch` alias — see [Mistral batch](#mistral-batch-via-the-airlock-batch-gateway) below |

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

# when completed, download the translated output
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

## Gateway auth & remaining work

Every gateway request (`?custom_llm_provider=aistudio|mistral` on `/v1/files` and
`/v1/batches`) is authenticated with the **`AIRLOCK_MASTER_KEY`** before any
upload/create/cancel/retrieve — the gateway runs ahead of LiteLLM's route-level
auth, so it enforces the master key itself (it mirrors the proxy's open-when-unset
behavior for parity).

Remaining gateway work:

- [ ] Guardrail scanning of batch content (async, off the request path) so batch
  stops bypassing the guards — close the caveat at the top of this page. The
  insertion point exists today as a no-op `scan_at_upload` stub in `batch_profile`.
