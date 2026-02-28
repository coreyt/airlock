#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Install aural for session continuity
pip install -e /home/coreyt/projects/aural

# Install airlock in editable mode
pip install -e .
