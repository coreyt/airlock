#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Persist rendered projections to git so the next cloud session has context
git add -f \
  .wake/projection.md \
  .wake/constraints.md \
  .wake/decisions.md \
  .wake/blocked.md \
  .wake/rejected.md \
  .wake/stack.json \
  2>/dev/null || true

# Only commit if there are staged changes
if git diff --cached --quiet; then
  exit 0
fi

git commit -m "chore(wake): persist session projections" --no-verify
git push -u origin HEAD 2>/dev/null || true
