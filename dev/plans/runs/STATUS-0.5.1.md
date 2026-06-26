# STATUS — 0.5.1  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-26 (kickoff scaffold) · base branch: `feat/0.5.0-resilience-admin`_

Release: **settings coherence + the in-memory DualCache STORE-seam.** Plan:
`dev/plans/0.5.1-plan.md`. Orchestrator: `dev/plans/prompts/0.5.1-ORCHESTRATOR.md`.
Audit source-of-record: `dev/notes/architecture-audit-0.5.0-2026-06.md`.

## 1. Current pack in flight + next action

- **In flight:** none — release not yet started.
- **Next action:** **HITL kickoff** — answer the three open questions (§6) + confirm
  the working branch. Then Phase A (add UN-25/UN-26 to `dev/user-needs.md`) and,
  since **Phase D is already PASS** (`0.5.1-design-review-20260625T004647Z-r2.md`),
  go straight to Phase E starting with `SET-loader` (and `STORE-seam` in parallel).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `SET-loader` | One typed `AirlockSettings` read in place; uniform `env>config>default` | — | NOT_STARTED | `dev/plans/runs/0.5.1-SET-loader-output.json` |
| `SET-unify` | Delete hidden budget/failover defaults; fix R6; derive from config; budget-doc note | SET-loader | NOT_STARTED | `dev/plans/runs/0.5.1-SET-unify-output.json` |
| `SET-warnratio` | Collapse 0.8/0.9 into one configurable warn ratio | SET-loader | NOT_STARTED | `dev/plans/runs/0.5.1-SET-warnratio-output.json` |
| `STORE-seam` | DualCache-backed store; rolling-window spend (R5); checkpoint-in-child (FIX-1) | — (∥) | NOT_STARTED | `dev/plans/runs/0.5.1-STORE-seam-output.json` |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` (`output.json` + head past baseline) → `REVIEWED` (`*-review-*.md`
with a `## Verdict:`) → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| UN-25 — unified settings precedence (no hidden defaults) | SET-loader, SET-unify, SET-warnratio | ⏳ |
| UN-26 — accurate + durable spend (R5 + restart survival, FIX-1) | STORE-seam | ⏳ |
| AC-R6 — monitor reads `router_settings` nesting | SET-unify | ⏳ |
| AC-R2 — failover targets exist in `model_list` | SET-unify | ⏳ |
| AC-0 — `0 ⇒ no enforcement` across all three layers | SET-unify | ⏳ |

## 4. Parallelization plan

`STORE-seam` runs ∥ the SET packs (disjoint files). Critical path:
`SET-loader → SET-unify → SET-warnratio`. **Serialize anything touching
`pyproject.toml`/`uv.lock`.** Max 3 worktrees per the runbook.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| _(none yet)_ | | | |

## 6. Open HITL questions

| # | Question | Recommendation | Blocking? |
|---|----------|----------------|-----------|
| 1 | Keep both pre-call swap + LiteLLM `fallbacks`, or converge on one? | keep both sharing one target map for now | kickoff |
| 2 | Is multi-worker / horizontal scaling actually anticipated? | defer (decides how far the seam reaches) | kickoff |
| 3 | Restore LiteLLM's hard-budget cache on restart if budgets>0 (FIX-2)? | accept reset while budgets are 0; revisit if reintroduced | kickoff |
| 4 | Working branch: stack on the train vs fresh `feat/0.5.1-settings`? | stack on `feat/0.5.0-resilience-admin` | kickoff |

## 7. Recent decisions (newest on top)

- 2026-06-26 — **Scaffolded for `/goal complete 0.5.1`:** added the lifecycle map,
  UN-25/UN-26 acceptance scoreboard, and production-ready DoD to the plan; authored
  this board + the orchestrator prompt. Phase D witnessed PASS (round 2) → release
  is Phase-E-ready pending HITL kickoff.
- 2026-06-24 — Scope set (user): config coherence (R1–R4) + in-memory DualCache
  STORE-seam; defer only the Redis backend + `--num_workers`. Design review PASS (r2).

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` (incl. [[airlock-production-safety]])
3. `dev/plans/0.5.1-plan.md` (register R1–R6, STORE-seam guardrails, acceptance)
4. `dev/plans/prompts/0.5.1-ORCHESTRATOR.md` (your operating contract)
5. **this file** §1+§2 6. `dev/plans/prompts/0.5.1-<pack>.md` (once authored).
Then re-derive each pack's state from witnesses before acting.
