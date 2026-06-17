#!/bin/bash
# qa-dashboard installer for Mac. Idempotent — re-run safely.
#
# What it does:
#   1. Verifies prerequisites (python3 >= 3.9, node >= 18, deploycli CLI, claude CLI)
#   2. Installs Python deps (backend/requirements.txt)
#   3. Installs Node deps (frontend/)
#   4. Copies the qa-evidence skill to ~/.claude/skills/qa-evidence.md
#   5. Creates ~/.qa-dashboard.env from the template if missing
#
# What it does NOT do:
#   - Overwrite an existing ~/.qa-dashboard.env (you edit it once and forget)
#   - Overwrite an existing ~/.claude/skills/qa-evidence.md without asking
#   - Touch your pipeline-state.db, streams/, or other runtime state

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
USER_ENV_FILE="$HOME/.qa-dashboard.env"
SKILL_TARGET="$HOME/.claude/skills/qa-evidence.md"
SKILL_SOURCE="$ROOT/docs/qa-evidence.skill.md"

say()  { printf "\033[1;36m▸\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗\033[0m %s\n" "$*" >&2; exit 1; }

# ── 1. Prerequisites ──────────────────────────────────────────────────────
say "Checking prerequisites..."

command -v python3 >/dev/null || die "python3 not found. Install with: brew install python@3.12"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 9)))')
[ "$PY_OK" = "1" ] || die "python3 $PY_VER is too old. Need >= 3.9."
ok "python3 $PY_VER"

command -v node >/dev/null || die "node not found. Install with: brew install node"
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 18 ] || die "node $NODE_MAJOR is too old. Need >= 18."
ok "node $(node -v)"

command -v npm >/dev/null || die "npm not found (should ship with node)."
ok "npm $(npm -v)"

command -v deploycli >/dev/null || warn "deploycli CLI not on PATH — the build/deploy pipeline won't work. Install internally per acme docs."
command -v claude >/dev/null || warn "claude CLI not on PATH — FRIDAY chat won't work. Install via npm install -g @anthropic-ai/claude-code or your usual path."

# ── 2. Python deps ────────────────────────────────────────────────────────
say "Installing Python deps..."
python3 -m pip install --quiet --upgrade -r "$ROOT/backend/requirements.txt"
ok "Python deps installed"

# ── 3. Node deps ──────────────────────────────────────────────────────────
say "Installing Node deps..."
(cd "$ROOT/frontend" && npm install --silent)
ok "Node deps installed"

# ── 4. qa-evidence skill ──────────────────────────────────────────────────
mkdir -p "$(dirname "$SKILL_TARGET")"
if [ -f "$SKILL_TARGET" ]; then
    if cmp -s "$SKILL_SOURCE" "$SKILL_TARGET"; then
        ok "qa-evidence skill already up to date at $SKILL_TARGET"
    else
        warn "$SKILL_TARGET already exists and differs from the repo version."
        read -r -p "    Overwrite? [y/N] " ANS
        if [ "$ANS" = "y" ] || [ "$ANS" = "Y" ]; then
            cp "$SKILL_SOURCE" "$SKILL_TARGET"
            ok "qa-evidence skill updated"
        else
            warn "Skipped — your local skill is preserved. Diff with:"
            warn "    diff $SKILL_SOURCE $SKILL_TARGET"
        fi
    fi
else
    cp "$SKILL_SOURCE" "$SKILL_TARGET"
    ok "qa-evidence skill installed at $SKILL_TARGET"
fi

# ── 5. User env file ──────────────────────────────────────────────────────
if [ -f "$USER_ENV_FILE" ]; then
    ok "User env already exists at $USER_ENV_FILE — not touching it"
else
    say "Creating $USER_ENV_FILE — you'll need to fill in your Jira token and envs."
    cp "$ROOT/qa-dashboard.env.example" "$USER_ENV_FILE"
    chmod 600 "$USER_ENV_FILE"
    ok "Wrote $USER_ENV_FILE (mode 600)"
fi

echo ""
ok "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Edit $USER_ENV_FILE — set JIRA_TOKEN and QA_DASH_ENVS to YOUR Deploy envs."
echo "  2. Get a Jira API token at https://id.atlassian.com/manage-profile/security/api-tokens"
echo "  3. ./start.sh"
echo ""
echo "Then open http://localhost:5173"
