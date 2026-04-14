# Agent Harness Runbook

Operational playbook for Claude operating as an orchestrator of subagents
performing test-driven development on fathomdb. Read this at session start.

For prompt templates, failure recovery, and infrastructure details, see
[agent-harness-reference.md](agent-harness-reference.md).

---

## 1. Orchestrator Responsibilities

The orchestrator coordinates — it does not implement.

**Do:**
1. Brief subagents with complete, unambiguous prompts (including the
   absolute worktree path and a clean base commit from main).
2. Track progress via tasks and status updates to the user.
3. Merge completed work from each worktree back into main after
   verification, then remove the worktree.
4. Adapt when agents fail — diagnose before retrying.
5. Identify cross-agent design conflicts before they become code bugs.
6. Protect your own context — delegate all code work to subagents.

**Do NOT:**
- Edit source or test files in `/home/coreyt/projects/fathomdb` directly
  (delegate to an implementer in a worktree).
- Iterate on clippy/fmt errors (delegate to a cleanup implementer).
- Read large source files (delegate to an Explore agent).
- Run the full test suite in the foreground (run in background or `| tail -5`).
- Debug code by running ad-hoc scripts (delegate to an agent).
- Hold raw agent output in context after extracting findings.
- Push, force-push, or amend commits without explicit user approval.

**Why this matters:** Editing files directly on main leaves dirty tracked
files that break worktree creation, which cascades into every subsequent
agent launch.

---

## 2. Pre-Flight

Run `scripts/preflight.sh` before every agent launch — not just at
session start, but after every agent completion, merge, or failure.

```bash
cd /home/coreyt/projects/fathomdb
./scripts/preflight.sh              # standard checks
./scripts/preflight.sh --baseline   # include cargo check baseline (slow, session start only)
```

The script checks: branch, HEAD, clean working tree, active worktree
count, disk space, cargo/rustc toolchain, and optional python venv.
Exit code 1 means a gate failed — fix it before proceeding.

If a gate fails:
- Not on `main` → switch, or ask the user.
- Dirty tree → see reference Section 6 (Dirty State Recovery).
- Stale worktrees → `git worktree remove <path> --force && git worktree prune`.
- Disk <10GB → free space before launching (cargo target dirs are large;
  `cargo clean` on the worktree you can spare).
- Missing cargo/rustc → install toolchain, don't retry.
- Missing `python/.venv` → only matters if the pack touches Python bindings;
  run `pip install -e python/` (do NOT `cargo build` + copy the .so by hand).

**Subagents must only branch from a clean main.** The orchestrator is
responsible for ensuring main is clean and at a known commit BEFORE
creating a worktree. Record the commit hash — every implementer prompt
must include it so the agent can verify its base.

For a baseline compile state, run `./scripts/preflight.sh --baseline`
once per session and note any pre-existing warnings. Include them in
implementer prompts so a pack isn't blamed for pre-existing noise.

---

## 3. Launch Flow

### Step 1: Canary

Never launch multiple agents in parallel until one has completed the
full cycle. Infrastructure failures (permissions, worktree, disk) affect
all agents equally — launching N agents into a broken environment wastes
N × the tokens.

**Step 1a: Permission canary (first agent of every session).**
Before any real implementer, launch one minimal implementer whose entire
prompt is: "Run `./scripts/agent-permission-canary.sh` inside the
worktree. Report the exit code and stdout. Do not edit or commit any
files." If it exits non-zero, the `.claude/settings.json` allow/deny
list is incomplete or out of sync with the subagent environment — fix
the list and re-run the canary before any real work. This catches Bash
permission-denied failures up front instead of during the first real
pack, and also verifies that the deny list actually blocks destructive
commands (including `cargo publish`, `git push`, `rm`, `curl`). The
canary implementer can run foreground since it's <10s.

**Step 1b: Implementation canary.**
1. Run `./scripts/preflight.sh`. Confirm main is clean.
2. Create the worktree from the recorded base commit:
   ```bash
   cd /home/coreyt/projects/fathomdb
   git worktree add .claude/worktrees/{BRANCH} -b {BRANCH} {BASE_COMMIT}
   ```
   Note the absolute path — this is `{WORKTREE_ABSOLUTE_PATH}` for the
   prompt template.
