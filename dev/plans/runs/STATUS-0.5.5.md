# STATUS — 0.5.5  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-26 (kickoff scaffold) · base branch: TBD at kickoff (needs 0.5.1 STORE-seam on base)_

Release: **bulkhead / isolation — exploration → decision → implementation.** Plan:
`dev/plans/0.5.5-plan.md`. Orchestrator: `dev/plans/prompts/0.5.5-ORCHESTRATOR.md`.
Audit source-of-record: `dev/notes/architecture-audit-0.5.0-2026-06.md` (Part 2,
Bulkhead row).

## 1. Current pack in flight + next action

- **In flight:** none — release not yet started.
- **Next action:** **Phase 0 — `EXPLORE`.** Run the time-boxed study (throwaway
  spikes on a separate dir+port) → author the decision memo
  `dev/notes/design-bulkhead-isolation.md` → codex design-review PASS → **`DECIDE`
  HITL gate** (human picks the mechanism C1–C5 + answers the horizontal-scale
  question). **No Phase-E pack is authored until DECIDE is recorded.**

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `EXPLORE` | Time-boxed study + spikes → decision memo (measured) | audit | NOT_STARTED | `dev/notes/design-bulkhead-isolation.md` + `dev/plans/runs/0.5.5-BULKHEAD-design-review-<ts>.md` |
| `DECIDE` | HITL: pick mechanism (C1–C5); answer scale Q; finalize UN-23/24 | EXPLORE (codex PASS) | NOT_STARTED | decision recorded in §7 + finalized §3 |
| `ENABLE-stateprovider` | Inject `StateProvider`; retire global `store` (Tier 3 #7) | DECIDE | NOT_STARTED | `dev/plans/runs/0.5.5-ENABLE-stateprovider-output.json` |
| `ENABLE-statesplit` | Split `state.py` god-object (Tier 3 #9) | ENABLE-stateprovider | NOT_STARTED | `dev/plans/runs/0.5.5-ENABLE-statesplit-output.json` |
| `IMPL-*` | The chosen mechanism (shape set by DECIDE) | ENABLE-* + 0.5.1 STORE-seam | NOT_STARTED | `dev/plans/runs/0.5.5-IMPL-*-output.json` |
| `DOCS` | UN-23/24; as-built memo; ops guide; changelog | IMPL merged | NOT_STARTED | `dev/plans/runs/0.5.5-DOCS-output.json` |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` → `REVIEWED` → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| AC-DECISION — evidence-based mechanism choice + scale Q answered | EXPLORE, DECIDE | ⏳ |
| UN-23 — noisy-neighbor protection / latency under load *(criteria finalized at DECIDE)* | IMPL-* | ⏳ |
| UN-24 — per-client resource fairness *(criteria finalized at DECIDE)* | IMPL-* | ⏳ |
| AC-ENABLE — enablers behavior-preserving | ENABLE-stateprovider, ENABLE-statesplit | ⏳ |

## 4. Parallelization plan

Phase 0 is sequential (`EXPLORE → DECIDE`). Phase E enablers are sequential
(`ENABLE-stateprovider → ENABLE-statesplit`, both touch `state.py`). IMPL packs
may parallelize per the chosen mechanism. Max 3 worktrees.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| _(none yet)_ | | | |

## 6. Open HITL questions (Phase-0 agenda; resolved at DECIDE)

| # | Question | Recommendation | Blocking? |
|---|----------|----------------|-----------|
| 1 | Is horizontal scaling / multi-tenant SLA actually required? (closes 0.5.1 open-Q #2) | answer at DECIDE — decides C1 vs C2/C4 | DECIDE |
| 2 | Fairness policy: equal-share / tiered (PrioritySignal) / per-client quotas? | tiered via PrioritySignal | DECIDE |
| 3 | Shed vs queue under contention? | shed (`429 + Retry-After`) — honest backpressure | DECIDE |
| 4 | UN-23/UN-24 wording in `dev/user-needs.md` | confirm at DECIDE | DECIDE |

## 7. Recent decisions (newest on top)

- 2026-06-26 — **Scaffolded for `/goal complete 0.5.5`:** added lifecycle map (the
  two-stage Phase 0 → Phase E with the DECIDE gate), acceptance scoreboard
  (AC-DECISION/AC-ENABLE + mechanism-conditioned UN-23/24), production-ready DoD;
  authored this board + the orchestrator prompt.
- 2026-06-25 — Scope set (user): bulkhead/isolation as exploration + trade-off →
  decision → implementation; the mechanism is undecided and is Phase 0's output;
  StateProvider + state.py split folded in as isolation enablers.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` (incl. [[airlock-production-safety]])
3. `dev/plans/0.5.5-plan.md` (candidate matrix C1–C5, guardrails, acceptance)
4. `dev/plans/prompts/0.5.5-ORCHESTRATOR.md` (the two-stage contract)
5. **this file** §1+§2 6. `dev/notes/design-bulkhead-isolation.md` (once authored).
Then re-derive pack state from witnesses. **Never author a Phase-E pack before
DECIDE is recorded.**
