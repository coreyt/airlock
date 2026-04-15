#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PYPROJECT_TOML="$REPO_ROOT/pyproject.toml"
INIT_PY="$REPO_ROOT/airlock/__init__.py"
TRACING_PY="$REPO_ROOT/airlock/callbacks/tracing.py"

get_python_version() {
    sed -n 's/^version = "\([^"]*\)"/\1/p' "$PYPROJECT_TOML" | head -1
}

set_all_versions() {
    local version="$1"

    # pyproject.toml
    sed -i "s/^version = \"[^\"]*\"/version = \"$version\"/" "$PYPROJECT_TOML"

    # airlock/__init__.py
    sed -i "s/^__version__ = \"[^\"]*\"/__version__ = \"$version\"/" "$INIT_PY"

    # airlock/callbacks/tracing.py
    sed -i "s/trace.get_tracer(\"airlock\", \"[^\"]*\")/trace.get_tracer(\"airlock\", \"$version\")/" "$TRACING_PY"

    # Sync the lock file automatically
    cd "$REPO_ROOT" && uv lock
}

check_files() {
    local python_v init_v tracing_v
    python_v="$(get_python_version)"
    init_v="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$INIT_PY" | head -1)"
    tracing_v="$(sed -n 's/.*trace.get_tracer("airlock", "\([^"]*\)").*/\1/p' "$TRACING_PY" | head -1)"

    local rc=0
    if [ "$python_v" != "$init_v" ]; then
        echo "MISMATCH: pyproject.toml=$python_v  __init__.py=$init_v" >&2
        rc=1
    fi
    if [ "$python_v" != "$tracing_v" ]; then
        echo "MISMATCH: pyproject.toml=$python_v  tracing.py=$tracing_v" >&2
        rc=1
    fi
    if [ "$rc" -eq 0 ]; then
        echo "OK: all versions are $python_v"
    fi
    return $rc
}

parse_version() {
    local v="$1"
    IFS='.' read -r MAJOR MINOR MICRO <<< "$v"
}

usage() {
    cat <<'USAGE'
Usage: set-version.sh [OPTIONS]

Options:
  --set-version VERSION   Set all version files to VERSION (e.g. 1.2.3)
  --increment-major       Bump major, reset minor and micro to 0
  --increment-minor       Bump minor, reset micro to 0
  --increment-micro       Bump micro (patch) version
  --check-files           Check all version files are in sync (exit 1 if not)
  -h, --help              Show this help message

Exactly one action must be specified.
USAGE
}

ACTION=""
SET_VERSION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --set-version)
            ACTION="set"
            SET_VERSION="$2"
            shift 2
            ;;
        --increment-major)
            ACTION="inc-major"
            shift
            ;;
        --increment-minor)
            ACTION="inc-minor"
            shift
            ;;
        --increment-micro)
            ACTION="inc-micro"
            shift
            ;;
        --check-files)
            ACTION="check"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$ACTION" ]; then
    usage >&2
    exit 2
fi

case "$ACTION" in
    check)
        check_files
        ;;
    set)
        if [ -z "$SET_VERSION" ]; then
            echo "error: --set-version requires a VERSION argument" >&2
            exit 2
        fi
        set_all_versions "$SET_VERSION"
        check_files
        ;;
    inc-major)
        current="$(get_python_version)"
        parse_version "$current"
        new="$((MAJOR + 1)).0.0"
        echo "$current -> $new"
        set_all_versions "$new"
        check_files
        ;;
    inc-minor)
        current="$(get_python_version)"
        parse_version "$current"
        new="${MAJOR}.$((MINOR + 1)).0"
        echo "$current -> $new"
        set_all_versions "$new"
        check_files
        ;;
    inc-micro)
        current="$(get_python_version)"
        parse_version "$current"
        new="${MAJOR}.${MINOR}.$((MICRO + 1))"
        echo "$current -> $new"
        set_all_versions "$new"
        check_files
        ;;
esac
