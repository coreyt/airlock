# STATUS — 0.5.7  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: 2026-08-01T20:00:00Z · mainline: `main` @ `b6e15df`_

## 1. Current pack in flight + next action

- **In flight:** none — all planned implementation packs are closed on `main`.
- **Closeout:** 0.5.7 is an internal odd-numbered release train. It has changelog,
  build, and CI evidence but is deliberately **not version-bumped, tagged, or
  published**. Follow-on work continues in 0.5.8.

## 2. Pack scoreboard

| Pack | Goal (1 line) | Prompt | Depends on | State | Witness |
|------|---------------|--------|------------|-------|---------|
| `0.5.7-F1` | `X-Airlock-Admission` header + real Retry-After on shed | `prompts/0.5.7-F1.md` | — | CLOSED | `runs/0.5.7-F1-output.json`; `ac7dd74` |
| `0.5.7-F2` | True async semaphore acquire/release for concurrency cap | `prompts/0.5.7-F2.md` | **F-1 merged** | CLOSED | `runs/0.5.7-F2-output.json`; `a5c8b2d`; stream-slot regression `5a76926` |
| `0.5.7-F3` | Helpful 404 + suggestions for refused model names | `prompts/0.5.7-F3.md` | — | CLOSED | `runs/0.5.7-F3-output.json`; `f2d777b` |
| `0.5.7-F4` | `enhanced/*` must not record $0.00 against real spend | `prompts/0.5.7-F4.md` | — | CLOSED (no production fix) | `runs/0.5.7-F4-output.json`; `157c343` |

States (furthest witnessed wins):
`WORKTREE_CREATED` → `IMPLEMENTING` → `IMPLEMENTED` (`output.json` + branch head past
baseline) → `REVIEWED` (`<pack>-review-<ts>.md` with a `## Verdict:` line) →
`MERGED` → `CLOSED` → `CLEANED`.

The closeout review found two admission-slot lifetime defects; both are fixed by
`5a76926` and the same focused test coverage. No new provider/API behavior was
introduced by the closeout.

## 3. Acceptance / requirement scoreboard

| Requirement | Pack | Status |
|-------------|------|--------|
| `X-Airlock-Admission: admitted` / `shed; retry_after=N` reaches the client | F-1 | ✅ |
| Concurrency cap is an exact hard limit, not an approximation | F-2 | ✅ |
| Refused model name returns 404 with a usable suggestion, not litellm's generic error | F-3 | ✅ |
| `error.message` is self-sufficient without parsing the structured block | F-3 | ✅ |
| Suggestions never leak a model outside the caller's catalog | F-3 | ✅ (current catalog is global) |
| `gemini-coding` records non-zero cost matching the target model | F-4 | ✅ (already propagated) |
| Long-context (>200K) records the surcharged rate, not the base rate | F-4 | ✅ (inner cost preserved) |
| Self-hosted vLLM models still record $0 — no fake pricing | F-4 | ✅ |

## 4. Parallelization plan

**F-3 and F-4 both run independently of F-1/F-2 and of each other.** F-4 touches
`providers/enhanced_passthrough.py` and the cost path (`litellm_adapter.py`);
F-3 touches resolution (`fast/model_alias.py`, `fast/guardian.py`,
`proxy_errors.py`); admission touches `fast/admission.py`. No overlap.

No `pyproject.toml` / `uv.lock` changes are expected from any of the four.

**F-1 before F-2** — F-1 is smaller and self-contained; F-2 changes the gate
interface and will touch the same files.

Max 3 worktrees (F-1/F-2 serialized in one, F-3 in another, F-4 in a third).

> ⚠️ **Shared-surface warning:** F-1 and F-3 both add a response header. Neither may
> invent a serializer — both reuse the `;`-joined `key=value` grammar from
> `transparency._mutation_token`. If F-1 lands first, F-3 follows its pattern.

## 5. Carried-in context (read before starting)

**Inherited from 0.5.6, unresolved — do not lose these:**

- **`max` reasoning effort is unresolved and blocks 0.5.8 P-2.** Treated as
  unsupported on a *guess*; needs one live call with a funded OpenAI key. Not 0.5.7
  scope, but if a funded key appears during this release, settle it opportunistically.
- **The warn-only measurement window is RUNNING: 2026-07-21 → 2026-08-21.** Runbook:
  `runs/warn-only-measurement-window.md`. **T-2 (confirm the events are queryable, not
  just greppable) should be done in week 1** — if they are not reaching the event
  store, that blocks the whole measurement and is far cheaper to find now than at T-4.
- **GPT-5.6 has never served a live request** — listed, priced, tiered, routed, but
  never exercised end to end (no quota on the available key).
- **Live gap:** `gemini-flash-lite` / `gemini-pro` / `gemini-flash` advanced generation
  in 0.5.6 with no client-facing disclosure. Fix is 0.5.8 P-6
  (`X-Airlock-Model-Alias`) — flagged there as "do NOT defer again".

**Repo health at kickoff:**

- CI green on all four product jobs (`test`, `lint`, `docker`, `security`) as of the
  2026-08-01 dependency workflow validation; Dependabot updater health is tracked
  separately from release acceptance.
  This was **not** true before 0.5.6 — `lint` and `docker` had been red since
  ~2026-06-29. Keep them green; a red board hides new failures, which is exactly how
  17 mypy errors accumulated unnoticed.
- Suite: 2695 passed, 106 skipped.
- `config.local.yaml` is a tracked default-empty extension point. A machine's local
  override is purposeful but must remain unstaged; use `git add -p` around it.

## 6. Deferred / out of scope

TUI work, Redis multi-process scale-out, new guardrail features, FathomDB expansion
(from the 0.5.7 plan). Plus everything in `0.5.8-plan.md`, most of which is gated on
the measurement window closing.
