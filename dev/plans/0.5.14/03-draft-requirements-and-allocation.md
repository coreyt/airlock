# Slice 3 — draft needs, requirements, acceptance criteria, and allocation

**Status:** Slice 3 started as draft only. After Slice 6 approval, slices 10–90
and 120 ratified their allocated drafts and wrote their completed requirements
to `dev/user-needs.md` / `dev/requirements.md`; Slice 100 remains deferred and
Slice 110 remains conditional.

## Draft additions and revisions

| Draft | Proposed user need / requirement / acceptance criterion | Allocated slice |
| --- | --- | --- |
| DUN-14 | Benchmark operators need Airlock to serve a configured low-cost chat model and embedding model with the same access, policy, and audit boundaries as normal inference. | 20, 30 |
| DFR-24 | Airlock shall expose configured OpenAI chat aliases for `gpt-4o-mini` only through explicit reviewed model-list entries; normal and streaming requests retain ordinary policy and attribution. | 20 |
| DAC-24 | Configuration/template consistency tests prove aliases; mocked chat and stream tests prove authentication, guard traversal, resolved alias, and served-provider attribution; a funded non-sensitive smoke is recorded separately. | 20 |
| DFR-25 | Airlock shall support `/v1/embeddings` only for explicitly configured, embedding-capable aliases and shall preserve batch input plus supported `dimensions`/`encoding_format` options through policy and observability paths. | 30 |
| DAC-25 | Tests prove a configured `text-embedding-3-small` request succeeds through the endpoint, correct model capability advertises embeddings, an unconfigured alias is rejected, and unsupported options fail clearly without upstream dispatch. | 30 |
| DFR-26 | Operators shall have a benchmark logging profile that redacts request/response content in enterprise JSONL and disables unredacted SQL/Fathom raw-content retention paths. | 10 |
| DAC-26 | A sentinel request and response are absent from persisted artifacts while redacted fields are present; profile documentation names the settings and the no-model-call health probe. | 10 |
| DFR-27 | Provider configuration shall be explicit, discovery informational and base-bound, provider errors sanitized, and served-provider attribution truthful. | 40 |
| DAC-27 | No-key/no-model/no-redirect/conflicting-base tests pass; provider error sentinels never enter logs/events/traces; 401/402/429/500/503 classification and gateway/native attribution are characterized. | 40 |
| DFR-28 | OpenRouter shall be an operator-configured gateway integration: optional discovery, chat/stream verification, and operator-selected routing/privacy controls. Client OpenRouter routing overrides are rejected; Airlock never claims downstream-provider control. | 50 |
| DAC-28 | Mock catalog IDs normalize exactly, no catalog result authorizes a model, root/nested `route`/`models`/`transforms` fail as OpenAI-shaped pre-dispatch 400s, stream/error boundaries are safe, and funded smoke covers one configured alias. | 50 |
| DFR-29 | DeepSeek shall use the explicit stable API base, curated aliases, optional discovery, and OpenAI function tools only; Airlock shall not forward client identity as provider `user_id`. | 60 |
| DAC-29 | Mock discovery, standard/stream success, final-provider function-tool pass-through, non-function OpenAI-shaped 400, default-free config, and typed provider-error tests pass; funded smoke covers one configured alias. | 60 |
| DFR-30 | Ordinary TUI tests shall render the production widget tree without unrelated background workers; named integration tests retain lifecycle/cancellation coverage. | 70 |
| DAC-30 | Duration evidence improves the TUI tail and integration tests retain shutdown, stale-callback, JSONL, and MCP-worker coverage. | 70 |
| DFR-31 | Authenticated operators shall receive bounded, source-labeled views of routing classification, session affinity, QoS priority, and exporter health without adding inference-path dependencies. | 80, 90 |
| DAC-31 | Each view/action has unavailable/stale/source and secret-safety tests; session-pin break is authenticated and auditable. | 80, 90 |
| DFR-32 | Deferred to 0.5.15: administrators shall manage masked virtual keys, create with one-time reveal, and revoke through an explicit authenticated seam. | 0.5.15 Slice 100 |
| DAC-32 | Deferred to 0.5.15: creation/reveal/revocation, no-secret-in-snapshot/log/read, policy/spend display, and unauthorized-action tests pass. | 0.5.15 Slice 100 |
| DFR-33 | Conditional: operators may select FathomDB for specified operational reads while JSONL/in-memory fallback, source labeling, bounded results, single-owner lifecycle, and incomplete-erasure honesty remain intact. | 110 |
| DAC-33 | Enabled/disabled/unavailable/erasure integration tests prove source and degraded-result behavior. | 110 |

## Ratification record

| Drafts | Decision | Durable implementation/status record |
| --- | --- | --- |
| DUN-14, DFR/DAC-24–26 | Ratified and delivered | Slices 10, 20, 30 |
| DFR/DAC-27 | Ratified and delivered | Slice 40 |
| DFR/DAC-28–29 | Ratified and delivered | Slices 50, 60 |
| DFR/DAC-30 | Ratified and delivered | Slice 70 |
| DFR/DAC-31 (routing/affinity) | Ratified as 31a and delivered | Slice 80 |
| DFR/DAC-31 (QoS/telemetry) | Ratified as 31b and delivered | Slice 90 |
| DFR/DAC-32 | Rejected for 0.5.14; retained for 0.5.15 | 0.5.15 Slice 100 |
| DFR/DAC-33 | Ratified, implemented, and independently reviewed in Slice 110. | Slice 110 |

## Draft CRUD actions to canonical documentation

| Canonical artifact | Draft action | Conditions |
| --- | --- | --- |
| `dev/user-needs.md` | Add DUN-14; clarify that logging can be configured to redact content, so comprehensive audit logging is not an unconditional raw-content guarantee. | Slice 10 approval. |
| `dev/requirements.md` | Add DFR-24 through DFR-33 and revise stale health wording to `/health/liveliness`. | Ratify only approved feature drafts. |
| Provider design | Keep the detailed OpenRouter/DeepSeek design as the authority for DFR-27–29; add a supersession pointer if future feature-slice design changes it. | Slice 40–60 reviews. |
| TUI backlog | Split existing issue statements into DFR-30–32 and retain issue links/evidence. | Slice 70–100 reviews. |
| Fathom architecture/ops docs | Add a source-of-truth and failure-mode contract only if DFR-33 is approved. | Slice 110 review. |

## Architecture draft allocations

- Slice 30 owns the capability contract and embedding request seam; it must not
  make `chat` aliases appear embedding-capable by inference.
- Slice 40 owns cross-provider discovery/error/attribution primitives. Slices
  50 and 60 must consume those primitives rather than add parallel provider
  transports or logging behavior.
- Slices 80–100 own read/management seams but may not subscribe UI logic to
  request callbacks or persist key secrets.
- Slice 100 is deferred to 0.5.15. It must reconcile with 0.6.0 tenant-key/
  keystore work before design; it may deliver a thin existing-key operator
  surface, but not invent a competing durable credential store.
