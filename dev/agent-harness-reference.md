# Agent Harness Reference

Templates, failure handling, and recovery procedures for the agent
harness. Read on-demand — the operational playbook is in
[agent-harness-runbook.md](agent-harness-runbook.md).

---

## 1. Agent Types

### Implementer

Writes code to fix failing tests. Follows a strict TDD cycle.
Definition lives at `.claude/agents/implementer.md` (create if missing).

| Property | Value |
|---|---|
| Tools | MUST include `Bash`, `Read`, `Edit`, `Write`, `Glob`, `Grep`. Bash is required — without it the agent cannot run cargo, clippy, or commit. |
| Isolation | `isolation: "worktree"` (always). Fall back to non-isolated only when worktree creation is broken AND disk is critically low. |
| Background | `run_in_background: true` (always) |
| Model | default (`opus`) for complex packs, `sonnet` for small/mechanical fixes |
| Parallelism | Parallel with worktree isolation. Serial if sharing files without worktrees. |

**Variants** (same agent definition, different prompt scope):

- **Fixer** — addresses specific review findings. 1-5 findings per launch.
  Use `model: "sonnet"`.
- **Cleanup** — fixes clippy, fmt, or commits changes a prior agent left
  uncommitted. Use `model: "haiku"` (mechanical work).

### Reviewer

Inspects diffs for bugs, scope creep, and security issues. Read-only.
Definition lives at `.claude/agents/code-reviewer.md` (create if missing).

| Property | Value |
|---|---|
| Tools | `Read`, `Glob`, `Grep`, `Bash` (read-only commands like `git show`, `git diff`, `cargo check`). No `Edit`/`Write`. |
| Background | `run_in_background: true` (always) |
| Model | `sonnet` (not haiku — 50% false positive rate observed with haiku) |
| Parallelism | unlimited (read-only) |
| When to use | Production code with weak test coverage, `unsafe` blocks, WAL / lock / file-descriptor paths, query compilation, external bindings (python, typescript) |
| Skip when | Pack only edits test files, or test coverage is comprehensive |

### Planner

Designs implementation steps before code is written. Read-only.

| Property | Value |
|---|---|
| Subagent type | `Plan` |
| Background | `run_in_background: true` |
| Isolation | none (read-only) |
| Parallelism | unlimited (read-only) |

---

## 2. Implementing Agent Prompt Template

Every implementing agent prompt must contain ALL of these sections.
Missing any section risks a wasted launch. Copy this template and
fill in the bracketed values.

The worktree path is supplied by the harness when `isolation: "worktree"`
is set — the agent starts *inside* the worktree. The orchestrator must
still name it explicitly so the agent can verify it is in the right tree
and never wanders back to `/home/coreyt/projects/fathomdb`.

