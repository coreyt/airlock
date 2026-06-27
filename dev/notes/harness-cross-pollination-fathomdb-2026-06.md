# Harness cross-pollination — FathomDB → airlock (2026-06-26)

A FathomDB agent (which mirrors airlock's harness pattern) sent six reverse-direction
recommendations. Each was verified against airlock's current state before acting; the
decisions below are the durable record.

| # | Recommendation | Verified state | Decision |
|---|----------------|----------------|----------|
| 1 | Stale-base merge-base guard | `scripts/preflight.sh` is a CI-mirror; no worktree-base check existed | **ADOPTED** — new `scripts/check-worktree-base.sh` (cut-time guard + survey mode); wired into runbook §3 worktree-create step + §7 between-steps checklist. |
| 2 | Gate-enforced `DOC-INDEX.md` (per-doc owner + last-touched, staleness gate) | airlock already has 3 cold-start maps — `dev/plans/README.md`, `docs/index.md`, and the **`mkdocs.yml` nav gated by `mkdocs build --strict`** — plus `dev/update-docs.md`'s epoch marker + change→docs classification table | **DECLINED (parity via existing mechanisms).** Per-commit doc gating already exists (mkdocs --strict in preflight/CI; one-docs-commit-per-transition). A separate DOC-INDEX with last-touched metadata is marginal over update-docs.md §1's structure map + classification table. Revisit only if "which doc do I read?" actually becomes hard. |
| 3 | Reserved-gap (mod-5) pack numbering | airlock names packs **semantically** (`SET-loader`, `STORE-seam`, …), not numerically | **DECLINED.** The need mod-5 solves — "a structured home for surprise work without renumbering the ladder" — is already met: semantic names never renumber, surprise work lands as a new descriptively-named pack or a fixer-resume, and the overflow signal mod-5 provides ("gap band full ⇒ mis-scoped") airlock already gets from the runbook §1.5 rule "fixes past a small bound halt to HITL." Numeric gaps would add ceremony without new safety here. |
| 4 | Console-logging contract (`[id][PHASE]` + DETECT/RESOLVE → blockers_encountered) | **Already present** — `SLICE-TEMPLATE.md` §"Console logging contract" (PLAN/RED/GREEN/COMMIT + DETECT/RESOLVE + SUMMARY, mirrored to `output.json.blockers_encountered`) | **PARITY (no change).** FathomDB's extra `CHECK`/`MERGE` verbs are marginal: `MERGE` is orchestrator-side (implementers don't merge), `COMMIT` already covers the commit phase. |
| 5 | Implementer-side test-claim verification (mirror of the author-side check) | author-side check existed (`SLICE-TEMPLATE` authoring checklist `:37`); implementer-side mirror did **not** | **ADOPTED** — added to `SLICE-TEMPLATE` §READ: if a step claims "test X asserts Y" and GREEN depends on it, read the whole assertion at base and STOP+escalate if false; never fake a green by weakening a real test. |
| 6 | Typed dev-loop verbs (`scripts/agent-*.sh` emitting structured JSON) | no `agent-build/test` wrappers; the `uv run --extra test python -m pytest` quirk demonstrably cost multiple implementers a DETECT/RESOLVE this session | **PARTIAL — adopted the highest-value slice; full suite is a recommended follow-up.** Captured the correct pytest invocation in `SLICE-TEMPLATE` §RED (so every pack gets it). A full typed-verb layer (`agent-build/lint/typecheck/test/verify` with concise-on-pass / structured-on-fail JSON, short-circuit in latency order) is worthwhile but larger — tracked here as a follow-up. |

## Follow-up (not done here)
- **#6 full:** build `scripts/agent-{lint,typecheck,test,verify}.sh` emitting structured
  JSON (concise on pass, diagnostic on fail, full output spilled to a log when capped),
  fronting the existing `uv run …` invocations. Would standardize the env quirks (the
  `--extra test` Python resolution; ruff/mypy versions) and give a uniform execution layer.

## Not changed (FathomDB confirmed airlock already has these)
plan/runbook split · witness-derived state + invariants · the 14-row failure-handling
reference table · independent codex review + verdict promotion · orchestrator≠implementer
with physical tool omission.
