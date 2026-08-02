# STATUS — 0.5.8  (live state board)

> The current state is derived from release evidence and commits; this board is a
> compaction-safe pointer, not a substitute for them.

_Last updated: 2026-08-02 · released: [`v0.5.8`](https://github.com/coreyt/airlock/releases/tag/v0.5.8) @ `2bf044f`_

## Current state

- **In flight:** none — 0.5.8 is released.
- **Next action:** begin the separately scoped follow-up release; the deferred backlog
  in `dev/plans/0.5.8-plan.md` is not automatically accepted scope.

## Scope scoreboard

| Item | State | Witness |
|---|---|---|
| P-2 reasoning-effort enforcement | CLOSED | `5a7c714`, `5b20c4e`; tests in `tests/test_reasoning_effort.py` |
| P-2b cross-tier fuzzy refusal | CLOSED | `5a7c714`; tests in `tests/test_model_suggestion.py` |
| P-6 alias disclosure / P-6a / P-6b | CLOSED | `1062343`; header and interface tests |
| P-7 documentation | CLOSED | routing/configuration docs and 0.5.8 plan |
| P-8 config applicability (#34) | CLOSED | `5b86675`; 12 ConfigPane tests green |
| P-9 release hygiene / #22 audit | CLOSED | `2bf044f`; #22 closed; CI run `30755606843`; release run `30755862399` |

## Constraints

- `config.local.yaml` is a deliberate, default-empty extension mechanism and any
  machine-local override remains unstaged.
- Liveness validation uses `/health/liveliness` only—never `/health`.
- `v0.5.8` was pushed only after CI run `30755606843` passed. Release run
  `30755862399` then built, published to PyPI, and created the GitHub release.
