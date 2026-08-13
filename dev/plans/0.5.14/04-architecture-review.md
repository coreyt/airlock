# Slice 4 — architecture review and code-alignment proposal

**Scope:** high-level review of the approved candidate work against the current
architecture. No code or architecture document changes are made by this record.

## Findings and proposed architecture changes

| Area | Current alignment evidence | Proposed change | Owning slice |
| --- | --- | --- | --- |
| Model capabilities | `airlock/capability.py` has one model-entry contract and its endpoint list presently covers chat/batch, not embeddings. | Add an explicit per-entry capability declaration; derive `/v1/models` capability output and embedding dispatch eligibility from it. Never infer embedding support from provider prefix alone. | 30 |
| Embedding policy/logging | Current guard/logging seams were built around chat request events. | Characterize the embedding payload at every guardian/recorder/projection seam; add adapters only where data shape differs. Apply the same auth, PII policy, redaction, and safe-error contract before dispatch. | 30, 10 |
| Provider discovery | `airlock/models_catalog.py` has provider-specific discovery and a generic OpenAI-compatible seam; the provider design identifies unsafe fixed-base/error-text behavior. | Add one reviewed configured-base validation/normalization path, no redirects, no key on unsafe/conflicting base, and bounded errors. OpenRouter/DeepSeek wrappers use it. | 40 |
| Provider attribution | Served provider is derived from LiteLLM model/provider information; classification lacks the planned `openrouter` gateway and `deepseek` native terms. | Extend the existing attribution classification, preserving the immediate `openrouter` gateway token and never guessing its downstream host. | 40, 50, 60 |
| Provider failure propagation | Request event, monitor, tracing, and enterprise projections can receive provider exception text through different paths. | Centralize a bounded sanitizer before artifact boundaries; preserve typed HTTP status for policy/circuit logic before replacement. | 40 |
| Provider transport | LiteLLM 1.94.1 supplies both provider adapters and request/stream parsing. | Do not add custom adapters or a second OpenAI-compatible transport. DeepSeek adds only narrow pre-dispatch non-function-tool validation if characterization confirms LiteLLM drops those tools. | 50, 60 |
| TUI lifecycle | The TUI app constructs background workers even when tests assert only composition/navigation. | Add a test-only lifecycle profile/fixture around real widget construction; preserve named production-worker integration tests. | 70 |
| TUI read model | TUI backlog needs current state that may otherwise be callback-driven. | Define bounded, pull/read snapshots or authenticated read endpoints with source and staleness; no UI subscription on the inference hot path. | 80, 90, 100 |
| Virtual keys | 0.6.0 separately plans keystore/identity. | Deferred to 0.5.15: treat it as a UI/control-plane adapter to an existing management seam. Do not create a second persistence/identity system. | 0.5.15 Slice 100 |
| FathomDB | FathomDB is an optional lazy request-analysis sink; the TUI and Advisor worker run in separate processes from the proxy. | Define a selectable backend contract with the proxy as sole engine owner. Route opted-in TUI/Advisor reads through loopback-only proxy-admin views; never open the shared DB from UI processes. Retain explicit source labels, JSONL fallback, and degraded/erasure states. | 110 |

## High-level code alignment verdict

The existing code is broadly aligned with the 0.5.14 direction: configuration
is model-list driven; `models_catalog`, `capability`, `transparency`, guardian,
callbacks, and TUI are separable seams; optional FathomDB already has dedicated
callback/query modules. It is **not yet aligned** with embedding support,
provider discovery safety/sanitized error propagation, new provider
classification, or bounded TUI read contracts. Those are implementation
requirements, not cosmetic documentation updates.

The canonical needs/requirements documents also lag current behavior: they
describe unconditional message/response logging and contain stale `/health`
wording. Slice 3's drafts resolve that mismatch; implementation must not claim
conformance until docs, tests, and code are updated together.

## Architectural guardrails for all delivery slices

1. Explicit configuration remains the authorization boundary; environment keys
   and catalog discovery do not expose models.
2. Secrets, raw provider exceptions, key material, prompts, and completions do
   not enter tracked configuration, tests, TUI snapshots, or safe artifacts.
3. Keep LiteLLM as provider transport/parser and preserve typed errors before
   sanitization.
4. New read/UI work is bounded and fails visibly; optional FathomDB never
   silently replaces the zero-infrastructure JSONL/in-memory path. The proxy
   alone owns its engine; a separate-process TUI or Advisor reaches it only via
   a loopback-only admin bridge.
5. The existing PII observe-only release gate and FathomDB/LiteLLM memory
   blocker are recorded constraints, not workarounds for this release.
