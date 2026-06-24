# STATUS — 0.5.0 Transparency Workstream  (live state board)

> Single source of truth for the 0.5.0 transparency workstream's live state. The
> orchestrator maintains it, one docs commit per transition. Implementer/reviewer
> agents never edit it. On resume, re-derive each pack's state from its witnesses
> (runbook §1.5) and trust the witnesses over this file. Parent board:
> `STATUS-0.5.0.md` (resilience+admin, all CLOSED).

_Last updated: 2026-06-24 · branch: `feat/0.5.0-resilience-admin` · **PHASE E IN PROGRESS — OBS-core CLOSED (merged `b997de0`); launching OBS-served ∥ OBS-ledger from that HEAD.**_

## 1. Current state + next action

- **Phase A (requirements): DONE** — UN-19 (transparent mutations), UN-20
  (truthful serving-backend attribution) in `dev/user-needs.md`.
- **Phase B (architecture/design): DONE** — `dev/notes/design-mutation-and-provider-transparency.md`
  complete; `dev/architecture.md` §3.7 (Transparency Layer) + response-header
  catalog added; the streaming seam pinned (CC-T6); the `_hidden_params` contract
  pinned to **LiteLLM 1.89.0**.
- **Phase C (pack spine): DONE** — `dev/plans/0.5.0-transparency-plan.md` ladder +
  this board.
- **Phase D (design-time codex gate): ✅ PASSED (round 2).** codex `gpt-5.5` round 1
  = BLOCK (2 high + 4 medium + 1 low — all real holes: streaming served-by source,
  `drop_params` path, failure-path accounting, callback ordering, header value-leak,
  explain SSE-safety, back-compat wording). All resolved in the design; round 2 =
  **PASS, no new findings.** Promoted verdict:
  `dev/plans/runs/0.5.0-TRANSPARENCY-design-review-20260624T183245Z-r2.md`.
- **Phase E (implementation): IN PROGRESS.** Preconditions verified (Phase D codex
  PASS; resilience+admin CLOSED).
  - **OBS-core: ✅ CLOSED.** Merged `b997de0` (`--no-ff`) into
    `feat/0.5.0-resilience-admin`. Pure `airlock/transparency.py` module, zero
    call-sites changed, 44 unit tests green. codex `gpt-5.5` = **CONCERN** (1 medium
    byte-bound-on-tiny-budget, 1 low string-bool coercion) → both **fixed** + the
    byte-bound invariant orchestrator-verified → promoted PASS-after-fix. Verdict:
    `dev/plans/runs/0.5.0-OBS-core-review-20260624T220523Z.md`. Implementation commits
    `76aa191` (impl) + `29b81f2` (fix); worktree removed.

**Next action:** launch **OBS-served ∥ OBS-ledger** (both depend only on OBS-core),
each in its own worktree cut from `b997de0`. Anchors re-verified against branch HEAD
(model_override_headers hook `:24-54` returns `dict|None`, reads `data["metadata"]`;
proxy startup wiring after `proxy.py:394`; ~18 mutation sites confirmed, note
`enhanced_passthrough.py` moved to `airlock/providers/` and is provider-side w/o
metadata in scope; drop_params resolution at `guardian.py:291`/`:433`).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| OBS-core | `airlock/transparency.py` dataclasses + `record_mutation`/`attribute_served_backend`/header serializers + `transparency.*` config | — | **✅ CLOSED** (merged `b997de0`) | `0.5.0-OBS-core-output.json`; review `…220523Z.md` |
| OBS-served | post-call attribution + flush in `model_override_headers.py`; `X-Airlock-Served-By`/`-Region`; streaming | OBS-core ✅ | **READY** (launching) | — |
| OBS-ledger | retrofit every mutation site → `record_mutation` | OBS-core ✅ | **READY** (launching) | — |
| OBS-headers | ledger → `X-Airlock-Mutations` (bounded) + `X-Airlock-Explain` body envelope | OBS-core, OBS-served, OBS-ledger | **PENDING** | — |
| OBS-log | `_build_record`: `mutations`/`served`/`attribution` | OBS-core, OBS-served, OBS-ledger | **PENDING** | — |
| OBS-accounting | spend + rate-limit/quarantine keyed off **served** provider | OBS-served | **PENDING** | — |
| OBS-metrics-tui | `airlock_mutations_total` + served label/column | OBS-core, OBS-ledger, OBS-served | **PENDING** | — |

All packs PENDING — design complete, implementation not started.

