# Batch Processing

Batch lets you submit many requests as one asynchronous job for **~50% lower
cost** (typical ~24h turnaround). Airlock exposes the OpenAI-compatible Batch API
(`/v1/files` + `/v1/batches`) on the proxy.

!!! warning "Batch bypasses Airlock guardrails today"
    Batch is a LiteLLM passthrough. Airlock's guardrails (PII redaction, keyword,
    etc.) run on `/v1/chat/completions`, **not** on batch jobs — the request
    content lives inside the uploaded file, which the chat-path guards never see.
    Pre-redact client-side, or use the guarded chat path, for sensitive data.
    (A guarded batch gateway is in design — see *In progress* below.)

## Support matrix

| Provider | Batch through Airlock? | Notes |
|---|---|---|
| **OpenAI** (`gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini/nano`, …) | ✅ **Working** | Needs `files_settings` (below) + a proxy restart |
| **Vertex AI (Gemini)** | ✅ **Working (regional models)** | See [Vertex AI Batch](vertex-batch.md). Batch needs a **regional** model; Gemini 3.x is `global`-only and **cannot batch** |
| **Anthropic / Azure / Bedrock** | ✅ Wired in LiteLLM | Not configured here by default |
| **Google AI Studio (Gemini 3.x)** | 🚧 **In progress** | LiteLLM doesn't wire the `gemini/` provider for batch — needs the Airlock batch gateway |
| **Mistral** | 🚧 **In progress** | Same: not wired in LiteLLM — needs the batch gateway |

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

## In progress — AI Studio (Gemini 3.x) and Mistral

!!! note "TODO — not yet available through Airlock"
    **Google AI Studio (Gemini 3.x)** and **Mistral** both have native batch APIs
    with the 50% discount, but **LiteLLM does not wire either provider** for
    `/v1/batches`, so they are **not usable through the proxy today**. The unified
    design to add them (an Airlock-owned batch gateway with per-provider adapters
    and guardrail scanning of batch content) is specified in
    `dev/design-unified-batch-gateway.md`.

    **To do, before this guide can document them as working:**

    - [ ] Build the Airlock Batch Gateway (middleware front-controller on
      `/v1/files` + `/v1/batches`, dispatching `custom_llm_provider=aistudio|mistral`).
    - [ ] AI Studio adapter (`google-genai`): Gemini 3.x via `client.batches.*`,
      OpenAI↔Gemini request/response translation, `GOOGLE_AISTUDIO_API_KEY`.
    - [ ] Mistral adapter (`mistralai`): `client.batch.jobs.*`, near-identity
      JSONL mapping, `MISTRAL_API_KEY`.
    - [ ] Guardrail scanning of batch content (async, off the request path) so
      batch stops bypassing the guards — close the caveat at the top of this page.
    - [ ] Replace this section with the working `aistudio` / `mistral` recipes and
      move those rows in the support matrix from 🚧 to ✅.
