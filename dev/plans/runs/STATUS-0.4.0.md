# STATUS — 0.4.0  (live state board)

> Single source of truth for 0.4.0 live state. The orchestrator maintains it, one
> docs commit per transition. Implementer/reviewer agents never edit it. On
> resume, re-derive each pack's state from its witnesses (runbook §1.5) and trust
> the witnesses over this file.

_Last updated: 2026-06-14 · mainline: `main` @ `a45bd88`_

## 1. Current pack in flight + next action

- **Pack A: CLOSED** — merged `e35ab66` (codex PASS after fix-1). To-do #3.
- **Pack B: CLOSED** — merged `7644bca` (codex CONCERN low/test-only → override). To-do #4.
- **Pack C: CLOSED** — merged `0766c0f` + fixes `470cb78`. codex BLOCK→fix-1 (auth +
  streaming + CAS, codex-confirmed CLOSED) → BLOCK on lease-fencing → HITL accepted
  the design §3.7 at-least-once bound → fix-2 test-proved it. To-do #1 + §7.3/§7.4.
- **0.4.0 batch track COMPLETE on main** (`470cb78`); 263 A/B/C tests green together.
  **Operator live AI Studio e2e: PASSED** 2026-06-15 @ `e738858` (model `gemini-3.5-flash`,
  job completed ~40s, both rows round-tripped) — `tests/test_aistudio_batch_e2e.py`.
  Remaining: two small reserved-gaps below (TUI BATCH-label assertion + lease fencing).
- **Model note:** orchestrator-owned worktrees working end-to-end (baseline pick →
  worktree → spawn → codex → merge → cleanup). `isolation: worktree` removed from
  implementer.md (HITL); git ops `deny→ask` (`1f21233`).
