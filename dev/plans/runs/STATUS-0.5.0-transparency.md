# STATUS — 0.5.0 Transparency Workstream  (live state board)

> Single source of truth for the 0.5.0 transparency workstream's live state. The
> orchestrator maintains it, one docs commit per transition. Implementer/reviewer
> agents never edit it. On resume, re-derive each pack's state from its witnesses
> (runbook §1.5) and trust the witnesses over this file. Parent board:
> `STATUS-0.5.0.md` (resilience+admin, all CLOSED).

_Last updated: 2026-06-24 · branch: `feat/0.5.0-resilience-admin` · **✅ WORKSTREAM COMPLETE — DoD MET. All 7 packs + 2 follow-up fixes (Site-12, streaming served-by) CLOSED & merged; full suite 2102 green; docs written; both HITL smoke-tests PASS (operator-confirmed); transparency folded into 0.5.0 release sign-off. Branch ready to push/tag on operator command (not pushed).**_

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

  - **OBS-served: ✅ CLOSED.** Merged `55b9690` (`--no-ff`). `X-Airlock-Served-By`/
    `-Region` from `attribute_served_backend` (streaming-correct, CC-T3/CC-T6),
    default-on + config-gated, omitted when provider unknown; `configure_transparency`
    wired at proxy startup; 14 tests. **Review:** codex was infra-unavailable this run
    (`bwrap: loopback` sandbox failure under concurrent load) → **fell back to the
    `code-reviewer` subagent (sonnet)** per reference §3.2, which caught a **critical
    null-metadata stash crash** (`data.setdefault("metadata", {})` → `None` when client
    sends `"metadata": null`) → **fixed** (`f6f0fae`, isinstance guard + 2 regression
    tests) + re-verified. Verdict: `0.5.0-OBS-served-review-20260624T223530Z.md`.
    **◆ HITL pending** — see Blocked.

  - **OBS-ledger: ✅ CLOSED.** Merged `40520e5` (`--no-ff`). 16/17 mutation sites +
    derived `drop_params` recorded via `record_mutation`/`record_redaction`
    (observe-only CC-T4; legacy `airlock_*` keys preserved CC-T1; PII value-free CC-T2);
    285 targeted + full suite (2064) green, purely additive (+817/−1). **Review:** codex
    `gpt-5.5` = **CONCERN** (1 medium — Site 12 `enhanced_passthrough` config-fallback
    injection unrecorded; Site 6 dual-record + drop_params + CC-T1/T2/T4 all verified
    correct) → **override-to-merge with rationale** (medium coverage gap, not a bug; 16
    sites clean; proper fix is cross-module) → **follow-up `OBS-ledger-passthrough`**.
    Verdict: `0.5.0-OBS-ledger-review-PROMOTED.md`.

**Engineering COMPLETE.** All 7 packs + 2 follow-up fixes (Site-12 ledger gap; streaming
served-by) merged + codex-reviewed; full suite 2102 green; observability docs written;
served-header smoke test executed (native served-by + explain envelope correct; streaming
bug found, fixed, and re-confirmed live). **Remaining = operator/HITL only, not orchestrator
code work:**
1. **OBS-accounting HITL** — validate accounting + dashboards on real traffic before GA
   (default `attribute_accounting_to_served: on`; opt-out documented).
2. **Gateway served-by validation** — the smoke test's vertex call needs `google-auth` in the
   test venv (`uv pip install google-auth` in the runtime, or validate on prod creds); native
   path already validated.
3. **0.5.0 release sign-off** (HITL) — fold transparency into the release; tag.
4. **Local hygiene** — `dev/smoketest/.runtime/` holds a copied `.env` (real keys); operator
   may `rm -rf dev/smoketest/.runtime` when done (gitignored, never committed).
5. **Nothing is pushed** — branch `feat/0.5.0-resilience-admin` advanced locally; push on the
   operator's go.

## ◆ Smoke-test results (OBS-served HITL — run 2026-06-24, isolated instance port 4137)

Harness (`dev/smoketest/`) built + a startup bug fixed (litellm resolves config handler
modules relative to the config dir → symlink `airlock/` into the runtime). Live run on an
isolated instance (production :4000 untouched):
- ✅ **native non-streaming**: `gemini-3.5-flash-aistudio` → `X-Airlock-Served-By: gemini`
  (real backend, not the alias). Correct.
- ✅ **`X-Airlock-Explain`**: `airlock.mutations` body envelope + header present (showed the
  `fallbacks=suppressed` mutation from `guardian.pin`). Correct.