```markdown
You are an implementing agent for Pack {PACK_ID} — {DESCRIPTION}.

## Environment

Worktree (your working directory): {WORKTREE_ABSOLUTE_PATH}
Branch: {BRANCH}
Base commit (fresh from main): {COMMIT_HASH}

Do ALL work inside the worktree. Do NOT cd into
/home/coreyt/projects/fathomdb for any reason. Do NOT edit, stage, or
commit files there. If any command below targets the main checkout,
STOP and report.

Verify first:
\```bash
cd {WORKTREE_ABSOLUTE_PATH}
git rev-parse --show-toplevel    # must equal {WORKTREE_ABSOLUTE_PATH}
git log --oneline -1             # must show {COMMIT_HASH}
git status --short               # must be clean
cargo nextest run {TEST_SPEC} 2>&1 | tail -5
\```
Must see: {HASH}, clean tree, {N} failing. If any check fails, STOP
and report to the orchestrator — do not attempt repairs yourself.

## File Ownership

You MODIFY: {file1}, {file2}
You READ ONLY: {file3:lines}, {file4:lines}
You DO NOT TOUCH: {files owned by other agents or out of scope}

If a fix requires changes to a DO NOT TOUCH file, STOP and
report the dependency to the orchestrator.

## Design Decisions (already resolved)

- {Decision}: {resolution and rationale}
- {Decision}: {resolution and rationale}

(The implementer has no conversation history. It cannot infer
decisions from the coordinator's context. Include every relevant
resolution.)

## Target Tests
\```bash
cd {WORKTREE_ABSOLUTE_PATH} && cargo nextest run {TEST_SPEC}
\```

(For Python-bindings packs, substitute:
 `cd {WORKTREE_ABSOLUTE_PATH}/python && uv run python -m pytest {TEST_SPEC}`
 after `pip install -e .` from the worktree's `python/` dir.)

## Development Cycle: RED -> READ -> GREEN -> LINT -> COMMIT -> REPORT

### 1. RED
Run the target tests. Confirm they fail. Record exact error messages.
If a test passes unexpectedly, report it — do not "fix" it.

### 2. READ
Read ONLY the files listed in READ ONLY above, using targeted line
ranges. Do NOT read entire large files. Do NOT read unlisted files.
- {file: lines} — {what to look for}
- {file: lines} — {what to look for}

### 3. GREEN
{1-3 sentences describing the expected approach.}
Follow existing code patterns. Do NOT refactor, add docstrings,
or clean up surrounding code.
Run the target tests. If they fail, iterate (max 3 attempts).
If still failing after 3 attempts, STOP and report what you learned.

### 4. LINT
\```bash
cd {WORKTREE_ABSOLUTE_PATH}
cargo clippy --all-targets -- -D warnings
cargo fmt --check
\```
Fix any violations. Re-run to confirm clean. Scope clippy to the
changed crates with `-p <crate>` if the workspace build is too slow.

### 5. COMMIT — CRITICAL, DO NOT SKIP
\```bash
cd {WORKTREE_ABSOLUTE_PATH}
git add {specific-files-only}
git status   # verify only scoped files are staged, and you are in the worktree
git commit -m "{COMMIT_MESSAGE}"
git log --oneline -1   # capture the new HEAD to report back
\```
Commit MUST happen inside the worktree. The orchestrator merges
worktree → main; you do not push or merge anything yourself.
Do NOT use `git add -A` or `git add .` — this stages `target/`,
`.venv/`, and unrelated files.
If you do not commit, your work WILL BE LOST when the worktree is
removed.
If the commit is rejected by a pre-commit hook, fix the issue
and commit again. Do NOT use --no-verify.
Do NOT run `cargo publish`, `cargo yank`, or touch the crates.io
registry. Do NOT push the branch. Do NOT merge into main.
Never mention internal IPs, hostnames, or network details in commit
messages.

### 6. REPORT
Return this exact structure to the orchestrator:
- worktree: {WORKTREE_ABSOLUTE_PATH}
- branch: {BRANCH}
- head_commit: <new hash from `git log --oneline -1`>
- tests_targeted: [list of test IDs]
- tests_passed: N
- tests_failed: N + [IDs] + [error summary for each]
- files_changed: [list of paths]
- approach: 1-2 sentence summary of what was done
- blockers: anything that prevented full completion
- questions: open questions the orchestrator must resolve before next work

## Communication With The Orchestrator

You run in background. Talk to the orchestrator only when necessary:
- At the end, via the REPORT structure above.
- Mid-run ONLY if you hit a blocker that requires a decision you cannot
  make from the prompt (ambiguous requirement, DO NOT TOUCH file is in
  the way, infrastructure failure). Stop and surface the question;
  do not guess.
- Do NOT chat for progress updates. Do NOT ask for confirmation on
  decisions already listed under "Design Decisions".

## Scope Constraints
- Do NOT add features, refactor, or clean up code outside the fix.
- Do NOT add docstrings, comments, or type annotations to unchanged code.
- Do NOT touch Cargo.toml/Cargo.lock at the workspace root unless the
  pack explicitly requires a dependency bump.
- {PACK-SPECIFIC CONSTRAINTS, e.g.: "Do NOT modify the WAL format" or
  "Do NOT touch python_types.rs".}
- If you discover a related issue, report it — do not fix it.
- 3-iteration cap. If you cannot get tests green, stop and report.
```