- **Follow-up (small, non-blocking):** add an assertion that a batch record renders
  the `BATCH` label in TUI `_populate_table` (codex B finding #1).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| A | `is_batch_call` seam + guardian gating + null-route sweep | — | **CLOSED** | merge `e35ab66`; review `0.4.0-A-fix1-review-20260615T115144Z.md` |
| B | `write_batch_record` + TUI/monitor batch tagging | A ✓ | **CLOSED** | merge `7644bca`; review `0.4.0-B-review-20260615T121038Z.md` |
| C | batch gateway middleware + AI Studio adapter + idempotency §3.7 | A ✓ + B ✓ | **CLOSED** | merge `0766c0f`+`470cb78`; `0.4.0-C-closure.md` |
| D | Mistral batch adapter (thin: MistralBackend + alias dispatch + extra) | C ✓ | **CLOSED** | merge `8af9b69`; review `0.4.0-D-review-*` (PASS) |

## 3. Acceptance scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| #3 systemic `is_batch_call` null-route fix | A | ✅ |
| #4 batch observability | B | ✅ |
| #1 AI Studio batch gateway | C | ✅ (unit + **live e2e PASSED** 2026-06-15 @ `e738858`) |
| §7.3 result-file ≠ job expiry | C | ✅ |
| §7.4 `airlock_batch` no sync-path leak | C | ✅ |
| Mistral batch gateway | D | ✅ unit + integration + **live e2e PASSED** 2026-06-15 (`mistral-small-latest`) |

## 4. Parallelization plan

Serial ladder A → B → C (each depends on the prior's surface). No concurrency
this release. C touches config + middleware; serialize anything touching
`pyproject.toml`/`uv.lock`.

## 5. Outstanding worktrees

None — all removed after Pack A close.

## 6. Open HITL questions

| # | Question | Options + recommendation | Blocking? |
|---|----------|--------------------------|-----------|
| — | none yet | | |

## 7. Recent decisions (newest on top)

- 2026-06-15 — **Mistral live e2e PASSED.** After the `MISTRAL_API_KEY` was refreshed
  (the prior one 401'd on every call), restored `tests/test_mistral_batch_e2e.py` and
  ran the real round-trip via the production gateway path — `mistral-small-latest`
  completed ~60s, both rows staged + translated correctly. **Both** gateway providers
  (AI Studio + Mistral) are now live-verified end-to-end; docs un-qualified accordingly.
- 2026-06-15 — **Batch integration tests + e2e sweep.** Added
  `tests/test_batch_gateway_integration.py` (12 tests): full HTTP lifecycle through
  the real ASGI middleware (upload→create→poll→stage→content), multipart upload,
  cancel, auth/400/404 — parametrized over **both** providers. AI Studio live e2e
  re-PASSED. Two real bugs surfaced by the sweep:
  (1) **`mistral` extra was broken** — pinned `mistralai>=1.0.0` floated to `2.4.9`,
  which restructured the package (no top-level `from mistralai import Mistral`;
  class moved to `mistralai.client.sdk`). Adapter is v1-shaped → capped extra
  `>=1.0.0,<2` (resolves `1.12.4`, import works). Real packaging fix.
  (2) **Mistral live e2e blocked** — the configured `MISTRAL_API_KEY` returns
  `401 Unauthorized` on **every** call (even `models.list`), i.e. invalid/expired,
  not a batch-entitlement issue. **HITL: dropped the Mistral live e2e test**
  (`test_mistral_batch_e2e.py` removed); Mistral stays integration-verified, live
  gate deferred until a valid key exists. Docs updated to not overclaim Mistral.
- 2026-06-15 — **Pack D (Mistral adapter) CLOSED.** codex PASS, no findings (it
  cross-checked live Mistral docs). Thin adapter on the existing gateway:
  `MistralBackend` + `backend_for_alias` dispatch + `_GATEWAY_PROVIDERS` += mistral
  + 2 config aliases + `mistral` extra. metadata-keyed `list_jobs` makes §3.7
  reconcile work for Mistral. Merged `8af9b69`; 64 targeted / 104 broad green, no
  aistudio regression. First clean-PASS-on-first-review pack — thin-adapter
  discipline (no new security surface; reused C's reviewed core) paid off. Live
  Mistral e2e = future operator gate.
- 2026-06-15 — **Docs-sync gap (flagged, not fixed):** `scripts/setup-dev.sh` `--pip`
  path installs `.[test,metrics,tracing,search,s3,sql]` — missing `aistudio`,
  `vertex`, `tui`, `db`. So a `--pip` dev setup cannot run AI Studio/Vertex batch.
  The `uv` path (`uv sync --all-extras`) is fine. Surfaced during the update-docs
  reconciliation; `docs/getting-started/installation.md` now documents the real
  extras + per-provider batch extras. Script fix left to an owner (docs pass does
  not touch code).
- 2026-06-15 — **Live AI Studio e2e gate PASSED** (`tests/test_aistudio_batch_e2e.py`,
  opt-in `AIRLOCK_LIVE_AISTUDIO_E2E=1`, plan in `dev/aistudio-batch-e2e-test-plan.md`).
  Real round-trip vs Google's Gemini batch endpoint via the production `gateway`
  path (create→upload→create job→poll→fetch→stage) — completed ~40s, both rows
  translated correctly. First run surfaced a real nuance (not a bug): `gemini-3.5-flash`
  is a thinking model, so `max_tokens=16` finished `MAX_TOKENS` with empty content;
  raising to 512 yields clean `finish_reason=stop`. AI Studio batch path is now
  **verified working end-to-end**, not just unit-mocked.
- 2026-06-15 — **Pack C CLOSED; 0.4.0 batch track COMPLETE.** codex caught a real
  unauthenticated-ingress on the new gateway (28 green tests missed it) →
  fix-forward closed auth + streaming + immediate-CAS. 2nd BLOCK was codex applying
  exactly-once to a design (§3.7) that deliberately targets at-least-once +
  ≤1-duplicate-auto-cancel → HITL accepted the design bound; fix-2 test-proved it
  holds. Merged `470cb78`; 263 A/B/C tests green together. Reserved-gaps: tighter
  lease fencing (window-shrink only; exactly-once impossible) + Pack-B BATCH-label
  assertion + operator live e2e.
- 2026-06-15 — **Pack B CLOSED.** codex CONCERN (1 low, test-coverage only — BATCH
  label implemented but not asserted); impl verified correct → orchestrator
  override accepted (§7, prompt-induced low). Merged `7644bca`; 147 green on main.
  Small follow-up logged (assert the label). Dependency visibility confirmed: B's
  worktree (cut from post-A main) had `is_batch_call`.
- 2026-06-15 — **Pack A CLOSED.** fix-1 codex re-review = PASS (no findings);
  bypass closed, security property pinned by tests. Merged `e35ab66`; 77 green on
  main; worktrees/branches cleaned. End-to-end proof of the orchestrator-owned
  worktree loop (baseline→worktree→spawn→codex→merge→cleanup).
- 2026-06-15 — **Worktree-ownership model fixed.** Root cause of the earlier
  deadlock: imported fathomdb's "orchestrator owns baseline+merge" doctrine onto
  airlock's Agent-native isolation (harness owns worktree at a stale base, agent
  can't merge) = split-ownership state machine. Removed `isolation: worktree`
  (HITL) + git ops deny→ask → single owner restored.
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
