# Slice 110 — FathomDB operational reads design review

## Ratification

The owner authorized Slice 110, so **DFR-33/DAC-33 are ratified** with these
adjustments:

- `AIRLOCK_OPERATIONAL_READ_BACKEND=fathomdb` is the only opt-in. The default
  is `jsonl`, even when a FathomDB engine happens to exist.
- The initial supported readers are TUI history and Advisor error/search reads.
  Billing, retention, and erasure remain their existing explicitly scoped
  mechanisms; a FathomDB erasure receipt continues to say it does **not** erase
  JSONL.
- Every read is bounded, reports its actual source, and reports FathomDB
  unavailability as an explicit JSONL fallback. Invalid backend selection also
  falls back to JSONL with a reason.

## Architecture

`airlock.operational_reads` is a pull-only selection seam. It calls the
existing PID-bound `datastore.get_engine()` only when the operator selected
FathomDB; it never initializes FathomDB for default JSONL users. FathomDB list
reads use existing `api.queries.get_request_logs()` under its read limit, then
apply the caller's existing predicate and time filter. Advisor search uses the
existing active-only `api.queries.search_request_logs()` path: FTS-only is
labelled `lexical_only` when dense retrieval is unavailable, and hybrid mode
is labelled only when it actually contributes. JSONL uses `log_query` with the
same record bound. The returned page carries `source`, `degraded_reason`,
`truncated`, and `limit_hit`.

The proxy owns the FathomDB engine. Because the TUI and its Advisor worker are
separate processes, they reach proxy-owned FathomDB reads through loopback-only
admin endpoints (`admin.enabled: true`, `trust_loopback: true`); they never
open the shared database. They retain their
historical JSONL behavior by default and only show a FathomDB result if
explicitly opted in. The TUI displays source/degraded/truncation state. No
request callback, UI event, or secondary process opens a shared writer.
`datastore` remains one engine per owning process; separate-process writers are
unsupported.

## TDD plan

RED tests establish default JSONL even with an engine, opted-in FathomDB,
unavailable/invalid fallback, bounded/truncated result truth, TUI source
rendering, proxy-owner bridge authorization, Advisor source propagation, and an
erasure receipt that remains incomplete for JSONL. GREEN adds the shared reader
and minimal consumers. No provider call or real FathomDB data outside temporary
test databases is used.