- ⚠️ **gateway (vertex)**: call 500'd — test venv lacks `google-auth` (`No module named
  'google'`); a test-env dependency gap, NOT a header bug. **Gateway served-by unvalidated**
  — re-run after `uv pip install google-auth` (or `make sync`) in the runtime, OR validate
  on prod-equivalent creds.
- 🐞→✅ **streaming served-by BUG — FIXED + merged (`84f18c6`).** Same AI-Studio backend
  reported `gemini` (non-stream) vs `vertex_ai_beta` (stream). Root cause: litellm hardcodes
  `vertex_ai_beta` on the gemini stream wrapper; `api_base` disambiguates. `_normalize_served_
  provider` now maps `vertex_ai_beta`→`vertex_ai` and resolves to `gemini` on the AI-Studio
  host → streaming + non-streaming converge. codex PASS. **✅ RE-CONFIRMED live** — both
  non-streaming and streaming now report `X-Airlock-Served-By: gemini` for
  `gemini-3.5-flash-aistudio` (was `vertex_ai_beta` on stream). Production untouched.
- 🔧 **harness note**: uvicorn bound `0.0.0.0:4137` not loopback despite `AIRLOCK_HOST` —
  pass `--host` explicitly in the runbook (low risk; separate port).

## ◆ HITL gates — ✅ ALL CONFIRMED PASS (operator, 2026-06-24)

- ✅ **OBS-served smoke-test — PASS (operator-confirmed).** Served headers report the real
  backend (native + gateway). Orchestrator pre-validated native (`gemini`) + the explain
  envelope + streaming-after-fix live; operator confirmed the full gate incl. the gateway path.
- ✅ **OBS-accounting — PASS (operator-confirmed).** Accounting + dashboards validated on real
  traffic; spend keyed off the served provider is correct (default `attribute_accounting_to_served: on`).
- ✅ **0.5.0 release sign-off — transparency folded in (operator-confirmed).**

**Definition of Done: MET.** All 7 packs + 2 fixes CLOSED w/ promoted codex verdicts; UN-19 +
UN-20 satisfied; full suite green (2102); docs written; both HITL smoke-tests passed;
transparency folded into the 0.5.0 release sign-off. Branch `feat/0.5.0-resilience-admin` ready
to push/tag on the operator's command (not yet pushed).

### (historical) original HITL asks
- **OBS-accounting smoke-test (◆ HITL, before GA):** OBS-accounting now keys spend off
  the **served** provider (default on). Operator validates accounting + dashboards on real
  traffic before GA (a same-provider failover/backend-swap now bills the served backend).
  Opt-out: `transparency.attribute_accounting_to_served: false`.
- **DONE (was deferred):** CRLF-strip header values — fixed in the OBS-headers review-fix
  (`_header_safe()` on both `mutations_header` + `served_headers`).
- **Follow-up `OBS-ledger-passthrough` (Site 12, medium):** record the
  `EnhancedPassthroughProvider` config-fallback system-prompt injection in the outer
  ledger (Site 11 only covers the `litellm_params` path). Investigate metadata
  reachability empirically; likely extend the pre-call interceptor for config-resolved
  profiles. Do before release sign-off. Tracked as task.
- **User's in-progress budget edits (not ours):** uncommitted `config.yaml` +
  `airlock/fast/router.py` (`gemini` provider budget → 0) on the main checkout break 2
  existing `test_fast_router.py` default-budget tests; the committed transparency merges
  are green without those edits. Left untouched (the operator's work).

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| OBS-core | `airlock/transparency.py` dataclasses + `record_mutation`/`attribute_served_backend`/header serializers + `transparency.*` config | — | **✅ CLOSED** (merged `b997de0`) | `0.5.0-OBS-core-output.json`; review `…220523Z.md` |
| OBS-served | post-call attribution + flush in `model_override_headers.py`; `X-Airlock-Served-By`/`-Region`; streaming | OBS-core ✅ | **✅ CLOSED** (merged `55b9690`) ◆ HITL pending | review `…223530Z.md` (codex infra-fail → sonnet fallback → fixed) |
| OBS-ledger | retrofit every mutation site → `record_mutation` | OBS-core ✅ | **IMPLEMENTING** (worktree `obs-ledger` @ 827d126) | prompt `0.5.0-OBS-ledger.md` |
| OBS-headers | ledger → `X-Airlock-Mutations` (bounded) + `X-Airlock-Explain` body envelope | OBS-core/served/ledger ✅ | **✅ CLOSED** (merged `13110c2`) | codex CONCERN (CR/LF)→fixed; review `…PROMOTED.md` |
| OBS-log | `_build_record`: `mutations`/`served`/`attribution` | OBS-core/served/ledger ✅ | **✅ CLOSED** (merged `f933acf`) | codex **PASS**; review `…log-review-PROMOTED.md` |
| OBS-accounting | spend + rate-limit/quarantine keyed off **served** provider | OBS-served ✅ | **✅ CLOSED** (merged `b079b97`) ◆ HITL pending | codex CONCERN (silent fallback)→fixed |
| OBS-metrics-tui | `airlock_mutations_total` + served label/column | OBS-core/ledger/served ✅ | **✅ CLOSED** (merged `c31b362`) | codex **PASS**; review `…metrics-tui-review-PROMOTED.md` |

All packs PENDING — design complete, implementation not started.

## 3. Acceptance scoreboard (UN → pack)

| Requirement | Pack(s) | Status |
|-------------|---------|--------|
| UN-19 transparent request mutations | OBS-ledger ✅ + OBS-headers ✅ + OBS-log ✅ + OBS-metrics-tui ✅ | ✅ shipped (ledger + header + log + metrics counter); ⚠ Site-12 config-fallback injection gap tracked |
| UN-20 truthful serving-backend attribution | OBS-served ✅ + OBS-accounting ✅ + OBS-log ✅ | ✅ shipped (header + log + served-keyed accounting; HITL smoke-tests pending) |

## 4. Parallelization plan

Critical path: `OBS-core → OBS-served → OBS-headers`. `OBS-ledger` runs ∥
`OBS-served` (both depend only on `OBS-core`). `OBS-log` after served+ledger;
`OBS-accounting` after served; `OBS-metrics-tui` after ledger+served. Serialize
anything touching `pyproject.toml`/`uv.lock`. `OBS-served`, `OBS-headers`, and
`OBS-log` all touch the post-call flush hook / record builder — serialize or
rebase carefully to avoid conflicts on `model_override_headers.py` and
`enterprise_logger.py`.

## 5. Outstanding worktrees

- All wave-2 worktrees (obs-log/accounting/headers) removed after merge. Next:
  `.claude/worktrees/obs-metrics-tui` (base `13110c2`).
- Note: a stray `[main]` worktree under another session's scratchpad shows in
  `git worktree list` — not part of this workstream; do not remove.

## 6. Resolved design questions (were open; closed in Phase B)

| # | Question | Resolution |
|---|----------|------------|
| H1 | Does `_hidden_params` carry the served backend on the **streaming** path (CC-T6)? | **Yes.** `custom_llm_provider`/`api_base`/`region_name`/`model_id` are on the stream wrapper at header-flush time (`streaming_handler.py:745-749`), so `X-Airlock-Served-By` works for streams too; `response_cost` + post-call mutations land via `async_log_success_event` on the assembled response (`litellm_logging.py:2824-2847`). |
| H2 | Default for `attribute_accounting_to_served` | **On**, documented as a bugfix, with a one-line opt-out. Operator confirms at the OBS-accounting HITL. |
| H3 | `X-Airlock-Mutations` default-on vs opt-in | **Default-on compact** (observability-as-benefit is the point); values never leaked (CC-T2); opt-out via `transparency.mutation_headers: off`. |

## 7. Recent decisions (newest on top)

- 2026-06-24 — **ALL 7 TRANSPARENCY PACKS CLOSED.** Wave 2 (headers/log/accounting/
  metrics-tui) implemented in parallel (disjoint files), reviewed by codex **serially at
  zero load** (parallel codex sandboxes re-trigger the load-correlated `bwrap` failure).
  Verdicts: OBS-log PASS; OBS-metrics-tui PASS; OBS-accounting CONCERN (silent
  attribution-failure fallback → fixed: warning+exc_info); OBS-headers CONCERN (header
  values not CR/LF-normalized → fixed: `_header_safe()` on both serializers, also closing
  the OBS-served CRLF nit). All merged through `c31b362`. UN-19 + UN-20 shipped. Remaining:
  Site-12 follow-up, live served-header smoke test (harness committed at
  `dev/smoketest/`), accounting HITL, observability docs, release sign-off.
- 2026-06-24 — **OBS-served CLOSED (pack 2/7).** Served-backend headers shipped
  default-on. **Reviewer-fallback exercised:** codex hit a `bwrap` sandbox/network
  failure under concurrent load and could not inspect → used the `code-reviewer`
  subagent (sonnet, reference §3.2), which caught a real critical bug (null-metadata
  `setdefault` crash on the hot path) the 12 tests missed → fixed + 2 regression tests.
  Lesson: run codex review when worktree/agent load is low; the Claude fallback is a
  genuine safety net, not a rubber stamp. Merged `55b9690`. ◆ HITL smoke-test pending.
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
