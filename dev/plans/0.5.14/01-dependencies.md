# Slice 1 — Dependabot and library sweep proposal

**Purpose:** assess the locked dependency graph and Dependabot policy. This
record performs no upgrade and does not widen compatibility ranges.

## Current evidence

- Dependabot runs weekly for `uv` and GitHub Actions, groups core proxy,
  optional integration, and developer-tooling updates, blocks majors, and
  explicitly holds FathomDB `>=0.4`, Newscatcher `>=2`, and Textual `>=6.3`.
- `uv lock --check` passes. LiteLLM 1.94.1 → 1.96.2 and several routine
  patch/minor candidates remain deferred; FathomDB 0.8.22 is now locked and
  installed after its DB-extra contract verification.
- Textual 6.2.1 → 8.2.8 is intentionally out of range: the repository records
  the Rich 14 conflict with the validated LiteLLM proxy baseline. It is not a
  patch sweep candidate.
- Existing CI runs a locked full-extra suite, docs contract, lint, Docker build,
  and pip-audit with three explicitly documented cryptography suppressions.

## Dependabot response policy

| Candidate | Disposition | Required evidence before merge |
| --- | --- | --- |
| GitHub Actions weekly group | **Include.** Keep Dependabot configuration unchanged; review each PR's action provenance and run existing CI. | Workflow diff review; normal CI. |
| LiteLLM 1.94.1 → 1.96.2 | **Conditional include, Slice 40.** Provider work touches LiteLLM boundaries, so upgrade only after characterizing OpenRouter/DeepSeek, embedding, streaming, error, and attribution behavior on the current pin. | Focused provider regression matrix, full non-live suite, docs/lock/Docker checks, funded smoke after code verification. |
| FathomDB 0.8.21 → 0.8.22 | **Included and verified, Slice 110.** The pin and lock now resolve 0.8.22. | 110 focused DB-extra tests passed: optional import/lifecycle, projections, query/search, erasure, and proxy-owned operational reads (110 passed). |
| boto3/botocore 1.43.62 → 1.43.69 | **Include only with an S3-slice regression.** A patch update is reasonable but not a 0.5.14 release blocker. | `s3`-extra migration/logger tests. |
| google-genai, google-auth, anthropic, mistralai, SQLAlchemy, OpenTelemetry, pytest-cov, spaCy patch releases | **Postpone unless a security advisory or affected slice needs them.** These provide no direct 0.5.14 user value. | Affected extra's regression suite and changelog review. |
| cryptography 48.0.1 → 50.0.0 | **Postpone.** The current LiteLLM/Presidio upper bounds and documented audit exceptions make this a coordinated dependency migration, not a sweep update. | Removal of constraints, full security review, and removal/update of exceptions. |
| Newscatcher 1.5.1 → 3.1.1 | **Reject for 0.5.14.** The repository deliberately caps `<2` because API migration is required. | Dedicated compatibility design outside this release. |
| Textual 6.2.1 → 8.2.8 | **Reject for 0.5.14.** It crosses the explicit Rich/LiteLLM compatibility boundary. | LiteLLM migration validation and a dedicated TUI compatibility slice. |

## Proposed changes

1. Keep the Dependabot configuration as-is; its grouping and excludes match
   documented constraints.
2. In Slice 6, make a go/no-go decision only on LiteLLM and FathomDB patches.
   Other routine patches remain Dependabot work after the release unless a
   security finding changes priority.
3. Re-run `pip-audit` during release closeout. Each existing suppression must
   still match its documented reachability rationale; do not renew or add an
   exception without a new assessment.
