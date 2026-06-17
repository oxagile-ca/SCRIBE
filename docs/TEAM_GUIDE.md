# Agent Squad — QA Dashboard
## Team Guide: From Local Development to Release

> **Who this is for:** Everyone on the team — PM, Dev, and QA.  
> **What it covers:** What the dashboard does, how each role uses it, and how a ticket moves from code to release-ready.

---

## What Is Agent Squad?

Agent Squad is a local QA automation dashboard that each QA engineer runs on their own Mac. It replaces the manual process of:

- Figuring out which repos/branches are in a ticket
- Running deploycli build/deploy commands by hand
- Tracking whether a snapshot is actually live
- Running QA tests unattended
- Writing up evidence

Instead, you click **Start** on a Jira ticket and the dashboard handles the full pipeline. Progress streams live on screen. When it's done, there's a scored HTML evidence report you can share.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AGENT SQUAD DASHBOARD                              │
│                         http://localhost:5173                               │
│                                                                             │
│  ┌──────────────────┐   ┌─────────────────────────────────────────────┐    │
│  │  TICKET QUEUE    │   │  ACTIVE LANES (up to 3 parallel tickets)    │    │
│  │                  │   │                                             │    │
│  │ PROJ-333  [QA]  │   │  PROJ-355   env: qa-env              │    │
│  │ PROJ-355  [QA]  │   │  ▓▓▓▓▓▓▓▓░░  Building...  18 min left     │    │
│  │ PROJ-404  [QA]  │   │  [Check Deploy]  [Check Evidence]           │    │
│  │                  │   │                                             │    │
│  └──────────────────┘   └─────────────────────────────────────────────┘    │
│                                                                             │
│  ──────────────────────── EVIDENCE HISTORY ────────────────────────────    │
│  PROJ-333  ✅ PASS 92   PROJ-311  ✅ PASS 87   PROJ-290  ⚠ REVIEW     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The Pipeline at a Glance

Every ticket runs through 5 automated steps. Each step is represented by one of the four agent characters on the lane card.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         PIPELINE STAGES                                      │
│                                                                              │
│  STEP 1          STEP 2          STEP 3          STEP 4          STEP 5     │
│                                                                              │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐  │
│  │  ENV    │    │ BUILDER │    │ SHIPPER │    │INSPECTOR│    │ SCRIBE  │  │
│  │ CHECK   │───▶│         │───▶│         │───▶│         │───▶│         │  │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘  │
│                                                                              │
│  Check envs      Build the      Reset envs,    Emit QA        Score         │
│  STALE or OK     snapshot on    deploy the     command,       evidence,     │
│                  Jenkins        snapshot       collect        generate      │
│                  if needed      to your env    evidence       HTML report   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Full Team Flow: Local to Release

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    TICKET LIFECYCLE — WHO DOES WHAT                         │
│                                                                              │
│  DEV                    QA                      PM                          │
│  ─────────────────────  ──────────────────────  ──────────────────────────  │
│  Code feature            │                       │                          │
│       │                  │                       │                          │
│  Push branch to BB       │                       │                          │
│       │                  │                       │                          │
│  Open PR → Jira auto-    │                       │                          │
│  links the PR            │                       │                          │
│       │                  │                       │                          │
│  (optional) Pre-build    │                       │                          │
│  snapshot in dashboard ──┼──▶ Ticket appears in  │                          │
│       │                  │    queue as "In QA"   │                          │
│       │                  │         │             │                          │
│       │                  │    Click START        │                          │
│       │                  │    Pick an env        │                          │
│       │                  │         │             │                          │
│       │                  │    Pipeline runs      │                          │
│       │                  │    (auto)             │                          │
│       │                  │         │             │                          │
│       │◀─────────────────┼── Build takes ~18min  │                          │
│       │  (Watch for       │   Deploy ~20-25min   │                          │
│       │   Jenkins errors) │         │             │                          │
│       │                  │    Click CHECK DEPLOY │                          │
│       │                  │    ✅ Snapshot live   │                          │
│       │                  │         │             │                          │
│       │                  │    Click CHECK        │                          │
│       │                  │    EVIDENCE           │                          │
│       │                  │         │             │                          │
│       │                  │    Scribe scores      │                          │
│       │                  │    report             │                          │
│       │                  │         │             │                          │
│       │   ┌──── FAIL ─────┤         │             │                          │
│       │◀──┘  QA comment   │    PASS ──────────────┼──▶ PM sees ticket       │
│  Fix + re-push            │    Jira → "Ready for  │    status updated       │
│       │                   │    Release"           │                          │
│       │                   │    Share report link  │◀── Review evidence      │
│       └───────────────────┘         │             │    report               │
│                                     │             │         │               │
│                                     └─────────────┼── Sign off for release  │
│                                                   │                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Role-by-Role Guide

