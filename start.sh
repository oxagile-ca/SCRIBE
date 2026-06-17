#!/bin/bash
# Start Agent Squad QA Dashboard

echo "Starting Agent Squad..."

ROOT="$(cd "$(dirname "$0")" && pwd)"

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

# Backend
# --reload restarts on .py edits so we never run stale code by accident.
# --reload-dir keeps the watcher scoped to the backend folder.
cd "$ROOT/backend"
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 \
    --reload --reload-dir "$ROOT/backend" &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID) [reload enabled]"

# Frontend
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"

echo ""
echo "Agent Squad is running!"
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
