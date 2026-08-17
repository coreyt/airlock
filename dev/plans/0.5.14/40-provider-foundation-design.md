# Slice 40 — shared provider foundation design

**Status:** design ratified for TDD implementation. This slice is the required
foundation for Slices 50 (OpenRouter) and 60 (DeepSeek); it does not add either
provider to the default model list.

## Scope and requirements

- DFR-27/DAC-27: provider discovery is explicit-configuration only,
  informational, base-bound, no-redirect, and secret-safe.
- Provider attribution classifies the immediate LiteLLM token: `openrouter` is
  a gateway and `deepseek` is native. Airlock never guesses an OpenRouter
  downstream host.
- Provider failure data is reduced to a bounded provider, error type, and HTTP
  status record before it enters request events/projections, monitor state or
  logs, and tracing. Typed status inspection happens before sanitization, so
  429 handling remains intact.

## Design

1. Add one configured-base resolver in `models_catalog`. It reads only reviewed
   `model_list` entries for a provider, resolves an existing environment key,
   requires exactly one normalized HTTPS base, rejects credentials, query,
   fragments, `/models`, and conflicting bases, and derives the models URL from
   an API-root or `/chat/completions` base. It uses a no-redirect opener; an
   invalid provider skips discovery without blocking startup.
2. Add thin provider fetchers only after that resolver exists. They reuse the
   existing OpenAI-compatible JSON parser; they do not register a model,
   authorize it, or mutate routing.
3. Add a small provider-error summary helper with no free-form exception text.
   Request-event, monitor, tracing, and enterprise projection consume the
   summary while retaining existing typed rate-limit detection.
4. Extend served-backend classification only. Provider transport, streaming,
   client retry behavior, and default fallback policy stay in LiteLLM/existing
   Airlock seams.

## RED verification plan

- Configured-base tests: absent key/base, malformed/conflicting/unsafe base,
  no redirect/key leakage, exact slash-preserving ID normalization, and
  startup-best-effort behavior.
- Attribution tests for `openrouter` gateway and `deepseek` native.
- Failure-sanitization matrix for 401, 402, 429, 500, and 503, including a
  sentinel in exception text/metadata/user identity. It must be absent from all
  durable surfaces while a typed 429 still activates existing monitor behavior.

## Non-goals

No provider credentials in tracked files; no provider enabled merely by `.env`;
no custom provider transport/parser; no claim to propagate upstream
`Retry-After`; and no alteration to the existing separately governed general
request/response logging posture.
