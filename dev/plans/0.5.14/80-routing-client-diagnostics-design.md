# Slice 80 — routing and client diagnostics design review

## Scope decision

Slice 80 ratifies the Slice-3 drafts as **DFR-31a** and **DAC-31a**:

- Authenticated operators can inspect a bounded, source-labelled view of smart
  routing classifications and live session-affinity pins.
- Operators can break every pin for a selected client through an authenticated,
  audited control-plane action. No raw session identifier is displayed, logged,
  or accepted as an action target.
- The routing display is a bounded persisted-JSONL view; the pin display is a
  bounded `live_admin` read. Both say when their source is unavailable rather
  than reconstructing or guessing state.

The QoS and exporter parts of original DFR-31/DAC-31 remain allocated to Slice
90. Virtual-key work remains rejected here and deferred to 0.5.15.

## Architecture

The proxy owns affinity state and the TUI is a separate process, so JSONL is
not sufficient for pins. `StateStore` now keys affinity by `(client_id,
session_id)`, preventing one client from inheriting another client's pin when
the same client-supplied identifier is reused. It exposes an identifier-free,
100-record maximum snapshot. The existing authenticated admin perimeter
provides `/airlock/admin/sessions` and an audited
`POST /airlock/admin/clients/{client}/clear-sessions` operation. The loopback
TUI client uses that perimeter; remote callers need ordinary admin authority.

The Guards Routing tab reads only the existing bounded request window. It shows
the simple/moderate/complex distribution and, for the selected request, score,
resolved model, and safe reasons. Router metadata no longer includes the raw
session ID, because callbacks persist Airlock metadata to JSONL.

None of these reads or displays is called from the inference hot path. The
guardian merely supplies the authenticated client identity that it already
computed to routing when a pin is created or read.

## TDD and verification plan

RED tests cover client-scoped same-ID sessions, the identifier-free bounded
snapshot, authenticated/audited pin clearing, routing-only JSONL parsing,
distribution/source rendering, and no session ID in routing metadata. GREEN
implementation adds only the state, existing-admin, TUI-client, router, and
operator-view seams described above. Verification includes focused state/admin/
routing tests and a harnessed TUI composition check; no provider request is
needed.
