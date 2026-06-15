# STATUS — 0.4.0  (live state board)

> Single source of truth for 0.4.0 live state. The orchestrator maintains it, one
> docs commit per transition. Implementer/reviewer agents never edit it. On
> resume, re-derive each pack's state from its witnesses (runbook §1.5) and trust
> the witnesses over this file.

_Last updated: 2026-06-14 · mainline: `main` @ `a45bd88`_

## 1. Current pack in flight + next action

- **In flight:** Pack A **fix-1** — delegated implementer running in
  orchestrator-owned worktree `/tmp/airlock-0.4.0-A-fix1` (branch `0.4.0-A-fix1`
  from `930419b`), closing the caller-controlled batch-marker bypass + negative tests.
- **Resolved:** the worktree-ownership model. `isolation: worktree` removed from
  implementer.md (HITL) + git ops `deny→ask` (commit `1f21233`) → orchestrator now
  picks the baseline, creates the worktree, and merges. Model works end-to-end.
- **Next action:** await fix-1 → gate from git → re-run codex → on PASS merge
  `0.4.0-A-fix1` to main, clean both worktrees, close Pack A → spawn Pack B.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| A | `is_batch_call` seam + guardian gating + null-route sweep | — | REVIEWED (BLOCK) | `0.4.0-A-output.json`; `0.4.0-A-review-20260615T032242Z.md` |
| B | `write_batch_record` + TUI/monitor batch tagging | A | NOT_STARTED | — |
| C | batch gateway middleware + AI Studio adapter + idempotency §3.7 | A + B | NOT_STARTED | — |

## 3. Acceptance scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| #3 systemic `is_batch_call` null-route fix | A | ⏳ |
| #4 batch observability | B | ⏳ |
| #1 AI Studio batch gateway | C | ⏳ |
| §7.3 result-file ≠ job expiry | C | ⏳ |
| §7.4 `airlock_batch` no sync-path leak | C | ⏳ |

## 4. Parallelization plan

Serial ladder A → B → C (each depends on the prior's surface). No concurrency
this release. C touches config + middleware; serialize anything touching
`pyproject.toml`/`uv.lock`.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| (harness-created, reported by agent) | (auto) | A | IMPLEMENTING |

## 6. Open HITL questions

| # | Question | Options + recommendation | Blocking? |
|---|----------|--------------------------|-----------|
| — | none yet | | |

## 7. Recent decisions (newest on top)

- 2026-06-15 — **Pack A codex review = BLOCK** (confirmed by orchestrator code
  read). `is_batch_call` classified batch on caller-controlled `input_file_id`/
  `purpose=batch` regardless of `call_type` → guardrail bypass. Fix-1: make
  `call_type` authoritative; data markers only when `call_type` empty + payload
  isn't a completion; add negative tests. Caused by the prompt's "also match data
  markers, defense in depth" instruction — prompt defect, corrected for fix-1.
- 2026-06-15 — **Harness blocker surfaced:** `git merge`/`rebase`/`worktree
  add|remove`/`reset --hard` are session-wide `deny`; Agent-native isolation
  creates worktrees at a stale cached base (`90ee9c4`) agents can't advance.
  Blocks orchestrator merge, fix-1-in-same-worktree, and dependent packs. → HITL.
- 2026-06-14 — **Pack A spawned** (background implementer, worktree isolation)
  from base `a45bd88`. Canary — B/C blocked until A completes + merges.
- 2026-06-14 — **Preflight baseline** at `a45bd88`: git tree clean, deps synced,
  **1631 tests pass**, mypy/yamllint/mkdocs/version green. 3 PRE-EXISTING gate
  failures unrelated to batch work and to Pack A's files: `ruff check`
  (`tests/test_reasoning_stripper.py` F401), `ruff format`
  (`local_vllm_router.py`, `pii_guard.py`, `reasoning_stripper.py` + 2 tests),
  and the Dockerfile spacy-download step (needs network). Noted in Pack A's
  prompt so the pack isn't blamed; not launch-blocking.
- 2026-06-14 — Re-planned the monolithic 0.4.0 hand-off into 3 harness packs
  (A→B→C); live network e2e moved to an operator/HITL gate after C (agent can't
  restart the proxy / no network in unit tests).
- 2026-06-14 — codex (`gpt-5.5`, effort high) is the primary reviewer per the
  adopted harness; Claude `code-reviewer` is fallback.

## 8. Compaction-resume checklist

1. `AGENTS.md` 2. `MEMORY.md` 3. `dev/plans/0.4.0-plan.md` 4. `dev/PROGRESS.md`
top 5. **this file** §1+§2 6. `dev/plans/prompts/0.4.0-A-is-batch-call-seam.md`.
Then re-derive Pack A state from git + `0.4.0-A-output.json` before acting.