---

## 3. Review Agent Prompt Template

Use reviews only when the implementing agent edited production code
in areas with weak test coverage (WAL, lock manager, query compile,
coordinator, python bindings, external content paths).

```markdown
You are a review agent. READ-ONLY — do NOT edit any files.

## What to review
The implementing agent for Pack {PACK_ID} committed changes inside a
worktree. The orchestrator has NOT yet merged them into main. Review
the commit in the worktree, not main.

Worktree: {WORKTREE_ABSOLUTE_PATH}
Commit: {COMMIT_HASH}

Run this exact command to see the diff:
\```bash
git -C {WORKTREE_ABSOLUTE_PATH} show {COMMIT_HASH}
\```

IMPORTANT: Read ALL files from {WORKTREE_ABSOLUTE_PATH}/, NOT from
/home/coreyt/projects/fathomdb/. Worktree paths follow the pattern
`.claude/worktrees/agent-<hash>/` (or `.claude/worktrees/<branch>/` for
manually created ones). Verify you are reading the correct tree
before starting — a review of the wrong tree is worse than no review.

The changes are in:
- {file1} — {what was changed}
- {file2} — {what was changed}

## Review Checklist
For each changed file:
1. No debug prints, `eprintln!`, `dbg!`, or stray logging-level mistakes
2. No commented-out code or TODO placeholders
3. No unrelated changes (scope creep beyond {PACK_SCOPE})
4. No security issues — especially unchecked lock acquisition, file
   descriptor leaks, unsafe blocks without invariants documented, path
   traversal on user-supplied names, or dropped error contexts on WAL /
   projection writes
5. No broken imports, unused deps, or `pub` leakage across crate boundaries
6. Consistent with surrounding code style
7. Read 20 lines above and below each edit hunk for context
8. Check edge cases tests might miss:
   - None/empty handling on new code paths
   - Off-by-one in byte offsets, slices, or range queries
   - Resource cleanup (file handles, locks, `Arc` cycles, channel drops)
   - Thread-safety around shared engine/coordinator state
   - Python GIL / Send+Sync boundaries if python_types.rs is touched

## Verdict
Return this exact structure to the orchestrator:
- verdict: PASS | PASS_WITH_NOTES | NEEDS_FIXES
- pack: {PACK_ID}
- worktree: {WORKTREE_ABSOLUTE_PATH}
- files_reviewed: [list of paths]
- issues: [{severity: critical|warning|nit, file, line, description}]
- summary: 1-2 sentence overall assessment

NEEDS_FIXES only for critical issues:
- Bugs the tests don't catch
- Security vulnerabilities
- Broken behavior in untested code paths
- `unsafe` without a clear invariant
Warnings and nits are noted but do NOT block merge.
```

### Verifying review verdicts

If a review returns NEEDS_FIXES, the orchestrator MUST verify before acting:
1. Read the specific lines the reviewer flagged (from the worktree).
2. Check whether the reviewer read from the correct location (worktree vs main).
3. If the issue is real, delegate to a fixer implementer in the same worktree.
4. If the issue is a false positive (reviewer read wrong files), dismiss it.

---

## 4. Failure Handling