3. Launch 1 implementer with `run_in_background: true`,
   `isolation: "worktree"`. The agent definition MUST grant `Bash` or
   the agent will not be able to run `cargo test`, `cargo clippy`, or
   commit.
4. Wait for it to complete. Do not launch other agents.
5. If it succeeds: proceed with parallel launches (up to 3 concurrent).
6. If it fails on infrastructure:
   - Bash permission denied → **escalate to user immediately.** Do not retry.
   - Agent could Edit but not Bash → the agent definition is missing the
     Bash tool. Fix the definition, then relaunch.
   - `git rev-parse` failed → run pre-flight, fix dirty state
     (see [reference Section 6](agent-harness-reference.md#6-dirty-state-recovery)), retry once.
   - Worktree creation failed → check disk, prune worktrees, retry once.
7. If it fails on implementation: absorb findings, re-brief, relaunch.

### Step 2: Parallel Launches

After a successful canary:
- Max 3 concurrent worktree agents (each gets its own cargo target dir,
  which is large — budget ~5-10 GB per active worktree).
- Check disk before each launch: need >10GB free.
- Re-run `./scripts/preflight.sh` before each launch.
- All implementing agents use `isolation: "worktree"` and
  `run_in_background: true`.
- Each new worktree is created from the current clean main HEAD, not
  from another worktree's branch.

---

## 4. Briefing Implementers

Every implementer prompt MUST include all of these. Missing any risks a
wasted launch. The full template with copy-paste blocks is in
[reference Section 2](agent-harness-reference.md#2-implementing-agent-prompt-template).

| Required section | Why |
|---|---|
| Worktree absolute path (working directory) | Agent has no conversation history — it needs to verify it's in the right tree, not in main |
| Branch and base commit hash | Proves the worktree is fresh off a clean main |
| File ownership (MODIFY / READ ONLY / DO NOT TOUCH) | Prevents scope creep and cross-agent conflicts |
| Design decisions already resolved | Agent cannot infer decisions from orchestrator context |
| Target test command | Defines the success criteria (e.g. `cargo nextest run -p fathomdb-query text_query::`) |
| Approach hint (1-3 sentences) | Agents that know the direction produce better code than agents exploring |
| READ targets with specific line ranges | "Read coordinator.rs" is too broad; "lines 1-120, Coordinator::new" is right |
| COMMIT block with exact commands (inside the worktree) | Agents that don't commit lose their work when the worktree is removed |
| Communication rules | Agent only pings orchestrator for blockers, not progress |
| Scope constraints with DO NOT lines | For narrow packs, add explicit "Do NOT" lines based on what a confused agent might try |

### Key rules

1. Always specify the working directory as the **worktree absolute path**,
   never the main checkout.
2. Always list editable files explicitly — "only if needed" is too ambiguous.
3. Always include the commit step inside the worktree with the warning
   about lost work.
4. Provide the approach hint. 1-3 sentences.
5. Provide READ targets with line ranges.
6. Remind the agent: **orchestrator merges, agent commits.** The agent
   never pushes, never merges, never touches main.

---

## 5. Merge Protocol

After each implementing agent completes:

### If tests pass and no review needed:
```bash
cd /home/coreyt/projects/fathomdb

# Verify the worktree has the commit the agent reported
git -C {WORKTREE_ABSOLUTE_PATH} log --oneline -1     # expect agent's hash
git -C {WORKTREE_ABSOLUTE_PATH} status --short       # expect clean

# Re-run the target tests from the worktree before merging
(cd {WORKTREE_ABSOLUTE_PATH} && cargo nextest run {TEST_SPEC} 2>&1 | tail -15)

# Ensure main is clean before merging
git status --short                                   # expect clean on main

# Merge worktree branch into main
git merge {BRANCH} --no-ff -m "Merge Pack {ID}: {summary}"
# If this fails with conflicts, resolve manually and re-run affected tests.
# Never mention internal IPs, hostnames, or network details in merge messages.

# Clean up immediately
git worktree remove {WORKTREE_ABSOLUTE_PATH} --force
git worktree prune
git branch -d {BRANCH}    # only after the merge landed
```

### If tests pass but review is pending:
- Launch next-phase implementers immediately — reviews don't block next phase.
- Reviews gate merge, not next-phase launches.
- Reviewers read from the worktree path, not from main (the work isn't
  merged yet).

### After every merge:
```bash
cd /home/coreyt/projects/fathomdb
df -h / | tail -1                                    # check disk
git worktree list                                    # confirm removed
git status --short                                   # expect clean
```
Then re-run `./scripts/preflight.sh` before the next launch.

---

## 6. Phase Gates

Phases execute sequentially. Within a phase, agents run in parallel.

| Gate | Condition to proceed |
|---|---|
| Phase N → N+1 (implementing) | All implementing agents in Phase N reported. Tests pass for packs the next phase depends on. Merged worktrees removed. |
| Phase N → N+1 (reviews) | Reviews can still be in flight. They gate merge, not next-phase launches. |
| Pack → Merge | Agent reports tests pass. If review ran, verdict is not NEEDS_FIXES. Main is clean. |
| All phases → Done | Full regression (`cargo nextest run --workspace`). Document remaining failures. |

---

## 7. Between-Steps Checklist

After each implement + merge cycle, before the next agent:

```
[ ] ./scripts/preflight.sh passes
[ ] main is clean and on the expected commit
[ ] Commit hash and test count recorded
[ ] Modified files noted (for file-overlap detection in next phase)
[ ] Task tracking updated
[ ] Status reported to user
[ ] No stale worktrees: git worktree list
```

---

## 8. Communication

### Orchestrator → User
Report at these checkpoints:
1. **Phase launch** — agents, packs, target tests.
2. **Each agent completion** — pack, tests passed/failed, key findings.
3. **Each merge** — what landed on main.
4. **Phase completion** — summary table, gate criteria met.
5. **Failures or blockers** — immediately, with proposed next step.

Use tables:
```markdown
| Agent | Pack | Tests | Status |
|---|---|---|---|
| **fix-X** | A | 6/7 pass | **MERGED** |
| fix-Y | B | — | running |
```

Don't dump raw test output. Don't explain what you're about to do — do
it and report results. Don't go silent during long agent runs.

### Subagent → Orchestrator
Subagents talk to the orchestrator only when necessary:
- **Final report** on completion (structure in reference Section 2).
- **Blocker escalation** mid-run when the agent hits an ambiguity it
  cannot resolve from the prompt — e.g. a DO NOT TOUCH file is required,
  a design decision is missing, or infrastructure is broken.
- **Not** for progress updates, confirmation of listed decisions, or
  stylistic questions.

If an agent surfaces a blocker, the orchestrator resolves it (update
the prompt, change scope, or re-brief) and either answers the agent or
terminates and relaunches with the fix.

### Orchestrator → Subagent
- Brief once, completely, at launch. Re-brief by relaunching, not by
  chatting.
- If you must intervene mid-run, prefer terminating and relaunching
  with a corrected prompt over long back-and-forth.

---

## 9. Context Protection

The orchestrator's context window is finite and precious.

1. All implementing agents run in background. No foreground
   implementing agents ever.
2. Never read large source files — delegate or use targeted reads (<30 lines).
3. Never iterate on clippy errors — delegate to an implementer.
4. Never run the full test suite inline — run in background or `| tail -5`.
5. Verify agent results with minimal reads — `git log --oneline -3`, grep
   for a specific line, or run target tests from the worktree.
6. Dismiss stale context. After merging a pack, focus forward.

---

## 10. Parallel vs Serial

**Always parallel (safe):**
- Read-only agents (Planners, Reviewers) — unlimited concurrency.

**Parallel with worktrees (default):**
- Implementers in separate worktrees can run in parallel, even on
  overlapping files. The orchestrator's merge step handles conflicts.

**Serial (required):**
- Implementers sharing editable files without worktree isolation.
- Any agent after a fixer that changed shared files, where the next
  agent's worktree must be created from the new main HEAD.
- Packs that touch `Cargo.toml`/`Cargo.lock` at the workspace root —
  lockfile conflicts are noisy to resolve, so serialize these.

---

## 11. Anti-Patterns

These have all caused real failures. Do not repeat them.

| Anti-pattern | Do instead |
|---|---|
| Launch without pre-flight | Run `./scripts/preflight.sh` first |
| Foreground implementing agents | Always `run_in_background: true` |
| Skip commit instructions in prompt | Always include the commit block |
| Agent definition without `Bash` tool | Add `Bash` before launching — no Bash means no cargo, clippy, or commit |
| `git add -A` in agent prompt | List specific files |
| Haiku for code reviews | Use sonnet (haiku: 50% false positive rate) |
| Parallel agents sharing files (no worktree) | Worktree isolation or serial |
| Retry same failure without diagnosis | Diagnose root cause first |
| Hold raw agent output in context | Extract findings, discard raw output |
| Full test suite in foreground | Background or `\| tail -5` |
| Batch worktree merges | Merge and remove immediately |
| Scope-creep-prone prompt (no DO NOT list) | Add explicit DO NOT constraints |
| Edit files directly on main | Always delegate to worktree agents |
| Prompt points agent at main checkout | Prompt must name the worktree absolute path |
| Create worktree from dirty main | Clean main first, then `git worktree add` |
| Agent pushes or merges its own branch | Only the orchestrator merges worktree → main |
| Agent runs `cargo publish` | Denied by `.claude/settings.json`; if it tries, treat as scope creep |
| Manual `cargo build` + copy `.so` for python bindings | Always `pip install -e python/` |
| Launch N agents before validating 1 | Canary first (Section 3) |
| Debug code in orchestrator context | Delegate to Explore agent |
| Relaunch on permission failure | Escalate to user immediately |
| Argue with reviewers from your mental model when a quick experiment would settle it | Tell the fix agent to run an empirical test and observe — see §13.2 |
| Assign one reviewer per pack when two packs share an invariant | Use a joint reviewer with explicit cross-pack consistency checks — see §13.1 |
| Run a third "for safety" review pass after passes 1-2 are clean | Trust the diminishing-returns curve and ship — see §13.3 |
| Assume the harness's worktree base is the same as current main HEAD | Always run `git merge <main-HEAD> --ff-only` as the first step in every implementer prompt; see [reference.md §6 Worktree fast-forward](agent-harness-reference.md#worktree-fast-forward-trap) |
| Chatty mid-run subagent updates | Subagent reports at completion or on blockers only |

---

## 12. Handoff Documents

When a handoff document exists (e.g., design notes under `dev/notes/`
describing remaining work), use it as the starting point — not as a
prompt for re-investigation.

1. **Don't re-explore what's documented.** Read it, extract what you need,
   brief agents from it. If execution contradicts the handoff, update it.
2. **Don't re-plan what's planned.** Follow its execution order unless you
   have a specific reason to deviate.
3. **Do validate the baseline.** Run `./scripts/preflight.sh --baseline`
   to confirm the handoff's numbers are current.
4. **Do update the handoff** after each phase with what landed and what's left.

---

## 13. Review practices

### 13.1 Reviewer topology — joint vs siloed

Default to one reviewer per pack. Switch to a joint reviewer when two or more
packs share a domain or contract.

Siloed reviewers (one per pack) catch defects local to each pack but **cannot
catch cross-pack contradictions**. A joint reviewer can. The cost is one prompt
that's slightly longer; the benefit is catching invariants that fall between
packs.

**Joint when:**
- Two packs assert the same invariant against the same code path (e.g., a
  cross-language parity fixture vs an in-language harness scenario covering
  the same behavior).
- Two packs touch a shared contract that one of them defines and the other
  consumes.
- Two packs are mirror implementations of the same surface (e.g., Python SDK
  + TypeScript SDK).

**Siloed when:**
- The packs touch disjoint code with no shared contract.
- One pack is purely a refactor and the other is purely new behavior.
- The packs were authored against different design references and would
  benefit from independent perspectives.

**Reviewer prompts for joint reviews must include explicit cross-pack
consistency checks** — list the specific invariants both packs are expected
to share, with named identifiers, and ask the reviewer to verify they agree.
Without that explicit instruction, joint reviewers tend to review each pack
in isolation and the cross-pack value is lost.

**Example trap.** During a recent rollout, two parallel packs each authored
a "node matches both chunk and property FTS surfaces — assert which source
wins after dedup" test. Both packs passed locally because each used its own
seed data and the bm25 score tiebreak rolled different ways. A siloed review
of either pack would have approved it. The joint reviewer caught the
contradiction immediately and forced an empirical answer.

### 13.2 Empirical vs argued resolution of behavior questions

When a fix turns on what the code actually does — not what the contract
says it should do — instruct the agent to construct a minimal experiment
and observe. Do not reason from the contract.

Reasoning from the contract is fast but wrong-prone. The orchestrator may
have an incorrect mental model of how a component behaves, and propagating
that mental model into the fix prompt produces a fix that's wrong in a
confident-sounding way. The agent will follow the orchestrator's framing
unless the prompt explicitly empowers them to override it.

**Apply when:**
- A review surfaces a question of the form "the code does X, the design
  says Y, which is right?"
- Two callers disagree on the observed behavior of a shared dependency.
- A flake reproduces under specific data shapes but not others.

**Prompt pattern** (full template in
[reference.md Section 8](agent-harness-reference.md#8-resolving-behavior-questions-empirically)):

```
Step 1 — INVESTIGATE. Construct a minimal scratch test that:
  - sets up the conditions under dispute
  - exercises the actual code path
  - records what happens
Run the scratch test. Observe the answer empirically.
Step 2 — DECIDE. Use the observed answer, not the orchestrator's framing
of what "should" happen, as the canonical answer to encode in the fix.
Step 3 — DELETE the scratch test before commit.
```

**Do NOT apply when:**
- The question is about API design or contract intent, not runtime behavior.
- The behavior is fully specified in a written contract that the code is
  known to follow.
- Empirical observation is impractical (e.g., requires production-scale
  data, a specific OS, or a specific build configuration).

**Example trap.** During a recent rollout, a reviewer surfaced a cross-pack
contradiction on a dedup tiebreak. The orchestrator analyzed the contract
from memory and confidently told the fix agent "property wins, the design
supports it." The fix agent ran a 30-line scratch test, observed
`source=Chunk score=1e-6` for the canonical seed, and corrected the
orchestrator. The orchestrator's mental model was wrong; the empirical
test was right. Cost of the scratch test: ~5 minutes. Cost of landing the
wrong fix and re-reviewing: an hour minimum, plus the credibility hit.

### 13.3 Review cadence

For a multi-phase rollout, the right cadence is **one review pass per
major phase plus one cross-phase pass at the end**. Three or more passes
hit diminishing returns.

Empirical pattern from a recent ~24-merge rollout reviewing six core
phases:

| Pass | Criticals | Warnings | Notes/Lows |
|---|---|---|---|
| Pass 1 (per-phase, after merge) | 2 | 4 | 17 |
| Pass 2 (cross-phase, after pass 1 fixes) | 0 | 4 | 27 |
| Pass 3 (hypothetical) | ≈0 | ≈1-2 | long tail |

The marginal correctness yield drops sharply. The marginal cleanup yield
doesn't, but cleanup is cheap to land later or never.

**Apply this cadence when:**
- The rollout has more than ~6 sequential phases.
- Each phase's contract is well-defined enough that a per-phase reviewer
  can audit against it.
- Phases interact via shared invariants (so the cross-phase pass has
  something to find).

**Skip the cross-phase pass when:**
- All phases are mutually independent additions to disjoint subsystems.
- The total LoC across all phases is small (<2000) and the per-phase
  reviewers have full context.

**Skip per-phase reviews when:**
- The phase is purely mechanical (rename, format, lint).
- The phase is a dependency bump with no behavior change.
- The phase is a doc-only update that `mkdocs --strict` already validates.

**Anti-pattern.** Running a third "for safety" review pass after passes
1-2 are clean. The signal-to-noise ratio is too low — you mostly find
style nits, and the agent cycles you spend on the third pass crowd out
work that has higher impact (e.g., dispatching the next planned phase).

---

## Quick Reference

| Topic | Location |
|---|---|
| Prompt templates (implementing, review) | [reference.md Sections 2-3](agent-harness-reference.md#2-implementing-agent-prompt-template) |
| Agent type properties (including required tools) | [reference.md Section 1](agent-harness-reference.md#1-agent-types) |
| Failure handling (full table) | [reference.md Section 4](agent-harness-reference.md#4-failure-handling) |
| Recovery procedures | [reference.md Section 5](agent-harness-reference.md#5-recovery-procedures) |
| Dirty state recovery | [reference.md Section 6](agent-harness-reference.md#6-dirty-state-recovery) |
| Empirical resolution prompt pattern | [reference.md Section 8](agent-harness-reference.md#8-resolving-behavior-questions-empirically) |
| Infrastructure / filesystem | [reference.md Section 7](agent-harness-reference.md#7-infrastructure) |