---

### Developer

You interact with the dashboard minimally — mostly to confirm your branch is deployable.

#### What you need to do

1. **Push your branch** to Bitbucket and open a PR as normal
2. **Link your PR to the Jira ticket** (Bitbucket does this automatically via smart commits or the development panel)
3. **Optionally: pre-build your snapshot** to save QA time

   Open the dashboard at `http://localhost:5173` (anyone on the team can do this):
   - Find your ticket in the queue
   - Click **Start** → choose any free env
   - The Builder will trigger `deploycli build` for your branch
   - Build takes ~18–20 min. You can close the tab — it runs in the background.

4. **Confirm it's deployed** before telling QA "it's ready":
   - On the lane card, click **Check Deploy**
   - Green = snapshot is live. Grey = still building or needs a manual deploy.

#### What the dashboard discovers automatically

When QA starts your ticket, the dashboard calls the Jira dev-info API and finds:
- All repos your PRs touch
- All branch names
- Which services those repos map to

You don't need to tell QA anything — as long as your PRs are linked in Jira, the dashboard finds them.

#### Multi-PR tickets

If your ticket has PRs across multiple repos, the dashboard handles all of them. It uses this consolidation logic:
1. Drops DECLINED PRs
2. Prefers PRs targeting `main`, `master`, `develop`, or `release-*`
3. Drops PRs whose source branch is stacked on another PR's source branch (to avoid overwriting snapshots)

One snapshot per service is deployed.

---

### QA Engineer

This is your primary tool. It replaces most manual deploycli commands.

#### Daily startup

```bash
cd ~/qa-dashboard && ./start.sh
```

Then open `http://localhost:5173`. Leave the terminal window open while you work.

#### Working a ticket

```
┌───────────────────────────────────────────────────────────────────────┐
│  QA WORKFLOW — STEP BY STEP                                           │
│                                                                       │
│  1. Find ticket in queue                                              │
│     └─▶ Shows tickets assigned to you in PROJ / PROJC               │
│                                                                       │
│  2. Click START → choose env                                          │
│     └─▶ qa-env, qa-env-1, -2, or -3                       │
│         (pick one that isn't already in use)                         │
│                                                                       │
│  3. Watch the pipeline run                                            │
│     ┌─────────────────────────────────────────────────┐              │
│     │ ENV CHECK  →  BUILDER  →  SHIPPER  →  INSPECTOR │              │
│     │    ~1min      ~18-20min   ~20-25min    instant   │              │
│     └─────────────────────────────────────────────────┘              │
│     You can work on other tickets while waiting                       │
│                                                                       │
│  4. Click CHECK DEPLOY when Shipper finishes                          │
│     └─▶ Confirms snapshot is actually live on Deploy               │
│         Green checkmark = ready to test                              │
│                                                                       │
│  5. Click COPY QA CMD → paste into Claude Code                        │
│     └─▶ Runs /qa-evidence automatically (headless, unattended)       │
│                                                                       │
│  6. Click CHECK EVIDENCE when Inspector shows activity               │
│     └─▶ Picks up results from ~/evidence/<TICKET>/runs/              │
│                                                                       │
│  7. Scribe generates score + HTML report                              │
│     └─▶ Click VIEW REPORT to review                                  │
│         Click DISMISS when done with the lane                        │
│                                                                       │
│  8. Share evidence (optional)                                         │
│     └─▶ Send index.html directly — it's self-contained              │
└───────────────────────────────────────────────────────────────────────┘
```

