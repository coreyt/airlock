# Slice 90 — QoS priority and exporter health status

**Status:** complete

## Ratified scope

Slice 90 ratified the remaining DFR-31/DAC-31 work as DFR-31b/DAC-31b:
bounded last-observed QoS priority and truthful telemetry instrumentation health.
It does not claim a priority boost is effective while admission is disabled, and
it does not claim that Prometheus has been scraped or an OTLP collector has
received a span merely because Airlock recorded a local signal.

## Implementation and TDD evidence

- The guardian stores only score, boost, four bounded signal reasons, and an
  observation timestamp in the existing per-client StateStore entry.
- The authenticated client read is source-labelled `live_admin`. The TUI marks
  a missing or older-than-120-second observation **stale** and never calls it
  an active boost.
- Telemetry health has a separate bounded process-instrumentation seam:
  enabled state, safe scheme/authority only endpoint, local signals, export
  successes/failures, and an error category. It is not an exporter transport.
- A caller-provided string error is always recorded as `export_error`; it is
  never copied into an admin response or TUI. Invalid URL ports fail open to a
  null endpoint at tracing setup time.
- `/airlock/admin/telemetry` is authenticated and source-labelled
  `process_instrumentation`; the Overview says unavailable if the read fails.
- Focused verification after final fixes: **13 passed, 119 deselected in
  2.45s** across guardian/admin/metrics/QoS telemetry tests; Ruff and
  `git diff --check` clean.

The independent high-reasoning review found raw-error, malformed-endpoint, and
staleness issues in cycle 1; all were corrected and the cycle-2 review approved
the slice.
