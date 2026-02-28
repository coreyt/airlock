#!/bin/bash
# PreToolUse hook: warn if git commit with no Smart Events this session.
# Reads stdin JSON payload; exits fast for non-commit commands.
COMMAND=$(python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")

[[ "$COMMAND" != git\ commit* ]] && exit 0

aural commit-check
