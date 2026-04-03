#!/usr/bin/env bash
# Airlock — developer setup
# Everything in the standard setup, plus test dependencies, optional extras,
# pre-commit checks, and verification that the test suite passes.
#
# Usage:
#   ./scripts/setup-dev.sh            # uses uv (recommended)
#   ./scripts/setup-dev.sh --pip      # uses pip instead of uv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

USE_PIP=false
for arg in "$@"; do
  case "$arg" in
    --pip) USE_PIP=true ;;
    -h|--help)
      echo "Usage: ./scripts/setup-dev.sh [--pip]"
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

echo "==> Airlock developer setup"

# ---------- Detect / install uv ----------
if [ "$USE_PIP" = false ]; then
  if ! command -v uv &>/dev/null; then
    echo "    uv not found — installing via curl..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  echo "    Using uv ($(uv --version))"
fi

# ---------- Create venv & install with all extras ----------
if [ "$USE_PIP" = true ]; then
  echo "==> Creating virtual environment..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate

  echo "==> Installing airlock with all extras..."
  pip install --upgrade pip
  pip install -e ".[test,metrics,tracing,search,s3,sql]"
else
  echo "==> Installing airlock with all extras (uv)..."
  uv sync --all-extras
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

# ---------- Verify install ----------
echo "==> Verifying installation..."
if [ "$USE_PIP" = true ]; then
  python -c "import airlock; print(f'    airlock package OK')"
  python -c "import pytest; print(f'    pytest {pytest.__version__} OK')"
else
  uv run python -c "import airlock; print('    airlock package OK')"
  uv run python -c "import pytest; print(f'    pytest {pytest.__version__} OK')"
fi

# ---------- Run tests ----------
echo "==> Running test suite..."
if [ "$USE_PIP" = true ]; then
  python -m pytest tests/ -x -q --tb=short
else
  uv run pytest tests/ -x -q --tb=short
fi

# ---------- Done ----------
cat <<'MSG'

Developer setup complete!

Next steps:
  1. Edit .env and add your API keys (at minimum ANTHROPIC_API_KEY).
  2. Start the proxy:
       uv run airlock tui --start   # TUI dashboard (recommended)
       uv run airlock start         # headless mode
  3. Dogfood with Claude Code:
       eval $(uv run airlock dogfood)
       claude

Useful commands:
  uv run pytest tests/                     # run tests
  uv run pytest tests/ -m "not live"       # skip tests requiring a running proxy
  uv run airlock hooks install             # install Claude Code hooks
  uv run airlock status                    # check proxy health
MSG
