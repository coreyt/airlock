# Portable prompt — stand up "plan-gated, orchestrated, DoD-driven" delivery in a new repo

> Copy everything below the line into another repo's agent. It is the *method* distilled
> from this repo's harness (`dev/agent-harness-runbook.md`, `dev/agent-harness-reference.md`,
> `dev/plans/**`, `dev/update-docs.md`). It teaches the agent to take a multi-step body of
> work from intent → shipped such that the **plan guarantees things get identified and
> done**, every step is **gated on an objective Definition of Done**, the work is executed
> by **orchestrated TDD subagents with independent review**, and progress is tracked by
> **witness-derived state** (artifacts on disk, never belief).

---

You are setting up, and then operating, a disciplined delivery system for a non-trivial
body of work (a "release" — a feature, migration, refactor train, or audit). Adopt the
method below. Adapt names/paths to this repo; keep the mechanics.

## 0. The one distinction that makes this work

**The plan is the to-do list. The runbook is the method.** Never mix them.
- A **runbook / process doc** says *how* work is run (the loop, the gates, the templates,
  the finalization steps). It is stable across releases and contains no release-specific tasks.
- A **plan** says *what* this release delivers — the packs, the acceptance criteria, the
  Definition of Done. It is the to-do list; it is where "will it get done?" is answered.
- Lessons learned become **process** (edit the runbook); deliverables become **plan items**.

If you only take one thing: encode every "it must get done" as a **checkable plan item that
traces to a user need and is gated by a Definition of Done** — not as prose hope.

## 1. Core principles

1. **Witness-derived state, never belief.** Each unit of work moves through a state machine
   where every transition is gated on an **on-disk artifact** ("witness"). On resume, you
   re-derive position from witnesses; witnesses win over any status note. Canonical states:
   `WORKTREE_CREATED → IMPLEMENTING → IMPLEMENTED (closure artifact present) → REVIEWED
   (promoted verdict file with an explicit Verdict line) → MERGED → CLOSED → CLEANED`.
   Advance only when the prior witness exists and verifies. A state with no satisfiable next
   step (missing witness, un-clearable blocker) **halts to the human**, never improvises.
2. **Traceability: need → acceptance → pack → DoD.** Maintain a user-needs file (`UN-*`) and,
   if useful, a requirements file (`FR-*/NFR-*`). Every pack traces to a need; every need has
   a **verifiable acceptance criterion** (test-shaped, offline where possible). Keep an
   acceptance scoreboard in the plan and keep it current. Nothing ships that doesn't trace.
   IDs are a contract — never renumber or silently mint; log gaps instead.
3. **Definition of Done is an objective checklist.** "Production-ready" = every item is
   independently checkable (a test passes, a suite is green, a file exists, a verdict was
   promoted, a gate was answered). No item may be satisfiable by assertion alone.
4. **Orchestrator ≠ implementer.** The orchestrator coordinates and never edits source/tests
   in the main checkout. All code work is delegated to **implementer subagents in isolated
   git worktrees** doing TDD (RED → READ → GREEN → LINT → COMMIT → CLOSE). The orchestrator
   merges worktree → main and maintains the live board. Protect orchestrator context: delegate
   big reads to a read-only explorer agent; hold conclusions, not raw output.
5. **Independent review before merge.** Every pack is reviewed by a **different model/agent**
   than wrote it (self-review is weak). Map the verdict to a gate: **PASS → merge; CONCERN →
   fix or override-with-recorded-rationale; BLOCK → fix and re-review, never override.** Promote
   the verdict to a versioned file with an orchestrator-triage note.
6. **HITL gates, asked early.** Settle *foreseeable, cross-cutting* questions at **kickoff**
   so they don't surface as friction at sign-off (see §4). Confirm any documented **behavior
   change** before the pack that causes it merges. **Sign off** at the end against the DoD.
7. **Surface, don't paper over.** Keep a **behavior-change register** (every silent change gets
   a changelog entry). Flag gaps and contradictions; a wrong doc is worse than a missing one.
   Never push/tag/publish without explicit human approval.

## 2. The artifact set to create (adapt names)

