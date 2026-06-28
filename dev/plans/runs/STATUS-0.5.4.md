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

- **In flight:** **MIGRATE-sidechannels** (Phase E, pack 5, last MIGRATE). s3 + sql CLOSED.
  (codex reviewer rate-limited until ~14:14 → sonnet `code-reviewer` fallback in use.)
- **enterprise+fathom FULLY MIGRATED** (2a+2b-i+2b-ii all CLOSED): the recorder is the
  single live telemetry callback in enterprise's slot (before monitor); enterprise &
  fathom are projection-backed sinks; `_build_record`/`_base_record`/`_fathom_properties`
  **deleted**; equivalence frozen to `tests/fixtures/0.5.4-entfathom-golden.json`;
  no-double-emit proven (litellm dedup). Full no-network suite 2516 passed (1 pre-existing
  env failure: optional `fathomdb`). Merges: EVENT `bfe56bf`, 2a `7cd60d8`, 2b-i `29ca578`,
  2b-ii `41448df`.
- **EVENT CLOSED** (merge `bfe56bf`): `airlock/callbacks/request_event.py`
  (`RequestEvent` + `build_request_event` + `RequestRecorder`) + `tests/test_request_event.py`
  (20 tests). codex review CONCERN(low) → recorder lock/snapshot hardening (`e783e0c`) →
  merged. 20 target + 90 telemetry tests green; worktree cleaned. The seam is delivered
  but NOT yet live-installed (that's MIGRATE-enterprise).
- **Worktree-base note (for MIGRATE prompts):** the Agent-tool worktree is cut from
  `main` HEAD (`57010d1`), NOT from `feat/0.5.4-eventbus`. Each implementer must
  `git merge feat/0.5.4-eventbus --ff-only` first (the EVENT agent did this cleanly).
  MIGRATE prompts must instruct this up front.
- **DESIGN CLOSED:** 4 adversarial codex gates, findings 4→3→2→**0** (high→high→none→
  PASS). Verdicts: `...153724Z.md` (#1), `...154755Z.md` (#2), `...155901Z.md` (#3),
  `...160640Z.md` (#4 PASS). Note rev 4 is the authoritative RequestEvent + seam
  contract; its §-level golden-test obligations bind the EVENT/MIGRATE packs.
- **Done:** kickoff HITL answered; Phase A complete; gate #1 + #2 verdicts promoted
  (`...153724Z.md`, `...154755Z.md`). Gate #1's 4 findings confirmed resolved by gate
  #2's "what passed".
- **Gate #1 BLOCK (all FIXED, rev 2):** #1 `bare_exception_error` (s3/sql raw error);
  #2 fathom env-gated field sources (pure `project_fathom`); #3 scoped out
  `write_precall_block_record`/`write_batch_record` (§2a); #4 registration cutover +
  monitor-ordering invariant (§5a).
- **Gate #2 BLOCK (all FIXED, rev 3):** #1 carry **raw `response_obj`** (per-projection
  serialize) so fathom `_response_text` works on the raw object (§3.10); #2 fathom
  whole-sink `airlock_skip_fathom_logger` skip pinned (§3.11); #3 corrected §5a
  registration facts (fathom **async-only**; s3/sql **opt-in, no self-register**) +
  **firing-surface invariant**.
- **Gate #3 BLOCK (all FIXED, rev 4):** #1 [medium] metrics is success+failure not
  success-only — added §5b pinning `requests_total` on BOTH paths (don't drop failure
  counter) as a sidechannels golden obligation; #2 [low] monitor table corrected to
  success+failure.
- **Decision rule for gate #4 (TERMINAL):** PASS → Phase E (`EVENT`). ANY BLOCK →
  **halt to HITL** — no further autonomous re-gates (contract forbids overriding a
  BLOCK; convergence is near-complete but a human PASS is then required).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `DESIGN` | `design-request-event-bus.md` + codex design-review PASS | — | **CLOSED** (codex PASS, gate #4) | `design-request-event-bus.md` rev 4 + `0.5.4-EVENTBUS-design-review-20260628T160640Z.md` (PASS) |
| `EVENT` | Canonical `RequestEvent` + recorder/dispatcher seam (registration, ordering, per-sink failure isolation) | DESIGN ✅ | **CLOSED** (merge `bfe56bf`; codex CONCERN→fixed; 20 tests) | `dev/plans/runs/0.5.4-EVENT-output.json` |
| `MIGRATE-entfathom-project` (2a) | Pure `project_enterprise`/`project_fathom` + golden equivalence (additive; nothing rewired) | EVENT ✅ | **CLOSED** (merge `7cd60d8`; codex BLOCK→fixed; 44 tests; +faithful cost fix to `request_event.py`) | `dev/plans/runs/0.5.4-MIGRATE-entfathom-project-output.json` |
| `MIGRATE-entfathom-wire` (2b-i) | Extend recorder (async_only + is_async); add recorder callback + `record_event` sink methods on enterprise & fathom; wire the module-level recorder — **dormant** | 2a ✅ | **CLOSED** (merge `29ca578`; codex PASS; 11 tests; dormancy verified) | `dev/plans/runs/0.5.4-MIGRATE-entfathom-wire-output.json` |
| `MIGRATE-entfathom-cutover` (2b-ii) | Register recorder in enterprise's slot (before monitor); remove enterprise `_self_register`+config + fathom `_self_register_async`; **delete** `_build_record`/`_base_record`/`_fathom_properties`/old callback methods | 2b-i ✅ | **CLOSED** (merge `41448df`; codex CONCERN→both closed; 2516 suite green) | `dev/plans/runs/0.5.4-MIGRATE-entfathom-cutover-output.json` |
| `MIGRATE-s3` | S3 logger onto `RequestEvent` (`project_s3`+sink; keep `_redact_record`, narrow fields, bare error); delete `_build_record()`; recorder sink gated by `AIRLOCK_ENABLE_S3_LOGGER` | enterprise+fathom ✅ | **CLOSED** (merge; codex PASS; frozen golden) | `dev/plans/runs/0.5.4-MIGRATE-s3-output.json` |
| `MIGRATE-sql` | SQL logger onto `RequestEvent` (`project_sql`+sink; **JSON-string** messages/response, bare error, NO redaction); delete `_build_record()`; recorder sink gated by `AIRLOCK_ENABLE_SQL_LOGGER` | enterprise+fathom ✅ | **CLOSED** (merge; sonnet-fallback PASS; frozen golden) | `dev/plans/runs/0.5.4-MIGRATE-sql-output.json` |
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

- 2026-06-28 — **⚠️ codex REVIEWER UNAVAILABLE (usage limit; resets ~14:14).** The
  MIGRATE-sql codex review aborted with "You've hit your usage limit". Per
  `dev/agent-harness-reference.md` §3.2, falling back to the **`code-reviewer` (sonnet)**
  subagent for sql (and any pack reviewed while codex is down); noted on the board.
  Prefer codex once it resets for later packs (sidechannels/VERIFY). The sonnet fallback
  is acceptable for the clean s3-mirroring sql pack.
- 2026-06-28 — **enterprise+fathom CLOSED → MIGRATE-s3. ⚠️ BEHAVIOR-CHANGE-REGISTER
  ITEM (s3/sql opt-in mechanism).** Today s3/sql are opt-in via a deployment adding
  `proxy_s3_logger`/`proxy_sql_logger` to config.yaml `success_callback` (they have no
  `_self_register`, not in default config). With the recorder as the SOLE callback,
  that per-sink config opt-in no longer exists. **Decision:** gate the recorder's s3/sql
  sink registration on **`AIRLOCK_ENABLE_S3_LOGGER`/`AIRLOCK_ENABLE_SQL_LOGGER`** env
  flags (consistent with fathom's `AIRLOCK_ENABLE_FATHOM_LOGGER`). The emitted record is
  byte-identical when enabled; only the **opt-in mechanism** changes (config-entry → env
  flag). This is a deployment-facing change → **MUST be in the DOCS behavior-change
  register + release notes (migration note)**, and surfaced at sign-off for human review.
  s3/sql register as NORMAL recorder sinks (success+failure; not async-only). The
  per-sink write gates stay (`AIRLOCK_S3_BUCKET` for s3; engine for sql). VERIFY's
  isolated parity run validates no unintended divergence.
- 2026-06-28 — **2b-i CLOSED → 2b-ii cutover authored (registration ordering + fathom
  gating researched).** Verified facts pinned into the 2b-ii prompt: (i) **ordering**
  guaranteed by config-entry-order → import-order → `_self_register` append; monitor
  sets `airlock_provider_protection` in its **`log_failure_event`** (monitor.py:326-339),
  so the recorder must build before monitor on the FAILURE path — replicate via
  recorder FIRST in config.yaml + `recorder._self_register()` (all 4 lists). (ii) **fathom
  gating** = `AIRLOCK_ENABLE_FATHOM_LOGGER` (proxy.py:155-156/200-209); recorder must
  register the fathom sink only when set, and `proxy.py`'s fathom-append block is removed
  (recorder owns it). (iii) **oracle-freeze**: deleting the builders breaks the 2a golden
  oracle (it compares to the LIVE builders) AND ~25 old unit tests — so 2b-ii FIRST freezes
  the projection oracle to snapshots, THEN deletes, THEN adapts the old tests (no faked
  greens; deleted-test→replacement coverage mapped in output.json). Seam tests:
  snapshot-immutability/ordering, no-double-emit, fathom gating+async-only+skip, isolation.
- 2026-06-28 — **2a CLOSED → 2b split into wire(2b-i)+cutover(2b-ii) for risk.** 2b is
  the live-install + builder-deletion (riskiest pack; codex caught a subtle cost bug in
  the simpler 2a). Split: **2b-i wire** = additive + DORMANT (extend recorder, add the
  recorder callback + `record_event` sink methods on enterprise/fathom, wire the
  module-level recorder; do NOT register live, do NOT remove old registrations, do NOT
  delete builders) → zero behavior change, fully unit/seam tested. **2b-ii cutover** =
  flip the switch (register recorder in enterprise's slot before `proxy_monitor`; remove
  enterprise `_self_register`+config & fathom `_self_register_async`; delete
  `_build_record`/`_base_record`/`_fathom_properties` + old callback methods) with
  ordering/async-only/skip/isolation/no-double-emit seam tests + end-to-end equivalence
  vs the 2a goldens.
- 2026-06-28 — **EVENT CLOSED → MIGRATE-enterprise+fathom split into 2 sub-packs +
  async-only dispatch pinned.** Two refinements (both consistent with gated §5a/§5b —
  no re-gate):
  (1) **Async-only dispatch mechanism:** extend `RequestRecorder` —
  `register(sink, *, name, async_only=False)` + `dispatch(event, *, is_async)`. The
  live recorder callback passes `is_async=False` from sync `log_*_event` and `True`
  from async; async-only sinks (fathom) are skipped when `is_async=False`. Recorder
  registers on all 4 lists (matches enterprise's superset coverage); enterprise/metrics
  always fire, fathom async-only — preserving each sink's firing surface.
  (2) **Decomposition** of the coupled enterprise+fathom batch (3-iteration-cap +
  risk isolation):
    - **2a `MIGRATE-entfathom-project`** — add pure `project_enterprise(event)` +
      `project_fathom(event)`; capture golden SNAPSHOT fixtures of today's
      `_build_record`/`_fathom_properties` output over a representative request set;
      assert the projections reproduce them field-for-field. **Additive only — nothing
      rewired, builders still present, zero behavior change.** The snapshots survive
      into 2b (they outlive the deleted builders).
    - **2b `MIGRATE-entfathom-install`** — extend recorder (async_only + is_async);
      add the recorder callback (build once → dispatch); install it in **enterprise's
      slot, before `proxy_monitor`** (sync+async success+failure); make enterprise +
      fathom sinks; remove enterprise `_self_register`/config + fathom
      `_self_register_async`; **delete `AirlockLogger._build_record` + fathom
      `_base_record`**. Golden/seam tests: ordering, fathom async-only + skip flag,
      failure isolation, end-to-end equivalence vs the 2a snapshots.
- 2026-06-28 — **DESIGN CLOSED (codex PASS, gate #4) → EVENT pack authored + spawned.**
  4 adversarial codex gates converged 4→3→2→0 findings. **EVENT-scaffolding decision:**
  EVENT delivers `airlock/callbacks/request_event.py` (`RequestEvent` dataclass +
  `build_request_event` + `RequestRecorder`) + `tests/test_request_event.py` **only** —
  it touches **no** existing sink/registration/config and does **not** install into the
  live `litellm` callback manager. The LIVE recorder install + enterprise-slot ordering
  is deferred to **MIGRATE-enterprise** (the first sink to move). This is strictly more
  conservative than design-note §5a's wording (which assigned the registration to EVENT)
  — it keeps EVENT zero-behavior-change; the registration/ordering *mechanism* is
  delivered + unit-tested here against an in-memory recorder. No event-shape/seam-semantics
  change, so no re-gate needed. Pack prompt: `dev/plans/prompts/0.5.4-EVENT.md`.
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
