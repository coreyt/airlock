# Slice 90 — QoS priority and exporter health design review

## Scope decision

Slice 90 ratifies the remaining DFR-31/DAC-31 as **DFR-31b** and **DAC-31b**.
Operators need the last observed QoS priority for a client and truthful telemetry
health. The release does not make a priority claim when admission is disabled,
and it does not treat an enabled configuration or importable package as a
successful exporter.

## Architecture

The guardian already computes a priority signal before its optional admission
gate. It records the small last-observed `{score, boost, reasons, observed_at}`
state per client behind the existing `StateStore` lock. The existing
authenticated admin read returns it with an explicit `live_admin` source; the
TUI uses that live read and says unavailable/stale rather than rebuilding
priority from JSONL.

Telemetry health is a bounded process-local instrument, separate from tracing
or Prometheus transport. Prometheus is a pull endpoint: a completed request
proves metrics were recorded, not that any collector scraped it. Tracing reports
only whether its callback/dependency is available unless an installed exporter
reports its own successful export. Health records enabled state, a safe endpoint
origin (never credentials/query/path), last success, bounded success/failure
counts, and a bounded error category. An authenticated read snapshots it for
the TUI; no UI callback enters inference processing.

## TDD plan

RED tests cover priority snapshot and disabled-admission wording, telemetry
endpoint sanitization, success/failure counters and bounded error category,
admin authorization/source, and TUI unavailable rendering. GREEN code adds the
minimal StateStore and telemetry-health seams, with callback instrumentation
that fails open. Tests prove no credentials or raw exception text enters the
snapshot or UI.