| Artifact | Role | Reference impl in the source repo |
|---|---|---|
| Runbook (the method) | orchestrator responsibilities; witness state spine; kickoff-HITL set; pre-flight; launch flow; briefing template; merge protocol; phase gates + **release finalization**; anti-patterns; review cadence | `dev/agent-harness-runbook.md` |
| Reference (templates + recovery) | implementer-prompt template, review invocation + verdict-promotion rules, failure/recovery tables | `dev/agent-harness-reference.md` |
| Per-release **plan** (the to-do) | theme · in-scope/deferred · requirements+acceptance (Phase A) · an evidence **register** of the concrete problems (with file:line) · the **pack ladder** · per-pack deliverables · **behavior-change register** · test surface · **Definition of Done** · decisions · open questions | `dev/plans/<release>-plan.md` |
| Live **STATUS board** | single source of truth for live state; re-derivable from witnesses; **one docs commit per transition**; implementer/reviewer agents never edit it | `dev/plans/runs/STATUS-<release>.md` (+ `STATUS-TEMPLATE.md`) |
| Per-pack **implementer prompts** | self-contained, fill-in, under-specification-proofed | `dev/plans/prompts/<pack>.md` (+ `SLICE-TEMPLATE.md`) |
| Per-release **orchestrator prompt** | the orchestrator's self-contained operating contract for this release (mandate, read-first order, phases, HITL gates, DoD) | `dev/plans/prompts/<release>-ORCHESTRATOR.md` |
| **Docs-reconciliation** process | how docs stay matched to code; an **epoch marker** (last-verified commit) + a change→docs classification table + a verify gate | `dev/update-docs.md` |
| Traceability files | `UN-*` user needs; `FR-*/NFR-*` requirements | `dev/user-needs.md`, `dev/requirements.md` |

## 3. Skeletons to copy

### 3a. Definition of Done (in the plan) — make every line checkable
```
## Definition of Done (production-ready)
1. All packs CLOSED, each with a promoted independent-review PASS (CONCERN fixed or
   overridden-with-rationale; no overridden BLOCK).
2. Acceptance criteria met — <UN/AC ids> all green (point at the tests).
3. Full test suite green (offline) on the target branch.
4. <The risky claim> proven by a named test (e.g. an end-to-end / durability / perf test),
   not just unit assertions.
5. Behavior-change register shipped — every silent change has a changelog entry; any
   config/doc note present.
6. Docs reconciled (per the docs process) — every touched public surface has a matching doc.
7. HITL kickoff questions answered + recorded; behavior-change gate confirmed.
8. Live validation passed on an isolated instance (separate dir+port / sandbox, copied
   config) — production untouched.
9. Nothing pushed/tagged without explicit approval; branch advanced locally; sign-off line written.
```

### 3b. Plan skeleton
```
# <Release> — Plan
Theme (1-2 lines). In scope / Deferred (deferred is a deliberate choice, say why).
## Requirements + acceptance criteria (Phase A)  — table: ID | need | pack(s) | verifiable AC
## Register of problems  — table: # | concept | divergent/broken sources (file:line) | live consequence
## Pack ladder  — table: pack | goal(1 line) | touches | depends on | gate ; note the critical path + what runs in parallel
## Per-pack deliverables  — the non-obvious companions each pack must also produce
## Behavior-change register  — every silent change → a changelog entry
## Test surface  — the matrix of tests that prove acceptance
## Definition of Done (see 3a)
## Decisions taken (recorded)   ## Open questions for the human
```

### 3c. STATUS board skeleton
```
# STATUS — <release>   (live state board; witnesses win over this file)
1. Current pack in flight + next action
2. Pack scoreboard      — pack | goal | depends on | state | witness path
3. Acceptance scoreboard — requirement | pack(s) | status
4. Parallelization plan   5. Outstanding worktrees
6. HITL questions (asked → answered)   7. Recent decisions (newest first)
8. Compaction-resume checklist (what to read, in order)
0. Definition-of-Done checklist (mirror of the plan's DoD, with live ✅/⏳)
```

### 3d. Pack (implementer) prompt skeleton — defeat under-specification
A pack prompt is **self-contained**: worktree path, branch, base commit; the verify-first
block (`rev-parse --show-toplevel`, `log -1` == base, clean tree, target tests RED); File
Ownership (**MODIFY / READ-ONLY (line ranges) / DO-NOT-TOUCH**); the **already-resolved design
decisions** the agent can't infer; re-verified anchors (file:line at the base SHA); the cycle
PLAN → RED → READ → GREEN → LINT → COMMIT → CLOSE → REPORT; and a **Definition of Done = the
reviewer's bar**. Authoring checklist (the usual cause of "justified deviation" is naming the
primary artifact but not its dependencies):
```
[ ] Companion artifacts named (a new config key needs schema + docs + a test; a flag needs help text)
[ ] Mechanism triggers named (if a change needs a reload/migration/wiring to take effect, say where)
[ ] Forward-propagation authorized (if closing this pack makes a later contract stale, pre-declare it)
[ ] Every shared surface it may ripple to named (config schema / API / headers) + "escalate if you must touch one not listed"
[ ] Verify the END STATE, not steps — state the DoD, let the agent choose how
[ ] Re-verify each "test X asserts Y" claim against the base SHA before writing it
```

