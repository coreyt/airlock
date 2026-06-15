# Note: Add a third local vLLM model alias — `qwen36-27b-vllm`

**Date:** 2026-06-15
**Status:** Proposed (config-only change)
**Scope:** `config.yaml` `model_list`

## Summary

Add a model alias `qwen36-27b-vllm` to the local vLLM host so it can be
addressed as a first-class Airlock model. vLLM is already integrated as a
generic OpenAI-compatible backend (`openai/<model-id>` + `api_base`), so this is
a pure config addition — no code change.

## Context

The local vLLM host (`http://192.168.1.45:8000/v1`) already backs four aliases
in `config.yaml:252-285`: `gemma-4`, `kimi-dev`, `qwen3-32b`, and `qwen3.6-27b`.
Only one model is loaded on the host at a time; multiple aliases pointing at the
same `api_base` is the established pattern. The `local_vllm_router` guardrail
validates at call time that the requested alias is the one currently loaded
(queries `{api_base}/models`, ~5s cache) and returns an actionable error
otherwise.

Note: an alias `qwen3.6-27b` (config.yaml:281) already targets this model. This
note adds an explicitly-named `-vllm`-suffixed alias (matching a naming
convention that disambiguates the local-vLLM path from any same-family hosted
provider). If `qwen3.6-27b` is intended to be renamed rather than supplemented,
replace it instead of adding a duplicate.

## Change

Add to `model_list` in `config.yaml`, alongside the other local vLLM entries:

```yaml
  # --- Local vLLM (Qwen3.6 27B AWQ-INT4) — explicit -vllm alias ---
  - model_name: qwen36-27b-vllm
    litellm_params:
      model: openai/qwen3.6-27b          # must match the id vLLM serves (/v1/models)
      api_base: http://192.168.1.45:8000/v1
      api_key: os.environ/VLLM_API_KEY
```

## Verification checklist

- [ ] `model:` id (`qwen3.6-27b`) matches exactly what the vLLM server reports at
      `GET {api_base}/models` — the `openai/` prefix is stripped before forwarding
      (`local_vllm_router.py:76`), so the remainder must be the served id.
- [ ] If this model needs reasoning-block stripping (like `kimi-dev`), add the
      alias to `AIRLOCK_REASONING_STRIP_MODELS`; otherwise no extra config.
- [ ] With the model loaded on the host, a chat completion to `qwen36-27b-vllm`
      succeeds; with a *different* model loaded, the `local_vllm_router` guardrail
      returns "configured but not currently loaded".
- [ ] `AIRLOCK_LOCAL_VLLM_BASE_URL` matches this `api_base` so the router treats
      the alias as local.

## Out of scope

- Batch mode for this alias — the Airlock Batch Gateway does not yet have a vLLM
  backend. See [note-add-vllm-batch-backend](note-add-vllm-batch-backend.md).
