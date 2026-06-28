# STATUS — 0.5.3  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-28 (ALL 4 PACKS CLOSED + MERGED; release engineering-complete) · base branch: `main` (0.5.1 + 0.5.2 merged/tagged)_

Release: **decouple from LiteLLM internals (ACL) + unblock the hot path
(Presidio) + structural hygiene.** Plan: `dev/plans/0.5.3-plan.md`. Orchestrator:
`dev/plans/prompts/0.5.3-ORCHESTRATOR.md`. Audit source-of-record:
`dev/notes/architecture-audit-0.5.0-2026-06.md`.

## 1. Current pack in flight + next action

- **ALL 4 PACKS CLOSED + MERGED to `main`:** ACL `dc1330e`, RACE `fe45159`,
  DECOUPLE `2ff3f38`, **LATENCY `902617e`** (merge). Each codex/reviewer PASS.
- **LATENCY closeout (2026-06-28):** code-reviewer subagent **PASS** (codex
  bwrap-sandbox unavailable → sanctioned sonnet fallback;
  `0.5.3-LATENCY-review-20260628T142435Z.md`), 3 nits no-fix. Closure witness
  `0.5.3-LATENCY-output.json` written. **Full no-network suite: 2428 passed, 107
  skipped, 1 failed (`test_fathom_init.py` — known pre-existing fathom env failure,
  unrelated).**
- **Release engineering-complete:** version bumped 0.5.2 → **0.5.3** (pyproject +
  uv.lock); CHANGELOG `[0.5.3]` added; annotated tag `v0.5.3` cut LOCAL.
- **Remaining (sign-off):** isolated-port `dev/smoketest/` run as the parity +
  latency oracle (operator-gated — spends real provider tokens), then push/publish
  per separate approval (K3). **Nothing pushed.**

### Parallelization (REVISED per design gate F2 — conflict-free waves)

File-sharing graph forces waves (NOT the plan's original "LATENCY/RACE ∥"):
`model_override_headers.py`=ACL+DECOUPLE · `fast/guardian.py`=DECOUPLE+LATENCY+RACE ·
`guardrails/extract.py`=DECOUPLE+LATENCY.
- **Wave 1: ACL ∥ RACE** (provably disjoint files).
- **Wave 2: DECOUPLE** (cut after ACL+RACE merge; neutral text seam + proxy_bootstrap).
- **Wave 3: LATENCY** (cut after DECOUPLE merge; text cache on the new seam).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `ACL` | `litellm_adapter.py` — single owner of all LiteLLM-internal reads; migrate call sites (parity) | design ✓ | **CLOSED** (merge `dc1330e`) | `dev/plans/runs/0.5.3-ACL-output.json` |
| `RACE` | `threat_score` lock; identity/config consolidation | — (Wave 1 ∥ ACL) | **CLOSED** (merge `fe45159`) | `dev/plans/runs/0.5.3-RACE-output.json` |
| `DECOUPLE` | Break `fast`↔`guardrails` cycle; extract `proxy_bootstrap.py` | ACL + RACE merged ✓ | **CLOSED** (merge `2ff3f38`) | `dev/plans/runs/0.5.3-DECOUPLE-output.json` |
| `LATENCY` | Presidio → `to_thread`; shared text-extract; vLLM TTL | DECOUPLE merged ✓ | **CLOSED** (merge `902617e`; reviewer PASS) | `dev/plans/runs/0.5.3-LATENCY-output.json` |
| `OBS-eventbus` | Single `RequestEvent` + recorder (audit Tier 3 #8) | — | **DEFERRED → became release 0.5.4** | `dev/plans/0.5.4-plan.md` |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` → `REVIEWED` → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| UN-27 — predictable latency under concurrency (no Presidio serialization) | LATENCY | ✅ (merged 902617e; concurrency/non-blocking test green; redaction byte-identical) |
| AC-ACL — single ownership of internal reads; byte-parity headers/attribution | ACL | ✅ (merged dc1330e; 9 §3.7 parity fixtures green) |
| AC-DECOUPLE — no `fast`↔`guardrails` cycle; install order asserted | DECOUPLE | ✅ (merged 2ff3f38; AST guard + bootstrap-order test) |
| AC-RACE — no lost `threat_score`; one client-identity path | RACE | ✅ (merged fe45159; deterministic no-lost-update probe + golden parity) |

## 4. Parallelization plan

`ACL` is the anchor (lands first; its seam stabilizes `DECOUPLE`). `LATENCY` and
`RACE` touch disjoint files and run ∥. `DECOUPLE` depends on `ACL` (bootstrap
app-object access). Max 3 worktrees.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| _(none yet)_ | | | |

## 6. Open HITL questions

| # | Question | Resolution (2026-06-27 kickoff) | Blocking? |
|---|----------|----------------|-----------|
| 1 | Base: cut after 0.5.1 merges, or stack on the train now? | ✅ RESOLVED — base = `main` @ `fc67c33` (0.5.1 + 0.5.2 both merged/tagged) | — |
| 2 | Fold the observability event-bus (Tier 3 #8) in as a 5th pack? | ✅ RESOLVED — **defer; it became release 0.5.4** (former 0.5.4 bulkhead → 0.5.5) | — |
| 3 | Add an explicit NFR (UN-27) or treat as internal-quality? | ✅ RESOLVED — UN-27 added to `dev/user-needs.md` | — |
| K2 | Who runs the live smoke? | ✅ agent runs it on isolated dir+port (production-safe harness) | — |
| K3 | Version-bump/tag/push policy? | ✅ bump + CHANGELOG + annotated tag LOCAL only; push/publish = separate approval | sign-off |

## 7. Recent decisions (newest on top)

- 2026-06-27 — **Phase D design gate cleared (CONCERN, no BLOCK).** codex review
  (`0.5.3-design-review-20260627T181450Z.md`) resolved Q1–Q4 + 4 findings. Key
  outcomes baked into pack authoring: ACL scope = the full inventory (incl.
  health/proxy_errors/models_seam/docs); ACL owns `resolve_proxy_app()` + generic
  middleware-install mechanism (bootstrap owns order only); **revised
  parallelization to conflict-free waves** (ACL∥RACE → DECOUPLE → LATENCY) because
  guardian/extract/model_override_headers are shared.
- 2026-06-27 — **Kickoff resolved.** Base = `main` @ `fc67c33`. Event-bus deferred
  → **release 0.5.4**; former 0.5.4 (bulkhead) → 0.5.5; stale 0.6.0 tombstones
  noted. UN-27 added. K2 = agent-run smoke; K3 = local bump+tag, no push.
- 2026-06-26 — **Scaffolded for `/goal complete 0.5.3`:** added lifecycle map,
  acceptance scoreboard (UN-27 + AC-ACL/DECOUPLE/RACE), production-ready DoD;
  authored this board + the orchestrator prompt. ACL designated the anchor pack;
  Phase D codex gate required before any code.
- 2026-06-25 — Created from the audit (Tier 1 latency + Tier 2 structural not
  covered by 0.5.1). Behavior-preserving except the documented concurrency win.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` (incl. [[airlock-production-safety]])
3. `dev/plans/0.5.3-plan.md` 4. `dev/plans/prompts/0.5.3-ORCHESTRATOR.md`
5. **this file** §1+§2 6. `dev/notes/architecture-audit-0.5.0-2026-06.md`
(Part 1 ACL, Part 3 latency). Then re-derive pack state from witnesses.
