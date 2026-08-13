# Slice 10 — benchmark-safe logging profile status

**Status:** complete; no funded smoke required for this configuration/documentation
slice.

## Ratified scope

- DFR-26: publish a benchmark profile that redacts enterprise JSONL
  `messages,response` and disables SQL plus Airlock's optional Fathom request
  logger/raw-content paths.
- DAC-26: prove a persisted enterprise JSONL line contains `[REDACTED]` and no
  distinctive request/response sentinel; document the safe liveness probe.

## Design and code review

The design deliberately changes no runtime default. `AIRLOCK_LOG_REDACT_FIELDS`
is already an opt-in enterprise-log writer boundary; SQL projection is known to
be unredacted, while the Fathom content fields are explicit opt-ins. The profile
therefore turns off SQL/S3/Fathom request logging and keeps Fathom content flags
off, rather than claiming global sink redaction. The documentation clearly
separates logging retention from outbound PII enforcement.

Review confirmed the implementation matches the seams in
`enterprise_logger._write_log`, `projections.project_sql`, and
`projections.project_fathom`; it exposes no secret, prompt, or response in a
new artifact and changes no production code path.

## TDD and verification evidence

| Phase | Command / result |
| --- | --- |
| RED | `uv run --extra test python -m pytest tests/test_documentation_contract.py -q -k benchmark_safe_logging_profile` failed because the required profile did not exist. |
| GREEN | The same command passed: `1 passed, 5 deselected`. |
| JSONL evidence | `uv run --extra test python -m pytest tests/test_enterprise_logger.py -q -k redaction_applied_in_write_log` passed: `1 passed, 66 deselected`. The test checks both sentinels are absent from the written line. |
| Quality | Ruff check/format passed for changed tests; `uv run --extra docs mkdocs build --strict --site-dir /tmp/airlock-0.5.14-docs` passed. |

## Remaining release note

The complete `tests/test_documentation_contract.py` suite still has an existing
unrelated failing assertion for `plans/0.5.10-plan.md`. Slice 120 owns that
release-index contract repair. It does not invalidate the Slice 10 targeted
profile evidence.
