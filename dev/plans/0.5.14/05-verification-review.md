# Slice 5 — verification adequacy review

**Purpose:** assess traceability and test sufficiency before feature delivery.
This review proposes tests; it does not add or modify them.

## Traceability assessment

| Candidate requirement group | Acceptance criteria status | Existing evidence | Required addition |
| --- | --- | --- | --- |
| Benchmark chat alias | Drafted in DAC-24. | Alias/config, proxy, transparency, and harness tests exist, but no shipped `gpt-4o-mini` model-list contract. | Configuration/template parity; normal and stream boundary tests; funded smoke worksheet. |
| Embeddings | Drafted in DAC-25. | `capability` compatibility tests presently treat embeddings as unsupported; no endpoint contract proves Airlock dispatch. | RED endpoint/capability/auth/option/error/redaction tests, then mocked provider integration and funded smoke. |
| Benchmark redaction | Drafted in DAC-26. | Enterprise logger has `messages,response` redaction tests; SQL/Fathom projection behavior is separate. | Profile-level sentinel proof across enabled sinks and documentation-contract check. |
| Provider foundation | Detailed AC1–AC13 plus DAC-27. | Models-catalog, attribution, monitor, proxy-error, logger/projection tests exist. | Cross-sink sanitized-error matrix; unsafe-base/no-redirect tests; full typed status regression. |
| OpenRouter / DeepSeek | Detailed provider design AC1–AC13 and DAC-28/29. | LiteLLM pin characterization and no-credit configurations can be tested locally. | Provider-specific mocked discovery, stream/error, attribution, tool, and funded-smoke coverage. |
| TUI lifecycle | DAC-30 drafted. | Extensive TUI and thread-safety tests exist; duration report identifies slow lifecycle tail. | Separate deterministic component suite and named worker lifecycle suite; before/after duration evidence. |
| TUI diagnostics | DAC-31 drafted; DAC-32 deferred to 0.5.15. | TUI, admin, state, priority, metrics, and token tests exist. | Read-source/staleness/unavailability/auth/destructive-action/no-secret tests. |
| FathomDB operational reads | DAC-33 drafted. | Fathom init/logger/query/search/erasure tests exist. | End-to-end selectable-backend, unavailable, bounded-result, source-label, and incomplete-erasure tests. |
| Documentation release index | Existing documentation contract should pin the active plan. | Strict MkDocs build passes, but `tests/test_documentation_contract.py` currently expects `plans/0.5.10-plan.md` while `dev/README.md` names 0.5.12/0.5.14. | Add a focused release-index contract repair and re-run the strict docs/contract gate. |

## Overall adequacy verdict

The repository has broad unit and integration coverage (3,298 collected tests)
and CI gates for docs, full-extra tests, lint, Docker build, and pip-audit.
It does **not** yet have a release traceability matrix that proves every 0.5.14
draft requirement has acceptance criteria and executable evidence. The critical
paths below require explicit release-level evidence before publication.

The current docs contract test has one confirmed baseline failure: it asserts
the superseded 0.5.10 active-plan path while the developer index has already
moved to 0.5.12 as published and 0.5.14 as backlog. Slice 120 owns the small
test/index reconciliation; it must preserve the test's intent to detect future
release-index drift.

| Critical path | Required verification |
| --- | --- |
| Proxy ingress to configured model | Auth/alias/guard/dispatch/served-header normal and stream tests; no unintended fallback. |
| Content and artifact safety | PII/redaction sentinel tests through every enabled persistence sink; error/credential/user-ID sentinel absence. |
| Embedding ingress | Correct endpoint/capability/model/option handling, policy and recorder compatibility, safe failures. |
| Provider gateway behavior | Configured-base validation, catalog non-authority, attribution, status classification, stream errors, and no raw metadata. |
| Operator control plane | Auth, source/staleness, bounded reads, destructive action audit, and no secret material in reads/logs. |
| Optional datastore | Enabled/disabled/unavailable lifecycle, one-owner constraint, partial-data honesty, and per-client erasure. |
| Release system | Locked dependency check, full non-live tests, strict docs, lint/mypy scope, Docker, pip-audit reassessment, and funded smokes only after automation passes. |

## Required delivery evidence format

Each feature-slice status record must map: draft ID → ratified requirement →
acceptance criterion → test path/command → outcome → manual evidence (if any).
A passing unit test alone is insufficient for changes that cross the proxy,
provider, persistence, or TUI boundaries.
