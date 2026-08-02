# STATUS — 0.5.9 (internal milestone)

_Last updated: 2026-08-02 · package version retained: 0.5.8_

## Current state

- **In flight:** final validation and CI evidence collection.
- **Publication:** prohibited. No PyPI upload, public release, or version bump.
- **Closeout marker:** do not create `milestone/0.5.9-internal-closeout` until
  the required CI evidence is available.

## Scope scoreboard

| Pack | State | Evidence |
|---|---|---|
| TUI transparency and log navigation | Implemented | `tests/test_0_5_9_features.py` |
| Managed MCP readiness timeout | Implemented | focused MCP/TDD tests |
| Programmatic-tool code inspection | Implemented | safe JSONL propagation tests |
| Adaptive semantic selection | Implemented; corpus mechanism exercised | `dev/notes/0.5.9-adaptive-equivalence.json` |
| Advisory LLM analysis | Implemented | tool-loop, minimized remote sandbox, fallback tests |
| Docker liveness smoke | Passed | `GET /health/liveliness` returned `"I'm alive!"` |
| Full CI and internal marker | Pending | requires a committed CI run |

## Remaining gates

- Full non-live suite, package checks, and a fresh independent review.
- A committed GitHub CI run before the internal marker is created.

## Constraints

- Probe `/health/liveliness`, never `/health`.
- Adaptive selection remains opt-in (`AIRLOCK_SEMANTIC_SELECTION=adaptive`).
- Remote LLM analysis requires explicit `AIRLOCK_ANALYZER_REMOTE_SANDBOX=anthropic`
  and `AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY=code_execution`; it only
  receives minimized derived aggregates.
