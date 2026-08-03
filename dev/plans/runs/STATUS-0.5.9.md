# STATUS — 0.5.9 (internal milestone)

_Last updated: 2026-08-03 · package version retained: 0.5.8_

## Current state

- **In flight:** approved production prompt-injection classifier implementation
  and its closeout evidence.
- **Publication:** prohibited. No PyPI upload, public release, or version bump.
- **Closeout marker:** do not create `milestone/0.5.9-internal-closeout` until
  the required semantic evidence, independent review, and final committed CI
  evidence are available.
- **Steward handoff:** [`0.5.9-MASTER-HANDOFF.md`](../prompts/0.5.9-MASTER-HANDOFF.md)
  is the current execution handoff; re-derive state from its linked witnesses.

## Scope scoreboard

| Pack | State | Evidence |
|---|---|---|
| TUI transparency and log navigation | Implemented | `tests/test_0_5_9_features.py` |
| Managed MCP readiness timeout | Implemented | focused MCP/TDD tests |
| Programmatic-tool code inspection | Implemented | safe JSONL propagation tests |
| Adaptive semantic selection | Partial; production classifier approved, not implemented | [design](../../notes/design-prompt-injection-classifier.md); mechanism-only [prior report](../../notes/0.5.9-adaptive-equivalence.json) |
| Advisory LLM analysis | Implemented | tool-loop, minimized remote sandbox, fallback tests |
| Docker liveness smoke | Passed | `GET /health/liveliness` returned `"I'm alive!"` |
| Documentation CI and Pages | Passed | `385a110`; CI `30779548856`, Pages `30779548846` |
| Final CI and internal marker | Pending | requires the eventual implementation commit, production corpus, review, and a green committed CI run |

## Remaining gates

- Implement the approved [prompt-injection classifier design](../../notes/design-prompt-injection-classifier.md), including explicit semantic mode and direct-input boundary.
- Run and retain a meaningful redacted production corpus-equivalence result;
  the existing zero-classifier report is not performance evidence.
- Obtain the owner-approved bounded independent automated review after the
  implementation and retain its findings/re-review.
- Resolve or explicitly re-scope the remaining second-review blockers recorded
  in [`0.5.9-verification.md`](../../notes/0.5.9-verification.md).
- Re-run full non-live/package checks, push the final commit, and verify its
  committed GitHub CI run before creating the internal marker.

## Constraints

- Probe `/health/liveliness`, never `/health`.
- Adaptive selection remains opt-in (`AIRLOCK_SEMANTIC_SELECTION=adaptive`).
- Remote LLM analysis requires explicit `AIRLOCK_ANALYZER_REMOTE_SANDBOX=anthropic`
  and `AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY=code_execution`; it only
  receives minimized derived aggregates.
