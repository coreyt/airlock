# Slice 30 — configured credential without enabled alias warning

**Status:** implemented locally (2026-08-16); verification and disposition are
recorded in `30-provider-credential-warning-status.md`.

## Prior-slice closure and B3 resolution

Slice 20 is implemented locally and its focused verification is green; its
environment-limited ordinary-suite rerun is recorded for release closeout and
does not affect this independent startup-validation slice.

Slice 30 was conditional on B3: an authoritative enabled-model source and a
finite credential taxonomy. The current launcher reads the root YAML before
LiteLLM applies its direct `include:` entries, so using that raw `model_list`
would produce false warnings for included aliases. B3 is resolved narrowly:
this slice will derive *only* the effective `model_list` using the installed
LiteLLM one-level include semantics (listed direct includes, top-level list
extension, other top-level replacement, no recursion). It will not introduce a
generic configuration loader, alter the config passed to LiteLLM, or reuse a
recursive resolution path for an independent policy decision.

The finite registry is limited to documented provider credentials: Anthropic
(`ANTHROPIC_API_KEY`), OpenAI (`OPENAI_API_KEY`), Gemini (`GOOGLE_AISTUDIO_API_KEY`),
Mistral (`MISTRAL_API_KEY`), OpenRouter (`OPENROUTER_API_KEY`), DeepSeek
(`DEEPSEEK_API_KEY`), Perplexity (`PERPLEXITY_API_KEY`), Tavily
(`TAVILY_API_KEY`), and local vLLM (`VLLM_API_KEY`). vLLM is OpenAI-compatible,
so its enabled status is the reviewed explicit `backend: vllm` / credential-ref
mapping rather than a generic OpenAI provider token. It is not a scan of
ambient `*_API_KEY` variables. Nonblank values count as configured; values are
never returned, logged, measured, or placed in exceptions.

## Ratified contract and design

DFR-36/DAC-36 is ratified: after `.env` loading and before optional discovery
or LiteLLM launch, Airlock emits exactly one redacted local warning per
recognised provider that has a nonblank recognised environment credential and
zero aliases in the effective model list classified by `airlock_provider_for`.
The stable event is
`airlock.startup.provider_credential_without_alias` with only provider,
`credential_configured=true`, `configured_alias_count=0`, and
`source=startup_validation`. It is default-on, advisory, local/no-network, and
does not change startup status, routing, discovery, config, or the inference
path. Provider/admission errors and Admin/TUI state are out of scope.

`airlock/startup_validation.py` will contain pure registry, include/model-list
projection, evaluation, and redacted emission functions with injected
environment/path inputs. `proxy.main()` will invoke the adapter once after its
normal configuration load and before discovery. No warning state is retained
for a later Admin/TUI surface; that is Slice 40 work.

## Threat model, blast radius, rollback

The exposure boundary is startup output. The event schema is fixed and tests
will use sentinel values to prove neither values nor environment-variable names
appear. The resolver opens only explicit config paths already supplied to
LiteLLM; it makes no network/provider request and does not modify the runtime
configuration. A parser failure produces a bounded local validation warning and
does not invent an enabled alias. Rollback is removal of the new startup call;
it has no state, migration, dependency, or configuration cleanup.

## TDD and verification

1. RED tests first: registry/presence, one-level effective aliases including
   direct include order/list extension/nested-ignore, redaction, deduplication,
   no-network/no-mutation, and proxy startup order.
2. Minimal pure implementation and the one startup adapter call.
3. Run focused startup/proxy/capability tests, lint/format, `make verify`, and
   strict docs build. Review the changed seams and record residual risk/status.
