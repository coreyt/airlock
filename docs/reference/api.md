# API reference

Airlock extends LiteLLM's OpenAI-compatible API. The running proxy is the
authoritative source for the endpoint schema because its configured models and
enabled features determine the live surface.

## Live schema and interactive documentation

With a proxy running, open:

- [`/airlock/docs`](/airlock/docs) for Airlock's conceptual API notes and the
  interactive schema.
- [`/openapi.json`](/openapi.json) for the OpenAPI document consumed by client
  tooling.

For a remote proxy, replace the relative URL with that proxy's base URL, for
example `https://airlock.example.com/airlock/docs`.

## Airlock-specific contracts

### Embeddings

Airlock forwards OpenAI-compatible `POST /v1/embeddings` requests through the
normal authenticated proxy path for explicitly configured embedding aliases.
In the shipped benchmark configuration, use `text-embedding-3-small` or
`openai/text-embedding-3-small`. Chat-only aliases, `smart`, fuzzy aliases, and
unconfigured models receive an OpenAI-shaped `400` with
`error.code=model_endpoint_not_supported`; Airlock does not route or fall back
an embedding request. For `text-embedding-3-small`, `dimensions` must be an
integer from 1 through 1536 and `encoding_format` must be `float` or `base64`.
Completion-only controls are rejected before dispatch with
`error.code=invalid_embedding_option`.

The live schema includes Airlock additions to the standard endpoints, including
client attribution, supported request metadata, 429 responses, and response
headers. Use these static pages for the behavior behind those fields:

- [Response headers](response-headers.md)
- [Routing](../guide/routing.md)
- [Rate limiting and circuit breaker](../guide/rate-limiting.md)
- [Guardrails](../guide/guardrails.md)
- [Admin API](../guide/admin-api.md)

## Source of truth

The OpenAPI document is authoritative for request and response shape. The
versioned configuration template is authoritative for aliases and enabled
features. This reference and the guide pages explain behavior, operational
constraints, and examples that do not fit cleanly in a schema.
