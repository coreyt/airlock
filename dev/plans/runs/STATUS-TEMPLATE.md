# STATUS — {{RELEASE}}  (live state board)

> Single source of truth for this release's live state. The orchestrator
> maintains it, **one docs commit per transition**. Implementer/reviewer agents
> never edit it. On resume, this is a *cache* — re-derive from the on-disk
> witnesses (worktree list, `output.json`, `*-review-*.md`, merge commits) and
> trust the witnesses over this file on any conflict.

_Last updated: {{ISO_TIMESTAMP}} · mainline: `main` @ `{{HEAD_SHA}}`_

## 1. Current pack in flight + next action

- **In flight:** {{pack-id or "none"}}
- **Next action:** {{the single next step}}

## 2. Pack scoreboard

| Pack | Goal (1 line) | Depends on | State | Witness |
|------|---------------|------------|-------|---------|
| {{id}} | {{goal}} | {{dep or —}} | NOT_STARTED \| WORKTREE_CREATED \| IMPLEMENTING \| IMPLEMENTED \| REVIEWED \| MERGED \| CLOSED | {{path to output.json / review.md / merge sha}} |

States (furthest witnessed wins):
`WORKTREE_CREATED` (git worktree list shows it) →
`IMPLEMENTING` (agent active; transient, no witness) →
`IMPLEMENTED` (`output.json` present + branch head past baseline) →
`REVIEWED` (`<pack>-review-<ts>.md` with a `## Verdict:` line) →
`MERGED` (equivalent commit on `main`) →
`CLOSED` (CLOSED block in the plan + verdict promoted) →
`CLEANED` (worktree removed).

## 3. Acceptance / requirement scoreboard (optional)

| Requirement | Pack | Status |
|-------------|------|--------|
| {{req-id or test name}} | {{pack}} | ✅ \| ⚠️ \| ❌ \| ⏳ |

## 4. Parallelization plan

Which packs can run concurrently (max 3 worktrees), which must serialize
(shared editable files, `pyproject.toml`/`uv.lock` at root). {{fill}}

## 5. Outstanding worktrees

| Worktree path | Branch | Pack | State |
|---------------|--------|------|-------|
| {{.claude/worktrees/...}} | {{branch}} | {{pack}} | {{state}} |

(Empty when all packs are CLEANED.)

## 6. Open HITL questions

| # | Question | Options + recommendation | Blocking? |
|---|----------|--------------------------|-----------|
| {{n}} | {{question}} | {{options; recommended first}} | {{yes/no}} |

## 7. Recent decisions (newest on top)

- {{ISO date}} — {{decision + one-line rationale}}

## 8. Compaction-resume checklist

When picking this release back up cold, read in order:
1. `AGENTS.md` — invariants.
2. `MEMORY.md` — feedback/project memories.
3. `dev/plans/{{RELEASE}}-plan.md` — ladder + "Immediate Next Pack".
4. `dev/PROGRESS.md` top entry — newest-on-top session log.
5. **This file** §1 + §2 — live board.
6. `dev/plans/prompts/<pack-id>.md` — the pack itself.
