# STATUS — 0.5.2  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-06-26 (kickoff settled) · base branch: `feat/0.5.2-naming`
(cut from `main` @ `c3eaed7` — the 0.5.x train + v0.5.1 are merged into main, so
the old `feat/0.5.0-resilience-admin` base is moot)._

Release: **provider-explicit model naming (whole catalog) + machine-discoverable
capabilities.** Plan: `dev/plans/0.5.2-plan.md`. Orchestrator:
`dev/plans/prompts/0.5.2-ORCHESTRATOR.md`.

## 1. Current pack in flight + next action

- **In flight:** none — **Phase D DONE (design PASS).** Design note v3 codex-reviewed
  **PASS** (`0.5.2-NAMING-design-review-20260627T040523Z.md`); v1 BLOCK → v2 BLOCK →
  v3 PASS, all 3 findings resolved. Phase E may begin.
- **Next action:** **Phase E pack 1 — NAME-aliases.** Author
  `dev/plans/prompts/0.5.2-NAME-aliases.md` from SLICE-TEMPLATE with the resolved
  design decisions: (1) §4.1 collision-safe `model_alias` (two-pass loader +
  provider-aware `(provider,bare)→alias` strip; contradictory multi-provider prefix
  → `None`, no fuzzy/cache); (2) §4.2 shared `airlock_provider_for()` in new
  `airlock/capability.py`, used by `set_router_config`; (3) Appendix-A prefixed
  aliases dual-listed, legacy `deprecated:true`; (4) N6 consolidation. RED tests
  first; prove the router collision on a separate dir+port. Then the post-merge HITL
  smoke gate.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `DESIGN` | Design note covering N1–N6 + codex design-review PASS | — | **CLOSED ✅ (PASS v3)** | `dev/notes/design-provider-naming-and-capability-discovery.md` + `dev/plans/runs/0.5.2-NAMING-design-review-20260627T040523Z.md` |
| `NAME-aliases` | `provider/model` aliases for whole catalog (Appendix A); legacy dual-listed; collision-safe model_alias + shared classifier; slash-alias resolves, pins, attributes | DESIGN | **IMPLEMENTING** (wt `feat-0.5.2-NAME-aliases` @ 2f9b791) | `dev/plans/runs/0.5.2-NAME-aliases-output.json` |
| `CAP-modelinfo` | `model_info` capability blocks; `endpoints` derived from real wiring; exposed on `/model/info` | NAME-aliases | NOT_STARTED | `dev/plans/runs/0.5.2-CAP-modelinfo-output.json` |
| `CAP-v1models` | Additive `airlock:{provider,endpoints,underlying}` on `GET /v1/models` | CAP-modelinfo | NOT_STARTED | `dev/plans/runs/0.5.2-CAP-v1models-output.json` |
| `COMPAT-tests` | Cross-cutting regression: old+new alias resolve/pin/attribute; collision-safety; batch via both | CAP-v1models | NOT_STARTED | `dev/plans/runs/0.5.2-COMPAT-tests-output.json` |
| `DOCS` | UN-21/UN-22; design note as-built; user guides; header catalog; changelog + deprecation notice | NAME+CAP merged | NOT_STARTED | `dev/plans/runs/0.5.2-DOCS-output.json` |