| Scenario | Action |
|---|---|
| Agent reports 0 tests fixed | Read output. Re-brief with more specific guidance, or create a narrower follow-up pack. |
| Agent reports partial fix | Merge what works. Create a follow-up pack for remaining tests. |
| Agent couldn't commit | Enter the worktree and commit for it: `git -C {WORKTREE} add {files} && git -C {WORKTREE} commit -m "{msg}"`. If lint fails, delegate to cleanup. |
| Agent hit iteration cap (3 attempts) | Absorb findings. Re-scope with the new information. |
| Agent edited files outside scope | Check if justified. If not, `git -C {WORKTREE} checkout -- {file}` to revert inside the worktree. |
| Agent wrote into main instead of worktree | Treat as dirty main. See Section 6. Re-brief template with the worktree path clearly stated. |
| Worktree lost (no commit) | Relaunch. Should not happen if prompt template is followed. |
| Agent blocked by permissions | **STOP. Escalate to user immediately.** Do NOT relaunch. |
| Agent tried `cargo publish` / `git push` and got denied | Expected — the deny list did its job. Rebrief the agent to stop at commit. |
| Disk space exhausted | Merge and remove worktrees; `cargo clean` in the largest stale target dir. |
| Agent used Edit but not Bash (no commit) | The agent definition is missing the Bash tool. Fix the agent definition before relaunching. See Section 6 for salvage. |
| `git rev-parse` / `git worktree add` failure | Dirty tracked files on main, or main is not at the expected commit. See Section 6. |
| Multiple agents fail identically | Infrastructure issue. Diagnose once, fix, then relaunch. |
| Agent never reports back (30+ min) | Check if running. If dead, check `git -C {WORKTREE} status` for uncommitted work, salvage or relaunch. |
| Stale agent notification arrives | Check agent ID against currently expected agent. If duplicate, check whether it landed on main (may need revert). |

---

## 5. Recovery Procedures

### Python venv broken or missing (only matters for python-bindings packs)

```bash
cd /home/coreyt/projects/fathomdb
rm -rf python/.venv
pip install -e python/
```

Do NOT build the native extension by hand with `cargo build -p` and
copy `.so` files into `python/fathomdb/` — `pip install -e python/`
handles that, and bypassing it leaves a stale binary that looks correct
but runs old code.

### Agent committed to wrong branch or directly to main

```bash
cd /home/coreyt/projects/fathomdb
git log --oneline -3        # identify bad commit
git revert <hash>           # clean revert if pushed
# or if not pushed:
git reset --soft HEAD~1     # undo commit, keep changes
```

### Test suite broken after agent work

```bash
cd /home/coreyt/projects/fathomdb
cargo nextest run --workspace 2>&1 | grep -E "FAIL|failed"
# Categorize: agent's files or pre-existing?
# Agent's files -> launch fixer implementer
# Pre-existing -> investigate separately
```

### Disk exhaustion

```bash
df -h /
git worktree list                    # remove stale worktrees
git worktree remove <path> --force
git worktree prune
# If still tight, clean the largest stale target dir:
du -sh /home/coreyt/projects/fathomdb/.claude/worktrees/*/target 2>/dev/null
```

---

## 6. Dirty State Recovery

When agents leave uncommitted edits on main (because they could Edit
but not Bash to commit, or because a prompt accidentally pointed them
at the main checkout), the working tree is dirty and worktree creation
will fail.

### Diagnosis

```bash
cd /home/coreyt/projects/fathomdb
git status --short | grep "^ M"
```

If any tracked files show as modified, worktree agents CANNOT launch.
Fix the agent definition (ensure Bash is in its tools) and/or fix the
prompt (point working directory at the worktree) before relaunching.

### Recovery options

1. **Edits are useful (agent did good work):**
   ```bash
   cd /home/coreyt/projects/fathomdb
   git diff <file>                            # review
   cargo nextest run {tests}                  # test
   git add <specific-files>                   # stage
   git commit -m "<pack>: <description>"      # commit
   ```

2. **Edits are garbage or incomplete:**
   ```bash
   cd /home/coreyt/projects/fathomdb
   git checkout -- .
   ```

3. **Edits are mixed (some good, some bad):**
   ```bash
   cd /home/coreyt/projects/fathomdb
   git diff <file>          # review each file
   git checkout -- <bad-file>
   git add <good-file>
   git commit -m "<pack>: <description>"
   ```

