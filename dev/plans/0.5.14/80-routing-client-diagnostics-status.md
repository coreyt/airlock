# Slice 80 — routing and client diagnostics status

**Status:** complete

## Ratified scope

Slice 80 split and ratified DFR-31/DAC-31 for routing and session affinity
(DFR-31a/DAC-31a). QoS and exporter diagnostics remain Slice 90; virtual-key
work remains deferred to 0.5.15.

The operator receives a bounded Guards Routing view from persisted request
metadata and an identifier-free live-admin view of active session pins. A
selected client’s pins can be broken through an authenticated, audited control
plane operation. Session identifiers are neither put in persisted route metadata
nor shown to the operator.

## Implementation and TDD evidence

- `StateStore` keys pins by `(client_id, session_id)`, so one client cannot
  inherit another's affinity pin by reusing a session ID.
- `/airlock/admin/sessions` is source-labelled `live_admin`, capped at 100
  entries, and contains model/age/TTL only. The TUI's clear action uses an
  opaque URL-safe selector, avoiding path parsing ambiguity for header-derived
  client IDs.
- `POST /airlock/admin/session-clients/{selector}/clear` requires the existing
  `admin:clear_sessions` authority and writes an `admin_action` record.
- The Guards Routing tab reports a bounded simple/moderate/complex distribution
  plus selected-request score, model and safe route reasons. Malformed persisted
  metadata degrades safely rather than breaking rendering.
- Focused RED/GREEN test matrix: client-scoped affinity, identifier-free
  snapshot, authorization/audit, slash/percent identifier target correctness,
  routing-only records, distribution/source label, and malformed metadata.
- Focused verification: **143 passed** (fast-router, admin, TUI-routing and
  TUI-admin-client checks); `git diff --check` clean.

Independent high-reasoning review required two fixes (opaque target selector and
defensive persisted-metadata parsing), then approved cycle 2.
