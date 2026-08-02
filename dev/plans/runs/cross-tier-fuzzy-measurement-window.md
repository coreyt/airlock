# Cross-tier fuzzy measurement window — P-2b

**Status:** NOT STARTED. The instrumentation is committed on `main`, but this window
begins only after that exact code is deployed and its event is
queryable from the normal RequestEvent sink.

This is deliberately separate from the 2026-07-21 → 2026-08-21 reasoning-effort
window. That window cannot measure fuzzy alias traffic because its deployed code has
no `fuzzy_match_would_reject` event.

## Preconditions

- [ ] Non-mutating P-2b instrumentation is deployed.
- [ ] The deployed SHA/release is recorded below.
- [ ] At least one controlled cross-tier fuzzy request proves the WARNING event and
      mutation ledger arrive in the normal event sink, not only process logs.
- [ ] Window open and close dates are set. The window must cover one full billing
      cycle after queryability verification.

## What is measured

For a fuzzy request with close configured candidates from more than one cost tier,
Airlock preserves its current best-alias route and emits:

```
event=fuzzy_match_would_reject requested=<input> served=<current alias>
suggested=<alternate alias> score=<score> from_tier=<current tier>
to_tier=<alternate tier> client_id=<id>
```

The corresponding `model_alias_would_reject` ledger marker is content-safe. Exact
aliases, same-tier fuzzy aliases, unclassified aliases, and unmatched aliases must
continue to behave exactly as before.

## T-1 — prove queryability

Use the normal event-store/JSONL query path for the deployed environment. Record the
exact command, sample event identifier, and result here. A source-level unit test is
not proof that the production callback/sink wiring is live.

The local/CI acceptance path proves the exact payload and JSONL-query contract before
deployment:

```bash
uv run pytest tests/test_model_suggestion.py tests/test_request_event.py \
  tests/test_measurement_report.py -q
uv run python scripts/measurement-report.py cross-tier-fuzzy <exported-jsonl-or-log-dir> \
  --start <window-start> --end <window-end> \
  --dispositions <reviewed-client-dispositions.json> --require-dispositions
```

The report groups `requested`, `served`, `suggested`, `from_tier`, and `to_tier`, and
fails the disposition gate until every affected client (including `<unknown>`) is
assigned `notify`, `grace-extend`, `enforce`, or `investigate`. This local acceptance
does not replace the deployed controlled request required by T-1.

| Evidence | Value |
|---|---|
| Deployment SHA/release | |
| Controlled request/event identifier | |
| Event-store query and result | |
| Verified by / date | |

## T-2 — report and disposition

After the full window, produce:

- total events and distinct affected clients;
- request volume and distinct `(requested, served, suggested, from_tier, to_tier)`
  combinations;
- per-client disposition: notify, grace-extend, or enforce;
- explicit handling for unknown/no-client traffic.

No P-2b enforcement patch may be started from a zero that has not first passed T-1.

## Results

| Metric | Value |
|---|---|
| Window | not started |
| Total `fuzzy_match_would_reject` | |
| Distinct clients affected | |
| Distinct candidate combinations | |
| Decision | |
| Decided by / date | |