#### Running multiple tickets in parallel

You have 4 Deploy envs. You can run up to 3 lanes simultaneously. Each lane locks one env for the duration of the pipeline.

```
┌────────────────────────────────────────────────────────┐
│  PARALLEL LANES EXAMPLE                                │
│                                                        │
│  Lane 1: PROJ-355  ──▶  qa-env     [BUILDING]   │
│  Lane 2: PROJ-370  ──▶  qa-env-1  [DEPLOYING]   │
│  Lane 3: PROJ-380  ──▶  qa-env-2  [INSPECTOR]   │
│                                                        │
│  qa-env-3 is free — available for a 4th ticket   │
└────────────────────────────────────────────────────────┘
```

To release a locked env, click **Dismiss** on a completed lane.

#### Evidence reports

Evidence is stored at `~/evidence/<TICKET-KEY>/runs/<run-id>/`. The HTML report includes:

- **Score ring** — 0–100 confidence score
- **Verdict** — PASS / pass-with-issues / needs-review / FAIL
- **Screenshots** — full-width, click to expand
- **Test result narratives** — what the tester found per test case
- **What Works / Blockers** — green and red callout boxes
- **Visual diffs** — before/after comparisons
- **Environment table** — which services are snapshot vs k8s-stable
- **Run delta** — improvement vs prior run

#### Evidence History panel

At the bottom of the dashboard there's an **Evidence History** panel. It shows every ticket you've ever tested — even after dismissing lanes. Use it to:
- Find old evidence for a ticket being re-tested
- Check the score trend across runs
- Link back to the Jira ticket

---

### Product Manager

You don't run the dashboard yourself. Your visibility comes from:

1. **Jira ticket status** — QA transitions tickets as the pipeline runs
2. **Evidence reports** — QA shares the `index.html` file or a Drive link after Scribe finishes
3. **Score + verdict** — the top of every report shows the score (0–100) and band

#### What to look for in an evidence report

```
┌──────────────────────────────────────────────────────────────┐
│  EVIDENCE REPORT — PROJ-355                                 │
│                                                              │
│   ┌──────┐  Verdict: PASS                                   │
│   │  92  │  Band:    High Confidence                        │
│   │  ●   │  Tested:  2026-06-04 14:32                      │
│   └──────┘  Environment: qa-env                        │
│                                                              │
│  ✅ What Works                                               │
│   • Feature X renders correctly on all viewport sizes       │
│   • Form submission updates Jira as expected                │
│   • No regression in related feature Y                      │
│                                                              │
│  🔴 Blockers                                                 │
│   • (none)                                                   │
│                                                              │
│  Test Cases: 6/6 passed                                      │
└──────────────────────────────────────────────────────────────┘
```

#### Release sign-off checklist

Before approving a sprint for release, verify for each ticket:

