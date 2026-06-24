# STATUS — 0.5.0  (live state board)

> Single source of truth for 0.5.0 live state. The orchestrator maintains it, one
> docs commit per transition. Implementer/reviewer agents never edit it. On
> resume, re-derive each pack's state from its witnesses (runbook §1.5) and trust
> the witnesses over this file.

_Last updated: 2026-06-23 · mainline: `main` · **design gate PASSED; ready for Phase E (awaiting go-ahead)**_

## 1. Current pack in flight + next action

- **Phase A (requirements): DONE** — UN-10…UN-18 in `dev/user-needs.md`.
- **Phase B (architectural review): DONE** — `dev/notes/design-resilience-and-admin-overview.md`
  (CC-6…CC-12, mount order, pack DAG, R1…R11); `dev/architecture.md` §3.6/§8/§9;
  reconciliation notes in the breaker / observability / admin design docs.
- **Phase C (pack spine): DONE** — `dev/plans/0.5.0-plan.md` ladder + this board.
- **Phase D (design-time codex gate): ✅ PASSED (round 5).** R1 = 3 BLOCK (real
  design holes) + 5 CONCERN → fixed. R2–R4 = propagation-only BLOCKs (repo-wide
  `/health`→`/health/liveliness`; CC-10/CC-11 satellite-doc consistency). R5
  (`…-r5.md`) = **PASS, no BLOCK/no CONCERN**. (Note: round-5 first attempt hung on
  a codex stdin-block; relaunched with `</dev/null` — use that for all codex runs.)
- **Phase E (implementation): UNBLOCKED — awaiting operator go-ahead.** Launch
  `0.5.0-RES-breaker` (prompt pre-drafted) ∥ `0.5.0-RES-tls`; fill the runtime
  `{{WORKTREE_PATH}}/{{BRANCH}}/{{BASE_COMMIT}}` at spawn.
- **Phase E (implementation): BLOCKED on Phase D PASS + operator go-ahead.** First
  packs: `0.5.0-RES-breaker` (locks `state.py` shape) ∥ `0.5.0-RES-tls` (canary).
  **`0.5.0-RES-breaker` prompt PRE-DRAFTED** at `dev/plans/prompts/0.5.0-RES-breaker.md`
  (design fields filled; runtime `{{WORKTREE_PATH}}/{{BRANCH}}/{{BASE_COMMIT}}` left
  for spawn). Ready to launch the instant Phase D PASSes.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| RES-tls | TLS env → litellm ssl flags (`proxy.py`) | — | **CLOSED** | commit `1567b54`; review `0.5.0-RES-tls-review-*` (PASS, flags confirmed vs litellm 1.89.0) |
| RES-breaker | threshold breaker + per-client policy + `cleared_at`/`_half_open_probe` + no-re-arm | — | **CLOSED** (fix-1 **PASS**) | `41ab9d3` + `2c48517`; review R1 BLOCK → fix-1 **PASS**; `state.py` shape locked for ADM-state |
| RES-errors | `AirlockProviderBlocked` + handler + `Retry-After` | breaker | **CLOSED** (fix-1 **PASS**) | `cf452d6` + `f7569c2`; review R1 BLOCK → fix-1 **PASS**; HITL H1 (seam) pending for B acceptance |
| RES-observ | capture `x-ratelimit-*` + `record_type` + TUI headroom | breaker | **CLOSED** (PASS + fix-1) | `5f12aea`+`dff4ea5`; code-reviewer PASS; remaining=0/wiring tests added; log-enrichment/TUI/passthrough deferred to polish |
| ADM-jwt | HS256 mint/verify + `admin mint-token` | — | **CLOSED** (PASS_WITH_NOTES + fix-1) | `7dde6f9`+`2f24288`; require-jti, max-TTL cap, scope filter added |
| ADM-state | CC-8 clear/arm mutators + admin_action ingest | breaker, observ | **MERGED (review running)** | `8c14e58`; cascade R12, CC-6 floor, CC-9 ingest branch; unblocks ADM-http/tui/skip |
| RES-routing | `_suppress_fallbacks` + budget warn | breaker, errors, observ | **MERGED + fix-1 (re-review running)** | `a2c7f11`+`7d3d831`; review R1 BLOCK (CC-3 budget defaults) → fix-1 (explicit-config gate + A2 narrowed to large-prompt); **resilience workstream complete** |
| ADM-state | CC-8 mutators + `admin_action` + ingest | breaker, observ | **PLANNED** | — |
| ADM-jwt | HS256 mint/verify + `admin mint-token` | — | **PLANNED** | — |
| ADM-http | PDP + perimeter middleware + `/airlock/admin/*` | ADM-state, ADM-jwt, errors | **PLANNED** | — |
| ADM-tui | clear-quarantine keybindings → loopback client | ADM-http | **PLANNED** | — |
| ADM-skip | guardrail-skip resolver + `X-Airlock-Capability` | ADM-http, ADM-jwt | **PLANNED** | — |

