#!/usr/bin/env bash
# Pre-flight checks for agent harness launches.
# See dev/agent-harness-runbook.md Section 2.
#
# Usage:
#   ./scripts/preflight.sh            # standard checks
#   ./scripts/preflight.sh --baseline # include pytest baseline (slow)
#
# Exit codes:
#   0 = all gates pass
#   1 = one or more gates failed (fix before launching agents)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

INCLUDE_BASELINE=false
for arg in "$@"; do
    case "$arg" in
        --baseline) INCLUDE_BASELINE=true ;;
    esac
done

FAILED=0
WARN=0

pass()  { echo "  ✓ $1"; }
fail()  { echo "  ✗ $1"; FAILED=$((FAILED + 1)); }
warn()  { echo "  ~ $1"; WARN=$((WARN + 1)); }

echo "── Pre-flight checks ──"
echo ""

# 1. Branch
BRANCH=$(git branch --show-current)
echo "Branch: $BRANCH"
if [ "$BRANCH" = "main" ]; then
    pass "On main"
else
    warn "Not on main (on $BRANCH) — verify this is expected"
fi

# 2. HEAD
HEAD=$(git log --oneline -1)
echo "HEAD:   $HEAD"
pass "HEAD recorded"

# 3. Clean working tree (tracked files only)
DIRTY=$(git status --short | grep "^ M" || true)
if [ -z "$DIRTY" ]; then
    pass "No modified tracked files"
else
    fail "Dirty tracked files — commit or stash before launching agents:"
    echo "$DIRTY" | sed 's/^/        /'
fi

# 4. Worktrees
git worktree prune 2>/dev/null
WORKTREE_COUNT=$(git worktree list | wc -l)
# Subtract 1 for the main worktree
ACTIVE_WORKTREES=$((WORKTREE_COUNT - 1))
if [ "$ACTIVE_WORKTREES" -lt 3 ]; then
    pass "Worktrees: $ACTIVE_WORKTREES active (max 3)"
else
    fail "Too many worktrees: $ACTIVE_WORKTREES active (max 3). Remove stale ones:"
    git worktree list | tail -n +2 | sed 's/^/        /'
fi

# 5. Disk space
check_disk() {
    local mount=$1
    local avail_kb
    avail_kb=$(df --output=avail "$mount" 2>/dev/null | tail -1 | tr -d ' ')
    local avail_gb=$((avail_kb / 1048576))
    if [ "$avail_gb" -ge 10 ]; then
        pass "$mount: ${avail_gb}GB free"
    else
        fail "$mount: only ${avail_gb}GB free (need >10GB)"
    fi
}
check_disk /

# 6. Venv
if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
    PYTHON_VER=$(uv run python --version 2>/dev/null || echo "FAILED")
    pass "Venv exists: $PYTHON_VER"
else
    fail "Venv missing or broken — run: uv venv .venv && uv sync"
fi

# 7. Baseline (optional, expensive)
if [ "$INCLUDE_BASELINE" = true ]; then
    echo ""
    echo "── Baseline test run ──"
    BASELINE=$(uv run pytest --tb=no -q 2>&1 | tail -3)
    echo "$BASELINE"
fi

# 8. Summary
echo ""
echo "── Result ──"
if [ "$FAILED" -gt 0 ]; then
    echo "BLOCKED: $FAILED gate(s) failed. Fix before launching agents."
    exit 1
elif [ "$WARN" -gt 0 ]; then
    echo "READY with $WARN warning(s). Review above before proceeding."
    exit 0
else
    echo "READY. All gates passed."
    exit 0
fi
