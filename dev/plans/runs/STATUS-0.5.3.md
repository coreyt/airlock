# STATUS — 0.5.3  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-26 (kickoff scaffold) · base branch: `feat/0.5.0-resilience-admin` (recommended: cut after 0.5.1 merges)_

Release: **decouple from LiteLLM internals (ACL) + unblock the hot path
(Presidio) + structural hygiene.** Plan: `dev/plans/0.5.3-plan.md`. Orchestrator:
`dev/plans/prompts/0.5.3-ORCHESTRATOR.md`. Audit source-of-record:
`dev/notes/architecture-audit-0.5.0-2026-06.md`.

## 1. Current pack in flight + next action

- **In flight:** none — release not yet started.
- **Next action:** **HITL kickoff** (§6: confirm base + the event-bus fold-in
  decision). Then Phase A (UN-27) + build the **ACL call-site inventory**, then
  **Phase D codex design gate** (PASS required) over the plan + inventory + the
  Presidio change. Only then author `ACL` (the anchor pack).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `ACL` | `litellm_adapter.py` — single owner of all LiteLLM-internal reads; migrate call sites (parity) | design PASS | NOT_STARTED | `dev/plans/runs/0.5.3-ACL-output.json` |
| `LATENCY` | Presidio → `to_thread`; shared text-extract; vLLM TTL | — (∥) | NOT_STARTED | `dev/plans/runs/0.5.3-LATENCY-output.json` |
| `DECOUPLE` | Break `fast`↔`guardrails` cycle; extract `proxy_bootstrap.py` | ACL | NOT_STARTED | `dev/plans/runs/0.5.3-DECOUPLE-output.json` |
| `RACE` | `threat_score` lock; identity/config consolidation | — (∥) | NOT_STARTED | `dev/plans/runs/0.5.3-RACE-output.json` |
| `OBS-eventbus` *(candidate)* | Single `RequestEvent` + recorder (audit Tier 3 #8) | — | DEFERRED pending HITL | (open question) |

States (furthest witnessed wins): `WORKTREE_CREATED` → `IMPLEMENTING` →
`IMPLEMENTED` → `REVIEWED` → `MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| UN-27 — predictable latency under concurrency (no Presidio serialization) | LATENCY | ⏳ |
| AC-ACL — single ownership of internal reads; byte-parity headers/attribution | ACL | ⏳ |
| AC-DECOUPLE — no `fast`↔`guardrails` cycle; install order asserted | DECOUPLE | ⏳ |
| AC-RACE — no lost `threat_score`; one client-identity path | RACE | ⏳ |

## 4. Parallelization plan

`ACL` is the anchor (lands first; its seam stabilizes `DECOUPLE`). `LATENCY` and
`RACE` touch disjoint files and run ∥. `DECOUPLE` depends on `ACL` (bootstrap
app-object access). Max 3 worktrees.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| _(none yet)_ | | | |

## 6. Open HITL questions

| # | Question | Recommendation | Blocking? |
|---|----------|----------------|-----------|
| 1 | Base: cut after 0.5.1 merges, or stack on the train now? | after 0.5.1 (DualCache/STORE-seam on base) | kickoff |
| 2 | Fold the observability event-bus (Tier 3 #8) in as a 5th pack? | defer — independent + sizable; keep 0.5.3 tight | kickoff |
| 3 | Add an explicit NFR (UN-27) or treat as internal-quality? | add UN-27 as the latency anchor | Phase A |

## 7. Recent decisions (newest on top)

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