States (furthest witnessed wins):
`WORKTREE_CREATED` → `IMPLEMENTING` → `IMPLEMENTED` (`output.json` + head past
baseline) → `REVIEWED` (`<pack>-review-<ts>.md` with a `## Verdict:` line) →
`MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| UN-21 — Discoverable Provider Selection (enumerate provider/region; pin by stable name; verify via header) | NAME-aliases, CAP-*, DOCS | ⏳ |
| UN-22 — Declared Capabilities (`endpoints` published + provably match routing) | CAP-modelinfo, COMPAT-tests, DOCS | ⏳ |
| No client breaks — legacy aliases still resolve+pin | NAME-aliases, COMPAT-tests | ⏳ |
| `/v1/models` augmentation additive (standard fields intact) | CAP-v1models | ⏳ |
| Slash alias does not collide with native provider parsing | NAME-aliases | ⏳ |

## 4. Parallelization plan

Config-editing packs (`NAME-aliases`, `CAP-modelinfo`) **serialize** — both edit
`config.yaml model_list`; no concurrent worktrees on it. `CAP-v1models` (new
module) and `COMPAT-tests` (tests only) can overlap the tail of the CAP work if
their files are disjoint, but the critical path is linear. Max 3 worktrees per the
runbook; here ≤1 is typically in flight given the shared `config.yaml`.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| `.claude/worktrees/feat-0.5.2-NAME-aliases` | `feat/0.5.2-NAME-aliases` | NAME-aliases | IMPLEMENTING |

(Empty when all packs are CLEANED.)

## 6. Open HITL questions

_All kickoff questions resolved 2026-06-26 (see §7). Remaining HITL gates are the
post-NAME-aliases smoke and release sign-off (orchestrator HITL gates)._

| # | Question | Resolution (2026-06-26) | Status |
|---|----------|-------------------------|--------|
| 1 | Working branch for 0.5.2? | fresh `feat/0.5.2-naming` from `main` @ `c3eaed7` | ✅ settled |
| 2 | Bare-alias default provider for Gemini | keep `gemini-3.5-flash` → AI Studio (incumbent) | ✅ settled |
| 3 | Deprecation window for legacy aliases | one minor release — **removed in 0.6.0** | ✅ settled |
| 4 | N6: consolidate `-batch` twins? | **consolidate** to one `provider/model` entry (all families) | ✅ settled |
| K2 | Who runs the live smoke? | agent, via production-safe isolated harness (separate dir+port, copied config) | ✅ default adopted |
| K3 | Version/CHANGELOG/tag/push policy | bump + cut CHANGELOG + annotated tag **LOCAL**; push/publish = separate approval | ✅ default adopted |

## 7. Recent decisions (newest on top)

- 2026-06-27 — **Design PASS (codex, v3).** Three design refinements forced by the
  review, now baked into the note + the pack scopes: (1) **collision-safe
  `model_alias`** — the existing loader's `_exact[bare]` is last-write-wins and
  `resolve()`'s prefix-strip is lossy; adding `aistudio/`+`vertex/` (or a native
  `vertex_ai/…` input) could silently repoint the bare AI-Studio default →
  two-pass immutable-explicit-keys loader + provider-aware `(provider,bare)→alias`
  strip (NAME-aliases). (2) **shared `airlock_provider_for()`** in new
  `airlock/capability.py` (fixes `enhanced/`→`gemini` at the source; used by
  `set_router_config`). (3) **vertex batch is region-gated** — `endpoints_for()`
  advertises `batch` only for an `airlock_batch` marker OR a **regional**
  `vertex_ai/` model; current `vertex_location: global` ⇒ `vertex/…` advertises
  `[chat]` (supersedes the plan's optimistic Appendix-A `chat,batch` for vertex).
- 2026-06-26 — **Kickoff HITL settled (user):** (1) branch = fresh
  `feat/0.5.2-naming` from `main` @ `c3eaed7`; (2) bare `gemini-3.5-flash` stays →
  AI Studio (ops-repointable, prefixed names are the stable contract); (3)
  deprecation window = **one minor release** — legacy bare/`-aistudio`/`-vertex`/
  `-batch` aliases **removed in 0.6.0**; (4) **N6 = consolidate** the `-batch`/
  `-aistudio` twins into one `provider/model` entry that serves sync + advertises
  `endpoints:[chat,batch]` (all families). K2/K3 orchestrator defaults adopted.
- 2026-06-24 — **Roll the prefix scheme to ALL providers** (user), not Gemini-only;
  legacy names deprecated, not removed, in 0.5.2. (Appendix A enumerates the map.)
- 2026-06-24 — Scope set: design (reviewed) → orchestrated TDD packs → docs
  (requirements + project + user-facing). "Provider," never "lane."

## 8. Compaction-resume checklist

When picking this release back up cold, read in order:
1. `AGENTS.md` — invariants.
2. `MEMORY.md` — feedback/project memories (incl. [[airlock-production-safety]]).
3. `dev/plans/0.5.2-plan.md` — ladder, register N1–N6, Appendix A.
4. `dev/plans/prompts/0.5.2-ORCHESTRATOR.md` — your operating contract.
5. **This file** §1 + §2 — live board.
6. `dev/plans/prompts/0.5.2-<pack-id>.md` — the pack itself (once authored).
