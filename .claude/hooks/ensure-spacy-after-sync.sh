#!/usr/bin/env bash
# PostToolUse(Bash) guard.
#
# `uv sync` does an exact prune and deletes en_core_web_lg — the spaCy model
# Presidio's default-on PII guard loads via a bare AnalyzerEngine(). The model
# is a GitHub-wheel package kept out of uv.lock on purpose, so any `uv sync`
# removes it and silently breaks PII redaction on the next request.
#
# After a Bash tool call that ran `uv sync`, re-download the model if it's gone.
# Idempotent and quiet: it only acts when the command was a sync AND the model
# is actually missing. Never fails the tool call (always exits 0).
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
# shellcheck source=../../scripts/tool-versions.sh
. scripts/tool-versions.sh 2>/dev/null || exit 0

cmd="$(cat | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("tool_input",{}).get("command",""))
except Exception:
    pass' 2>/dev/null || true)"

case "$cmd" in
  *"uv sync"*)
    if ! uv run python -c "import $AIRLOCK_SPACY_MODEL" >/dev/null 2>&1; then
      echo "[airlock] uv sync pruned en_core_web_lg — restoring for the Presidio PII guard..." >&2
      if uv pip install --python .venv/bin/python "$AIRLOCK_SPACY_MODEL_URL" >/dev/null 2>&1; then
        echo "[airlock] $AIRLOCK_SPACY_MODEL==$AIRLOCK_SPACY_MODEL_VERSION restored." >&2
      else
        echo "[airlock] WARNING: could not restore $AIRLOCK_SPACY_MODEL. Run: make ensure-spacy" >&2
      fi
    fi
    ;;
esac
exit 0
