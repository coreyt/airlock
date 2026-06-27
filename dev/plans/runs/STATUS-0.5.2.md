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

- **In flight:** none — **CAP-v1models CLOSED** (merged `0beed30`; codex CONCERN→fix→PASS;
  `/v1/models` smoke PASS — 73/73 models carry the additive `airlock` object, standard
  OpenAI fields intact). **discover→pin→verify proven end-to-end** (`/v1/models` +
  `/model/info` + `X-Airlock-Served-By`).
- **Done:** DESIGN, NAME-aliases (+smoke), CAP-modelinfo (+smoke), CAP-v1models (+smoke).
- **Next action:** **COMPAT-tests** (tests-only; no config edits) — cross-cutting
  regression locking the whole contract: old+new alias resolve/pin/attribute; collision-
  safety (bare never repoints; native `vertex_ai/` → vertex); batch create via old+new
  alias hits same backend; `/model/info`+`/v1/models` capability shape additive. Cut
  worktree from current HEAD. Then DOCS + release sign-off.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| `DESIGN` | Design note covering N1–N6 + codex design-review PASS | — | **CLOSED ✅ (PASS v3)** | `dev/notes/design-provider-naming-and-capability-discovery.md` + `dev/plans/runs/0.5.2-NAMING-design-review-20260627T040523Z.md` |
| `NAME-aliases` | `provider/model` aliases for whole catalog (Appendix A); legacy dual-listed; collision-safe model_alias + shared classifier; slash-alias resolves, pins, attributes | DESIGN | **CLOSED ✅** (merged `4905150`; codex BLOCK→fix→CONCERN→fix; HITL smoke PASS) | `0.5.2-NAME-aliases-output.json` + `-review-20260627T043448Z.md` + `-HITL-smoke-20260627T131827Z.md` |
| `CAP-modelinfo` | computed `model_info` injected at startup (`proxy._prepare_runtime_config`); `endpoints` region-gated; served natively on `/model/info` | NAME-aliases | **CLOSED ✅** (merged `c26c01a`; codex PASS; /model/info smoke PASS) | `0.5.2-CAP-modelinfo-output.json` + `-review-20260627T133638Z.md` + `-smoke-20260627T133749Z.md` |
| `CAP-v1models` | Additive `airlock:{…}` on `GET /v1/models`+`/models` (ASGI response seam, reuse `capability_record`) | CAP-modelinfo | **CLOSED ✅** (merged `0beed30`; codex CONCERN→fix; /v1/models smoke PASS 73/73) | `0.5.2-CAP-v1models-output.json` + `-review-20260627T135029Z.md` + `-smoke-20260627T135420Z.md` |
| `COMPAT-tests` | Cross-cutting regression (tests-only): old↔new parity; collision-safety; batch backend parity; capability↔wiring; two-surface agreement | CAP-v1models | **IMPLEMENTING** (wt `feat-0.5.2-COMPAT-tests` @ dd58bda) | `dev/plans/runs/0.5.2-COMPAT-tests-output.json` |
| `DOCS` | UN-21/UN-22; design note as-built; user guides; header catalog; changelog + deprecation notice | NAME+CAP merged | NOT_STARTED | `dev/plans/runs/0.5.2-DOCS-output.json` |

States (furthest witnessed wins):
`WORKTREE_CREATED` → `IMPLEMENTING` → `IMPLEMENTED` (`output.json` + head past
baseline) → `REVIEWED` (`<pack>-review-<ts>.md` with a `## Verdict:` line) →
`MERGED` → `CLOSED` → `CLEANED`.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| UN-21 — Discoverable Provider Selection (enumerate provider/region; pin by stable name; verify via header) | NAME-aliases, CAP-*, DOCS | ✅ enumerate (/v1/models+/model/info, 73/73) + pin + verify (X-Airlock-Served-By) all proven live; DOCS recipe pending |
| UN-22 — Declared Capabilities (`endpoints` published + provably match routing) | CAP-modelinfo, COMPAT-tests, DOCS | ✅ published on `/model/info`+`/v1/models`, region-gated batch correct live; cross-cutting lock in COMPAT |
| No client breaks — legacy aliases still resolve+pin | NAME-aliases, COMPAT-tests | 🟡 legacy resolve+pin covered by NAME-aliases tests; cross-cutting in COMPAT |
| `/v1/models` augmentation additive (standard fields intact) | CAP-v1models | ✅ seam tests + live smoke (73/73 additive, std fields intact) |
| Slash alias does not collide with native provider parsing | NAME-aliases | ✅ unit (collision-safe loader/resolve) + live smoke (aistudio/vertex route distinctly) |

## 4. Parallelization plan

Config-editing packs (`NAME-aliases`, `CAP-modelinfo`) **serialize** — both edit
`config.yaml model_list`; no concurrent worktrees on it. `CAP-v1models` (new
module) and `COMPAT-tests` (tests only) can overlap the tail of the CAP work if
their files are disjoint, but the critical path is linear. Max 3 worktrees per the
runbook; here ≤1 is typically in flight given the shared `config.yaml`.

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| `.claude/worktrees/feat-0.5.2-COMPAT-tests` | `feat/0.5.2-COMPAT-tests` | COMPAT-tests | IMPLEMENTING |

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
