# Slice 2 — documentation lifecycle proposal

**Purpose:** enumerate the repository's documentation classes and propose their
lifecycle. This is a proposal only: no file is moved, deleted, renamed, or
edited by this slice.

## Inventory and proposed action

| Documentation set | Current location / enumeration | Proposed action | Rationale |
| --- | --- | --- | --- |
| Public landing and installation | `README.md`; `docs/index.md`; `docs/getting-started/*.md` | Keep | Supported user entry points; update during provider/embedding delivery. |
| Public guides | `docs/guide/*.md` (TUI, CLI, advisor, Fathom, guardrails, routing, rate limits, observability, provider quota, batch, MCP, paid services) | Keep | Published feature documentation governed by MkDocs navigation and contract tests. |
| Public operations/reference/architecture | `docs/operations.md`, `docs/troubleshooting.md`, `docs/reference/*.md`, `docs/architecture/*.md`, `docs/changelog.md` | Keep | Operator and API contract material; Slice 4 identifies required corrections. |
| Public-doc build configuration | `mkdocs.yml`; documentation-contract tests | Keep | Authoritative navigation and public-doc verification. |
| Product needs and requirements | `dev/user-needs.md`, `dev/requirements.md`, `dev/feature-*.md`, selected `dev/design-*.md` | Keep and revise through Slice 3 drafts | The two canonical documents need a current traceability refresh; do not delete their historical value. |
| Active release planning | `dev/plans/0.5.14-todo.md`, `dev/plans/0.5.14-openrouter-deepseek-design.md`, `dev/plans/0.5.14/` | Keep | Active 0.5.14 source of truth; new workspace separates preparation and delivery records. |
| Historical release plans | `dev/plans/0.4.0-plan.md` through `0.5.12-plan.md`, `dev/plans/archive/README.md` | Archive in place | Paths are evidence-linked. Labeling/indexing is safer than moving or deleting. |
| Historical orchestration evidence | `dev/plans/runs/*`, `dev/plans/prompts/*` | Archive in place | Immutable run/review records; retain stable paths and distinguish from live status. |
| Versioned design notes | `dev/notes/0.5.*-design.md`, `as-built-*`, `design-*`, `handoff-*`, audit and investigation notes | Archive in place after status annotation | Valuable engineering rationale, but not active requirements unless explicitly linked from an active plan. |
| Current technical design inputs | `dev/architecture.md`, `dev/architecture-overview.txt`, `dev/tui-design.md`, `dev/design-fathom-storage-model.md`, `dev/design-unified-batch-gateway.md` | Deprecate in place where duplicated; choose one canonical architecture source in Slice 4 | Multiple architecture summaries can drift. Keep source material until a successor and inbound-link review exist. |
| Development runbooks/harness material | `dev/agent-harness-*.md`, `dev/smoketest/*`, `dev/debugging/*`, `dev/dogfooding.md`, `dev/accessing-experiments.md` | Keep; archive superseded procedures in place | These are operational developer documentation; validate commands in Slice 5 before deprecation. |
| Research and externally volatile notes | `dev/competitor-tracker.md`, GTM briefs/role notes, model/provider research, `dev/substrate-decision-brief.md` | Keep local/untracked or archive in place; do not publish as product docs | They are decision inputs rather than stable product promises. The existing decision already keeps competitor tracking untracked. |
| Generated/binary diagnostic artifacts | root `oom-profile-*.bin`, generated run JSON, coverage/build output | Archive only when a linked investigation requires it; otherwise delete from the working tree, never treat as documentation | They are not program documentation and should not become accidental tracked release material. |

## Proposed cleanup rules for a later approved slice

1. **Keep** content only when it is canonical, active, or a referenced evidence
   record. Fix links and status labels rather than duplicating it.
2. **Deprecate in place** when a successor is named and inbound references have
   been redirected; retain a one-line replacement pointer.
3. **Archive in place** historical plans, prompts, run outputs, and design
   records. Do not bulk-move them because existing links are evidence.
4. **Delete** only untracked/generated artifacts or duplicate drafts after an
   owner confirms no evidence/reference need. Slice 6 must approve each
   deletion list explicitly.
