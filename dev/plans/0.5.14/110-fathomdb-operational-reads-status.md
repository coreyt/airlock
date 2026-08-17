# Slice 110 — FathomDB operational reads status

**Status:** complete

## Delivered contract

- `AIRLOCK_OPERATIONAL_READ_BACKEND=jsonl` remains the default. FathomDB is
  selected only by `fathomdb`, never merely because its writer is enabled.
- The proxy is the sole FathomDB engine owner. The separate-process TUI and
  Advisor reach selected reads through loopback-only
  `/airlock/admin/operational/{records,errors,search}` views, which require
  `admin.enabled: true` and `trust_loopback: true`.
- A missing bridge, unavailable store, invalid selection, or query failure
  yields bounded JSONL with a source and degradation reason; it never opens a
  second engine from a UI/Advisor process.
- TUI history shows source, degradation, and bounded-result state. Advisor
  errors carry their source/window; Advisor FathomDB search uses the existing
  active-only FTS/hybrid seam, truthfully labels lexical-only operation, and
  marks any pre-time-filter full FTS page as partial.
- Erasure is unchanged and explicit: a FathomDB receipt does not erase JSONL.

## Independent review / FIX-n evidence

`gpt-5.6-terra` independent review completed three cycles:

1. Fixed an Advisor bridge failure that could have opened the proxy-owned DB
   from a secondary process, and validated all LLM-controlled operational-read
   arguments at the admin boundary.
2. Preserved the pre-time-filter FTS page cap, so a stale full page cannot be
   presented as a complete empty time window.
3. Final review approved with no remaining blockers.

## Verification

- Focused Slice 110 suite: **23 passed, 56 deselected** (independent review).
- Local final suite: **79 passed** across operational-read, Advisor, FathomDB,
  admin, and TUI-thread-safety tests; ruff on all Slice 110 Python files and
  tests passed; strict MkDocs build and `git diff --check` passed.
- Verification used temporary FathomDB test databases only; no live provider or
  `.env` credential was read.

## Dependency update

FathomDB was upgraded from 0.8.21 to **0.8.22** under this slice's conditional
dependency gate. The exact package version was installed from the refreshed
lockfile and the DB-extra suite passed (110 tests), covering initialization,
projection declaration, bounded active-only reads/search, provenance erasure,
and the proxy-owned operational-read bridge.