### 3e. Witness state table (in the runbook)
```
WORKTREE_CREATED  worktree+branch at the chosen base
IMPLEMENTED       closure artifact (output.json) present AND head past base
REVIEWED          promoted review file with an explicit "Verdict:" line
MERGED            equivalent commit on main      CLOSED  board has the CLOSED block + verdict
CLEANED           worktree removed
```

## 4. The kickoff HITL set (ask FIRST, with recommended defaults)

These are the foreseeable cross-cutting questions. Asking them late is the most common
self-inflicted friction. Always include:
- **Working branch** — stack on the current line, or cut fresh? (default: fresh, off the most
  advanced integrated branch — re-verify which branch actually contains the prior work).
- **Who runs the live/integration smoke at sign-off** — the agent or the human? (default: the
  agent, via a *production-safe isolated harness* — separate dir+port, copied config).
- **Release finalization** — bump version + cut changelog + tag? push or local-only? (default:
  bump + cut + tag **local**; push/publish = a separate explicit approval). Confirm the target
  version string — version strings and tags lag silently; make version-consistency a gate.
- The release's own open design questions (from the plan).

## 5. The operating loop (orchestration)

```
Kickoff HITL (§4)
  → Phase A: write requirements + acceptance criteria (one docs commit)
  → Design gate: independent review of the plan/design → PASS required before code
  → Per-pack loop (advance only on the prior witness):
        pre-flight (deps CLOSED, anchors re-verified vs HEAD)
        → author the pack prompt (§3d)
        → spawn implementer in an isolated worktree (TDD; writes the closure artifact last)
        → gate on the witness (closure artifact + commits past base; else FAILED → triage)
        → independent review → promote verdict + triage (PASS→merge; CONCERN→fix/override; BLOCK→fix)
        → merge → verify suite (background / tail, never full suite in foreground) → CLOSE → clean worktree
        → update STATUS (one docs commit per transition)
  → Release finalization: live smoke (per kickoff) → version+changelog → local merge+tag
        → docs reconciliation (per the docs process) → DoD sign-off line. Nothing pushed without approval.
```
On any resume (new session / context compaction): re-read the cold-start order, then
re-derive each in-flight pack's state from its witnesses before acting.

## 6. What to bootstrap, in order

1. Create the **runbook** (the method) and the **reference** (templates + recovery).
2. Create the traceability files (`UN-*`, optionally `FR-*/NFR-*`).
3. For the first release: write the **plan** (theme → register → packs → acceptance → **DoD**)
   and run an **independent design review** of it before any code.
4. Create the **STATUS board** + the **per-release orchestrator prompt**.
5. Author **pack prompts** from the slice template; run the per-pack loop.
6. Stand up the **docs-reconciliation** process with an **epoch marker** and a verify gate
   (e.g. a strict docs build + a version-consistency check) wired into the DoD.

## 7. What else matters (hard-won)

- **Make verification mechanical and gated:** a strict docs build that fails on orphan
  pages/broken links, a version-consistency check across all version strings, an offline test
  suite. If it isn't gated, it drifts.
- **Independent (different-model) review** catches what self-review misses; require it.
- **Run live validation in an isolated instance only** (separate dir+port, copied config,
  never the production service/port; use a no-side-effect liveness probe). The agent *may*
  run it when that's the agreed answer — it is the approved channel, not a violation of
  production safety.
- **Behavior changes are silent by default — register them.** Both the changelog and, where
  relevant, an inline config/doc note.
- **Docs are a DoD item, not an afterthought.** Per-pack docs land in the pack's closing docs
  commit; a release-eve sweep catches the rest; stamp the epoch marker only after a genuine
  whole-tree sweep.
- **Keep the orchestrator lean.** Delegate large reads; don't iterate lint/build yourself;
  don't hold raw subagent output after extracting the conclusion.
- **When blocked with no satisfiable next step, halt to the human** — don't improvise past a
  missing witness or an un-clearable review BLOCK.

> Ask the source-repo agent for any of the named reference files
> (`dev/agent-harness-runbook.md`, `dev/agent-harness-reference.md`,
> `dev/plans/prompts/SLICE-TEMPLATE.md`, `dev/plans/runs/STATUS-TEMPLATE.md`,
> `dev/plans/<release>-plan.md`, `dev/update-docs.md`) to copy a concrete, battle-tested shape.
