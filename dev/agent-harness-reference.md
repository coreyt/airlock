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
| Tools | MUST include `Bash`, `Read`, `Edit`, `Write`, `Glob`, `Grep`. Bash is required — without it the agent cannot run tests, lint, or commit. |
| Isolation | `isolation: "worktree"` (always). Fall back to non-isolated only when worktree creation is broken AND disk is critically low. |
| Background | `run_in_background: true` (always) |
| Model | default (`opus`) for complex packs, `sonnet` for small/mechanical fixes |
| Parallelism | Parallel with worktree isolation. Serial if sharing files without worktrees. |

**Variants** (same agent definition, different prompt scope):

- **Fixer** — addresses specific review findings. 1-5 findings per launch.
  Use `model: "sonnet"`.
- **Cleanup** — fixes lint errors, formatting, or commits changes a prior
  agent left uncommitted. Use `model: "haiku"` (mechanical work).

### Reviewer

Inspects diffs for bugs, scope creep, and security issues. Read-only.
Definition lives at `.claude/agents/code-reviewer.md` (create if missing).

| Property | Value |
|---|---|
| Tools | `Read`, `Glob`, `Grep`, `Bash` (read-only commands like `git show`, `git diff`). No `Edit`/`Write`. |
| Background | `run_in_background: true` (always) |
| Model | `sonnet` (not haiku — 50% false positive rate observed with haiku) |
| Parallelism | unlimited (read-only) |
| When to use | Production code with weak test coverage, integration surfaces, edge-case-heavy logic (e.g. guardrails, rewrite engine, enforcer) |
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
and never wanders back to `/home/coreyt/projects/airlock`.

```markdown
You are an implementing agent for Pack {PACK_ID} — {DESCRIPTION}.

## Environment

Worktree (your working directory): {WORKTREE_ABSOLUTE_PATH}
Branch: {BRANCH}
Base commit (fresh from main): {COMMIT_HASH}

Do ALL work inside the worktree. Do NOT cd into
/home/coreyt/projects/airlock for any reason. Do NOT edit, stage, or
commit files there. If any command below targets the main checkout,
STOP and report.

Verify first:
\```bash
cd {WORKTREE_ABSOLUTE_PATH}
git rev-parse --show-toplevel    # must equal {WORKTREE_ABSOLUTE_PATH}
git log --oneline -1             # must show {COMMIT_HASH}
git status --short               # must be clean
uv run pytest {TEST_SPEC} --tb=no -q 2>&1 | tail -3
\```
Must see: {HASH}, clean tree, {N} failed. If any check fails, STOP
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
cd {WORKTREE_ABSOLUTE_PATH} && uv run pytest {TEST_SPEC} --tb=short -q
\```

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
uv run ruff check {changed-files}
uv run ruff format --check {changed-files}
\```
Fix any violations. Re-run to confirm clean.

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
Do NOT use `git add -A` or `git add .` — this stages `.venv/` and
unrelated files.
If you do not commit, your work WILL BE LOST when the worktree is
removed.
If the commit is rejected by a pre-commit hook, fix the issue
and commit again. Do NOT use --no-verify.
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
- {PACK-SPECIFIC CONSTRAINTS, e.g.: "Do NOT modify presidio recognizers"
  or "Do NOT touch the litellm proxy config schema".}
- If you discover a related issue, report it — do not fix it.
- 3-iteration cap. If you cannot get tests green, stop and report.
```

---

## 3. Review Agent Prompt Template

Use reviews only when the implementing agent edited production code
in areas with weak test coverage (guardrails, rewrite engine, enforcer,
circuit breaker, S3 export paths).

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
/home/coreyt/projects/airlock/. Worktree paths follow the pattern
`.claude/worktrees/agent-<hash>/` (or `.claude/worktrees/<branch>/` for
manually created ones). Verify you are reading the correct tree
before starting — a review of the wrong tree is worse than no review.

The changes are in:
- {file1} — {what was changed}
- {file2} — {what was changed}

