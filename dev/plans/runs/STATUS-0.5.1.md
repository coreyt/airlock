# STATUS — 0.5.1  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-26 (HITL kickoff answered; Phase A complete) · base branch: `feat/0.5.1-settings` (cut from `main` @ 91eabf7; `main` already contains the 0.5.0 train)_

Release: **settings coherence + the in-memory DualCache STORE-seam.** Plan:
`dev/plans/0.5.1-plan.md`. Orchestrator: `dev/plans/prompts/0.5.1-ORCHESTRATOR.md`.
Audit source-of-record: `dev/notes/architecture-audit-0.5.0-2026-06.md`.

## 1. Current pack in flight + next action

- **In flight:** `SET-unify` IMPLEMENTING (off 6af83f7); `STORE-seam` REVIEWING (codex running).
- **Done:** kickoff cleared; Phase A (UN-25/26); Phase D PASS. **`SET-loader` MERGED+CLOSED**
  (`6af83f7`; codex CONCERN→fixed, 37+122 tests green). `STORE-seam` IMPLEMENTED
  (`4797d5f`; full suite 2125 green, 1 pre-existing unrelated `fathomdb` skip-fail).
- **Next action:** (1) when `STORE-seam` codex verdict lands → triage → **HITL** smoke
  restart-durability at sign-off; (2) when `SET-unify` lands → codex review → **HITL
  confirm the auto-swap-off behavior change before merge** → merge → spawn `SET-warnratio`.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `SET-loader` | One typed `AirlockSettings` read in place; uniform `env>config>default` (additive) | — | **CLOSED** (merged `6af83f7`; codex CONCERN→fixed) | `dev/plans/runs/0.5.1-SET-loader-output.json` |
| `SET-unify` | Delete hidden budget/failover defaults; fix R6; derive from config; budget-doc note | SET-loader ✅ | IMPLEMENTING (off 6af83f7) | `dev/plans/runs/0.5.1-SET-unify-output.json` |
| `SET-warnratio` | Collapse 0.8/0.9 into one configurable warn ratio | SET-unify | NOT_STARTED (prompt drafted) | `dev/plans/runs/0.5.1-SET-warnratio-output.json` |
| `STORE-seam` | DualCache-backed store; rolling-window spend (R5); checkpoint-in-child (FIX-1) | — (∥) | IMPLEMENTED (`4797d5f`) → REVIEWING | `dev/plans/runs/0.5.1-STORE-seam-output.json` |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` (`output.json` + head past baseline) → `REVIEWED` (`*-review-*.md`
with a `## Verdict:`) → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| UN-25 — unified settings precedence (no hidden defaults) | SET-loader, SET-unify, SET-warnratio | ⏳ (defined in `dev/user-needs.md`) |
| UN-26 — accurate + durable spend (R5 + restart survival, FIX-1) | STORE-seam | ⏳ (defined in `dev/user-needs.md`) |
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
| `.claude/worktrees/0.5.1-SET-loader` | `feat/0.5.1-SET-loader` | SET-loader | MERGED — pending cleanup |
| `.claude/worktrees/0.5.1-STORE-seam` | `feat/0.5.1-STORE-seam` | STORE-seam | IMPLEMENTED (4797d5f) → reviewing |
| `.claude/worktrees/0.5.1-SET-unify` | `feat/0.5.1-SET-unify` | SET-unify | IMPLEMENTING (off 6af83f7) |

## 6. HITL questions — ANSWERED at kickoff (2026-06-26)

| # | Question | Decision (operator) |
|---|----------|---------------------|
| 1 | Keep both pre-call swap + LiteLLM `fallbacks`, or converge on one? | **Keep both, one shared target map** (both derive from `router_settings.fallbacks`). |
| 2 | Is multi-worker / horizontal scaling actually anticipated? | **Defer — not anticipated soon.** Build the seam in-memory + file-checkpoint; keep Redis a clean future config-flip, don't over-build. |
| 3 | Restore LiteLLM's hard-budget cache on restart if budgets>0 (FIX-2)? | **Accept reset while budgets are 0.** Scope restart-durability to Airlock warn/swap spend; LiteLLM-cache restore is a tracked follow-up if hard budgets return. |
| 4 | Working branch: stack on the train vs fresh branch? | **Fresh `feat/0.5.1-settings` off `main`** (`main` now fully contains `feat/0.5.0-resilience-admin`). |

The kickoff HITL gate is **CLEARED**. Remaining HITL gates: (a) before `SET-unify`
merges — confirm the operator accepts the documented auto-swap-off behavior change
(Q1/Behavior-change #1); (b) release sign-off — DoD met + green isolated-instance
smoke-test on a separate dir+port.

## 7. Recent decisions (newest on top)

- 2026-06-26 — **HITL kickoff cleared + Phase A complete.** All four kickoff
  questions answered with the recommended options (§6). Cut `feat/0.5.1-settings`
  from `main` @ 91eabf7 (`main` supersedes the stale `feat/0.5.0` base). Committed
  the config.yaml budgets→0 baseline (`227a6b0`). Added **UN-25** (unified settings
  precedence) and **UN-26** (accurate + durable spend) to `dev/user-needs.md`.
  Next: Phase E — `SET-loader` + `STORE-seam` in parallel.
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