After recovery, run `./scripts/preflight.sh` before launching agents.

### Worktree fast-forward trap

When `isolation: "worktree"` is set, the harness creates the agent's
worktree at a **cached base commit** (in this repo, `dbdc31b`),
regardless of where main is now. The agent must run
`git merge <current-main-HEAD> --ff-only` as its first step to
advance to current main.

A common failure mode: the agent runs `git log --oneline -3`, sees
its HEAD at `dbdc31b`, sees the target commit name in its briefing,
and decides "the target is reachable from my HEAD" — but **reachability
is directional**. `dbdc31b` is the *ancestor*, not the descendant.
Looking up the target commit and finding it in the log doesn't mean
it's already applied; it means it's somewhere in the branch's
history relative to `dbdc31b`.

This trap has fired at least twice in real sessions. Both times the
agent then reported "the API doesn't exist" or "the file doesn't
exist" because they were grepping against an ancestor commit that
genuinely didn't have the post-fast-forward state.

#### Mitigation in implementer prompts

Make the fast-forward step **unmissable** in every implementer prompt:

```markdown
## CRITICAL FIRST STEP — fast-forward verification

\```bash
pwd
git log --oneline -3
\```

If the first line is older than `{TARGET_COMMIT}`, run:
\```bash
git merge {TARGET_COMMIT} --ff-only
git log --oneline -3
\```

The first line MUST now read `{TARGET_COMMIT} {TARGET_COMMIT_SUBJECT}`.
If `git merge --ff-only` fails with "Already up to date", you were
already there or ahead — fine. If it fails for any other reason,
STOP and report.

**Do not interpret `{ANCESTOR} → {TARGET}` as "{TARGET} is reachable
from {ANCESTOR} so no merge needed". That is wrong. `{ANCESTOR}` is
the distant ancestor; `{TARGET}` is current main and contains N+
commits beyond `{ANCESTOR}`. Always fast-forward.**
```

Then verify the new world is visible:

```bash
grep -l "{KEY_SYMBOL_FROM_RECENT_PHASE}" {EXPECTED_PATH}   # expect: file path, not "no match"
```

If grep returns empty for a symbol the briefing assumes exists, the
fast-forward did not land — STOP and report. Do not proceed against
the wrong base.

#### Cleanup if the trap fires

If an agent has already reported a "missing API" blocker rooted in this
trap:

1. Confirm the worktree is still at `dbdc31b` via
   `git -C {WORKTREE} log --oneline -1`.
2. The worktree has no commits and made no changes — the harness will
   auto-clean it on the next launch (`isolation: "worktree"` cleans up
   worktrees with no changes).
3. Relaunch the agent with the **unmissable** fast-forward preamble
   above.
4. Specifically call out the prior agent's mistake by name in the new
   briefing so the new agent does not repeat it.

---

## 7. Infrastructure

### Filesystem

```
/home/coreyt/projects/fathomdb/            <- project root (main checkout)
/home/coreyt/projects/fathomdb/target      <- cargo target dir (large)
/home/coreyt/projects/fathomdb/python/.venv <- python venv (only if bindings work planned)
/home/coreyt/projects/fathomdb/.claude/worktrees/ <- worktree parent directory
    agent-<hash>/                                 <- harness-created (isolation: worktree)
    <branch>/                                     <- manually created
```

All storage is local. No external mounts needed.

### Rules

1. Before creating worktrees: `df -h /` to check space (need >10GB free;
   each worktree builds its own `target/` which can be 5-10 GB).
2. Main must be clean before `git worktree add` — see runbook Section 2.
3. Python venv lives at `python/.venv`. If broken, recreate with
   `pip install -e python/` — never hand-build the native extension.
4. Each worktree gets its own `target/` directory because cargo's build
   cache is per-checkout. If disk pressure is acute, serialize packs
   instead of running 3 in parallel.
