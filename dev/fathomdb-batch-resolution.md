# Resolution: FathomDB Agent — "batch failing through the Airlock proxy"

**Status: RESOLVED at the config level; one operator action (proxy restart) remains.**

## The note being resolved (verbatim)

> "The batch probe surfaced the decisive fact: file upload is disabled on this
> airlock (files_settings is not set), and the OpenAI Batches path needs it for
> the input file — so batch is not usable as currently configured without an
> airlock-side config change, even though the endpoint and a historical batch
> exist."

---

## 1. Root cause (confirmed)

OpenAI Batches requires uploading the input JSONL via `/v1/files`, and LiteLLM's
files endpoint refuses to upload for any non-vertex provider unless a
`files_settings` block exists in the proxy config. With no `files_settings`,
`/v1/files` raises **"files_settings is not set, set it on your config.yaml
file."** — so the batch flow could never get its input file uploaded.

This is confirmed directly in the running LiteLLM source:

`.venv/lib/python3.12/site-packages/litellm/proxy/openai_files_endpoints/files_endpoints.py:80-91`

```python
def get_files_provider_config(custom_llm_provider: str):
    global files_config
    if custom_llm_provider == "vertex_ai":
        return None                       # vertex is special-cased, needs no entry
    if files_config is None:
        raise ValueError("files_settings is not set, set it on your config.yaml file.")
    for setting in files_config:
        if setting.get("custom_llm_provider") == custom_llm_provider:
            return setting
    return None
```

`vertex_ai` returns early and needs nothing; `openai` falls through to the
`files_config is None` check and raises. So the FathomDB diagnosis was exactly
right.

## 2. Fix status (done in config)

`config.yaml` now contains a top-level `files_settings` block. Verified present
at **`config.yaml:286-288`**:

```yaml
files_settings:
  - custom_llm_provider: openai
    api_key: os.environ/OPENAI_API_KEY
```

Added in commit **`95b9bd8`** ("feat(config): add files_settings for OpenAI batch
via the proxy", 2026-06-14 18:10:52), on branch `chore/vertex-gemini-batch`.

At proxy startup `set_files_config()` (same file, lines 63-77) resolves the
`os.environ/OPENAI_API_KEY` reference and populates the module-global
`files_config`, which makes `get_files_provider_config("openai")` return the
setting instead of raising.

## 3. Remaining action: restart the running proxy

**The running proxy predates the config edit, so it has not loaded
`files_settings` yet** — which is why the FathomDB probe still saw it "not set."

Evidence (measured on this host):

| Fact | Value |
|------|-------|
| litellm process start (`ps -o pid,lstart -C litellm`) | PID **534987**, started **Sun Jun 14 18:04:36 2026** |
| `config.yaml` mtime (`stat -c %y config.yaml`) | **2026-06-14 18:10:40** |
| commit `95b9bd8` timestamp | **2026-06-14 18:10:52** |

The process started ~6 minutes **before** the config was written. LiteLLM reads
config once at startup, so the in-memory `files_config` for PID 534987 is still
`None`. A restart is the only remaining step.

**Operator command (agents are sandbox-blocked from running it):**

```bash
systemctl --user restart airlock
```

**Confirm it took effect:** the litellm PID changes and its start time is after
the config mtime:

```bash
ps -o pid,lstart -C litellm    # PID should no longer be 534987, start time > 18:10:40
```

## 4. Verified working recipe for the FathomDB Agent (after restart)

All calls go to the proxy on `:4000` with the Airlock master key. Pass
`custom_llm_provider=openai` explicitly. Although `openai` is LiteLLM's default,
being explicit ensures the provider resolves to the `files_settings` entry rather
than relying on the default (provider may be supplied as a form field or query
param). This recipe was independently confirmed working earlier in this work
(once `files_settings` is loaded): `/v1/files` (purpose=batch) → 200,
`/v1/batches` → 200 `validating`. OpenAI billing was also fixed during that work.

Note on the model field: with `custom_llm_provider=openai`, the input file is
uploaded straight to OpenAI, so the `model` in each JSONL line must be a real
OpenAI model id (e.g. `gpt-5.4-nano` — the upstream target that the proxy's
`gpt-5-nano` alias maps to in `config.yaml:50-52`), not an Airlock alias.

**Step 1 — upload the input file**

```bash
curl -s http://localhost:4000/v1/files \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -F purpose=batch \
  -F custom_llm_provider=openai \
  -F file=@batch_input.jsonl
# -> returns {"id": "file-...", ...}
```

Each line of `batch_input.jsonl`:

```json
{"custom_id":"req-1","method":"POST","url":"/v1/chat/completions","body":{"model":"gpt-5.4-nano","messages":[{"role":"user","content":"hello"}]}}
```

**Step 2 — create the batch**

```bash
curl -s http://localhost:4000/v1/batches \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input_file_id":"file-...","endpoint":"/v1/chat/completions","completion_window":"24h","custom_llm_provider":"openai"}'
# -> returns {"id":"batch_...","status":"validating", ...}
```

**Step 3 — poll**

```bash
curl -s "http://localhost:4000/v1/batches/batch_...?custom_llm_provider=openai" \
  -H "Authorization: Bearer $AIRLOCK_MASTER_KEY"
```

## 5. Scope and caveats

- This resolves **OpenAI** batch (and **vertex_ai**, which never needed
  `files_settings` — call it with `custom_llm_provider=vertex_ai`, using ADC +
  `GCS_BUCKET_NAME`, per the comment at `config.yaml:283-285`).
- **Still NOT available through the proxy:** Gemini-via-AI-Studio batch and
  Mistral batch. LiteLLM does not wire those providers for the batch/files path.
  The path to support them is the proxy-side gateway described in
  `dev/design-unified-batch-gateway.md` (see also `dev/design-aistudio-gemini-batch.md`
  and `dev/mistral-batch-findings.md`).
- **Standing caveat:** batch traffic bypasses Airlock's guardrails today. Files
  uploaded for batch and the batched completions are sent to the provider
  without going through the proxy's PII/guardrail middleware. Do not put
  data through batch that requires those protections until the unified batch
  gateway lands. See `dev/batch-guardrail-toggles-considerations.md`.

## 6. Sanity checks to run after restart (read-only)

curl may be sandbox-blocked for agents; if so, the **operator** should run these.

1. Proxy is up and serving model info:

   ```bash
   curl -s -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
     http://localhost:4000/v1/model/info | head
   ```

2. Tiny end-to-end batch probe with `custom_llm_provider=openai`: run Steps 1-2
   of the recipe above with a single-line JSONL; expect `/v1/files` → 200 with a
   `file-...` id, and `/v1/batches` → 200 with `status:"validating"`. If
   `/v1/files` still returns "files_settings is not set", the restart did not
   take effect — re-check the PID.

---

## TL;DR

- **Root cause:** `/v1/files` refused uploads because `files_settings` was unset, and OpenAI Batches needs that upload for its input file.
- **Fix status:** `files_settings` (provider `openai`) is now in `config.yaml:286-288` (commit `95b9bd8`).
- **Single remaining action:** operator runs `systemctl --user restart airlock` — the running proxy (PID 534987, started 18:04:36) predates the config edit (mtime 18:10:40), so it hasn't loaded the block; confirm via PID change.
- **Working recipe:** `POST /v1/files` (purpose=batch, custom_llm_provider=openai, multipart JSONL) → `POST /v1/batches` (input_file_id, endpoint=/v1/chat/completions, completion_window=24h, custom_llm_provider=openai) → `GET /v1/batches/{id}` to poll, all on :4000 with the master key.
