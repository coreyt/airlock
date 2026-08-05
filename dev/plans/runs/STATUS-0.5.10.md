# STATUS — 0.5.10 (catch-up and clean-up)

_Last updated: 2026-08-05 · package version: 0.5.8 · release decision pending_

## Current state

- **Planned, not started.** No pack has begun.
- Baseline is `b2752d7` / `milestone/0.5.9-internal-closeout`, CI green.
- Plan: [`0.5.10-plan.md`](../0.5.10-plan.md).
- Whether 0.5.10 publishes to PyPI or stays internal is an **open owner
  decision** (pack C-3). Do not bump the version or create a `v*` tag without it.

## Pack scoreboard

| Pack | Title | State | Notes |
|---|---|---|---|
| A-1 | Issue triage and closure | Not started | 6 issues verified against code; see plan |
| A-2 | Deprecation and dead-code sweep | Not started | 8 `datetime.utcnow()` sites; `_load_logs` shim |
| A-3 | Doc drift from 0.5.9 | Not started | plans README, dev README, stale handoff |
| B-1 | TUI semantic classifier visibility (#33) | Not started | reuse `semantic_report.py` |
| B-2 | TUI provider spend and budget (#23) | Not started | data exists in `fast/state.py` |
| B-3 | MCP startup timeout, proxy side (#20) | Not started | TUI half shipped; proxy half did not |
| C-1 | Advisory tool loop Part B | Not started | 0.5.9 obligation; needs `log_query` |
| C-2 | Auth/authz for paid side services (#21) | Not started | bound it; see plan |
| C-3 | Release decision, docs, closeout | Not started | includes pip-audit re-check |

## Carried obligations from 0.5.9

These were deferred **with a stated reason**, not dropped. Each must land or be
re-deferred deliberately:

- **F-1 Part B** — parameterized advisory tool arguments (pack C-1).
- **pip-audit suppressions** — three `cryptography` advisories ignored only
  while `litellm[proxy]` and `presidio-anonymizer` pin `<49`. Removal trigger in
  `dev/notes/security-pip-audit-exceptions.md`.
- **F-3** — code-inspection enforcement. Out of scope; needs its own observe
  window first.
- **Phase B indirect injection** — out of scope; design-first release.

## Verification gates

Same bar as 0.5.9:

1. Full non-live suite green.
2. Ruff check + format, mypy on `airlock/fast/`.
3. Strict MkDocs build + documentation contract tests.
4. **Every CI job run locally before pushing.** This caught a gating pip-audit
   failure in 0.5.9 that would otherwise have failed the push.
5. Green GitHub CI on the final SHA.