## 3. Acceptance scoreboard (UN → pack)

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| UN-19 transparent request mutations | OBS-ledger + OBS-headers + OBS-log (+ OBS-metrics-tui) | ◻ pending |
| UN-20 truthful serving-backend attribution | OBS-served + OBS-accounting + OBS-log | ◻ pending |

## 4. Parallelization plan

Critical path: `OBS-core → OBS-served → OBS-headers`. `OBS-ledger` runs ∥
`OBS-served` (both depend only on `OBS-core`). `OBS-log` after served+ledger;
`OBS-accounting` after served; `OBS-metrics-tui` after ledger+served. Serialize
anything touching `pyproject.toml`/`uv.lock`. `OBS-served`, `OBS-headers`, and
`OBS-log` all touch the post-call flush hook / record builder — serialize or
rebase carefully to avoid conflicts on `model_override_headers.py` and
`enterprise_logger.py`.

## 5. Outstanding worktrees

- OBS-core worktree removed after merge. OBS-served + OBS-ledger worktrees created
  next (base `b997de0`).

## 6. Resolved design questions (were open; closed in Phase B)

| # | Question | Resolution |
|---|----------|------------|
| H1 | Does `_hidden_params` carry the served backend on the **streaming** path (CC-T6)? | **Yes.** `custom_llm_provider`/`api_base`/`region_name`/`model_id` are on the stream wrapper at header-flush time (`streaming_handler.py:745-749`), so `X-Airlock-Served-By` works for streams too; `response_cost` + post-call mutations land via `async_log_success_event` on the assembled response (`litellm_logging.py:2824-2847`). |
| H2 | Default for `attribute_accounting_to_served` | **On**, documented as a bugfix, with a one-line opt-out. Operator confirms at the OBS-accounting HITL. |
| H3 | `X-Airlock-Mutations` default-on vs opt-in | **Default-on compact** (observability-as-benefit is the point); values never leaked (CC-T2); opt-out via `transparency.mutation_headers: off`. |

## 7. Recent decisions (newest on top)

- 2026-06-24 — **OBS-core CLOSED (pack 1/7).** Pure `airlock/transparency.py` shipped
  TDD (RED tests-only first), zero call-sites changed. codex CONCERN → fixed the
  `mutations_header` byte-bound (omit when even `…+N more` overflows; invariant: result
  ≤ budget_bytes) and string-bool config coercion; re-verified green (44 tests).
  Merged `--no-ff` as `b997de0`. The user's unrelated uncommitted `config.yaml`
  budget-limits change was stashed across the merge and restored. Downstream packs cut
  from `b997de0`.
- 2026-06-24 — **Phase D codex gate PASSED (round 2).** Round 1 BLOCK surfaced 7
  real design holes before any code; all fixed in the design set and re-verified
  PASS. Two operational notes carried into implementers: the streaming served-by
  provider must be read from the wrapper attribute (`response.custom_llm_provider`),
  not `_hidden_params`, at header time; and `drop_params` is captured by derived
  detection (`get_supported_openai_params`), not a hook. Design is
  implementation-ready.
- 2026-06-24 — **Folded into the 0.5.0 train as the transparency workstream.**
  Retargeted from a standalone 0.6.0 plan; lands on `feat/0.5.0-resilience-admin`
  after the 10 resilience+admin packs. Phase B completed this session: architecture
  §3.7 + header catalog, streaming seam pinned (response-headers hook for identity/
  pre-call mutations, `async_log_success_event` for cost/post-call), `_hidden_params`
  pinned to LiteLLM 1.89.0. H1–H3 resolved. Implementation deferred to a dedicated
  `/goal` orchestrator session gated on the Phase D codex PASS.
- 2026-06-24 — **0.6.0 design opened (now retargeted to 0.5.0).** Theme: mutation &
  serving-backend transparency (UN-19/UN-20), motivated by an audit finding ~30
  mutation sites mostly silent to clients, and `airlock_provider` being inferred
  from the model name while the served truth sits unused in
  `response._hidden_params`. Seven-pack ladder; CC-T1…CC-T7 defined.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` 3. `dev/plans/0.5.0-transparency-plan.md` (ladder +
   Immediate Next Action) 4. `dev/notes/design-mutation-and-provider-transparency.md`
   (CC-T1…CC-T7) 5. **this file** §1+§2 6. the next pack's prompt under
   `dev/plans/prompts/`. Then confirm the Phase D codex gate PASSed and launch the
   `/goal` orchestrator prompt.
