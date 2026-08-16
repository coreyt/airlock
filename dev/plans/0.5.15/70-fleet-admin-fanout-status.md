# Slice 70 — multi-instance Admin fan-out status

**Status:** conditional — planning/design re-evaluation complete; feature
implementation is blocked on the fleet authority/HITL decision.

## Re-evaluation evidence

- The draft is research-only and allocates DFR-40/DAC-40 only as a proposal;
  canonical requirements have not ratified a fleet feature.
- Slice 6 makes fleet authority conditional: named inventory, per-instance
  TLS/token isolation, operation limits, and an external desired-state boundary
  must be approved before scheduling.
- Slice 40 supplies a bounded redacted configuration projection; Slice 50
  supplies a constrained single-target host-console connection. Neither is a
  fleet transport or a cross-instance authority.
- Independent scope audit and design review found no inventory parser/owner,
  target selection/count/concurrency model, per-target audience, hardened
  redirect/proxy/body/DNS/SSRF controls, aggregate result model, correlation
  audit contract, or fleet test coverage.

## Rejected implementation paths

- Do not reuse the ordinary `urllib` Admin client as a fleet client: it lacks
  redirect/proxy/response-cap/DNS-revalidation/fan-out safety controls.
- Do not infer authorization from Docker, a subnet, service discovery, host
  management, or a shared bearer credential.
- Do not add SSH, Docker, systemd, Kubernetes, Ansible, Terraform, Nomad,
  Consul, or a central Airlock fleet/configuration store to the TUI.
- Do not write desired configuration or lifecycle state; deployment controllers
  remain the external owner and the TUI can only observe target Admin state.

## Decision-ready baseline and acceptance plan

The reviewed minimum candidate is a read-only, static owner-only local
inventory: per-target literal HTTPS origin, CA/token-file references, distinct
credentials, explicit target selection, no wildcard/default-all, bounded
concurrency/time/body, TLS name validation, no redirects/proxy environment,
and secret-blind per-target outcomes. The eventual RED suite must cover malformed
profiles; target/credential cross-use; CA/hostname/audience/scope failures;
metadata/link-local/redirect/proxy/DNS-rebinding denial; slow-target isolation;
selection and bound enforcement; no host/orchestrator imports; no secret/raw
error rendering; and ordinary TUI/Admin/inference regressions.

Before code, the owner must ratify inventory authority, topology and identity
contract, private-network/DNS egress rule, mutation semantics (or read-only
v1), operation/audit retention, and the external desired-state boundary. A
later Slice 70 design review must then turn those decisions into canonical
requirements and executable acceptance criteria before RED/GREEN work begins.
