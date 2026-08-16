# 0.5.15 Slice 6 — HITL release review

**Status:** complete — owner decision recorded 2026-08-15. Slices 0–5 are
complete planning records. The planning hard stop is closed; an included feature
slice may start only after its own prerequisites and lifecycle gates are met.

Scores use 4 as highest: **understood** measures evidence/contract clarity and
**risk** measures security, correctness, and delivery blast radius.

## Scorecard and recommendation

| Item | Understood | Risk | Effort | Recommendation | Prerequisites / owner decision |
| --- | ---: | ---: | ---: | --- | --- |
| Slice 10 — Gitleaks local/CI controls | 4 | 3 | M | **Include** | Retain reviewed pins and narrow baseline; GitHub administrator must approve/perform disposable-PR, required-check, branch-protection, and ownership enforcement. |
| Slice 20 — typed Fast Guardian threat-backoff 429 | 4 | 3 | M | **Include** | Ratify DFR-34/DAC-34 detailed design and preserve existing provider/admission 429 contracts. |
| Slice 30 — configured credential/no-alias warning | 3 | 2 | S | **Conditional** | Resolve B3 with authoritative effective-model list and finite credential-source taxonomy; review warning/redaction tests. |
| Slice 40 — read-only provider/configuration projection | 3 | 3 | M | **Conditional** | Resolve B2 snapshot/version boundary; explicitly retain read-only YAML-plus-restart posture and distinct capability. |
| Slice 50 — host-console container TUI | 2 | 4 | L | **Conditional** | Owner/security decision on supported topology, TLS/CA, scopes, rotation/revocation, actor audit, and topology evidence (B2/B4). |
| Slice 60 — native virtual keys | 1 | 4 | XL | **Postpone** | Wait for ratified 0.6.0 identity/keystore/crypto/durability/authorization contract (B5). |
| Slice 70 — multi-instance fleet TUI | 2 | 4 | XL | **Conditional** | Approve named-inventory owner, per-instance identity/TLS/token isolation, operation limits, and external desired-state boundary. |
| Documentation-lifecycle cleanup | 4 | 2 | M | **Postpone** | Perform only as a small indexed backlog item after release decision; delete generated files only after preserving reproducible evidence. |
| Even-minor roadmap migration | 3 | 2 | M | **Postpone** | Separate roadmap owner resolves permanent-link and multi-worktree migration concerns. |
| Dependabot PR #45 / Actions PR #44 / new libraries | 4 | 2 | S–M | **Postpone** | Independent compatibility and immutable-pin review; no coupling to feature delivery. |
| Provider/configuration CRUD | 2 | 4 | L | **Postpone** | Requires an independently approved durable configuration ownership/lifecycle model. |
| Claude Code subscription relay | 2 | 4 | XL | **Postpone (out of scope)** | Retain research; no 0.5.15 allocation. |

## Recommended release shape

Authorize Slice 10 and Slice 20 first, subject to their per-slice lifecycle and
the external GitHub-administrator gate for enforcement. Treat Slices 30, 40,
50, and 70 as conditional investigations that may become feature slices only
after the stated decisions and revised designs are accepted. Do not include
Slice 60 in 0.5.15 delivery until its 0.6.0 dependency is ratified.

The release retains these non-negotiable boundaries:

- no new inference-hot-path management work;
- no raw secrets or sensitive operator data in API/TUI/log/docs/test evidence;
- Docker/host/orchestrator reachability is not identity or TUI authority;
- provider policy remains reviewed YAML plus restart, with no CRUD expansion;
- fleet remains named non-secret inventory plus target-local Admin-API fan-out;
- virtual keys do not invent a storage, crypto, or identity substitute.

## Exact start order after owner response

1. Record the owner’s include/conditional/postpone response in this file or a
   linked decision record.
2. If included, begin Slice 10 only after confirming the GitHub administrator
   and security reviewer can supply the external-enforcement evidence.
3. If included, begin Slice 20 with a detailed exception/HTTP mapping design and
   RED tests; it may run independently of Slice 10.
