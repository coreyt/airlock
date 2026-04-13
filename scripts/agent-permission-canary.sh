#!/usr/bin/env bash
# agent-permission-canary.sh
#
# Exercises the Bash allowlist a TDD implementer subagent needs. Run this
# inside a fresh worktree as the first task of a canary implementer before
# launching real work. Exit 0 means the allowlist is complete; exit 1 lists
# the failing buckets.
#
# Paired with .claude/settings.json. See dev/agent-harness-runbook.md Section 3.

set -u

pass=0
fail=0
failed=()

check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  PASS: $label"
        pass=$((pass + 1))
    else
        echo "  FAIL: $label  (cmd: $*)"
        fail=$((fail + 1))
        failed+=("$label")
    fi
}

deny_check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  DENY-LEAK: $label should be denied but ran  (cmd: $*)"
        fail=$((fail + 1))
        failed+=("deny-leak:$label")
    else
        echo "  PASS: $label correctly denied"
        pass=$((pass + 1))
    fi
}

echo "── Allowlist checks ──"
check "git-status"      git status --short
check "git-log"         git log --oneline -1
check "git-rev-parse"   git rev-parse --show-toplevel
check "git-diff"        git diff --stat HEAD
check "git-show"        git show --stat HEAD
check "pwd"             pwd
check "ls"              ls -1
check "cat-readme"      cat README.md
check "head"            head -1 README.md
check "tail"            tail -1 README.md
check "wc"              wc -l README.md
check "grep"            grep -r "def " --include="*.py" -l .
check "find"            find . -maxdepth 2 -name "*.py"
check "df"              df -h /
check "date"            date
check "uv-run-pytest"   uv run pytest --collect-only -q
check "uv-run-ruff"     uv run ruff --version

echo ""
echo "── Deny checks (MUST be blocked) ──"
deny_check "rm"         rm --help
deny_check "curl"       curl --help
deny_check "git-push"   git push --dry-run origin HEAD

echo ""
echo "── Result ──"
echo "$pass pass, $fail fail"
if [ "$fail" -ne 0 ]; then
    printf 'failing buckets:\n'
    printf '  - %s\n' "${failed[@]}"
    echo ""
    echo "Fix .claude/settings.json permissions.allow / permissions.deny and re-run."
    exit 1
fi
echo "READY. Allowlist is complete."
exit 0
