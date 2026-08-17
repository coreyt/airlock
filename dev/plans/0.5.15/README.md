# Airlock 0.5.15 — Slice plan

This is the canonical 0.5.15 release workspace index.

**Status:** implementation and non-publishing release closeout complete. Slices
0–6 are complete planning/HITL records. Delivered slices 10, 20, 30, 40, 50,
70, 71, 80, and 90 have independent review and exact-head CI evidence. Slice
60 is deferred to 0.6.0. No release tag or registry publication is part of this
release workspace; a later owner-authorized tag remains a separate action.

**Post-planning guard:** do not begin a feature/function slice merely because it
is listed or included. First recheck the prior slice closure, update the slice
plan for changes since it was drafted, and satisfy its stated security,
architecture, verification, and external-authority gates.

## Release objective

Improve Airlock's operator and security control plane while preserving the
inference hot path, secret boundaries, per-instance Admin authorization, and
external deployment/configuration ownership. The release must distinguish work
that is accepted for investigation from work that is approved for delivery.

## Scope inventory

| Candidate | Current evidence | Initial allocation |
| --- | --- | --- |
| Gitleaks local and CI secret scanning | Implemented and externally enforced | Slice 10 |
| Typed Fast Guardian threat-backoff HTTP 429 | Implemented and exact-head CI verified | Slice 20 |
| Configured-provider credential with no enabled alias warning | Implemented and exact-head CI verified | Slice 30 |
| Read-only provider/configuration visibility in Admin/TUI | Implemented; CRUD remains out of scope | Slice 40 |
| Secure host-console TUI administration for containers | Implemented as default-off host-console profile | Slice 50 |
| Airlock-native virtual-key management | **Deferred to 0.6.0**; depends on the native identity/keystore foundation | Slice 60 (0.6.0) |
| Multi-instance TUI administration | Implemented as a bounded same-host read-only view | Slice 70 |
| Slice 70 live container topology verification | Complete; two disposable TLS container targets verified locally and in CI | Slice 71 |
| CI review and improvement | Complete; immutable workflow pins, independent checks, least privilege, and failure diagnostics | Slice 80 |
| Release closeout | Complete; version/readiness and exact-head CI/protection evidence recorded, without publication | Slice 90 |

Claude Code subscription-preserving pass-through is explicitly out of scope for
0.5.15. The even-minor roadmap migration is planning/documentation work, not a
0.5.15 product feature; Slice 2 must classify it without executing it.

## Architecture invariants

Every slice and draft must preserve these rules:

1. No Admin/TUI, warning, or management addition may subscribe to or contend
   with the inference hot path.
2. Provider and virtual-key secrets never appear in logs, API responses, TUI
   snapshots, test evidence, or documentation. Configuration views expose only
   bounded, source-labelled, redacted state.
3. Docker bridge reachability, SSH, Docker sockets, systemd, and Kubernetes
   access are not operator identity or TUI authority. Every instance remains an
   Admin policy-enforcement point.
4. Provider policy remains reviewed YAML plus restart unless a later owner
   decision establishes durable configuration ownership, validation, atomicity,
   rollback, audit, and secret-manager semantics.
5. Fleet management is a named non-secret inventory plus authenticated Admin API
   fan-out. It must not become a competing desired-state, host-control, or
   provider-configuration authority.
6. Virtual-key persistence, encryption, identity, and authorization must reuse
   the ratified 0.6.0 keystore contract. No UI-owned secret store, master-key
   reuse, or LiteLLM key CRUD substitute is permitted.

## Slices 0–6: discovery, proposals, and HITL

These slices write durable records under `dev/plans/0.5.15/`. They do not change
product behavior, requirements, architecture, test suites, deployment
configuration, dependencies, documentation lifecycle, or GitHub settings.

### Slice 0 — Environment and execution readiness

**Purpose:** identify the smallest reproducible environment and any required
environment changes before feature work.

**Plan:** inventory the dedicated 0.5.15 worktree/branch, Python/uv/lock state,
test extras, Docker availability, Admin/TUI test topology, configuration fixture
handling, and Gitleaks/pre-commit/CI tooling. Identify which tests are offline,
funded, or require explicit operator authorization. Record existing uncommitted
Gitleaks controls as a worktree baseline, not as released behavior.

