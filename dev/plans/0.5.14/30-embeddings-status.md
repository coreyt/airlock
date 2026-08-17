# Slice 30 — FathomDB embedding readiness status

**Status:** complete; funded smoke passed.

## Ratified scope

- DFR-25/DAC-25: serve `/v1/embeddings` only through the explicit
  `text-embedding-3-small` and `openai/text-embedding-3-small` aliases.
- Preserve strings and string batches, use normal Airlock authentication,
  policy, redaction, attribution, and observability seams, and advertise the
  alias as embedding-only.
- Pass `dimensions` (integer 1–1536) and `encoding_format` (`float` or
  `base64`) through unchanged. Reject all other client options before dispatch;
  never silently drop them.

## Design and review outcome

The implementation uses the existing LiteLLM transport. An explicit
`airlock_embeddings` marker supplies the single capability source of truth.
The guardian performs exact configured-alias lookup only and treats every
embedding request as pinned: client routing preferences, smart routing,
circuit-failover substitution, LiteLLM fallback, and retries cannot change the
validated alias.

Input text passes through Airlock PII redaction before downstream checks; no
hydration map is retained because embeddings have no tool-call response. The
validation has two layers: an authenticated endpoint wrapper validates raw
client JSON after LiteLLM has authenticated and cached it, but before the
embedding endpoint or proxy augmentation run; the guardian validates the
augmented payload. This rejects direct dispatch and header controls before
LiteLLM can honor body-supplied values without pre-auth body buffering. Unknown
client fields fail with an OpenAI-shaped 400 and `invalid_embedding_option`
before transport.

An independent `gpt-5.6-terra` high-reasoning reviewer completed the original
three FIX cycles, then the owner authorized two additional as-needed cycles.
The reviews drove correction of routing/failover bypass, LiteLLM's internal
error translation, incomplete option rejection, missing `aembedding` policy
evidence, proxy-created fields, and client-controlled direct-dispatch/header
controls. The fifth and final authorized review found alternate endpoint
coverage missing. The owner subsequently authorized completion of the slice;
the route-coverage correction then exposed a pre-auth body-buffering problem.
The final review approved the corrected authenticated endpoint wrapper: it
preserves LiteLLM authentication before validation, covers all four embedding
routes, and retains pinned exact-alias handling.

## TDD and verification evidence

| Phase | Command / result |
| --- | --- |
| RED | Focused capability/extraction tests initially failed because `is_embedding_call` did not exist. |
| GREEN | Capability, extraction, config, guardian, proxy-error, and legacy capability-compatibility subset passed: `282 passed, 1 xpassed`. |
| Review fixes | Embedding-focused guardian/proxy tests passed: `13 passed, 54 deselected`; PII embedding helper/hook tests passed: `2 passed`. |
| Ingress fix | Raw-body boundary, identity precedence, guardian, proxy-error, and bootstrap tests passed: `77 passed, 54 deselected`. |
| Auth-boundary fix | Authenticated wrapper, proxy bootstrap, guardian, proxy-error, and identity tests passed: `136 passed`. |
| Final independent review | Approved; no correctness or security blocker. |
| Quality | Ruff check/format passed for all Slice 30 source/tests; `mkdocs build --strict` passed. |
| Integrity | `git diff --check` passed. |

The broad PII module command did not return a terminal summary in this sandbox
after starting its expensive suite; its two changed embedding tests passed
individually with deterministic stubbed redaction. This is a verification
environment limitation, not a release claim; rerun the full PII suite in CI or
a writable home-cache environment before release closeout.

## Release-closeout evidence

After all no-credit release tests are green, an operator may run one
non-sensitive, authenticated embedding smoke. Record only the requested
alias, time, HTTP outcome, and safe Airlock headers. Do not record keys, input,
vectors, request bodies, provider headers, or raw response payloads.

**Funded smoke — 2026-08-12:** one isolated-loopback request to
`text-embedding-3-small` returned HTTP 200 with
`X-Airlock-Served-By: openai` and `X-Airlock-Mutations: fallbacks=suppressed`.
The request used the fixed non-sensitive input `smoke`; no vector, key, request
body, raw response, or provider header was retained. This was within the
operator-authorized $1 embedding budget.
