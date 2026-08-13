# Airlock 0.5.14 release workspace

This directory is the durable planning and delivery record for 0.5.14. It is
the detailed companion to [the release index](../0.5.14-todo.md). Historical
plans and run evidence retain their existing paths under `dev/plans/`.

## Release sequence

| Order | Record or delivery slice | Purpose | Status |
| --- | --- | --- | --- |
| 0 | [environment](00-environment.md) | Establish reproducible local, CI, and funded-smoke prerequisites. | proposal complete |
| 1 | [dependency sweep](01-dependencies.md) | Triage Dependabot and library changes. | proposal complete |
| 2 | [documentation review](02-documentation-cruft-proposal.md) | Classify documentation lifecycle; do not alter it. | proposal complete |
| 3 | [draft requirements](03-draft-requirements-and-allocation.md) | Draft needs, requirements, acceptance criteria, and allocations. | proposal complete |
| 4 | [architecture review](04-architecture-review.md) | Review proposed architecture and current high-level code alignment. | proposal complete |
| 5 | [verification review](05-verification-review.md) | Trace requirements to acceptance criteria and evidence. | proposal complete |
| 6 | [HITL release review](06-hitl-release-review.md) | Decide include, conditional, or postpone for every candidate. | approved |
| 10 | [Benchmark-safe logging profile](10-benchmark-safe-logging-status.md) | Redaction/retention proof and runbook. | complete |
| 20 | [FathomDB chat readiness](20-fathomdb-chat-status.md) | `gpt-4o-mini` aliases and chat/stream verification. | complete |
| 30 | [FathomDB embedding readiness](30-embeddings-status.md) | `text-embedding-3-small` and `/v1/embeddings`. | complete; funded smoke passed |
| 40 | [Shared provider foundation](40-provider-foundation-status.md) | Discovery safety, attribution, and sanitized provider failures. | complete; funded smoke belongs to provider slices |
| 50 | [OpenRouter](50-openrouter-status.md) | Operator-configured gateway recipe and routing-control boundary. | complete; funded smoke passed |
| 60 | [DeepSeek](60-deepseek-status.md) | Operator-configured stable endpoint and function-tool boundary. | complete; funded smoke passed |
| 70 | [TUI test lifecycle](70-tui-lifecycle-status.md) | Fast deterministic ordinary TUI tests. | complete |
| 80 | [TUI routing/client diagnostics](80-routing-client-diagnostics-status.md) | Classification and session-affinity views/actions. | complete |
| 90 | [TUI QoS/exporter health](90-qos-exporter-health-status.md) | Priority and telemetry operational views. | complete |
| 100 | Virtual-key management | Deferred to [0.5.15](../0.5.15-todo.md); draft package only. | postponed |
| 110 | [FathomDB operational reads](110-fathomdb-operational-reads-status.md) | Optional, proxy-owned operational backend. | complete |
| 120 | [Documentation release-index contract](120-documentation-contract-status.md) | Repair stale active-release assertion before release closeout. | complete |
| 130 | [Release closeout](130-release-closeout.md) | Local release matrix, artifact, version, and tag handoff. | ready to tag locally |

Feature slices use the existing `prompts/SLICE-TEMPLATE.md` workflow: review
their allocation, ratify or revise drafts, write a design memo, commit RED
tests, implement GREEN, obtain independent design/code review (three FIX cycles
maximum), verify, and write a slice status record under `dev/plans/runs/`.

No feature slice starts until the Slice 6 decision is recorded. A conditional
slice remains planned evidence, not release scope, until its listed dependency
and HITL decision are satisfied.
