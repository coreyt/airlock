# Slice 50 — OpenRouter status

**Status:** complete; funded smoke passed.

## Ratified scope and delivery

DFR-28/DAC-28 are approved with an operator-only interpretation of “curated”:
Airlock ships no OpenRouter alias, fallback, or default enablement. An operator
uses the documented exact `model_list` recipe with `OPENROUTER_API_KEY` and the
stable OpenRouter API base. Slice 40 supplies the shared, configured-base
discovery, gateway attribution, and bounded provider-error behavior.

LiteLLM 1.94.1 recognizes `route`, `models`, and `transforms` as OpenRouter
routing controls. Slice 50 rejects them at the request root or inside
`extra_body`, after final alias/routing/failover resolution identifies
OpenRouter and before dispatch. The typed error reaches clients as an
OpenAI-shaped 400. Other providers and unrelated `extra_body` fields are not
changed by this narrow control.

Documentation states the immediate gateway boundary: `openrouter` never claims
an OpenRouter downstream host or control over upstream fallback, pricing,
availability, or retention.

## TDD, review, and verification

- RED: documentation/default-config/guardian/proxy-error contracts were added.
- GREEN: documented operator recipe, environment-template entry, served-provider
  wording, and narrow pre-dispatch validation were added.
- Independent review cycle 1 found missing actual-guardian and LiteLLM wire-path
  evidence. Cycle 2 approved the added final-provider and exact-400 tests.
- Local focused verification passed: `217 passed, 1 xpassed`; the reviewer’s
  overlapping focused set passed `226 passed, 1 xpassed`. Strict MkDocs build
  and `git diff --check` passed. No provider key was read or sent.

## Release closeout

After the full no-credit matrix is green, record one ordinary and one streaming
non-sensitive request through an operator-configured alias. Retain only alias,
timestamp, HTTP/stream outcome, and safe Airlock headers; never prompts,
responses, keys, or raw upstream headers.

**Funded smoke — 2026-08-12:** isolated-loopback ordinary and streaming calls
through temporary alias `smoke-openrouter` both returned HTTP 200 with
`X-Airlock-Served-By: openrouter` and
`X-Airlock-Mutations: fallbacks=suppressed`; the streaming response completed
its SSE sequence. The fixed non-sensitive prompt and all response content,
keys, and raw upstream headers were discarded. This was within the
operator-authorized $1 OpenRouter budget.
