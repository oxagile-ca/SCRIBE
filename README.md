# qa-dashboard ("Agent Squad")

Per-user local dashboard for the acme QA pipeline: fetches Jira tickets,
runs build → deploy → test → evidence flows against your Deploy envs,
and chats with FRIDAY (Claude) for everything else.

Each teammate runs their own copy locally. Nothing is hosted — your laptop
is the server.

## Prerequisites

Mac only (the codebase assumes zsh/bash + macOS paths). You'll need:

- **Python 3.9+** — `brew install python@3.12`
- **Node 18+** — `brew install node`
- **`deploycli` CLI** — internal acme tool, install per the team docs.
  Without it the build/deploy pipeline is non-functional.
- **`claude` CLI** — `npm install -g @anthropic-ai/claude-code`.
  Required for the FRIDAY chat panel and for the `/qa-evidence` skill.
- **Jira API token** — create at
  https://id.atlassian.com/manage-profile/security/api-tokens

## Install (~2 minutes)

```bash
git clone https://bitbucket.org/acme/agent-friday.git ~/qa-dashboard
cd ~/qa-dashboard
./setup.sh
```

`setup.sh` is idempotent. It:

- Verifies prerequisites
- Installs Python + Node deps
- Copies the `/qa-evidence` skill to `~/.claude/skills/qa-evidence.md`
- Creates `~/.qa-dashboard.env` from the template (the only file you'll edit)

Then edit `~/.qa-dashboard.env`:

```
JIRA_EMAIL=your.name@example.com
JIRA_TOKEN=<paste your token here>
QA_DASH_ENVS=qa-env,qa-env-1,qa-env-2,qa-env-3
```

## Run

```bash
./start.sh
```

Opens:
- Frontend: http://localhost:5173
- Backend:  http://localhost:8000

The backend auto-reloads on `.py` edits. The frontend HMRs on `.tsx`/`.css` edits.

## Update

```bash
cd ~/qa-dashboard
git pull
./setup.sh   # picks up any new dep + reinstalls the skill if updated
```

Your `~/.qa-dashboard.env`, pipeline history (`pipeline-state.db`), and SSE
stream logs (`streams/`) are local-only and untouched by updates.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `port 8000 already in use` | `pkill -f "uvicorn server:app"` then `./start.sh` |
| `port 5173 already in use` | `pkill -f vite` then `./start.sh` |
| FRIDAY chat just spins | Check `claude` is on PATH (`which claude`). Hard-reload the browser tab. |
| Tickets list is empty | Open DevTools → Network. If `/api/tickets` is 401, your `JIRA_TOKEN` is wrong or expired. Re-issue at the Atlassian token page. |
| Deploy says "Successfully sent" but env stays on `k8s-stable` | Open the lane card → click an agent chip → check the log. Most common: snapshot name typo, or the snapshot artifact never finished building on Jenkins. |
| "Env in use by …" 409 on Start | Another lane is holding that env. Dismiss that lane to release the lock, or pick a different env from the dropdown. |
| `setup.sh` says `deploycli not on PATH` | Install deploycli per acme internal docs. Without it, build/deploy is read-only. |

## What's where

```
backend/
  server.py          FastAPI + SSE + env locks
  agents.py          run_pipeline (Builder → Shipper → Inspector → Scribe)
  jira_client.py     /api/tickets, dev-info, huddle, 3x3
  chat.py            FRIDAY chat (wraps `claude -p`)
  streams.py         disk-backed SSE stream replay (survives reload)
  pipeline_store.py  SQLite atomic pipeline state
  config.py          PROJECTS, ENVIRONMENTS, REPO_LIST, etc
  tests/             pytest, 64+ cases

frontend/
  src/App.tsx        lane state machine, SSE wiring
  src/components/    LaneCard, Queue, TopBar, ChatPanel, ...
  src/laneSchema.ts  versioned localStorage migration

docs/
  qa-evidence.skill.md  Source of the /qa-evidence skill installed to ~/.claude/skills/
```

## Configuration reference

Per-user (in `~/.qa-dashboard.env`):

| Var | Purpose |
|---|---|
| `JIRA_EMAIL` | Atlassian account email |
| `JIRA_TOKEN` | Atlassian API token |
| `QA_DASH_ENVS` | Comma-separated Deploy env names |
| `QA_DASH_DEFAULT_ENV` | Optional. First entry of `QA_DASH_ENVS` if unset. |
| `QA_DASH_SESSION_DIR` | Optional. Claude Code session dir override. Auto-derived from `$HOME` if unset. |
| `CLAUDE_BIN` | Optional. Default `claude`. |

Shared (in `backend/config.py` — edit + PR if it affects everyone):

| Const | Purpose |
|---|---|
| `PROJECTS` | Jira project keys in the dropdown |
| `TEAM` | Members surfaced in huddle/3x3 |
| `REPO_LIST` / `REPO_MAP` | Repos the pipeline knows about |
| `QA_ASSIGNEE_FIELD` | Jira custom field for QA assignee |
| `SERVICE_REFERENCE_MAP` | Which stable reference each service resets to |