5. Never run `cargo publish`, `cargo yank`, or `cargo owner` from an
   agent. These are denied in `.claude/settings.json`; release cuts go
   through the orchestrator with explicit user approval.

---

## 8. Resolving behavior questions empirically

When a fix or review surfaces a question about what the code actually does
(not what the contract says it should do), brief the implementer to run a
scratch experiment and observe — never to reason from the orchestrator's
mental model. Cross-reference: [runbook §13.2](agent-harness-runbook.md#132-empirical-vs-argued-resolution-of-behavior-questions).

### Prompt pattern

```markdown
## Step 1 — INVESTIGATE

Construct a minimal scratch test that:
- sets up the conditions under dispute
- exercises the actual code path (not a mock, not a stub)
- records what happens via `eprintln!` / `print(...)` / `console.log` /
  whatever the language's standard observation tool is

Run the scratch test:
\```bash
{TEST RUN COMMAND, e.g. `cargo test --test scratch dual_match -- --nocapture`}
\```

Observe the answer empirically. Record the observed value verbatim — do
not paraphrase.

## Step 2 — DECIDE

Use the observed answer as the canonical answer to encode in the fix.
**Do NOT use the orchestrator's framing** of what "should" happen. If the
observed answer contradicts the orchestrator's mental model, the
observation wins.

## Step 3 — DELETE

Delete the scratch test before commit. It served its purpose at decision
time. The canonical assertion belongs in whatever production-test file
the fix is for, not in a scratch file that a future reader would have to
reverse-engineer.

If the scratch test reveals that the actual behavior is itself a bug
(not just disputed), STOP and surface it as a blocker — do not silently
fix the bug as a side effect of the original task.
```

### When to use this pattern

- A reviewer flags a contradiction between two packs' assertions about the
  same invariant.
- A test is flaky and the orchestrator wants to fix it without first
  understanding what's actually flaking.
- The orchestrator's briefing to the fix agent contains "the answer is X"
  but the agent has no way to verify X.

### When NOT to use this pattern

- The question is about contract / API design, not runtime behavior.
- The behavior is fully specified in a written contract that the code is
  known to follow (and recently re-validated).
- The cost of constructing a representative scratch test exceeds the cost
  of a full-on review of the relevant code path.

### Worked example

Briefing to a fix agent:

```
P8x-1 critical — cross-pack assertion contradiction on dedup tiebreak.

**Step 1: Investigate.** Construct a minimal Rust experiment in scratch
form (you can put it in `crates/fathomdb/tests/scale.rs` near the new
tests, or in a scratch test file you'll delete before commit) that:

1. Opens a fresh tempdir engine
2. Registers a scalar property FTS schema on `$.title` for kind "DualMatch"
3. Inserts ONE node with both:
   - Chunk content containing the query token (e.g., `chunks: [Chunk { id: "c1", text_content: "dualmatchneedle" }]`)
   - Property content matching the same token (e.g., `properties: {"title": "dualmatchneedle"}`)
4. Runs `engine.query("DualMatch").text_search("dualmatchneedle", 10).execute()`
5. `eprintln!`s the resulting `SearchHit { source, score, ... }` for inspection

Run the test with `cargo test --test scale dual_match -- --nocapture`.
Observe which source wins in the canonical case.

**Step 2: Decide.** Update all three call sites (P8a's scenarios.json,
P8b Python harness, P8b TypeScript harness) to assert whichever source
the experiment observed.

**Step 3: Delete the experiment.** Don't commit a `dual_match` exploration
test — it's only there to drive Step 1's observation.
```

The agent's actual observation: `source=Chunk score=1e-6 match_mode=Strict`.
The orchestrator had previously asserted "property wins" from a contract
reading; the empirical test corrected that. Total experiment cost: ~5
minutes. Avoided cost: a wrong fix landed and re-reviewed.
