# STATUS — 0.5.4  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-28 (kickoff gate answered → Phase A done → DESIGN gate in flight) · base branch: `main`; working branch: `feat/0.5.4-eventbus` @ base `57010d1`_

Release: **observability event-bus unification — one `RequestEvent` model + one
recorder/dispatcher behind every telemetry sink.** Plan: `dev/plans/0.5.4-plan.md`.
Orchestrator: `dev/plans/prompts/0.5.4-ORCHESTRATOR.md`. Audit source-of-record:
`dev/notes/architecture-audit-0.5.0-2026-06.md` (Part 2 telemetry row + Tier 3 #8).

## 1. Current pack in flight + next action

- **In flight:** **DESIGN** (Phase B) — design note finalized, READY FOR CODEX GATE.
- **Done:** kickoff HITL answered (branch `feat/0.5.4-eventbus`, UN-28,
  sequential/small-batch MIGRATE); Phase A complete (UN-28 in `dev/user-needs.md`;
  plan's UN-27 collision fixed).
- **Next action:** run the **codex design gate** over
  `dev/notes/design-request-event-bus.md` + anchored modules →
  `dev/plans/runs/0.5.4-EVENTBUS-design-review-<ts>.md` with Orchestrator triage.
  **PASS required** before any Phase-E pack. Then Phase E starting with `EVENT`.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `DESIGN` | `design-request-event-bus.md` + codex design-review PASS | — | IN_FLIGHT (note ready; codex gate next) | `dev/notes/design-request-event-bus.md` + `dev/plans/runs/0.5.4-EVENTBUS-design-review-<ts>.md` |
| `EVENT` | Canonical `RequestEvent` + recorder/dispatcher seam (registration, ordering, per-sink failure isolation) | DESIGN | NOT_STARTED | `dev/plans/runs/0.5.4-EVENT-output.json` |
| `MIGRATE-enterprise` | Enterprise logger onto `RequestEvent`; delete its `_build_record()` | EVENT | NOT_STARTED | `dev/plans/runs/0.5.4-MIGRATE-enterprise-output.json` |
| `MIGRATE-fathom` | Fathom logger onto `RequestEvent`; delete `_build_record()` | EVENT | NOT_STARTED | `dev/plans/runs/0.5.4-MIGRATE-fathom-output.json` |
| `MIGRATE-s3` | S3 logger onto `RequestEvent`; delete `_build_record()` | EVENT | NOT_STARTED | `dev/plans/runs/0.5.4-MIGRATE-s3-output.json` |
| `MIGRATE-sql` | SQL logger onto `RequestEvent`; delete `_build_record()` | EVENT | NOT_STARTED | `dev/plans/runs/0.5.4-MIGRATE-sql-output.json` |
| `MIGRATE-sidechannels` | Mutation ledger + metrics fed from the same seam | EVENT | NOT_STARTED | `dev/plans/runs/0.5.4-MIGRATE-sidechannels-output.json` |
| `VERIFY` | Cross-sink equivalence harness + isolated-instance parity run | all MIGRATE-* | NOT_STARTED | `dev/plans/runs/0.5.4-VERIFY-output.json` |
| `DOCS` | UN + as-built design note + changelog/behavior-change register | VERIFY | NOT_STARTED | `dev/plans/runs/0.5.4-DOCS-output.json` |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` → `REVIEWED` → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| **UN-28** (next-free; plan says "UN-27" but that COLLIDES with 0.5.3 — allocate UN-28 at Phase A) — one canonical event drives all sinks | EVENT, MIGRATE-* | ⏳ |
| AC-EQUIV — every sink emits field-for-field identical records before vs after | MIGRATE-*, VERIFY | ⏳ |
| AC-SEAM — one dispatch seam with per-sink failure isolation | EVENT | ⏳ |
| AC-SMOKE — any logged/served field change is smoke-covered (else parity oracle) | VERIFY, DOCS | ⏳ |

## 4. Parallelization plan

`EVENT` is the anchor (all MIGRATE-* depend on it). The MIGRATE packs touch shared
telemetry wiring — **confirm parallel-safety at kickoff; default to sequential or
small disjoint batches** (one sink per worktree). `VERIFY` after all MIGRATE-*;
`DOCS` last. Max 3 worktrees per the runbook.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| _(none yet)_ | | | |

## 6. Open HITL questions

| # | Question | Resolution | Status |
|---|----------|------------|--------|
| 1 | Working branch for 0.5.4? | `feat/0.5.4-eventbus` off `main` @ `57010d1` | ✅ answered 2026-06-28 |
| 2 | UN number (plan's "UN-27" collides with 0.5.3) | **UN-28** (next-free; in `user-needs.md`) | ✅ answered 2026-06-28 |
| 3 | MIGRATE parallel-safety (sinks share telemetry wiring) | sequential / small disjoint batches: [enterprise+fathom], [s3], [sql], [sidechannels] | ✅ answered 2026-06-28 |

## 7. Recent decisions (newest on top)

- 2026-06-28 — **Kickoff gate answered + Phase A done.** Branch
  `feat/0.5.4-eventbus` off `main`; **UN-28** allocated in `dev/user-needs.md` and
  the plan's `UN-27` collision fixed; MIGRATE runs **sequential / small disjoint
  batches** ([enterprise+fathom] coupled — fathom reuses `AirlockLogger._build_record`
  — then [s3], [sql], [sidechannels]). Design note finalized (branch + timestamp
  convergence RESOLVED; seam-shape=one fan-out callback, redaction=per-sink, metrics
  standalone helpers OUT OF SCOPE — all PROPOSED to the codex gate). DESIGN now
  awaiting the codex design gate (PASS required before any Phase-E pack).
- 2026-06-28 — **Scaffolded for `/goal complete 0.5.4`:** authored this board + the
  orchestrator prompt (the plan already had the lifecycle/acceptance/DoD spine +
  pack ladder). Flagged the plan's `UN-27` placeholder as a collision with 0.5.3 →
  allocate **UN-28** at Phase A. `RequestEvent` should source LiteLLM-internal
  fields through the 0.5.3 ACL (`litellm_adapter.py`), not by re-reading internals.
- 2026-06-27 — **Split from 0.5.3 (kickoff):** the event-bus / `RequestEvent`
  unification (audit Tier 3 #8) deferred to its own release **0.5.4**; the former
  0.5.4 (bulkhead/isolation) renumbered to **0.5.5**.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` (incl. [[airlock-production-safety]])
3. `dev/plans/0.5.4-plan.md` (builder inventory, pack ladder, guardrails)
4. `dev/plans/prompts/0.5.4-ORCHESTRATOR.md` (your operating contract)
5. **this file** §1+§2 6. `dev/notes/design-request-event-bus.md` (once authored).
Then re-derive each pack's state from witnesses. **No Phase-E pack before the
design gate PASSes.**