## Review Checklist
For each changed file:
1. No debug prints, logging-level mistakes, or stray `print()`
2. No commented-out code or TODO placeholders
3. No unrelated changes (scope creep beyond {PACK_SCOPE})
4. No security issues — especially prompt injection, PII leakage in
   logs, unvalidated input at LLM boundaries, or bypasses of
   guardrails/enforcer
5. No broken imports or circular dependencies
6. Consistent with surrounding code style
7. Read 20 lines above and below each edit hunk for context
8. Check edge cases tests might miss:
   - None/empty handling on new code paths
   - Off-by-one in queries or loops
   - Resource cleanup (connections, file handles, S3 clients)
   - Thread-safety around shared guardian/monitor state

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
| Disk space exhausted | Merge and remove worktrees. |
| Agent used Edit but not Bash (no commit) | The agent definition is missing the Bash tool. Fix the agent definition before relaunching. See Section 6 for salvage. |
| `git rev-parse` / `git worktree add` failure | Dirty tracked files on main, or main is not at the expected commit. See Section 6. |
| Multiple agents fail identically | Infrastructure issue. Diagnose once, fix, then relaunch. |
| Agent never reports back (30+ min) | Check if running. If dead, check `git -C {WORKTREE} status` for uncommitted work, salvage or relaunch. |
| Stale agent notification arrives | Check agent ID against currently expected agent. If duplicate, check whether it landed on main (may need revert). |

---

## 5. Recovery Procedures

### Venv broken or missing

```bash
cd /home/coreyt/projects/airlock
rm -rf .venv
uv venv .venv
uv sync
```

### Agent committed to wrong branch or directly to main

```bash
cd /home/coreyt/projects/airlock
git log --oneline -3        # identify bad commit
git revert <hash>           # clean revert if pushed
# or if not pushed:
git reset --soft HEAD~1     # undo commit, keep changes
```

### Test suite broken after agent work

```bash
cd /home/coreyt/projects/airlock
uv run pytest --tb=line -q 2>&1 | grep FAILED
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
```

---

## 6. Dirty State Recovery

When agents leave uncommitted edits on main (because they could Edit
but not Bash to commit, or because a prompt accidentally pointed them
at the main checkout), the working tree is dirty and worktree creation
will fail.

### Diagnosis

```bash
cd /home/coreyt/projects/airlock
git status --short | grep "^ M"
```

If any tracked files show as modified, worktree agents CANNOT launch.
Fix the agent definition (ensure Bash is in its tools) and/or fix the
prompt (point working directory at the worktree) before relaunching.

### Recovery options

1. **Edits are useful (agent did good work):**
   ```bash
   cd /home/coreyt/projects/airlock
   git diff <file>                            # review
   uv run pytest <tests> --tb=short -q        # test
   git add <specific-files>                   # stage
   git commit -m "<pack>: <description>"      # commit
   ```

2. **Edits are garbage or incomplete:**
   ```bash
   cd /home/coreyt/projects/airlock
   git checkout -- .
   ```

3. **Edits are mixed (some good, some bad):**
   ```bash
   cd /home/coreyt/projects/airlock
   git diff <file>          # review each file
   git checkout -- <bad-file>
   git add <good-file>
   git commit -m "<pack>: <description>"
   ```

After recovery, run `./scripts/preflight.sh` before launching agents.

---

## 7. Infrastructure

### Filesystem

```
/home/coreyt/projects/airlock/            <- project root (main checkout)
/home/coreyt/projects/airlock/.venv       <- local venv (NOT a symlink)
/home/coreyt/projects/airlock/.claude/worktrees/ <- worktree parent directory
    agent-<hash>/                                 <- harness-created (isolation: worktree)
    <branch>/                                     <- manually created
```

All storage is local. No external mounts needed.

### Rules

1. Before creating worktrees: `df -h /` to check space (need >10GB free).
2. Main must be clean before `git worktree add` — see runbook Section 2.
3. Venv is a local directory. If broken, recreate:
   ```bash
   cd /home/coreyt/projects/airlock
   rm -rf .venv && uv venv .venv && uv sync
   ```
4. Each worktree gets its own `.venv` if tests need to run in it. Budget
   disk accordingly (~1-2 GB per worktree).
