# Slice 60 — DeepSeek design review

**Draft review outcome:** approve DFR-29/DAC-29 with one additional acceptance
criterion: the configured stable API base is mandatory, and no default alias is
shipped.

## Scope

An operator configures an exact `deepseek/<model>` alias with
`DEEPSEEK_API_KEY` and `https://api.deepseek.com`. Slice 40 provides optional
configured-base discovery, native attribution, and bounded provider failures.
This slice adds no adapter, parser, retry loop, user-identity mapping, or
default fallback.

## Architecture and TDD decision

Pinned LiteLLM drops non-function DeepSeek tools. Airlock’s final-provider
guardian seam validates `tools` only after alias/routing/failover determines
the provider is `deepseek`, and before dispatch. A function-tool list passes
unchanged; absent, malformed, or non-function tool items receive a typed
OpenAI-shaped 400. Tests cover the direct validator and final guardian path;
documentation provides the stable configuration and privacy boundary.

Funded closeout will later run one normal and one streaming non-sensitive call
through an operator-configured alias; no key is used for no-credit tests.
