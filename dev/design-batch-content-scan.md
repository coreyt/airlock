# Design — Batch content-scan pipeline (to-do #2, closes the guardrail-bypass gap)

Status: implemented in 0.4.0. Closes the last open item from
`dev/design-unified-batch-gateway.md` §3.2/§4 (async guard-scan) and §7.1
(async-scan failure UX). Supersedes the `scan_at_upload` NO-OP stub.

## The gap

Batch uploads went to the provider **unscanned**: `_handle_file_upload` streamed
the JSONL to disk and returned `processed`, and `create_batch` uploaded that raw
file. Keyword/PII guardrails that gate the chat path were bypassed entirely on
the batch path — a real compliance hole.

## Constraints that shaped the design (from the unified design §1)

- **A1 — no inline scan.** Batches go to 1M rows / 2 GB; Presidio (spaCy NER) is
  slow. Scanning must be **async**, **row-streamed** (never fully buffered), and
  **capped** (`max_rows`/`max_bytes`). The provider job is created only after a
  clean scan.
- **A2 — terminal redaction default.** Do **not** persist a placeholder→original
  PII reverse map for batch (it is a self-inflicted PII-at-rest lake). Default
  posture is terminal redaction: the scrubbed JSONL is what ships, no map stored.
  (`pii_hydrate_output: false` — hydration remains an unbuilt opt-in seam.)
- **No existing worker.** The unified design assumed `airlock/slow/…` was a
  think-slow execution worker. It is **not** — it is an offline log-analysis
  engine. So the scan execution machinery is built here, not wired into it.

## Architecture

```
POST /v1/files ─► stream to disk (raw)         [middleware._handle_file_upload]
                  │ record_file_upload → UPLOADED
                  │ schedule_scan(...)  ───────────────► background task
                  └─ return {status: pending}            [worker.run_scan]
                                                            │ claim_file_scan (CAS → SCANNING)
                                                            │ run_in_executor: scan_file  [scan.py]
                                                            │   stream rows → keyword check
                                                            │   (hit ⇒ reject whole upload)
                                                            │   → PII redact (terminal)
                                                            │   → write scrubbed JSONL
                                                            └─ set_file_ready / set_file_rejected

POST /v1/batches ─► await_file_ready(file_id)   [middleware._handle_create_batch]
                    READY    → create from the SCRUBBED file
                    REJECTED → 400 {reason}      ← async-scan failure UX (§7.1)
                    FAILED   → 400
                    SCANNING → 400 retry (still validating)

GET /v1/files/{id} ─► file status (pending|processed|error{reason})  [status poll]
```

### File state machine (design §3.7 `files:` row)

`UPLOADED → SCANNING → READY | REJECTED | FAILED`, persisted in a new
`batch_files` table in the existing `BatchStore`. `claim_file_scan` is a
race-free CAS (`BEGIN IMMEDIATE`, lease) mirroring the batch-claim pattern, so a
double-scheduled scan runs once.

### Module layout

| Module | Responsibility |
|--------|----------------|
| `airlock/batch/scan.py` | Pure, guard-injected stream pipeline (`scan_stream`) + IO wrapper (`scan_file`). No asyncio, no SQLite — unit-testable without Presidio. |
| `airlock/batch/worker.py` | Async orchestration: `schedule_scan` (background task), `run_scan` (claim → executor → store), `await_file_ready` (bounded poll). Thread-pool executor keeps the event loop free during CPU-bound NER. |
| `airlock/batch/store.py` | `batch_files` table + `record_file_upload`, `claim_file_scan`, `set_file_ready/rejected/failed`, `get_file`. |
| `airlock/batch/runtime.py` | `scrubbed_path`, `effective_batch_profile`. |
| `airlock/batch/middleware.py` | Wire upload→scan, gate create, `GET /v1/files/{id}` status. |

### Why thread-pool, not process-pool

The unified design floated a process pool. A thread pool is the MVP choice: it
keeps the event loop responsive (the blocking NER call runs off-loop) without the
fork/re-import/per-worker spaCy-reload cost and pickling constraints of a process
pool. `scan_file` is executor-agnostic, so swapping in a `ProcessPoolExecutor`
later is a one-line change. Worker count: `AIRLOCK_BATCH_SCAN_WORKERS` (default 2).

### Guard reuse

- Keyword: `keyword_guard._blocked_keywords()` + `_normalize_text()`; a hit
  rejects the **whole upload** (bulk blast radius, design §4).
- PII: `pii_guard._scrub_messages()` with a throwaway mapping (terminal
  redaction — the map is discarded, never persisted).

Both are gated by the `batch_profile` flags (`keyword_block`, `pii_redact`), not
by caller-supplied metadata (trust boundary, design §4).

## Scope boundaries (deliberately out)

- **Output scanning** (`output_scan_mode: observe`) stays a no-op — the bypass is
  on *input* content reaching the provider; result-side scanning is a separate,
  non-bypass concern and remains a future seam.
- **PII hydration** — terminal redaction only (A2); the encrypted reverse-map
  store is not built.
- **`max_concurrent_jobs`** — resource control, not part of the content gap.
- **Webhooks** — polling MVP (§7.5).

## Hardening (post-review)

- **Scanned-READY must have a scrubbed artifact.** `batch_files.scan_enabled`
  distinguishes a *scanned* READY file from a *scan-disabled* READY file. If a
  scanned file is READY but its scrubbed artifact is missing (external deletion /
  disk fault), `create` returns 400 (`scrubbed_input_missing`) and logs an error
  rather than silently shipping the raw upload — the terminal-redaction guarantee
  is never violated by a fallback.
- **Cancellation safety.** A scan cancelled by loop shutdown drives the file to
  `FILE_FAILED` (then re-raises) so it is not stranded `SCANNING` — there is no
  reconciliation loop re-issuing scans on restart.
- **Path-traversal guard.** Caller-supplied file ids are validated against
  `^file-[0-9a-f]{32}$` before any filesystem path is built from them.
- File-scan states are namespaced (`FILE_*`) so they can never collide with the
  batch lifecycle states.

## Config knobs

- `batch_profile.default.scan_at_upload` — master switch (default on). Off ⇒
  legacy behavior (file immediately READY, no scrubbing).
- `AIRLOCK_BATCH_SCAN_WORKERS` — scan thread-pool size (default 2).
- `AIRLOCK_BATCH_SCAN_WAIT_SECONDS` — how long `create` waits for an in-flight
  scan before telling the client to retry (default 30).
- `AIRLOCK_BLOCKED_KEYWORDS`, `AIRLOCK_PII_ENTITIES` — reused from the guards.
