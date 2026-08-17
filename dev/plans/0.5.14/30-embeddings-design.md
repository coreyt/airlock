# Slice 30 — embedding capability design

## Decision

Add explicit embedding-only model entries for `text-embedding-3-small` and
`openai/text-embedding-3-small`. A truthful capability record lists
`["embeddings"]`, not chat. LiteLLM remains the `/v1/embeddings` transport;
Airlock adds no provider client or response parser.

## Boundary

The fast guardian resolves embedding requests only against an explicitly
configured alias marked embedding-capable. It rejects a chat-only configured
alias, an unconfigured name, and `smart` before dispatch; it does not fuzzy
resolve, route, or fall back an embedding model. Ordinary authentication,
admission, provider protection, recorder, and attribution continue after that
validation.

`input` is the embedding text surface. Text extraction and the PII guard must
scan/redact string input values before downstream keyword/threat guards run.
Embedding requests retain no PII hydration map because embeddings have no
tool-call response to hydrate. The public request body is an explicit allowlist:
`model`, `input`, optional `user`, optional `dimensions` (integer 1–1536), and
optional `encoding_format` (`float` or `base64`). Proxy-created internal fields
are permitted only when they are safe to accept after the proxy adds them. An
early Airlock ASGI boundary validates the raw JSON *before* LiteLLM adds proxy
fields, so it can reliably distinguish a client option from a proxy-created
field. It covers LiteLLM's `/v1/embeddings`, `/embeddings`, engine, and Azure
deployment embedding routes. Direct-dispatch and header controls—including
`api_base`, `api_key`, provider/deployment fields, forwarding headers, and
client metadata—are rejected there; LiteLLM can otherwise honor body-supplied
values rather than overwrite them. Any other client option receives a clear 400
before dispatch; it must never be silently removed by `drop_params`.

## Verification

No-credit tests prove the config/template aliases, truthful capability record,
guardian acceptance/rejection and routing/failover suppression, option
pass-through/rejection, extraction/redaction of `aembedding` input, real
LiteLLM error translation, and existing model-info surface. A later manually
authorized smoke uses a non-sensitive batch input and records only alias,
outcome, and safe headers.
