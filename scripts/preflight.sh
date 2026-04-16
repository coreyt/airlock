#!/usr/bin/env bash
# Pre-push preflight — mirrors .github/workflows/ci.yml locally.
# Running this green guarantees CI will pass on GitHub.
#
# Usage:
#   ./scripts/preflight.sh              # all checks (test + lint + docker + security)
#   ./scripts/preflight.sh --fast       # lint only (seconds, not minutes)
#   ./scripts/preflight.sh --fix        # auto-fix formatting, then run all checks
#   ./scripts/preflight.sh --no-docker  # skip docker build
#
# Exit codes:
#   0 = all checks pass
#   1 = one or more checks failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
FAST=false
FIX=false
DOCKER=true
for arg in "$@"; do
    case "$arg" in
        --fast)      FAST=true ;;
        --fix)       FIX=true ;;
        --no-docker) DOCKER=false ;;
    esac
done

# ---------------------------------------------------------------------------
# Pinned versions — keep in sync with ci.yml
# ---------------------------------------------------------------------------
RUFF_VERSION="0.15.9"
MYPY_VERSION="1.20.0"
PIP_AUDIT_VERSION="2.7.3"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
FAILED=0
STEP=0

step() {
    STEP=$((STEP + 1))
    echo ""
    echo "[$STEP] $1"
    echo "────────────────────────────────────────"
}

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; FAILED=$((FAILED + 1)); }
skip() { echo "  ~ $1 (skipped: $2)"; }

run_or_fail() {
    local label=$1
    shift
    if "$@"; then
        pass "$label"
    else
        fail "$label"
    fi
}

# ---------------------------------------------------------------------------
# Ensure deps + linters installed
# ---------------------------------------------------------------------------
step "Install dependencies"
uv sync --extra test --extra db
uv pip install "ruff==$RUFF_VERSION" "mypy==$MYPY_VERSION"
pass "Dependencies synced"

# ---------------------------------------------------------------------------
# Auto-fix (optional)
# ---------------------------------------------------------------------------
if [ "$FIX" = true ]; then
    step "Auto-fix formatting"
    uv run ruff check --fix airlock/ tests/ || true
    uv run ruff format airlock/ tests/
    pass "ruff format applied"
fi

# ---------------------------------------------------------------------------
# Lint: Python (mirrors ci.yml lint job)
# ---------------------------------------------------------------------------
step "Ruff check"
run_or_fail "ruff check" uv run ruff check airlock/ tests/

step "Ruff format check"
if ! uv run ruff format --check airlock/ tests/; then
    fail "ruff format (run with --fix to auto-format)"
else
    pass "ruff format"
fi

step "Mypy (fast subsystem)"
run_or_fail "mypy airlock/fast/" uv run mypy airlock/fast/ --ignore-missing-imports

# ---------------------------------------------------------------------------
# Lint: GitHub Actions workflows
# ---------------------------------------------------------------------------
step "GitHub Actions workflow lint"
if command -v actionlint &>/dev/null; then
    run_or_fail "actionlint" actionlint .github/workflows/*.yml
else
    skip "actionlint" "not installed (go install github.com/rhysd/actionlint/cmd/actionlint@latest)"
fi

# ---------------------------------------------------------------------------
# Lint: YAML
# ---------------------------------------------------------------------------
step "YAML lint"
uv pip install yamllint 2>&1 | grep -v "already" || true
YAMLLINT_CONF=$(mktemp)
cat > "$YAMLLINT_CONF" <<'YAMLEOF'
extends: default
rules:
  line-length:
    max: 200
  truthy:
    check-keys: false
  comments:
    min-spaces-from-content: 1
  document-start: disable
YAMLEOF
if uv run yamllint -c "$YAMLLINT_CONF" .github/workflows/*.yml config.yaml airlock/cli/templates/config.yaml mkdocs.yml 2>&1; then
    pass "yamllint"
else
    fail "yamllint"
fi
rm -f "$YAMLLINT_CONF"

# ---------------------------------------------------------------------------
# Lint: Shell scripts
# ---------------------------------------------------------------------------
step "Shell script lint"
if command -v shellcheck &>/dev/null; then
    SHELL_SCRIPTS=$(find scripts/ -name '*.sh' -type f 2>/dev/null || true)
    if [ -n "$SHELL_SCRIPTS" ]; then
        # shellcheck disable=SC2086
        run_or_fail "shellcheck" shellcheck $SHELL_SCRIPTS
    else
        pass "no shell scripts found"
    fi
else
    skip "shellcheck" "not installed (apt install shellcheck)"
fi

# ---------------------------------------------------------------------------
# Lint: Markdown (docs build)
# ---------------------------------------------------------------------------
step "Documentation build"
if command -v mkdocs &>/dev/null; then
    if mkdocs build --strict --clean --quiet 2>&1; then
        pass "mkdocs build --strict"
    else
        fail "mkdocs build --strict"
    fi
else
    skip "mkdocs" "not installed (pip install mkdocs)"
fi

# ---------------------------------------------------------------------------
# Version consistency
# ---------------------------------------------------------------------------
step "Version consistency"
run_or_fail "version check" uv run python scripts/check-version-consistency.py

if [ "$FAST" = true ]; then
    echo ""
    echo "── Fast mode: skipping tests, docker, security ──"
    echo ""
    echo "════════════════════════════════════════"
    if [ "$FAILED" -gt 0 ]; then
        echo "FAILED: $FAILED check(s) failed."
        exit 1
    fi
    echo "LINT OK."
    exit 0
fi

# ---------------------------------------------------------------------------
# Test (mirrors ci.yml test job)
# ---------------------------------------------------------------------------
step "Tests"
run_or_fail "pytest" uv run pytest --tb=short -q

# ---------------------------------------------------------------------------
# Docker (mirrors ci.yml docker job)
# ---------------------------------------------------------------------------
if [ "$DOCKER" = true ]; then
    step "Docker build"
    if command -v docker &>/dev/null; then
        run_or_fail "docker build" docker build -t airlock:ci .
    else
        skip "docker build" "docker not available"
    fi
else
    echo ""
    echo "── Skipping docker build (--no-docker) ──"
fi

# ---------------------------------------------------------------------------
# Security (mirrors ci.yml security job)
# ---------------------------------------------------------------------------
step "Security audit"
uv pip install "pip-audit==$PIP_AUDIT_VERSION" 2>&1 | grep -v "already satisfied" || true
if uv run pip-audit; then
    pass "pip-audit"
else
    echo "  ~ pip-audit found warnings (non-blocking, matches CI behavior)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "════════════════════════════════════════"
if [ "$FAILED" -gt 0 ]; then
    echo "FAILED: $FAILED check(s) failed. Fix before pushing."
    exit 1
else
    echo "ALL CHECKS PASSED. Safe to push."
    exit 0
fi
