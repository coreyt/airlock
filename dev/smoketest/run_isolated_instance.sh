#!/usr/bin/env bash
#
# run_isolated_instance.sh — stand up a FULLY ISOLATED Airlock test instance.
#
# PURPOSE
#   Production Airlock is live on this host (port 4000, plus something on 8090).
#   This script lets an OPERATOR launch a throwaway second instance on a spare
#   port, backed by an isolated runtime dir, so the served-header smoke test can
#   run WITHOUT touching production state, ports, or config.
#
# SAFETY BY CONSTRUCTION
#   * Refuses to use ports 4000 or 8090 (production).
#   * Refuses to proceed if the chosen port is already in use.
#   * COPIES config.yaml + .env into an isolated runtime dir; never edits the
#     originals.
#   * Overrides every shared-state path in the copied .env so the test instance
#     writes its logs / sqlite DB / circuit-breaker checkpoint into the runtime
#     dir only — and neutralizes remote sinks (S3 / SQL / FathomDB).
#
# THE AGENT THAT WROTE THIS DID NOT RUN IT. A human operator runs it.
#
# USAGE
#   ./dev/smoketest/run_isolated_instance.sh prepare   # copy + rewrite env, validate port
#   ./dev/smoketest/run_isolated_instance.sh start     # prepare (if needed) + launch in background
#   ./dev/smoketest/run_isolated_instance.sh stop      # kill ONLY the test PID
#   ./dev/smoketest/run_isolated_instance.sh status    # show PID / port / runtime dir
#
#   PORT=4137 ./dev/smoketest/run_isolated_instance.sh start   # override port
#
set -euo pipefail

# --- configuration ----------------------------------------------------------
PORT="${PORT:-4137}"                 # default spare port; override via env
HOST="${HOST:-127.0.0.1}"            # loopback only — never expose the test box
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="${RUNTIME_DIR:-$REPO_ROOT/dev/smoketest/.runtime}"
PID_FILE="$RUNTIME_DIR/airlock-test.pid"
LOG_TAIL="$RUNTIME_DIR/state/logs/launch.out"

FORBIDDEN_PORTS=(4000 8090)

# --- helpers ----------------------------------------------------------------
die() { echo "ERROR: $*" >&2; exit 1; }

refuse_forbidden_port() {
  for bad in "${FORBIDDEN_PORTS[@]}"; do
    if [[ "$PORT" == "$bad" ]]; then
      die "port $PORT is a PRODUCTION port. Refusing. Pick another (default 4137)."
    fi
  done
}

assert_port_free() {
  # ss -tlnH "sport = :PORT" prints a row only if something LISTENS on PORT.
  if ss -tlnH "sport = :$PORT" 2>/dev/null | grep -q .; then
    die "port $PORT is already in use. Pick a free port via PORT=<n>."
  fi
}