| Check | How to verify |
|-------|---------------|
| Ticket is "Ready for Release" in Jira | Jira board |
| Evidence report exists with a score | QA shares the HTML or Drive link |
| Score is PASS or pass-with-issues | Top of report |
| No open blockers in the report | "Blockers" section in report |
| No related tickets still in QA | Jira sprint board |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     HOW IT ALL CONNECTS                                      │
│                                                                              │
│                        YOUR MAC                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                                                                     │    │
│  │  Browser (localhost:5173)                                           │    │
│  │  ┌──────────────────────────────────────────────────────────────┐  │    │
│  │  │  React Frontend  (Vite + TypeScript)                         │  │    │
│  │  │  • Ticket queue + lane cards                                 │  │    │
│  │  │  • Live progress via SSE                                     │  │    │
│  │  │  • Evidence History panel                                    │  │    │
│  │  │  • FRIDAY chat panel                                         │  │    │
│  │  └────────────────────────┬─────────────────────────────────────┘  │    │
│  │                           │ HTTP / SSE                              │    │
│  │  ┌────────────────────────▼─────────────────────────────────────┐  │    │
│  │  │  FastAPI Backend  (localhost:8000)                           │  │    │
│  │  │  • server.py     — routes, SSE fan-out, env locks           │  │    │
│  │  │  • agents.py     — pipeline logic (build/deploy/test)       │  │    │
│  │  │  • jira_client.py — Jira REST API calls                     │  │    │
│  │  │  • streams.py    — disk-backed SSE (survives reload)        │  │    │
│  │  │  • pipeline_store.py — SQLite state persistence             │  │    │
│  │  └──────┬──────────────────┬───────────────────────────────────┘  │    │
│  │         │                  │                                       │    │
│  │  ┌──────▼──────┐   ┌───────▼───────────────┐                      │    │
│  │  │ Jira REST   │   │  deploycli CLI              │                      │    │
│  │  │ API         │   │  • deploy build     │                      │    │
│  │  │ acme.    │   │  • deploy deploy    │                      │    │
│  │  │ atlassian   │   │  • deploy ls        │                      │    │
│  │  │ .net        │   └───────────┬───────────┘                      │    │
│  │  └─────────────┘               │                                   │    │
│  │                                │                                   │    │
│  │  ┌─────────────────────────────▼──────────────┐                   │    │
│  │  │  ~/evidence/<TICKET>/runs/<run-id>/         │                   │    │
│  │  │  summary.json, index.html, screenshots …   │                   │    │
│  │  └────────────────────────────────────────────┘                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                │                                             │
│                                ▼                                             │
│                    Deploy (acme infra)                                  │
│                    Jenkins builds + k8s envs                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Timing Reference

