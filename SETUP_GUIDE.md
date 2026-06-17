# Agent Squad — QA Dashboard
## Team Setup Guide

> Each teammate runs their own copy locally on their Mac.  
> Nothing is hosted — your laptop is the server.

---

## What this tool does

Agent Squad is a local dashboard that automates the QA pipeline for Jira tickets:

1. **Fetches** your assigned PROJ/PROJC tickets from Jira  
2. **Builds** the branch snapshot on Jenkins via `deploycli build`  
3. **Deploys** the snapshot to your personal Deploy env  
4. **Runs QA evidence** via the `/qa-evidence` Claude Code skill (headless, unattended)  
5. **Generates** a self-contained HTML evidence report with screenshots, narratives, score, and visual diffs  
6. **Keeps a persistent history** of all tested tickets — visible even after lanes are dismissed

The pipeline is non-blocking: you can monitor progress, click "Check Deploy" and "Check Evidence" buttons, and run multiple tickets in parallel across up to 4 personal Deploy envs.

---

## Prerequisites

You'll need all four of these on your Mac before running `setup.sh`.

| Tool | Min version | Install |
|------|-------------|---------|
| Python | 3.9+ | `brew install python@3.12` |
| Node | 18+ | `brew install node` |
| `deploycli` CLI | any | acme internal — install per Confluence docs |
| `claude` CLI | any | `npm install -g @anthropic-ai/claude-code` |

Also required: a **Jira API token** from https://id.atlassian.com/manage-profile/security/api-tokens  
(Use your acme Atlassian account; scope: all projects read + write)

---

## Install (2 minutes)

### Step 1 — Clone the repo

```bash
git clone <repo-url> ~/qa-dashboard
cd ~/qa-dashboard
```

Replace `<repo-url>` with the actual repo URL shared with you (ask maintainer).

### Step 2 — Run setup

```bash
./setup.sh
```

This is safe to re-run. It:
- Checks all prerequisites and warns if anything is missing
- Installs Python deps (`fastapi`, `uvicorn`, `httpx`, etc.)
- Installs Node deps (`react`, `vite`, `typescript`)
- Copies the `/qa-evidence` skill to `~/.claude/skills/qa-evidence.md`
- Creates `~/.qa-dashboard.env` (your personal config file — only created once)

### Step 3 — Edit your personal config

```bash
open ~/.qa-dashboard.env
```

Fill in these three values:

```bash
JIRA_EMAIL=your.name@example.com          # your Atlassian account email
JIRA_TOKEN=replace-me                      # paste your API token here
QA_DASH_ENVS=qa-env,qa-env-1,qa-env-2,qa-env-3
```

For `QA_DASH_ENVS`: use your personal Deploy envs. maintainer's are `qa-env`, `qa-env-1`, etc.  
Each teammate has their own set — check Deploy UI or ask maintainer for yours.

### Step 4 — Start it

```bash
./start.sh
```

Then open **http://localhost:5173** in your browser.

---

## Daily use

### Starting the dashboard

```bash
cd ~/qa-dashboard && ./start.sh
```

Runs in the foreground. Press `Ctrl+C` to stop.

### Basic workflow for a ticket

1. Select your Jira project from the dropdown in the top bar  
2. Find your ticket in the queue on the left  
3. Click **Start** — a lane card appears  
4. Choose a Deploy env from the dropdown (the tool picks the first free one by default)  
5. The pipeline runs automatically: **Builder → Shipper → Inspector → Scribe**  
6. Click **Check Deploy** to verify the snapshot is live on Deploy  
7. Click **Check Evidence** to pick up results from the Inspector  
8. When the Scribe finishes, click **View Report** to see the full evidence HTML  
9. Click **Dismiss** when you're done with the lane (the ticket stays in Evidence History)

### Evidence History panel

At the bottom of the page there is a collapsible **Evidence History** panel.  
It shows every ticket ever tested — even after dismissing the lane.  
Filter by ticket key, click the Jira link, view or generate reports from here.

### FRIDAY chat

Click the chat bubble icon in the top bar to open FRIDAY (powered by Claude).  
Ask it anything: ticket status, QA questions, how to use the tool, etc.

### Huddle / 3×3 buttons

The **Huddle** button generates a standup summary for your project.  
The **3×3** button generates a 3×3 grid summary (status · risk · next step per ticket).

---

## Evidence reports

Evidence for each ticket is stored at:

```
~/evidence/<TICKET-KEY>/runs/<run-id>/
├── automated/          screenshots captured during the run
├── markup/             annotated screenshots (if qa-markup CLI is installed)
├── diffs/              before/after visual diff images
├── manual/             manually added screenshots
├── summary.json        verdict, score, TC results, narratives, what-works/blocks
├── headless.log        full Claude Code output
└── index.html          self-contained HTML report (generated automatically)
```

The HTML report (`index.html`) is generated the first time you click "Check Evidence" after a run completes.  
It embeds all images as base64 — you can open it offline or share it with developers directly.