**Durable output:** `dev/plans/0.5.15/00-environment.md` with commands, observed
versions, safe test boundaries, required changes, and owners. No setup change is
made in this slice.

### Slice 1 — Dependabot and library sweep

**Purpose:** identify dependency/security work that could affect 0.5.15.

**Plan:** review Dependabot alerts/PRs, locked direct and transitive
dependencies, EOL/security advisories, and libraries proposed by existing
designs. Assess compatibility, license, test/upgrade cost, supply-chain
provenance, and whether each item belongs in this release. Include Gitleaks
Action/Gitleaks binary pin maintenance and existing LiteLLM/FathomDB constraints.

**Durable output:** `dev/plans/0.5.15/01-dependencies.md`, recording each item as
keep, upgrade proposal, defer, reject, or external monitoring. No dependency,
lockfile, workflow version, or package metadata is changed in this slice.

### Slice 2 — Repository-wide cruft review

**Purpose:** classify documentation and engineering artifacts without altering
them.

**Plan:** enumerate and classify, at minimum:

- program/release documentation and changelogs;
- `dev/` needs, requirements, acceptance criteria, architecture, code/test
  notes, designs, plans, run evidence, and intermediate material;
- developer documentation, agent notes, prompts, examples, and templates;
- public README, guides, reference pages, deployment documentation, and API
  documentation;
- obsolete release/version references, duplicate authorities, generated
  artifacts, and stale code/test comments discovered during review.

For each item or coherent group, propose exactly one disposition: **keep**,
**deprecate in place**, **archive**, or **delete**. State authority, consumer,
risk, replacement/link needed, and whether the proposed action belongs in
0.5.15 or the unallocated backlog.

**Durable output:** `dev/plans/0.5.15/02-cruft-proposal.md`. This is a proposal only:
no document, code, test, design, or public page is moved, edited, archived, or
deleted in Slice 2.

### Slice 3 — Draft needs, requirements, acceptance criteria, and allocation

**Purpose:** reconcile the release candidates into draft product contracts and
allocate each draft item to a future feature slice.

**Plan:** inspect existing `dev/user-needs.md`, `dev/requirements.md`, test
contracts, acceptance criteria, and the detailed 0.5.15 designs. Propose CRUD
changes as drafts only: create, rename, revise, supersede, or delete proposed
User Needs, Requirements, and Acceptance Criteria. Each draft must name its
source, rationale, security/privacy impact, and owning feature slice.

The draft allocation must at least cover Gitleaks, threat backoff, provider
warning, read-only provider visibility, host-console TUI, virtual keys, and
fleet TUI. It must label virtual-key and fleet requirements conditional, and
must keep provider CRUD and Claude subscription pass-through out of the delivery
allocation unless separately approved.

**Durable output:** `dev/plans/0.5.15/03-draft-requirements-and-allocation.md` with a
draft-to-slice matrix. Do not mutate canonical needs/requirements/acceptance
criteria in Slice 3.

### Slice 4 — Architecture review and high-level code alignment

**Purpose:** propose architecture changes before implementation and determine
whether the current code broadly aligns with the intended boundaries.

**Plan:** use Slices 0–3 and existing design/research evidence to review:

- inference-path isolation and import boundaries;
- configuration, credential, identity, authorization, persistence, and audit
  ownership;
- Admin/TUI data freshness, redaction, capability, topology, and deployment
  boundaries;
- Gitleaks CI's GitHub token, action, baseline, and branch-protection boundary;
- dependencies on the 0.6.0 keystore contract and external desired-state
  systems.

Then perform a deliberately high-level code review of the relevant seams to
identify alignment, drift, missing seams, and code-versus-architecture changes.
No code change is made.

**Durable output:** `dev/plans/0.5.15/04-architecture-review.md`, including proposed
architecture/code changes, alignment table, blast radius, and rejected
alternatives.

### Slice 5 — Verification adequacy review

**Purpose:** determine whether the repository can prove the release contracts.

**Plan:** trace every draft requirement to acceptance criteria and candidate
tests. Review whether tests adequately cover Airlock's product goals and known
critical paths: inference behavior, response/error contracts, authorization and
negative access, secret redaction, configuration/restart behavior, TUI/Admin
data seams, container/native topology, CI/release controls, and failure/rollback
paths. Classify unit, integration, topology, live/funded, regression, and manual
evidence; identify gaps and test-fixture safety needs.