prepare() {
  refuse_forbidden_port
  assert_port_free

  [[ -f "$REPO_ROOT/config.yaml" ]] || die "no config.yaml at repo root: $REPO_ROOT"

  mkdir -p "$RUNTIME_DIR/state/logs"

  # COPY production config + env into the runtime dir (originals untouched).
  cp "$REPO_ROOT/config.yaml" "$RUNTIME_DIR/config.yaml"

  # litellm's get_instance_fn resolves custom-handler / callback module paths
  # (custom_provider_map, callbacks, success/failure_callback) RELATIVE TO the
  # config file's DIRECTORY when loaded from config (litellm/proxy/types_utils/utils.py
  # uses spec_from_file_location at <config_dir>/<dotted.module>.py). The runtime dir
  # has no airlock/ source tree, so symlink it in — without this the proxy aborts at
  # startup with "Could not import tavily_handler from airlock.providers.tavily_provider".
  ln -sfn "$REPO_ROOT/airlock" "$RUNTIME_DIR/airlock"
  if [[ -f "$REPO_ROOT/.env" ]]; then
    cp "$REPO_ROOT/.env" "$RUNTIME_DIR/.env"
  else
    echo "WARNING: no .env at repo root; creating a minimal one." >&2
    : > "$RUNTIME_DIR/.env"
  fi

  # --- override shared-state paths in the COPIED .env -----------------------
  # We append overrides; later assignments win in python-dotenv, so these
  # take precedence over any value copied from production.
  #
  # State dirs discovered by grepping the codebase for AIRLOCK_*DIR / datastore
  # paths (airlock/datastore.py, airlock/cli/main.py, airlock/proxy.py):
  #   AIRLOCK_LOG_DIR    — log files; also the FALLBACK state dir.
  #   AIRLOCK_STATE_DIR  — airlock.db (FathomDB) + cb_state.json checkpoint.
  # Remote sinks that would otherwise write to SHARED stores (neutralized):
  #   AIRLOCK_S3_BUCKET  — blank => s3_logger discards instead of uploading.
  #   AIRLOCK_SQL_URL    — blank => sql_logger disables itself.
  #   AIRLOCK_ENABLE_FATHOMDB=0 — keep FathomDB storage off in the test run.
  cat >> "$RUNTIME_DIR/.env" <<EOF

# ===== isolated smoke-test overrides (appended by run_isolated_instance.sh) =====
AIRLOCK_HOST=$HOST
AIRLOCK_PORT=$PORT
AIRLOCK_CONFIG=$RUNTIME_DIR/config.yaml
AIRLOCK_LOG_DIR=$RUNTIME_DIR/state/logs
AIRLOCK_STATE_DIR=$RUNTIME_DIR/state
# Neutralize remote/shared log sinks so the test never writes prod data stores:
AIRLOCK_S3_BUCKET=
AIRLOCK_SQL_URL=
AIRLOCK_ENABLE_FATHOMDB=0
# Keep guardrails in observe mode for the smoke test (no blocking surprises):
AIRLOCK_ENFORCE_MODE=observe
EOF

  echo "Prepared isolated runtime at: $RUNTIME_DIR"
  echo "  port    : $PORT (host $HOST)"
  echo "  config  : $RUNTIME_DIR/config.yaml (copy)"
  echo "  state   : $RUNTIME_DIR/state (logs, airlock.db, cb_state.json)"
  echo "Review $RUNTIME_DIR/.env before starting."
}

start() {
  # (Re)prepare to guarantee the port is still free and env is fresh.
  prepare

  echo "Launching isolated Airlock on $HOST:$PORT ..."
  # 'airlock start' loads .env from <config dir>/.env and applies AIRLOCK_*.
  # Run FROM the runtime dir so it picks up the copied config + overridden env.
  # Passing --port is redundant with AIRLOCK_PORT but makes the bind explicit.
  (
    cd "$RUNTIME_DIR"
    nohup uv run --project "$REPO_ROOT" airlock start --port "$PORT" \
      >"$LOG_TAIL" 2>&1 &
    echo $! > "$PID_FILE"
  )
  sleep 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  echo "Started PID ${pid:-unknown}. Logs: $LOG_TAIL"
  echo "Probe it:  python $REPO_ROOT/dev/smoketest/served_header_client.py \\"
  echo "             --base-url http://$HOST:$PORT --health"
}

stop() {
  [[ -f "$PID_FILE" ]] || die "no PID file at $PID_FILE — nothing to stop."
  local pid
  pid="$(cat "$PID_FILE")"
  [[ -n "$pid" ]] || die "empty PID file."
  if kill -0 "$pid" 2>/dev/null; then
    echo "Stopping test instance PID $pid ..."
    kill "$pid"
    # give it a moment, then SIGKILL if still alive
    sleep 2
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  else
    echo "PID $pid not running."
  fi
  rm -f "$PID_FILE"
}

status() {
  echo "runtime dir : $RUNTIME_DIR"
  echo "port        : $PORT"
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "instance    : RUNNING (pid $pid)"
    else
      echo "instance    : pid $pid not alive (stale pid file)"
    fi
  else
    echo "instance    : not started (no pid file)"
  fi
  echo "listeners on :$PORT ->"
  ss -tlnH "sport = :$PORT" 2>/dev/null || true
}

# --- dispatch ---------------------------------------------------------------
cmd="${1:-status}"
case "$cmd" in
  prepare) prepare ;;
  start)   start ;;
  stop)    stop ;;
  status)  status ;;
  *) die "unknown command: $cmd (use: prepare | start | stop | status)" ;;
esac
