# qa-dashboard ("Agent Squad")

Per-user local dashboard for QA pipelines: fetches issue tracker tickets,
runs build -> deploy -> test -> evidence flows against your QA environments,
and generates shareable evidence reports.

This guide is for a technical user who wants to run SCRIBE locally and connect it to their own product, issue tracker, repositories, and QA environment.

SCRIBE runs as a local web app:

- Frontend: React + Vite
- Backend: Python + FastAPI
- Local dashboard: `http://localhost:5173`
- Local API: `http://localhost:8000`

## 1. Prerequisites

Install these before starting:

| Tool | Required | Notes |
|---|---:|---|
| Git | Yes | Used to clone the repo |
| Python | Yes | Python 3.9+ |
| Node.js | Yes | Node 18+ |
| npm | Yes | Usually installed with Node |
| Claude / Anthropic access | Yes | Required for AI evidence generation |
| Issue tracker token | Yes | Jira, Linear, Azure DevOps, or GitHub Issues |
| VCS token | Yes | GitHub, Bitbucket, or Azure DevOps Repos |
| Test environment access | Yes | Staging, QA, deployed URL, or local dev server |

The current helper scripts are shell scripts, so macOS or Linux is the smoothest path. On Windows, use WSL or Git Bash.

## 2. Clone the Repository

```bash
git clone https://github.com/oxagile-ca/SCRIBE.git
cd SCRIBE
```

## 3. Install Dependencies

Run the setup script from the repo root:

```bash
./setup.sh
```

The setup script installs backend and frontend dependencies and prepares the local environment files.

If you prefer to install manually:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
```

## 4. Optional Bootstrap Environment File

This step exists because `start.sh` can load `~/.qa-dashboard.env` before the onboarding wizard has generated a product-specific config.

If you are setting up a new product through the onboarding wizard, you usually do not need to fill out all product details here. Treat this file as a bootstrap or fallback config only.

`setup.sh` creates this file automatically if it does not exist. You can also create it manually:

```bash
cp qa-dashboard.env.example ~/.qa-dashboard.env
open ~/.qa-dashboard.env
```

For a first local launch, the most useful values are:

```bash
JIRA_EMAIL=your.name@example.com
JIRA_TOKEN=optional-bootstrap-token
QA_DASH_ENVS=https://your-staging.example.com
```

`QA_DASH_ENVS` can be:

- One or more staging / QA URLs
- One or more named deploy environments
- A local app URL such as `http://localhost:3000`

Example:

```bash
QA_DASH_ENVS=https://staging.example.com,https://qa.example.com
```

Once onboarding is complete, SCRIBE primarily uses the generated product config and secrets, not this bootstrap file.

## 5. Start SCRIBE

From the repo root:

```bash
./start.sh
```

Then open:

```text
http://localhost:5173
```

The backend runs on:

```text
http://localhost:8000
```

Keep the terminal running while using the dashboard.

## 6. Complete Product Onboarding

On first launch, SCRIBE may show the onboarding wizard. This is the main product configuration step.

You will need:

- Organization name
- Product name
- Product description
- Product type
- Primary product URLs
- Test environment mode
- Issue tracker provider and token
- Issue tracker project keys
- QA status names such as `Ready for QA` and `In QA`
- Version control provider and token
- Repo list
- Critical user flows
- Known risk areas
- Baseline checks
- Optional documentation source, such as Notion or Confluence
- Anthropic API key or runner credential

The onboarding flow writes the real product setup locally:

```text
backend/instance.config.json      non-secret product configuration
backend/.secrets.env              local secrets loaded by the backend
~/.claude/skills/qa-evidence-*    product-specific QA evidence skill
instances/<product-slug>/         repo-local generated skill copy
```

In short: section 4 is only a bootstrap/fallback path; section 6 is the source of truth for a new product setup.

## 7. Environment Modes

Choose the mode that matches how your product is tested.

| Mode | Use When |
|---|---|
| Static staging URL | The app is already deployed at a fixed URL |
| Already-deployed environment | QA chooses from existing deployed test environments |
| Local dev server | You want SCRIBE to test a local app like `http://localhost:3000` |
| Build/deploy scripts | SCRIBE should run provided build and deploy commands before testing |

For the fastest first run, use a static staging URL or already-deployed QA environment.

## 8. Run a First Ticket

1. Open `http://localhost:5173`.
2. Select the configured project.
3. Find a ticket in the queue.
4. Start the ticket.
5. Select the target environment.
6. Let SCRIBE analyze the ticket and run the QA evidence workflow.
7. Review the result.
8. Open the generated evidence report.

Start with a small ticket that touches a visible part of the app. This makes it easier to verify that tracker access, repo access, environment access, and evidence generation are all working.

## 9. Evidence Output

Evidence is written locally. By default, reports are stored under:

```text
~/evidence/<TICKET-KEY>/runs/<run-id>/
```

Typical output includes:

```text
automated/       screenshots captured during the run
manual/          optional manually added screenshots
diffs/           visual diffs, when available
summary.json     structured verdict and findings
headless.log     raw AI/test runner output
index.html       self-contained evidence report
```

The `index.html` report can be opened locally in a browser.

## 10. Updating

From the repo root:

```bash
git pull
./setup.sh
```

Local evidence, local secrets, and generated runtime state are not intended to be committed.

## 11. Troubleshooting

| Symptom | What to Check |
|---|---|
| Frontend does not open | Confirm `npm install` completed and port `5173` is free |
| Backend does not start | Confirm Python deps installed from `backend/requirements.txt` |
| Tickets do not load | Check issue tracker URL, email, token, project keys, and status mapping |
| Repo data does not load | Check VCS provider, token permissions, org/workspace, and repo names |
| Evidence does not generate | Check Anthropic/Claude credentials and runner availability |
| Test cannot log in | Verify test user credentials, login URL, MFA/SSO requirements, and session restrictions |
| Wrong environment tested | Check `QA_DASH_ENVS` and selected environment in the ticket lane |
| Port already in use | Stop the existing process or change the port in local config |

Useful checks:

```bash
python3 --version
node --version
npm --version
```

Backend-only debug:

```bash
cd backend
python3 server.py
```

Frontend-only debug:

```bash
cd frontend
npm run dev
```

## 12. Files Worth Knowing

```text
README.md                         this guide
SETUP_GUIDE.md                    more detailed setup notes
qa-dashboard.env.example          local environment template
setup.sh                          installer
start.sh                          starts backend and frontend
backend/server.py                 FastAPI backend
backend/onboarding.py             onboarding generator
backend/config.py                 default config and instance overrides
frontend/src/App.tsx              main dashboard app
frontend/src/components/Onboarding/OnboardingWizard.tsx
```

## 13. Security Notes

- Use least-privilege tokens for the pilot.
- Use test product accounts instead of personal production accounts.
- Keep `.env`, `.secrets.env`, generated instance config with secrets, and evidence output out of Git.
- Review generated evidence before sharing because screenshots may contain product or customer data.
