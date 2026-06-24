#!/bin/bash
# Start Agent Squad QA Dashboard

echo "Starting Agent Squad..."

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Prefer the project venv's python. On this machine `python3` is the Windows
# Store shim (Python 3.14, no uvicorn), so running it crash-loops the backend
# supervisor and the board stays blank. Fall back to python3 only if the venv
# is missing.
PYTHON="$ROOT/.venv/Scripts/python.exe"
[ -x "$PYTHON" ] || PYTHON="python3"

# Per-user config: Jira credentials, Deploy envs. File is optional —
# if missing, the backend falls back to its hardcoded defaults (the
# maintainer's setup). Teammates create this via setup.sh.
USER_ENV_FILE="$HOME/.qa-dashboard.env"
if [ -f "$USER_ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$USER_ENV_FILE"
    set +a
    echo "Loaded user env from $USER_ENV_FILE"
fi

# ---------------------------------------------------------------------------
# Backend (supervised)
# ---------------------------------------------------------------------------
# The backend runs under a supervisor that:
#   * logs all uvicorn stdout/stderr to $BACKEND_LOG (the launcher used to
#     discard this, so crashes left no trace), and
#   * automatically restarts uvicorn if it exits OR stops serving :8000.
#
# By default we run WITHOUT --reload. A single process means a crashed worker
# actually exits, so the supervisor can bring it back. uvicorn's --reload only
# restarts the worker on file changes, NOT when it dies — that is what left the
# dashboard silently down (idle reloader, nothing bound to :8000). For active
# backend development you can opt into hot-reload with DASH_BACKEND_RELOAD=1
# (restart-on-exit only; the health watchdog is disabled in that mode because
# the reloader spawns a child worker a single kill would orphan).
BACKEND_LOG="$ROOT/backend.log"

log_backend() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$BACKEND_LOG"; }

backend_supervisor() {
    cd "$ROOT/backend" || exit 1
    local reload_args=()
    local watchdog=1
    if [ "${DASH_BACKEND_RELOAD:-0}" = "1" ]; then
        reload_args=(--reload --reload-dir "$ROOT/backend")
        watchdog=0
        log_backend "hot-reload ON (DASH_BACKEND_RELOAD=1); health watchdog disabled"
    fi
    # The watchdog needs curl to probe :8000; without it, fall back to
    # plain restart-on-exit so we never false-restart a healthy server.
    command -v curl >/dev/null 2>&1 || watchdog=0

    while true; do
        log_backend "starting: uvicorn server:app on 0.0.0.0:8000"
        "$PYTHON" -m uvicorn server:app --host 0.0.0.0 --port 8000 \
            "${reload_args[@]}" >>"$BACKEND_LOG" 2>&1 &
        local upid=$!
        if [ "$watchdog" = "1" ]; then
            # Poll :8000 while the process lives; if it stops answering, kill
            # it so the loop restarts a clean instance. Any HTTP reply (even
            # 404) means "alive"; connection refused / timeout means down.
            # First poll is after 10s, which covers normal startup.
            while kill -0 "$upid" 2>/dev/null; do
                sleep 10
                if ! curl -s -m 5 -o /dev/null "http://127.0.0.1:8000/"; then
                    log_backend "unresponsive on :8000 — killing and restarting"
                    kill "$upid" 2>/dev/null
                    break
                fi
            done
        fi
        wait "$upid" 2>/dev/null
        log_backend "backend stopped — restarting in 2s"
        sleep 2
    done
}

log_backend "=== session begin (start.sh) ==="
backend_supervisor &
BACKEND_PID=$!
echo "Backend supervisor started (PID: $BACKEND_PID) — logging to $BACKEND_LOG"

# Frontend
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"

echo ""
echo "Agent Squad is running!"
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000  (log: $BACKEND_LOG)"
echo ""
echo "Press Ctrl+C to stop."

cleanup() {
    echo ""
    echo "Stopping Agent Squad..."
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
    # Best-effort: stop the supervised uvicorn child if it outlived the loop.
    command -v pkill >/dev/null 2>&1 && pkill -f "uvicorn server:app" 2>/dev/null
    exit 0
}
trap cleanup INT TERM
wait