**Durable output:** `dev/plans/0.5.15/05-verification-review.md`, with a
requirement-to-acceptance-to-test matrix and proposed test work allocated to the
owning feature slice. No test/code change is made.

### Slice 6 — Release review and HITL decision

**Purpose:** collect Slices 0–5 into a concise owner decision record.

**Plan:** enumerate every proposed product, security, dependency,
documentation-lifecycle, requirement, architecture, and verification item.
Score each with **understood** and **risk** on a 1–4 scale (4 is highest), and
with effort (`XS`, `S`, `M`, `L`, `XL`). Recommend **include**, **conditional**,
or **postpone**, with prerequisites and owner decisions visible.

Slice 6 must explicitly decide the included feature slices, conditional slices,
documentation/dependency proposals, Gitleaks external enforcement, provider
CRUD posture, host-console topology, virtual-key 0.6.0 dependency, and fleet
authority boundary.

**Durable output:** `dev/plans/0.5.15/06-hitl-release-review.md`, containing the
scorecard, recommendation, recorded owner response, and exact start order.

**Hard stop:** present Slice 6 to HITL and stop. Do not start Slice 10 until the
owner has recorded the decision and all prerequisites for the first included
slice are satisfied.

## Feature/function slices — planned only after Slice 6

Each future slice follows the mandatory lifecycle below; the slice names do not
authorize work before Slice 6.

| Slice | Candidate scope | Initial condition |
| --- | --- | --- |
| 10 | Gitleaks local/CI controls, baseline, ownership, and GitHub enforcement | Complete; enforced on `main`. |
| 20 | Typed Fast Guardian threat-backoff exception and HTTP 429 contract | Complete; typed/redacted 429 contract verified. |
| 30 | Redacted startup warning for configured credential/no enabled alias | Complete; finite taxonomy and redaction contract verified. |
| 40 | Read-only provider/configuration Admin/TUI visibility | Complete; read-only posture retained, no CRUD expansion. |
| 50 | Secure host-console TUI administration for containerized Airlock | Complete; default-off host-console TLS/capability profile only. |
| 60 | **Deferred to 0.6.0** virtual-key management | The 0.6.0 B5 identity/keystore foundation must close first; not a 0.5.15 delivery slice. |
| 70 | Multi-instance TUI named inventory and read-only Admin-API fan-out | Complete; same-host read-only v1 only. |
| 71 | Slice 70 Docker topology verification | Complete; local and CI two-container verification without new product authority. |
| 80 | CI review and improvement | Complete; preserve its least-privilege, pinned-action, and required-check controls in future changes. |
| 90 | Release closeout | Complete and non-publishing; only delivered, verified slices are included and deferrals are explicit. |

### Mandatory lifecycle for every included feature slice

1. Review the slice plan, assigned feature/functions, and Slice 3 draft
   allocations. Approve, reject, or revise each draft; add any newly discovered
   need, requirement, or acceptance criterion durably.
2. Write a slice design with architecture alignment, threat/privacy impact,
   dependencies, rollback, and blast radius. Obtain and resolve design review.
3. Implement through TDD: commit/record failing **RED** tests, implement the
   minimal **GREEN** behavior, then add refactoring only while tests remain
   green.
4. Obtain and resolve code review. Run the slice's unit/integration/topology
   verification and any approved funded/manual checks.
5. Write `dev/plans/0.5.15/<slice>-status.md` with accepted requirements, evidence,
   review results, unresolved risk, and release disposition.

## Existing detailed evidence

- [0.5.15 release TODO](../0.5.15-todo.md)
- [Provider Admin/TUI design](20-provider-configuration-admin-tui-design.md)
- [Credential/alias warning design](30-configured-credential-missing-alias-startup-warning-design.md)
- [Host-console TUI design](30-host-console-tui-container-control-plane-design.md)
- [Virtual-key design](40-virtual-key-management-0.6-contract-design.md)
- [Fleet-control-plane research](50-multi-instance-tui-fleet-control-plane-research.md)
- [Gitleaks CI design](70-gitleaks-ci-secret-scan-design.md) and [review](71-gitleaks-ci-secret-scan-design-review.md)
- [Independent 0.5.15 design review](90-independent-design-review.md)
- [Even-minor roadmap migration plan](60-even-minor-release-roadmap-migration-plan.md)
