# Slice 70 — multi-instance Admin fan-out status

**Status:** complete — ratified same-host, read-only v1 implemented and
verified on 2026-08-16. Remote targets, discovery, mutations, desired-state
integration, and retained fleet audit remain out of scope.

## Re-evaluation evidence

- The draft was research-only and allocated DFR-40/DAC-40 only as a proposal;
  the owner ratified the narrow v1 contract on 2026-08-16.
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
profiles; target/credential cross-use, including distinct signing-secret replay
denial; CA/hostname/scope failures;
metadata/link-local/redirect/proxy/DNS-rebinding denial; slow-target isolation;
selection and bound enforcement; no host/orchestrator imports; no secret/raw
error rendering; and ordinary TUI/Admin/inference regressions.

The owner ratified inventory authority, topology and identity contract,
loopback-only DNS/egress rule, read-only mutation boundary, ephemeral result
handling, and the external desired-state boundary. The design review turned
these into canonical requirements and executable acceptance criteria before
RED/GREEN work began.

## Ratification

The owner selected the recommended static owner-only inventory, Slice 50
remote-TUI container topology with distinct per-target signing secret/CA/token
references, loopback-only revalidated egress, and explicit bounded read-only
operation. Generic systemd is out of v1 because it does not establish the
remote-TUI PDP path.
The canonical DFR-40/DAC-40 record now fixes the 10-target, 4-concurrency,
2-second-connect/5-second-total, 64 KiB, manual/no-retry behavior. Design review
and RED/GREEN implementation may proceed only within that boundary.

## Implementation record

Implemented on the release worktree with a protected static inventory, a direct
loopback-only TLS transport, explicit selection, four-request bound, and a
read-only Textual view. Target servers additionally opt into
`admin.fleet_read_tui: true`; the PDP then accepts only a 15-minute capability
whose complete scope set is `admin:remote_tui` plus `admin:read`, preventing a
fleet credential from being repurposed for an Admin mutation. The target's
distinct signing secret remains the cross-target replay control. Verification
records focused RED/GREEN tests, policy tests, lint/format, strict docs, and
the release sync/verify gates. Final `make test` passed: 3,420 passed, 111
deselected, 1 xpassed. A live Docker two-container topology was not run because
the sandbox cannot access the Docker daemon; static transport, server-PDP, and
cross-signing-secret regression tests cover that contract in-process.