Understanding timing helps everyone set expectations:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PIPELINE TIMING                                                         │
│                                                                          │
│  Start ticket                                                            │
│       │                                                                  │
│       ├── Env check          ~1 min                                      │
│       │                                                                  │
│       ├── Jenkins build      ~18-20 min  ◀ longest step                 │
│       │   (only if snapshot doesn't exist yet)                          │
│       │                                                                  │
│       ├── Snapshot deploy    ~20-25 min  ◀ second longest               │
│       │                                                                  │
│       ├── Evidence run       ~5-10 min   (headless, unattended)         │
│       │                                                                  │
│       └── Report generation  ~1 min                                     │
│                                                                          │
│  Total (cold start):  ~45-55 min                                        │
│  Total (pre-built):   ~25-35 min  (Dev pre-built the snapshot)          │
│  Total (re-test):     ~6-12 min   (snapshot already deployed)           │
└──────────────────────────────────────────────────────────────────────────┘
```

**Pro tip for QA:** Start 2–3 tickets at once. While PROJ-355 is building, PROJ-370 can be deploying, and PROJ-380 can be collecting evidence.

---

## Environment Map

```
┌────────────────────────────────────────────────────────────────────┐
│  DEPLOY ENVIRONMENTS                                             │
│                                                                    │
│  qa-env     ──▶  maintainer's primary env                         │
│  qa-env-1   ──▶  maintainer's second env                          │
│  qa-env-2   ──▶  maintainer's third env                           │
│  qa-env-3   ──▶  maintainer's fourth env                          │
│                                                                    │
│  Each env can hold ONE ticket pipeline at a time.                  │
│  Non-ticket services → reset to k8s-stable reference.             │
│  Ticket services → deployed as feature snapshots.                 │
│                                                                    │
│  Snapshot naming: BRANCH-NAME-UPPERCASE, slashes → dashes         │
│  Example: feature/my-fix → FEATURE-MY-FIX                         │
└────────────────────────────────────────────────────────────────────┘
```

---

## Common Issues & Fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Ticket queue empty | Jira token expired | Get new token at id.atlassian.com → paste in `~/.qa-dashboard.env` |
| Check Deploy stays grey | Jenkins build still running | Wait 18-20 min from when Builder started. Check lane log. |
| Check Deploy green but feature not visible | Wrong env in browser | Confirm you're on the env shown on the lane card |
| Evidence score is null | Run completed but no `summary.json` | Click Check Evidence again — or check `~/evidence/<KEY>/runs/` for `headless.log` |
| "Env in use" on Start | Another lane holds that env | Dismiss the other lane, or pick a different env |
| Pipeline fails at Shipper | `service-assets-b` uses `projd-stable` not `k8s-stable` | Known issue — maintainer to fix. Manually reset that service if needed. |
| Backend won't start | Missing dependency | Run `./setup.sh` again |
| Port 8000 in use | Old uvicorn process | `pkill -f "uvicorn server:app"` |

---

## Repo Coverage

These are the repos the pipeline knows how to build and deploy:

| Repo | Type | Notes |
|------|------|-------|
| service-cms | Deployable service | |
| service-a | Deployable service | PROJ-404 has open backend enum gap — needs sibling backend PR |
| service-b | Deployable service | |
| service-assets | Deployable service | |
| service-assets-b | Deployable service | Uses `projd-stable` not `k8s-stable` for reference |
| service-rel-mgr | Deployable service | |
| service-config-mgr | Deployable service | |
| service-user-mgmt | Deployable service | |
| lib-framework | Library | Not deployable — pipeline skips deploy step |
| lib-rules | Library | Not deployable — pipeline skips deploy step |

---

## Getting Set Up (New Team Member)

> Full instructions are in `SETUP_GUIDE.md`. Quick version:

```
1. Install prereqs: Python 3.9+, Node 18+, deploycli CLI, claude CLI
   └─▶ brew install python@3.12 node
   └─▶ npm install -g @anthropic-ai/claude-code

2. Get a Jira API token
   └─▶ https://id.atlassian.com/manage-profile/security/api-tokens
   └─▶ Use your acme Atlassian account
   └─▶ Select Atlassian API token (NOT Bitbucket App Password — those are deprecated)

3. Ask maintainer for: repo URL + your Deploy env names

4. Clone and install
   git clone <repo-url> ~/qa-dashboard
   cd ~/qa-dashboard && ./setup.sh

5. Edit ~/.qa-dashboard.env with your email, token, env names

6. Start it
   cd ~/qa-dashboard && ./start.sh
   └─▶ Open http://localhost:5173
```

---

## Known Gaps (Next Steps)

These things work but could be better:

| Gap | Current workaround | Planned fix |
|-----|--------------------|-------------|
| Dashboard is localhost-only — PM can't access it | QA shares the `index.html` file directly | Deploy to shared internal host or auto-post to Jira |
| No Slack notification when build finishes | QA watches the lane card | Add Slack webhook when Builder completes |
| Evidence not auto-attached to Jira | QA manually shares report | Scribe step to post Drive link as Jira comment |
| No sprint-level release readiness view | PM checks tickets one by one in Jira | Add sprint summary view to dashboard |
| `service-assets-b` cleanup uses wrong reference | Manual reset | Fix `SERVICE_REFERENCE_MAP` in `config.py` |
| `_poll_deploy` / `_deploy_service` in `agents.py` are unused | Harmless dead code | Clean up |

---

## Support

Slack maintainer for: setup help, Deploy env questions, dashboard bugs  
Jira project: PROJ (primary), PROJC (secondary)  
Evidence questions: check `~/evidence/<TICKET>/runs/` first — `headless.log` has the full run output