4. Before any Slice 30 work, amend/review the B3 taxonomy/model-list design.
5. Before any Slice 40 work, ratify and test the B2 launcher-to-child snapshot
   seam; before Slice 50, also close B4.
6. Do not schedule Slice 60 until 0.6.0 ratifies B5. Do not schedule Slice 70
   until its fleet authority decision is written and reviewed.

## Recorded owner decision

The owner directed Slice 6 to be completed. This record applies the scorecard’s
recommendations as the HITL decision:

| Area | Recorded disposition | Consequence |
| --- | --- | --- |
| Slice 10 — Gitleaks | **Include** repository-controlled work; external enforcement remains **conditional** | The slice may enter its admission lifecycle. It still needs security re-review, ratified DFR-35/DAC-35, a clean current scanner verification, and GitHub-administrator evidence before it claims merge-blocking enforcement. |
| Slice 20 — typed threat-backoff 429 | **Include** | It may enter detailed design and RED-test planning after rechecking this closure; it must preserve existing provider/admission 429 behavior. |
| Slices 30, 40, 50, and 70 | **Conditional** | Do not start until their named blockers/owner decisions are resolved: B3 warning taxonomy; B2 snapshot; B4 remote topology/identity; and fleet identity/TLS/token/inventory authority. |
| Slice 60 — virtual keys | **Postpone** | Do not schedule before the 0.6.0 identity/keystore/crypto/durability contract is ratified (B5). |
| Gitleaks external enforcement | **Conditional external action** | The approved least-privilege workflow direction is retained, but only a GitHub administrator can configure and prove required `gitleaks / scan` and code-owner protection on `main`. It is not active yet. |
| Provider CRUD | **Postpone** | Keep read-only/YAML-plus-restart posture; a durable configuration-ownership proposal is required before reconsideration. |
| Host-console topology | **Conditional** | Security/operations must explicitly approve TLS/CA, scopes, token rotation/revocation, actor audit, and a supported non-loopback topology. |
| Virtual-key dependency | **Postpone** | The 0.6.0 contract is a hard dependency, not an implementation detail that 0.5.15 may invent. |
| Fleet authority boundary | **Conditional** | Retain named non-secret inventory and target-local Admin-API fan-out; reject host/orchestrator access, automatic discovery, and desired-state ownership. |
| Documentation lifecycle, roadmap migration, dependencies/Dependabot, and Actions majors | **Postpone** | Keep these as independently authorized maintenance/backlog work; do not bundle them with included feature slices. |
| Claude Code subscription relay | **Postpone / out of scope** | No 0.5.15 implementation allocation. |

The existing acceptance of Gitleaks’s least-privilege no-secret boundary and
scoped ownership remains in force. The Action’s runtime-binary provenance is an
accepted residual risk only within that read-only, no-secret job; an ownership
or organization-license change requires re-review.

## Subsequent owner disposition — Slice 60 transfer

On 2026-08-16, the owner moved virtual-key management out of the 0.5.15
release. Slice 60 is **deferred** here and transferred to the 0.6.0 release,
where it remains blocked on the B5 native identity/keystore/crypto/durability
foundation. This does not authorize that foundation or Slice 60 implementation;
it preserves the prior no-substitute constraints.

## Post-decision start order

1. Recheck this decision and update the selected slice plan for any changed
   environment, baseline, repository, or dependency state.
2. Admit Slice 10 only when its current security/baseline and GitHub-authority
   gates are satisfied; it may deliver repository controls before external
   enforcement is proven, but must state the latter is inactive.
3. Admit Slice 20 after its detailed typed-error design and RED-test plan are
   reviewed; it can proceed independently of external GitHub settings.
4. Leave all conditional and postponed items dormant until their recorded gates
   are resolved in a new or updated HITL decision.

## Close condition

Slice 6 is closed. No canonical requirement, dependency, GitHub setting, test,
or product behavior changed as part of this decision record. Each included
feature slice remains responsible for its own review, RED/GREEN evidence,
external authorization, and status record.
