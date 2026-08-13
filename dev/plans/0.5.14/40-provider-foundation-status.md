# Slice 40 — shared provider foundation status

**Status:** implementation complete and locally verified; independent code review
approved after two cycles. This foundation does not enable a provider or add a
default model.

## Delivered

- Discovery is available only when an existing `model_list` entry explicitly
  configures an OpenRouter or DeepSeek model, an HTTPS `api_base`, and a
  resolvable key. It derives the same-origin `/models` endpoint, rejects unsafe
  or conflicting bases, refuses redirects, and treats failure as best-effort.
- Discovered model IDs retain the provider and any slash-separated upstream
  model path. Discovery neither registers aliases nor changes routing.
- Served-backend attribution identifies OpenRouter as the immediate gateway and
  DeepSeek as the native provider.
- Provider exceptions bearing LiteLLM's provider marker are reduced to provider,
  exception type, and HTTP status before request events and every projection,
  monitor rate-limit state/logging, or OpenTelemetry tracing receive them. The
  existing raw-text handling for Airlock-local validation/evaluation errors is
  unchanged. Typed 429 detection still occurs before the bounded reason is used.

## Verification

Local focused verification passed on 2026-08-12:

```
UV_CACHE_DIR=/tmp/airlock-uv-cache uv run --extra test pytest \
  tests/test_models_catalog.py tests/test_provider_errors.py \
  tests/test_request_event.py tests/test_projections_equiv.py \
  tests/test_tracing.py tests/test_fast_monitor.py \
  tests/test_served_attribution.py -q
# 197 passed
```

The added tests cover unsafe and conflicting configured bases, no-redirect
opener use, model-ID normalization, no provider discovery without an explicit
configuration/key, OpenRouter/DeepSeek attribution, and a 401/402/429/500/503
sentinel matrix across events, enterprise/S3/SQL projections, monitor handling,
and tracing. Independent review cycle 1 also closed two safety gaps: generic
provider configuration cannot impersonate OpenRouter or DeepSeek, and an
unrecognized provider marker is not emitted. Cycle 2 approved the correction.
No funded-provider call was made.

## Handoff

Slices 50 and 60 may now use these primitives. Their individual configuration,
provider-specific request behavior, and funded smoke verification remain their
own work; this slice supplies no provider credentials or model aliases.
