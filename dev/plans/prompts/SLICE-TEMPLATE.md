<!--
========================================================================
ORCHESTRATOR FILL-IN GUIDE  (this comment block is harmless if left in)
Replace every {{PLACEHOLDER}} before handing the prompt to an implementer.
Required fills:
  {{PACK_ID}}        short pack id (e.g. A3)
  {{DESCRIPTION}}    one-line what this pack delivers
  {{WORKTREE_PATH}}  absolute worktree path (.claude/worktrees/<branch>)
  {{BRANCH}}         branch name
  {{BASE_COMMIT}}    clean main HEAD the worktree was cut from
  {{TARGET_TESTS}}   pytest path/IDs that define success
  {{MODIFY}}         files the pack may edit
  {{READ_ONLY}}      files (with line ranges) to read, not edit
  {{DO_NOT_TOUCH}}   files owned by other packs / out of scope
  {{DECISIONS}}      design decisions already resolved (agent can't infer them)
  {{ANCHORS}}        re-verified file:line touch-points (state the SHA verified at)
  {{APPROACH}}       1-3 sentence approach hint
  {{CONSTRAINTS}}    pack-specific DO NOT lines
  {{OUTPUT_JSON}}    abs path: dev/plans/runs/{{PACK_ID}}-output.json
  {{COMMIT_MESSAGE}} commit subject
  {{DOD}}            the reviewer's bar (definition of done)

AUTHORING CHECKLIST — prevent justified-deviation-by-under-specification.
(Most in-scope "out of bounds" edits are the agent minimally deviating to make
an in-scope deliverable actually work, because the prompt named the primary
artifact but not its dependencies. Name them up front:)
  [ ] Companion artifacts, not just the primary file (a new config key needs its
      schema/validation + docs + a test; a new CLI flag needs its help text).
  [ ] Mechanism triggers, not just the data change (if a change needs a reload/
      migration/cache-bust to take effect, say where it's wired).
  [ ] Forward-propagation the pack authorizes (if closing this pack makes a later
      pack's contract stale, pre-declare it so the edit is in-scope).
  [ ] Every shared surface the change can ripple to (config schema, API/route,
      guardrail contract) named explicitly, with "escalate if you must touch one
      not listed."
  [ ] Verify end-STATE, not steps: state the DoD; let the agent choose the how.
  [ ] Verify each load-bearing "test X asserts Y" claim against the file at
      {{BASE_COMMIT}} BEFORE writing it here — prompt anchors drift.
========================================================================
-->

You are an implementing agent for Pack {{PACK_ID}} — {{DESCRIPTION}}.
This prompt is **self-contained** — trust it and the on-disk state, not any
memory of prior conversation. You do **not** spawn subagents. You commit inside
your worktree; the **orchestrator** merges worktree → main.

## Environment

Worktree (your working directory): {{WORKTREE_PATH}}
Branch: {{BRANCH}}
Base commit (fresh from main): {{BASE_COMMIT}}

Do ALL work inside the worktree. Do NOT cd into /home/coreyt/projects/airlock
for any reason. Do NOT edit, stage, or commit files there. If any command below
targets the main checkout, STOP and report.

Verify first:
```bash
cd {{WORKTREE_PATH}}
git rev-parse --show-toplevel    # must equal {{WORKTREE_PATH}}
git log --oneline -1             # must show {{BASE_COMMIT}}
git status --short               # must be clean
uv run pytest {{TARGET_TESTS}} 2>&1 | tail -5
```
Must see: {{BASE_COMMIT}}, clean tree, failing target tests. If any check fails,
STOP and report — do not attempt repairs yourself.

> **Worktree fast-forward trap.** The harness may create the worktree at a cached
> base commit older than current main. If `git log --oneline -1` is older than
> {{BASE_COMMIT}}, run `git merge {{BASE_COMMIT}} --ff-only` then re-verify. An
> ancestor commit is NOT the descendant — always fast-forward. See
> `dev/agent-harness-reference.md` §6.

## File Ownership

You MODIFY: {{MODIFY}}
You READ ONLY: {{READ_ONLY}}
You DO NOT TOUCH: {{DO_NOT_TOUCH}}

If a fix requires changes to a DO NOT TOUCH file, STOP and report the dependency.

## Design Decisions (already resolved — you cannot infer these)

{{DECISIONS}}

## Anchors (re-verified at main = {{BASE_COMMIT}}; WILL drift as you edit)

{{ANCHORS}}
Re-grep every anchor before relying on it; record drift in
`output.json.additional_changes_made_in_scope`.

## Development Cycle: PLAN -> RED -> READ -> GREEN -> LINT -> COMMIT -> CLOSE -> REPORT

### 0. PLAN (design memo first, for non-trivial packs)
If the pack is more than a one-file mechanical change, write a short design memo
(approach + key decisions + test plan) at `dev/notes/<pack-id>-design.md` **under
the worktree**, commit it, and `echo "[{{PACK_ID}}][PLAN] design memo committed"`.
Skip for trivial packs.

### 1. RED
Run {{TARGET_TESTS}}. Confirm they fail. Record exact errors. If a test passes
unexpectedly, report it — do not "fix" it. Commit the RED tests on their own
(tests-only commit) and record that sha for `tdd_evidence.red_commit_sha`.
`echo "[{{PACK_ID}}][RED] tests failing as expected"`.

### 2. READ
Read ONLY the READ ONLY files above, using the given line ranges. Do NOT read
entire large files. Do NOT read unlisted files.

### 3. GREEN
{{APPROACH}}
Follow existing code patterns. Do NOT refactor, add docstrings, or clean up
surrounding code. Run target tests after each change. Max 3 attempts — if still
failing, STOP and report what you learned. `echo "[{{PACK_ID}}][GREEN] target tests pass"`.

### 4. LINT
```bash
cd {{WORKTREE_PATH}}
uv run ruff check {{MODIFY}}
uv run ruff format --check {{MODIFY}}
```
Fix any violations. Re-run to confirm clean.

### 5. COMMIT — CRITICAL, DO NOT SKIP
```bash
cd {{WORKTREE_PATH}}
git add {{MODIFY}}
git status                # only scoped files staged; you are in the worktree
git commit -m "{{COMMIT_MESSAGE}}"
git log --oneline -1      # capture the new HEAD
```
Do NOT use `git add -A` / `git add .` (stages `.venv/`, unrelated files).
If a pre-commit hook rejects the commit, fix the issue and retry — never `--no-verify`.
Do NOT push, merge, or touch main. Do NOT run `uv publish`. Never mention
internal IPs/hostnames/network details in commit messages.
`echo "[{{PACK_ID}}][COMMIT] <head sha>"`.

### 6. CLOSE — write `output.json` LAST
After all commits, write the closure artifact to {{OUTPUT_JSON}} (schema in
`.claude/agents/implementer.md` §6). Write it even if you halt on a blocker.

### 7. REPORT
Return the REPORT structure from `.claude/agents/implementer.md` §7, including
`output_json: {{OUTPUT_JSON}}`.

## Console logging contract
Emit a one-line `[{{PACK_ID}}][PHASE]` console message at each phase transition
(PLAN/RED/GREEN/COMMIT) and, the instant you detect any problem, `[DETECT]`
before fixing it and `[RESOLVE]` after (stating what you changed and how you
verified). End with a `[SUMMARY]` line. Mirror each DETECT/RESOLVE into
`output.json.blockers_encountered`.

## Scope discipline — creep vs. justified deviation
- **Scope creep — forbidden.** A broader/nicer solution your mandate doesn't
  require. Record it in `additional_changes_made_in_scope` and move on. When
  unsure whether something is in-scope, it isn't — flag it.
- **Justified deviation — sometimes required.** A path above is genuinely
  blocked (anchor gone, stated precondition false, contract inconsistent). Then:
  (1) smallest viable change that unblocks *your* mandate; (2) `[DETECT]` it loud
  and record the why; (3) **escalate** if it would change this pack's contract,
  affect another pack, or alter a shared surface (config schema, API, guardrail
  contract) — that's the orchestrator's call, not yours to absorb quietly.
- Additional standing limits: {{CONSTRAINTS}}
- Do NOT touch `pyproject.toml`/`uv.lock` at the repo root unless the pack
  explicitly requires a dependency bump.

## Definition of Done (the bar the reviewer checks)
{{DOD}}