## 3. Acceptance scoreboard (UN → pack)

| Requirement | Pack | Status |
|-------------|------|--------|
| UN-10 operator quarantine clear | ADM-state + ADM-http (+ ADM-tui) | ⬜ |
| UN-11 capability auth (loopback + JWT) | ADM-jwt + ADM-http | ⬜ |
| UN-12 native TLS | RES-tls | ⬜ |
| UN-13 per-request guardrail skip | ADM-skip | ⬜ |
| UN-14 no self-inflicted quarantine storms | RES-breaker | ⬜ |
| UN-15 per-client breaker tuning | RES-breaker | ⬜ |
| UN-16 correct client backoff signaling | RES-errors | ⬜ |
| UN-17 provider quota observability | RES-observ | ⬜ |
| UN-18 bounded fallback/budget blast-radius | RES-routing | ⬜ |

## 4. Parallelization plan

`RES-tls` ∥ everything; `ADM-jwt` ∥ the RES packs; `RES-errors` ∥ `RES-observ`
(disjoint files) once `RES-breaker` lands. Critical path:
`RES-breaker → {RES-observ → ADM-state} → ADM-http → {ADM-tui, ADM-skip}`.
Serialize anything touching `pyproject.toml`/`uv.lock`.

## 5. Outstanding worktrees

None — pre-implementation.

## 6. Open HITL questions

| # | Question | Options + recommendation | Blocking? |
|---|----------|--------------------------|-----------|
| H1 | Does an `AirlockProviderBlocked` raised in the guardian pre-call hook reach the FastAPI exception handler (RES-errors)? | Operator integration test against the live litellm proxy: quarantine a client → expect 429 + `Retry-After` + `X-Airlock-*`. Unit tests cover the handler/error in isolation. | Not blocking pack merges; blocks the **B acceptance** sign-off |

## 7. Recent decisions (newest on top)

- 2026-06-24 — **Phase E underway (3 of 10 packs).** RES-tls CLOSED (codex PASS).
  RES-breaker + RES-errors merged with a fix-1 each after codex BLOCK (real
  findings, all fixed), re-reviews running. Two operational learnings: (a) `codex
  exec` blocks on stdin — always run with `</dev/null`; (b) codex's read-only
  sandbox cannot run `git` (bwrap loopback), so per-pack reviews must point codex
  at the FILES, not `git show`. RES-errors CONCERN decisions: handler stays
  subclass-only (provider-429 shaping deferred to RES-observ/C, which supplies the
  reset headers); the pre-call-hook→FastAPI exception-handler seam needs operator
  **integration verification** (cannot be unit-proved without the live litellm app)
  — tracked as a §6 HITL item.
- 2026-06-23 — **Design-review round 1: codex BLOCK → resolved.** codex `gpt-5.5`
  caught 3 real design gaps before any code: (1) clearing `/providers/{p}` left the
  per-client victim quarantined (pinned check is client→provider first,
  `guardian.py:224`) → added `clear_client_provider_quarantine` + provider-clear
  cascade; (2) `sub == client_id` was forgeable via `X-Airlock-Client` → skip authz
  now binds to the authenticated key-derived id only; (3) the `cleared_at` floor
  missed `impacted_clients()` → a provider clear could re-arm on pre-clear history.
  Plus 5 CONCERNs (fail-closed TLS, ASGI→guardian resolver handoff, per-mutator
  `cleared_at` scope, `overview.py:603` anchor, Docker `/health`→`/health/liveliness`).
  All folded into CC-6/CC-8/CC-10/CC-11/CC-12 + the detail notes. codex confirmed
  CC-7, CC-9, mount order, CC-12, UN-trace accurate. **Re-review pending before
  Phase E.**
- 2026-06-23 — **0.5.0 train opened (designs only).** Folded the
  admin/auth/TLS/guardrail-skip work into the same release as the large-context
  resilience pack (one train; 0.4.0 batch is complete). Phases A–C (requirements,
  architectural review + CC-6…CC-12 reconciliation, pack spine) landed as docs.
  Implementation is gated behind the Phase D design-time codex review per the
  user directive. Design set: `dev/notes/design-resilience-and-admin-overview.md`
  + the six notes.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` 3. `dev/plans/0.5.0-plan.md` 4.
   `dev/notes/design-resilience-and-admin-overview.md` (CC-6…CC-12) 5. **this
   file** §1+§2 6. the next pack's design note. Then run the Phase D codex gate
   before any implementation.
