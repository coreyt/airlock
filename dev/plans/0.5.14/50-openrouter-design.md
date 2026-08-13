# Slice 50 — OpenRouter design review

**Draft review outcome:** approve DFR-28/DAC-28 with one clarification: this
slice adds no built-in alias. A provider exists only in an operator-reviewed
`model_list` deployment configuration; the shipped root and template configs
must remain free of OpenRouter entries and fallback references.

## Allocated need, requirements, and acceptance criteria

- **Need:** operators want access to OpenRouter's model catalog without
  Airlock overstating control over OpenRouter's downstream routing, privacy,
  pricing, retention, or fallback choices.
- **DFR-28 (ratified):** Airlock documents a curated, explicit OpenRouter
  deployment recipe using a provider-prefixed alias and stable API base. It
  preserves normal Airlock policy, rejects client gateway-routing overrides,
  and reports only the immediate `openrouter` gateway.
- **DAC-28 (adjusted):** no default model/configuration or fallback is added;
  an explicit configuration is correctly recognized by the alias/capability
  contract; informational discovery preserves slash-containing IDs and cannot
  authorize a model; documentation names the gateway/privacy boundary; focused
  no-credit checks and independent review pass. A funded normal and streaming
  smoke remains release closeout evidence.

## Architecture and implementation decision

Slice 40 is the sole discovery, attribution, and failure-sanitization seam.
Slice 50 adds no adapter, transport, model registry, fallback, or response
metadata parser. The implementation is configuration documentation plus
contract tests plus one narrow guardian validation: an operator adds an exact alias with
`openrouter/<publisher>/<model>`, an environment reference, and
`https://openrouter.ai/api/v1`. Startup discovery is optional and informational
only; it reports the gateway rather than a guessed downstream provider.

## TDD plan

RED tests first require the documented recipe, explicit non-default posture,
provider capability/alias resolution, and gateway boundary wording. GREEN adds
the documentation/environment-template entries and narrow pre-dispatch
validation needed to satisfy those contracts. No live credential is read or
sent.
