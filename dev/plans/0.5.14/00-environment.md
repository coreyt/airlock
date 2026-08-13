# Slice 0 — environment readiness proposal

**Purpose:** identify release prerequisites. This record makes no configuration,
secret, dependency, service, or CI change.

## Observed baseline

| Area | Evidence | Proposal / owner |
| --- | --- | --- |
| Runtime | `uv 0.11.6`; project interpreter Python 3.12.3; CI uses Python 3.12. | Keep Python 3.12 as the release baseline. Slice 1 evaluates dependency changes against it. |
| Dependency integrity | `uv lock --check` resolved the 184-package lockfile successfully. | Require `uv lock --check` in every feature-slice closeout. |
| Test environment | Collection succeeds: 3,298 tests collected and five live tests deselected. | Use `uv run --extra test python -m pytest` for targeted test work; preserve `-m 'not live'` release gate. |
| Optional features | `db`, `s3`, `sql`, `metrics`, `tui`, `search`, `vertex`, `aistudio`, `analyzer`, `mistral`, and `tracing` extras exist. | Slice owners must name the smallest extras required by their tests; no all-extras local requirement except integration/release verification. |
| PII guard | Deployed PII egress is observe-only and Wake records a human DECIDE gate. | No 0.5.14 work changes enforcement posture. Benchmark work documents this as a separate outbound-control boundary. |
| Benchmark secrets | Runtime `.env` contains the provider keys previously supplied by the operator; values were not inspected. | Slice 10–60 use only environment references. Tests use sentinel values; funded smokes require an explicit operator authorization after no-credit tests pass. |
| Content retention | Enterprise logger supports `AIRLOCK_LOG_REDACT_FIELDS`; Fathom raw-message/response flags and SQL logging are separately configured. | Slice 10 creates the documented benchmark profile: redact `messages,response`; keep SQL logging and Fathom raw content storage disabled. |
| Health endpoint | `/health/liveliness` is the no-model-call probe; Wake marks `/health` unsafe for liveness. | All new smoke/runbook commands use `/health/liveliness`; test and docs changes must not reintroduce `/health` as liveness. |
| FathomDB lifecycle | FathomDB remains optional and single-owner; a LiteLLM-child memory-owner diagnosis is open. | Slice 110 must retain JSONL fallback and cannot claim multi-process shared writers. Fathom benchmark clients do not resolve the memory blocker. |

## Required setup before feature implementation

1. Create a sanitized benchmark environment example, not a tracked `.env`;
   it contains names/settings only, never secret values.
2. Create a funded-smoke worksheet that records operator authorization, alias,
   timestamp, HTTP/stream outcome, and safe response headers only.
3. Add the actual 0.5.14 feature tests to the existing non-live CI path. Run
   manual funded smokes outside CI and exclude their prompt/response content
   from artifacts.
4. For FathomDB-backed test paths, install the `db` extra and use a temporary
   store owned by the test process. Do not share it with a running Airlock
   instance.

## Acceptance for this preparation slice

- Every feature slice can state its Python/extras/test command and whether it
  needs a manual funded smoke.
- No planned path requires reading, copying, or committing a provider secret.
- The release closeout can prove the chosen health probe and content-retention
  controls without inspecting production traffic.