**Report features:**
- Score ring showing confidence (0–100)
- Verdict + band (PASS / pass-with-issues / needs-review / FAIL)
- Full-width screenshots with click-to-expand lightbox
- Test Result Narrative per TC (what the tester found, verbatim)
- What Works / Blockers callout boxes (green + red)
- Run-over-run progress delta (shows improvement vs prior run)
- Next Actions list
- Annotated markup screenshots (purple border)
- Visual diffs / before-after comparisons
- Environment layout table (which services are snapshot vs reference)
- Sticky top nav with Jira link

To share a report: send the `index.html` file directly — it's fully self-contained and offline-viewable.

---

## Updating

```bash
cd ~/qa-dashboard
git pull
./setup.sh
```

`setup.sh` will prompt before overwriting your `~/.qa-dashboard.env` or the skill file.  
Your evidence (`~/evidence/`), pipeline history (`pipeline-state.db`), and SSE logs (`streams/`) are untouched.

---

## Configuration reference

### `~/.qa-dashboard.env` (per-user, never committed)

| Variable | Required | Purpose |
|----------|----------|---------|
| `JIRA_EMAIL` | Yes | Your Atlassian account email |
| `JIRA_TOKEN` | Yes | Atlassian API token (all project scopes) |
| `QA_DASH_ENVS` | Yes | Your Deploy envs, comma-separated |
| `QA_DASH_DEFAULT_ENV` | No | Default env for auto-pick (defaults to first in list) |
| `QA_DASH_SESSION_DIR` | No | Claude Code session dir if non-standard |
| `CLAUDE_BIN` | No | Path to `claude` if not on default PATH |

### `backend/config.py` (shared, commit changes as PR)

| Constant | Purpose |
|----------|---------|
| `PROJECTS` | Jira project keys shown in the dropdown |
| `TEAM` | Team members for huddle/3×3 |
| `REPO_LIST` / `REPO_MAP` | Repos the pipeline builds |
| `QA_ASSIGNEE_FIELD` | Jira custom field ID for QA assignee |
| `SERVICE_REFERENCE_MAP` | Which `k8s-stable` ref each service resets to |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `port 8000 already in use` | `pkill -f "uvicorn server:app"` then `./start.sh` |
| `port 5173 already in use` | `pkill -f vite` then `./start.sh` |
| Tickets list is empty | Open DevTools → Network. If `/api/tickets` returns 401, your `JIRA_TOKEN` is expired. Get a new one at the Atlassian token page. |
| FRIDAY chat just spins | Run `which claude` in terminal. If missing: `npm install -g @anthropic-ai/claude-code` |
| "Env in use by …" on Start | Another lane is holding that env. Dismiss it to release the lock, or pick a different env. |
| Deploy says done but env is still on k8s-stable | Click the agent chip to expand the log. Usually: Jenkins build not finished yet (18–20 min), or snapshot name mismatch. |
| `setup.sh` says `deploycli not on PATH` | Install deploycli per acme Confluence docs. The dashboard still works for evidence viewing without it. |
| View Report button missing | Click "Check Evidence" — this triggers report generation. It only appears once evidence is confirmed. |
| Evidence History empty | Evidence is stored in `~/evidence/`. If that folder is empty, no runs have completed yet. |
| Backend crashes on start | Run `cd ~/qa-dashboard/backend && python3 server.py` to see the raw error. Most common: missing dep — run `./setup.sh` again. |

---

## Architecture (for the curious)

```
~/qa-dashboard/
├── backend/              Python 3.9+ FastAPI server (port 8000)
│   ├── server.py         All API routes, SSE streams, env locks
│   ├── agents.py         Pipeline logic: build/deploy/test/evidence/HTML report
│   ├── jira_client.py    Jira REST API wrapper
│   ├── chat.py           FRIDAY chat (wraps `claude -p`)
│   ├── streams.py        Disk-backed SSE fan-out (survives reload)
│   ├── pipeline_store.py SQLite atomic pipeline state
│   ├── config.py         All shared constants
│   ├── requirements.txt  Python deps
│   └── tests/            64+ pytest cases
│
├── frontend/             Vite + React + TypeScript (port 5173)
│   └── src/
│       ├── App.tsx        Lane state machine, SSE wiring, all logic
│       ├── components/    LaneCard, Queue, TopBar, ChatPanel, EvidenceHistory, …
│       └── api.ts         All fetch() wrappers
│
├── docs/
│   └── qa-evidence.skill.md   Source for the /qa-evidence Claude Code skill
│
├── setup.sh              Idempotent installer
├── start.sh              Starts both servers
├── qa-dashboard.env.example   Template for ~/.qa-dashboard.env
└── SETUP_GUIDE.md        This file

~/evidence/               Evidence output (NOT inside the repo)
~/.qa-dashboard.env       Your personal credentials (NOT in the repo)
```

---

## Support

Slack: ask maintainer  
Jira: PROJ project  
