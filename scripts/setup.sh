#!/usr/bin/env bash
# Airlock — standard setup
# Installs Airlock and its dependencies, initializes config files,
# and downloads the spaCy model needed for PII redaction.
#
# Usage:
#   ./scripts/setup.sh            # uses uv (recommended)
#   ./scripts/setup.sh --pip      # uses pip instead of uv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

USE_PIP=false
for arg in "$@"; do
  case "$arg" in
    --pip) USE_PIP=true ;;
    -h|--help)
      echo "Usage: ./scripts/setup.sh [--pip]"
      echo "  --pip   Use pip instead of uv for installation"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

cd "$PROJECT_ROOT"

echo "==> Airlock setup"

# ---------- Detect / install uv ----------
if [ "$USE_PIP" = false ]; then
  if ! command -v uv &>/dev/null; then
    echo "    uv not found — installing via curl..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  echo "    Using uv ($(uv --version))"
fi

# ---------- Create venv & install ----------
if [ "$USE_PIP" = true ]; then
  echo "==> Creating virtual environment..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate

  echo "==> Installing airlock..."
  pip install --upgrade pip
  pip install -e .
else
  echo "==> Installing airlock (uv)..."
  uv sync
fi

# ---------- spaCy model for Presidio PII ----------
echo "==> Downloading spaCy language model (en_core_web_lg)..."
if [ "$USE_PIP" = true ]; then
  python -m spacy download en_core_web_lg
else
  uv run python -m spacy download en_core_web_lg
fi

# ---------- airlock init ----------
echo "==> Running airlock init..."
if [ "$USE_PIP" = true ]; then
  airlock init
else
  uv run airlock init
fi

# ---------- Done ----------
cat <<'MSG'

Setup complete!

Next steps:
  1. Edit .env and add your API keys (at minimum ANTHROPIC_API_KEY).
  2. Start the proxy:
       uv run airlock tui --start   # TUI dashboard (recommended)
       uv run airlock start         # headless mode
  3. Test it:
       curl http://localhost:4000/v1/chat/completions \
         -H "Content-Type: application/json" \
         -H "Authorization: Bearer sk-airlock-change-me" \
         -d '{"model":"claude-sonnet","messages":[{"role":"user","content":"Hello!"}]}'
MSG
