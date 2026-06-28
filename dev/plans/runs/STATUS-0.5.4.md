# STATUS — 0.5.4  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-28 (kickoff scaffold) · base branch: `main` (0.5.1/0.5.2/0.5.3 shipped + tagged)_

Release: **observability event-bus unification — one `RequestEvent` model + one
recorder/dispatcher behind every telemetry sink.** Plan: `dev/plans/0.5.4-plan.md`.
Orchestrator: `dev/plans/prompts/0.5.4-ORCHESTRATOR.md`. Audit source-of-record:
`dev/notes/architecture-audit-0.5.0-2026-06.md` (Part 2 telemetry row + Tier 3 #8).

## 1. Current pack in flight + next action

- **In flight:** none — release not yet started.
- **Next action:** **HITL kickoff** (§6: confirm branch, the next-free UN number,
  MIGRATE parallel-safety). Then Phase A (allocate the UN), then **Phase B**: author
  `dev/notes/design-request-event-bus.md` (canonical `RequestEvent` shape + dispatch
  seam + verified builder inventory) and run the **codex design gate (PASS
  required)** before any code. Then Phase E starting with `EVENT`.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `DESIGN` | `design-request-event-bus.md` + codex design-review PASS | — | NOT_STARTED | `dev/notes/design-request-event-bus.md` + `dev/plans/runs/0.5.4-EVENTBUS-design-review-<ts>.md` |
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

| # | Question | Recommendation | Blocking? |
|---|----------|----------------|-----------|
| 1 | Working branch for 0.5.4? | a fresh `feat/0.5.4-eventbus` off `main` | kickoff |
| 2 | UN number (plan's "UN-27" collides with 0.5.3) | allocate **UN-28** (next-free) | Phase A |
| 3 | MIGRATE parallel-safety (sinks share telemetry wiring) | sequential or small disjoint batches; confirm in the design note | kickoff/design |

## 7. Recent decisions (newest on top)

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
