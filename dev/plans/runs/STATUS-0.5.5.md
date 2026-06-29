# STATUS — 0.5.5  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-28 (DOCS CLOSED — Phase E complete) · base branch: main · base commit: 75f7c38_

Release: **bulkhead / isolation — C1 chosen (in-loop admission control).** Plan:
`dev/plans/0.5.5-plan.md`. Orchestrator: `dev/plans/prompts/0.5.5-ORCHESTRATOR.md`.
Decision memo: `dev/notes/design-bulkhead-isolation.md`.

## 1. Current pack in flight + next action

- **In flight:** none — Phase E complete.
- **Next:** HITL sign-off → version bump 0.5.4 → 0.5.5 → tag → PyPI publish.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `EXPLORE` | Time-boxed study + spikes → decision memo (measured) | audit | **CLOSED** | `dev/notes/design-bulkhead-isolation.md` (written 2026-06-28) |
| `DECIDE` | HITL: pick mechanism; answer scale Q; finalize UN-23/24 | EXPLORE | **CLOSED** | decisions d-027–d-031 in wake store; memo §10 updated 2026-06-28 |
| `ENABLE-stateprovider` | Inject `StateProvider`; retire bare global `store` (Tier 3 #7) | DECIDE + design-review PASS | **CLOSED** | `dev/plans/runs/0.5.5-ENABLE-stateprovider-output.json` (commit a78cd71, merged ce7b776) |
| `ENABLE-statesplit` | Split `state.py` god-object into core/spend/persistence/mcp (Tier 3 #9) | ENABLE-stateprovider | **CLOSED** | `dev/plans/runs/0.5.5-ENABLE-statesplit-output.json` (commit 9e3fb42, merged 64f9615) |
| `IMPL-admission` | C1: per-client token-bucket RPM + semaphore concurrency gate in guardian | ENABLE-* | **CLOSED** | `dev/plans/runs/0.5.5-IMPL-admission-output.json` (commit 04400d5, merged 257086c) |
| `DOCS` | UN-23/24 in user-needs; as-built memo; ops guide; changelog | IMPL-admission | **CLOSED** | `dev/plans/runs/0.5.5-DOCS-output.json` (commit 75f7c38) |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` → `REVIEWED` → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| AC-DECISION — evidence-based mechanism choice + scale Q answered | EXPLORE, DECIDE | ✅ CLOSED (d-025 through d-031) |
| UN-23 — noisy-neighbor protection / latency under load | IMPL-admission | ✅ CLOSED (gate shed path p50=0.58µs; 16 tests) |
| UN-24 — per-client resource fairness | IMPL-admission | ✅ CLOSED (RPM tiered; concurrency peek active; full acquire/release follow-up) |
| AC-ENABLE — enablers behavior-preserving | ENABLE-stateprovider, ENABLE-statesplit | ✅ CLOSED (2412 tests, 0 regressions) |

## 4. Parallelization plan

Phase E enablers are sequential (`ENABLE-stateprovider → ENABLE-statesplit`, both
touch `state.py`). `IMPL-admission` follows both ENABLEs. `DOCS` follows IMPL.
No parallelism in this release — single pack in flight at a time. Max 1 worktree.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| _(none)_ | | | |

## 6. HITL questions — all resolved (DECIDE closed 2026-06-28)

| # | Question | Decision | Wake ref |
|---|----------|----------|----------|
| 1 | Is horizontal scaling / multi-tenant SLA required? | Not yet — single-process now, Redis-flip later via DualCache seam | d-031 |
| 2 | Fairness policy? | PrioritySignal-tiered; default = equal-share; `boost=True` clients get 1.5× cap | d-027 |
| 3 | Shed vs queue? | Shed: `429 + Retry-After`; Retry-After = precise token bucket refill time | d-028 |
| 4 | Off by default? | Yes — `admission.enabled: false`; explicit opt-in | d-029 |
| 5 | Counter storage? | New `AdmissionStore` (SpendStore pattern, integer request counts) | d-030 |

## 7. Recent decisions (newest on top)

- **2026-06-28 — DECIDE CLOSED.** All implementation questions answered.
  C1 ratified by human. Horizontal-scale Q#2 closed. Decisions d-027–d-031 in wake.
  - d-027: Fairness = PrioritySignal-tiered (equal-share default, boost=True → 1.5×)
  - d-028: Shed with 429 + Retry-After (precise refill time from token bucket)
  - d-029: Off-by-default (admission.enabled: false)
  - d-030: New AdmissionStore class (SpendStore pattern, integer request counts)
  - d-031: Single-process now; Redis-flip later
- **2026-06-28 — EXPLORE CLOSED.** Decision memo authored with measured numbers
  for all three axes (guard chain latency, CPU/GIL, DualCache seam). Four benchmark
  scripts run in-process; C1 gate spike measured at +35µs overhead, <1µs 429 path.
  C2/C4/C5 rejected on evidence. C3 adopted as accounting seam for C1 counters.
- **2026-06-26 — Scaffolded for `/goal complete 0.5.5`:** lifecycle map, acceptance
  scoreboard, production-ready DoD; STATUS board + orchestrator prompt authored.
- **2026-06-25 — Scope set (user):** bulkhead/isolation as exploration → decision →
  implementation; mechanism undecided (Phase 0's output); StateProvider + state.py
  split folded in as isolation enablers.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` (incl. [[airlock-production-safety]])
3. `dev/plans/0.5.5-plan.md` (candidate matrix C1–C5, guardrails, acceptance)
4. `dev/plans/prompts/0.5.5-ORCHESTRATOR.md` (the two-stage contract)
5. **this file** §1+§2 6. `dev/notes/design-bulkhead-isolation.md` (DECIDE-final)
Then re-derive pack state from witnesses. EXPLORE and DECIDE are CLOSED. Phase E
may begin after codex design-review PASS on the memo.
