# STATUS — 0.5.8  (live state board)

> The current state is derived from release evidence and commits; this board is a
> compaction-safe pointer, not a substitute for them.

_Last updated: 2026-08-02 · release candidate: `v0.5.8` (local candidate)_

## Current state

- **In flight:** P-9 release hygiene.
- **Next action:** commit the release candidate, close the audited documentation issue,
  push for GitHub Actions, then create the `v0.5.8` publishing tag only after CI is
  green.

## Scope scoreboard

| Item | State | Witness |
|---|---|---|
| P-2 reasoning-effort enforcement | CLOSED | `5a7c714`, `5b20c4e`; tests in `tests/test_reasoning_effort.py` |
| P-2b cross-tier fuzzy refusal | CLOSED | `5a7c714`; tests in `tests/test_model_suggestion.py` |
| P-6 alias disclosure / P-6a / P-6b | CLOSED | `1062343`; header and interface tests |
| P-7 documentation | CLOSED | routing/configuration docs and 0.5.8 plan |
| P-8 config applicability (#34) | CLOSED | `5b86675`; 12 ConfigPane tests green |
| P-9 release hygiene / #22 audit | IN PROGRESS | `0.5.8-release-evidence-2026-08-02.md` |

## Constraints

- `config.local.yaml` is a deliberate, default-empty extension mechanism and any
  machine-local override remains unstaged.
- Liveness validation uses `/health/liveliness` only—never `/health`.
- The release tag triggers PyPI trusted publishing; it follows, rather than precedes,
  a green GitHub Actions run.
