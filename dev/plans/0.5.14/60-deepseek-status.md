# Slice 60 — DeepSeek status

**Status:** complete; funded smoke passed.

## Ratified scope and delivery

DFR-29/DAC-29 are approved. Airlock ships no DeepSeek alias, fallback, or
automatic enablement. Operators use the documented stable
`https://api.deepseek.com` API base and `DEEPSEEK_API_KEY` in a reviewed exact
`model_list` entry. Slice 40 supplies optional configured-base discovery, native
served-provider attribution, and bounded provider failure handling.

Pinned LiteLLM drops non-function DeepSeek tools. The guardian therefore checks
the final resolved provider before dispatch: function tool lists pass unchanged;
missing/malformed/non-function tool values fail as an OpenAI-shaped 400. The
implementation does not map Airlock client or authenticated identity data to
DeepSeek `user_id`.

## TDD, review, and verification

- RED: stable-endpoint docs, function-tool boundary, typed error, and default
  configuration contracts were added.
- GREEN: narrow final-provider validation, operator docs, and environment
  template entry were added without a provider adapter/parser/retry loop.
- Independent review cycle 1 found missing valid-tool, non-DeepSeek, LiteLLM
  wire-shape, and default-exposure evidence. Cycle 2 approved the complete
  matrix.
- Local focused verification passed: `229 passed, 1 xpassed`; the reviewer’s
  overlapping focused set passed `238 passed, 1 xpassed`. `git diff --check`
  passed. No provider key was read or sent.

## Release closeout

After the full no-credit matrix is green, record one ordinary and one streaming
non-sensitive request through an operator-configured alias. Retain only alias,
timestamp, HTTP/stream outcome, and safe Airlock headers; never prompts,
responses, keys, `user_id`, or raw upstream headers.

**Funded smoke — 2026-08-12:** isolated-loopback ordinary and streaming calls
through temporary alias `smoke-deepseek` both returned HTTP 200 with
`X-Airlock-Served-By: deepseek` and
`X-Airlock-Mutations: fallbacks=suppressed`; the streaming response completed
its SSE sequence. The fixed non-sensitive prompt and all response content,
keys, `user_id`, and raw upstream headers were discarded. This was within the
operator-authorized $1 DeepSeek budget.
