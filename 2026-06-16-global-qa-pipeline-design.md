# QA Pilot — Self-Hostable QA Evidence Pipeline: Design & Build Plan

**Date:** 2026-06-16
**Status:** Draft
**Author:** Oxagile

> **Revision note (2026-06-16):** This doc was retargeted from a *hosted multi-tenant SaaS*
> to a **self-hostable, single-tenant, multi-user tool**. Any company runs its own instance,
> owns its data, and configures the tool to its own stack through plugins — no central
> hosting, no per-org isolation, no billing. The QA engine is rewritten to run on the
> Anthropic API + a headless browser instead of the Claude Code CLI. The five-layer
> architecture and plugin contract from the original draft survive; the org/tenancy/billing
> machinery is removed.

---

## Overview

QA Pilot turns any team's existing issue tracker, VCS, and deploy tooling into an automated
QA evidence pipeline. A team **self-hosts one instance** (Docker Compose), connects their Jira
or GitHub Issues, their GitHub or Bitbucket repos, and either a deploy command or a static
staging URL. From then on, any team member pastes a ticket key into the dashboard and the
system runs an AI-powered end-to-end QA pipeline — reading the PR diff, generating test cases,
executing Playwright tests in a headless browser, scoring the results, and publishing an
evidence package back to Jira, GitHub, Confluence, or Slack.

**Tagline:** Drop a ticket key. Get evidence.

**Core invariant:** Every run produces a self-contained evidence package — steps-log,
screenshots, field assertions, confidence score, and a linked report — regardless of which
tools the team uses.

**What this document covers:** the target end-state architecture, **and a phased,
test-protected plan ("how to do it") for transforming the existing single-user Acme tool
into this product.** The build strategy is a *strangler*: generalize the working code in place,
make the current Acme wiring the first plugin (and the regression oracle), and quarantine the
one genuine rewrite — the QA runtime — behind a single interface.

---

## Product Identity

### What it is
- A **self-hostable, single-tenant** web app. One deployment = one company.
- **Multi-user within the instance**: a QA team logs in; runs and evidence are attributed to users.
- An AI agent (Claude-powered, via the Anthropic API) that reads PR diffs and decides what to test.
- A 10-phase QA evidence pipeline, extracted from the Acme internal tool and made generic.
- A plugin system where any team's integration is a concrete implementation of 5 adapter interfaces.

### What it is NOT
- **Not a hosted SaaS** — there is no central service, no sign-up, no cross-company tenancy, no billing.
- Not a test framework — teams do not write test specs.
- Not a CI/CD replacement — it sits alongside existing pipelines.
- Not a monitoring tool — it tests specific tickets/PRs, not production.

### Who runs it
A company's platform/QA team deploys one instance (their infra, their data, their Anthropic
key). An **admin** completes first-run setup; **members** run pipelines. All secrets and
evidence stay on the company's own infrastructure.

---

## Target Architecture

Five layers, each with a single clear responsibility. "Org" from the original SaaS draft is
collapsed into "the instance."

```
┌─────────────────────────────────────────────────────────────┐
│  1. FRONTEND  — React + Vite (3-lane agent-squad dashboard)  │
│     + login, admin service-user-mgmt, first-run setup wizard   │
└─────────────────────────┬───────────────────────────────────┘
                          │ REST + SSE (authenticated)
┌─────────────────────────▼───────────────────────────────────┐
│  2. API SERVER  — FastAPI                                    │
│     Auth (sessions/JWT), instance config from DB,            │
│     plugin registry, run orchestration, webhook receiver     │
└──────┬────────────────────────────────────────┬─────────────┘
       │                                        │
┌──────▼──────────────┐              ┌──────────▼─────────────┐
│  3. PLUGIN LAYER    │              │  4. RUNNER (QA engine) │
│  Issue: Jira/GH     │              │  Anthropic API         │
│  VCS: GitHub/BB     │◄────────────►│  (claude-sonnet-4-6)   │
│  Deploy: script/URL │              │  Playwright on a       │
│  Env: pool/static   │              │  headless-chromium svc │
│  Publish: GH/Jira/  │              │  Calls adapters only   │
│    Confluence/Slack │              │  Streams SSE progress  │
└─────────────────────┘              └──────────┬─────────────┘
                                                │
┌───────────────────────────────────────────────▼─────────────┐
│  5. STORES                                                   │
│     Postgres: users, integrations, envs, runs, tc, manifests,│
│       webhooks, council                                      │
│     EvidenceStore: local FS volume (default) | S3/MinIO      │
│     Secret store: encrypted (Fernet), key from env           │
└─────────────────────────────────────────────────────────────┘
```

### Key architectural decisions

- **Plugin interface** is a Python ABC with 5 adapter types. Each adapter receives instance-scoped,
  decrypted credentials at instantiation. The Acme wiring becomes `AcmePlugin` — a bundled
  *example*, not a default.
- **Runner is stateless and CLI-free** — it receives a job payload, executes all pipeline phases
  against the Anthropic API and a headless browser, streams progress via SSE, uploads artifacts,
  and exits. No `claude` CLI on the host. No local state survives between runs.
- **Single tenant, multi-user** — auth is enforced at the API server. There is no `org_id`;
  rows, evidence, and config are instance-global. Users have roles (`admin`/`member`) and runs
  are attributed to the triggering user.
- **Config-driven, not hardcoded** — every Acme constant moves to DB-backed instance settings
  managed in the UI (see *Configuration*). Acme values ship only as a seedable example.
- **Postgres** is the metadata store, replacing the current SQLite `pipeline-state.db`.
- **Webhook triggers** — teams can wire GitHub PR events or Jira status transitions to kick off a
  run. Manual "Start" from the dashboard also works.
- **Concurrency** — up to N parallel runs (default 3, matching the 3-lane dashboard), as an async
  worker pool inside a single API process for v1. SSE remains disk/Postgres-backed for live
  progress and reload-survival.

---

## Plugin Interface (The "Any Stack" Contract)

Five Python ABCs. Every team's integration is a concrete implementation of these five. A team
ships all five as a single Python package, registered in instance settings.

```python
class IssueAdapter(ABC):
    """Reads tickets and posts comments/attachments back."""
    def get_ticket(self, key: str) -> Ticket: ...
    def get_pr_list(self, key: str) -> list[PullRequest]: ...
    def post_comment(self, key: str, body: str) -> None: ...
    def attach_file(self, key: str, path: str, name: str) -> None: ...
    def transition(self, key: str, status: str) -> None: ...

class VCSAdapter(ABC):
    """Fetches diffs and posts PR-level feedback."""
    def get_diff(self, repo: str, pr_id: str) -> str: ...
    def post_pr_comment(self, repo: str, pr_id: str, body: str) -> None: ...
    def get_branch(self, repo: str, pr_id: str) -> str: ...

class DeployAdapter(ABC):
    """Builds a snapshot and confirms readiness."""
    def build(self, repo: str, branch: str) -> BuildHandle: ...
    def deploy(self, env: str, service: str, handle: BuildHandle) -> None: ...
    def poll_ready(self, env_url: str, timeout_sec: int) -> bool: ...
    def reset(self, env: str, service: str) -> None: ...

class EnvAdapter(ABC):
    """Manages the pool of QA environments."""
    def acquire(self, ticket_key: str) -> Env: ...
    def release(self, env: Env) -> None: ...
    def list_envs(self) -> list[Env]: ...

class PublishAdapter(ABC):
    """Publishes the evidence package after a run."""
    def publish_report(self, run: RunResult) -> str: ...  # returns URL
    def publish_summary(self, run: RunResult) -> None: ...
```

### Plugin bundle format

Each plugin is a Python package with a `plugin.yml`:

```yaml
name: acme
label: Acme
adapters:
  issue: AcmeJiraAdapter
  vcs: AcmeBitbucketAdapter
  deploy: AcmeDeployAdapter
  env: AcmeEnvPoolAdapter
  publish: AcmeConfluenceAdapter
credentials_schema:
  - key: JIRA_TOKEN
    label: Jira API Token
    secret: true
  - key: BB_TOKEN
    label: Bitbucket Token
    secret: true
  - key: QA_DASH_ENVS
    label: Environment list (comma-separated)
    secret: false
```

### Built-in adapters at launch

| Adapter | Type | Notes |
|---|---|---|
| `JiraAdapter` | Issue | Atlassian Cloud — covers most teams. Generalizes current `jira_client.py`. |
| `GitHubIssuesAdapter` | Issue | GitHub Issues + linked PRs. |
| `GitHubVCSAdapter` | VCS | PR diff fetch, branch lookup. |
| `BitbucketVCSAdapter` | VCS | Atlassian API tokens. Generalizes current `bitbucket_client.py`. |
| `ScriptDeployAdapter` | Deploy | Teams provide shell commands: `build_cmd`, `deploy_cmd`, `readiness_url_pattern`. |
| `StaticEnvAdapter` | Env | Fixed URL list, no build/deploy — simplest onboarding path. |
| `GitHubPublishAdapter` | Publish | PR comment with link to evidence report. |
| `BitbucketPublishAdapter` | Publish | PR comment (matches current Acme behavior). |
| `JiraPublishAdapter` | Publish | Attachment + comment. |
| `ConfluencePublishAdapter` | Publish | Receives the Phase 9.5 HTML artifact; creates DRAFT or PUBLISHED page. |
| `SlackPublishAdapter` | Publish | Verdict + score to a configured webhook. |

**The `StaticEnvAdapter` + `ScriptDeployAdapter` combo is the zero-to-working path** for a new
team — no Kubernetes, no Deploy, no custom Python. Writing a custom adapter is the advanced
path. The Acme `deploycli`/`deploy` wiring ships as the bundled `AcmePlugin` example.

---

## The QA Runner (Runtime — Rewritten)

This is the deepest change and the dominant effort. Today the QA work runs *inside Claude Code*
(a user pastes `/qa-evidence …`), and Council + FRIDAY chat shell out to `claude -p`. The
standalone tool removes the `claude` CLI dependency entirely.

### Design

- **Agentic loop on the Anthropic API** (`claude-sonnet-4-6`) using tool-use. Tool definitions
  expose: browser actions (navigate, click, type, screenshot, reload, read DOM), evidence writes,
  `qa-markup`, `qa-score`, and adapter calls (issue/VCS reads, publish).
- **Headless browser** — the Runner drives Playwright connected to a `headless-chromium` service
  (`chromium.connect(wsEndpoint)`; Browserless or a Playwright server in the compose stack).
  Screenshots, HAR, and console logs are captured and written to the EvidenceStore. No local display.
- **Deterministic phases stay code, not prose.** The gates that make this tool trustworthy are
  re-implemented as ordinary Python routines the agent orchestrates — not free-form skill text:
  - **Document-lifecycle gates D1–D5** (save → reload → publish → reload → preview) per
    document-touching test case.
  - **Universal Validation Suite UV-1–6** (console errors, network errors, broken assets,
    document-lifecycle smoke, accessibility, visual-regression drift).
  - **Phase 8 gap gate**, **Phase 7.5 confidence scoring**, **Phase 7 report/traceability**,
    **Phase 9.7 timing analysis** — all deterministic.
- **AI phases are API calls**, matching the per-run usage budget:
  - **Phase 1 (manifest):** one large call — PR diff + ticket → ACs + TCs. Cached per ticket.
  - **Phase 2 (execution):** one call per TC for step-by-step Playwright instructions + assertion eval.
  - **Phase 7.5 (scoring):** one small call for the confidence explanation.
  - **Phase 8 (gap gate):** no AI call — deterministic.
- **Council** reviewers and **FRIDAY chat** move from `claude -p` subprocesses to Anthropic API
  calls. Verdict synthesis (PASS only if all reviewers pass; any BLOCK/ERROR → BLOCK) and the
  human-override + audit-log behavior are preserved.
- **`qa-markup` / `qa-score`** become in-process libraries (today they are CLI tools the skill invokes).

### Pipeline phases (unchanged in intent, re-homed in the Runner)

```
Phase -1   Build & Deploy          Only if a DeployAdapter is configured
Phase 0    Validate & Preflight    Env reachability, browser ping
Phase 1    Manifest Build          PR diff → AC extraction → TC generation (API)
Phase 2    Test Execution          Playwright on the headless browser service
Phase 2.5  Document Lifecycle      D1–D5 save→reload→publish→reload→preview gates
Phase 2.6  Universal Validation    UV-1–6 console, network, assets, a11y, visual regression
Phase 3    GIF Generation          ffmpeg, p0 TCs + failures
Phase 4    Manual Evidence Intake  Skipped in headless/auto mode
Phase 5    Cross-run Comparison    Visual + network + console diffs vs prior run
Phase 6    Markup                  Annotate screenshots
Phase 7    Report Generation       traceability.md, summary.md, index.html
Phase 7.5  Confidence Score        Deterministic scoring + one explanation call
Phase 8    Gap Gate                Hard blocks + warn-only checks (no AI)
Phase 9    Publish                 Per adapter, per run kind
Phase 9.5  HTML Artifact           Self-contained report, base64 screenshots
Phase 9.7  Timing Analysis         Wall-clock per phase
Phase 10   Cleanup                 Release env, close telemetry window
```

> **This runtime port (Phase 3 of the roadmap) is large enough to warrant its own detailed spec
> before implementation.** This document names and scopes it; a dedicated runtime spec will detail
> the tool schema, the agent loop, the headless-browser contract, and the per-gate test routines.

---

## Multi-User Auth & Roles

- **Users**: email + password (Argon2/bcrypt hash) or optional OAuth; role is `admin` or `member`.
- **Sessions/JWT** issued by the API server; all REST + SSE endpoints are authenticated.
- **Attribution**: every run records `triggered_by`; evidence and audit entries carry the user.
- **Roles**: members run pipelines and view evidence; admins additionally manage users,
  integrations, envs, publish targets, and the Anthropic key.
- **First-run bootstrap**: the first admin is created from `.env` (`ADMIN_EMAIL` / `ADMIN_PASSWORD`)
  or via the setup wizard on first boot. Members join via invite link.
- **No built-in auth is assumed at the network edge** — operators may still front the instance with
  their own SSO/VPN, but the tool ships usable out of the box for a team.

---

## Data Model (Postgres)

Instance-scoped — no `org_id`, no billing.

```sql
users
  user_id, email, password_hash, role (admin/member), created

integrations
  adapter_type (issue/vcs/deploy/env/publish),
  adapter_class, config (encrypted JSON)

envs
  env_name, url, status (free/busy), ticket_key, held_by (user_id), since

runs
  run_id, ticket_key, run_kind, status, triggered_by (user_id),
  started, completed, confidence, evidence_ref, phase_timings (JSON),
  claude_tokens, claude_cost

run_test_cases
  run_id, tc_id, title, status, steps_log (bool),
  evidence_paths (JSON array)

manifests
  ticket_key, manifest_yml (text), created, updated

webhooks
  source (github/jira), event, trigger_config (JSON)

council
  run_id, status (pending/pass/block/overridden),
  reviewers (JSON), override_user (user_id), override_reason, decided_at

settings
  key, value (JSON)   -- instance config + secret references
```

*Council gets a first-class table* (today it lives in JSON columns on `pipelines`).

---

## Evidence & Secret Storage

- **EvidenceStore interface** with two implementations:
  - `LocalFsEvidenceStore` (default) — a mounted volume. Layout: `{ticket_key}/runs/{run_id}/…`
    (the `org_id` prefix is dropped).
  - `S3EvidenceStore` (optional) — S3 or MinIO; signed URLs for the report portal.
- **Secret store** — adapter credentials and the Anthropic API key are encrypted at rest with
  Fernet; the key comes from `QA_SECRET_KEY` in the instance `.env`. Decrypted only in-memory at
  adapter instantiation.
- **Streams** — disk/Postgres-backed SSE replay (survives reload), as today.

```
evidence-root/
  {ticket_key}/
    manifest.yml
    runs/
      {run_id}/
        automated/{TC_ID}/  steps-log.json, field-assertions.json, *.png
        markup/
        diffs/
        summary.json
        index.html
```

---

## Packaging & Deployment

One **Docker Compose** stack, driven by a single `.env`:

| Service | Purpose |
|---|---|
| `api` | FastAPI server (auth, orchestration, webhooks, SSE) — serves the built frontend |
| `worker` | Runner pool (the QA engine); async, default 3 concurrent runs |
| `postgres` | Metadata store |
| `headless-chromium` | Playwright/Browserless server the Runner connects to |
| `minio` *(optional)* | S3-compatible evidence storage if not using a local volume |

**Instance `.env` keys:** `ANTHROPIC_API_KEY`, `QA_SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`,
`DATABASE_URL`, `EVIDENCE_BACKEND` (`local|s3`), plus optional S3 creds.

`docker compose up` → a working instance. Target: a new team completes onboarding and gets a
first evidence package in **under 15 minutes**.

---

## First-Run Onboarding Flow

A setup wizard, admin-only, run once on a fresh instance.

1. **Create admin** — from `.env` or the wizard.
2. **Connect issue tracker** — Jira Cloud (token/OAuth) or GitHub Issues (App install).
3. **Connect VCS** — GitHub (repo selection) or Bitbucket (token + workspace slug, validated with a test call).
4. **Configure deploy/env** —
   - *Fast path:* "I already have a staging URL." → `StaticEnvAdapter`. Done.
   - *Full path:* provide `build_cmd`, `deploy_cmd`, `readiness_url_pattern` (+ creds) → `ScriptDeployAdapter`. Custom Python plugin replaces this for advanced teams.
5. **Configure publish targets** — any combination of Jira comment, GitHub/Bitbucket PR comment, Slack webhook, Confluence page.
6. **Set Anthropic API key** — stored encrypted. The company pays Claude costs directly; usage is logged per run.
7. **Run first ticket** — paste a ticket key; the full pipeline runs; first evidence appears in 5–10 min.

Team members join via invite link and share the same dashboard.

---

## Configuration (De-Hardcoding)

Every tenant-specific value currently in `config.py` or per-user env moves into DB-backed instance
settings, surfaced in the Settings UI. Acme values are shipped only as a seedable example.

| Today (hardcoded / per-user) | Becomes |
|---|---|
| `JIRA_BASE_URL`, `JIRA_CLOUD_ID`, `QA_ASSIGNEE_FIELD` | Issue-adapter instance config |
| `PROJECTS`, `TEAM`, `STALE_DAYS` | Instance settings |
| `REPO_LIST`, `REPO_MAP` | VCS-adapter instance config |
| `ENVIRONMENTS`, `DEFAULT_ENV`, `SERVICE_REFERENCE_MAP`, `SERVICE_TEST_HOST_MAP` | Env/Deploy-adapter instance config |
| `AUTO_PROVISION_*`, parent-env keepalive | Env-adapter instance config (Acme plugin only) |
| `JIRA_EMAIL` / `JIRA_TOKEN` / `BITBUCKET_*` (per-user env) | Encrypted secret store |

---

## How To Do It — Phased Roadmap

Strangler strategy: generalize the working code in place, in independently shippable increments,
guarded by the existing ~3,600-line test suite. **Acme stays fully working until Phase 6 flips
the defaults** — it is the regression oracle the whole way.

### Phase 0 — Baseline & guardrails
Get the test suite running in CI. Add one end-to-end "golden" pipeline test against stubbed
`deploycli`/Jira/Bitbucket that pins current behavior. Inventory every Acme-specific assumption.
**Exit:** green CI + a behavior snapshot to refactor against.

### Phase 1 — De-hardcode into instance config
Introduce an `InstanceConfig` service (DB-backed) and route every `config.py` constant + per-user
env var through it; seed with current Acme values so behavior is unchanged. Add a minimal
Settings read path. **Exit:** zero hardcoded tenant constants in code; tests green with seeded config.

### Phase 2 — Plugin contract + extract the Acme plugin
Define the 5 ABCs. Refactor `jira_client`→`JiraAdapter`, `bitbucket_client`→`Bitbucket(VCS+Publish)Adapter`,
and the `deploycli`/`deploy` code in `agents.py`/`quartermaster.py`→`AcmeDeployAdapter`+`AcmeEnvAdapter`.
Build the generic `ScriptDeployAdapter`, `StaticEnvAdapter`, `GitHub*`, `Slack`, and `Confluence`
adapters. `run_pipeline`/`council` call **only adapter interfaces**. **Exit:** Acme runs as a
plugin; "swap an adapter without touching `pipeline.py`" holds; tests green.

### Phase 3 — Runtime port *(the big one — own sub-spec)*
Put a `Runner` interface in front of the engine. Build the API-driven Runner: agentic loop on the
Anthropic API + Playwright on the headless-chromium service; port the 10 phases (deterministic
gates as code, AI phases as API calls); reimplement `qa-markup`/`qa-score` as libraries; move
Council + FRIDAY chat off `claude -p` onto the API. **Exit:** a run completes end-to-end with no
`claude` CLI present.

### Phase 4 — Multi-user auth + roles
Users, sessions/JWT, login UI, admin service-user-mgmt, run/evidence attribution, first-run admin
bootstrap from `.env`. **Exit:** a team logs in; runs are attributed; admin/member roles enforced.

### Phase 5 — Storage backends + Postgres
Migrate `pipeline_store` to Postgres (Council → real table); implement the `EvidenceStore`
interface (local FS default, S3/MinIO optional); wire the encrypted secret store. **Exit:** no
SQLite/`~/evidence` dependency; secrets encrypted at rest.

### Phase 6 — Packaging & onboarding
Docker Compose stack; first-run setup wizard; generic README; **flip defaults off Acme** and
ship its wiring as the optional example plugin. **Exit:** `docker compose up` → a stranger
completes onboarding in <15 min and gets a first evidence package.

### Phase 7 — Hardening & docs
Security pass: secret handling, **SSRF/command-injection on `ScriptDeployAdapter`**, headless-browser
sandboxing, webhook signature verification. Generic Team Guide + a Plugin Authoring Guide.
**Exit:** security review clean; docs let an external team self-serve.

### Effort note
Phase 3 is roughly **50–60% of total effort** and is a *rewrite*, not a refactor. Phases 0–2, 4–7
are genuine refactor-and-extend of working code, each protected by the test suite.

---

## Build vs. Extract vs. Rewrite

### Extract / generalize from current code (refactor)

| Current file | New location | Changes |
|---|---|---|
| `backend/jira_client.py` | `plugins/builtin/jira_adapter.py` | Implement `IssueAdapter`; de-hardcode base URL/fields |
| `backend/bitbucket_client.py` | `plugins/builtin/bitbucket_adapter.py` | Implement `VCSAdapter` + `PublishAdapter`; de-hardcode workspace |
| `backend/agents.py` | `runner/pipeline.py` + `plugins/acme/*` | Split: generic orchestration vs `deploycli`/`deploy` → `DeployAdapter`/`EnvAdapter` |
| `backend/quartermaster.py`, `auto_provision.py` | `plugins/acme/*` | Acme env auto-provisioning → `EnvAdapter` (plugin-specific) |
| `backend/streams.py` | `runner/streams.py` | Keep disk/Postgres-backed SSE |
| `backend/pipeline_store.py` | `runner/store.py` | SQLite → Postgres |
| `backend/config.py` | `runner/instance_config.py` | Hardcoded constants → DB-backed instance settings |
| `backend/qa_patterns.py` | `runner/qa_patterns.py` | Mostly generic; de-hardcode patterns-file path |
| `backend/otel.py` | `runner/telemetry.py` | Generalize cost/telemetry source |
| `backend/council.py`, `council_prompts.py` | `runner/council.py` | Adapter calls; `claude -p` → Anthropic API |
| `frontend/src/` | `frontend/src/` | Add login + user-mgmt + setup wizard; pipeline UI largely unchanged; drop org-switcher |

### Build from scratch

| What | Why |
|---|---|
| Plugin ABC definitions + registry | New abstraction layer |
| `ScriptDeployAdapter` / `StaticEnvAdapter` | Generic deploy/env — do not exist today |
| `GitHub*` adapters | Only Bitbucket exists today |
| `SlackPublishAdapter` | Config option in the skill, not implemented |
| Auth service (users, roles, sessions) | Local tool has no auth |
| First-run setup wizard | New UI |
| `EvidenceStore` interface + S3 backend | `~/evidence/` local FS today |
| Encrypted secret store | `~/.claude/mcp.json` flat file / env today |
| Webhook receiver | Runs are manual today |
| Docker Compose packaging | No packaging today |

### Rewrite

| What | Why |
|---|---|
| **QA Runner** (Anthropic API + headless browser) | Replaces the Claude Code `/qa-evidence` skill runtime entirely |
| `qa-markup` / `qa-score` as libraries | Currently CLI tools the skill shells out to |
| Council + FRIDAY chat on the API | Currently `claude -p` subprocesses |

---

## Success Criteria

1. A new team can self-host and complete onboarding (steps 1–7) in **under 15 minutes** without writing code.
2. The first run produces a complete evidence package: manifest, steps-log, screenshots, confidence score, published report.
3. The Acme team can switch from the local tool to a QA Pilot instance with no change to their QA workflow — `AcmePlugin` is a drop-in.
4. Removing a plugin adapter and swapping in a different implementation requires **no changes to `runner/pipeline.py`**.
5. A run completes end-to-end with **no `claude` CLI** installed on the host.
6. Secrets are encrypted at rest; evidence and credentials never leave the company's infrastructure.

---

## Out of Scope (v1)

- Hosted multi-tenant SaaS, sign-up, cross-company isolation, and billing.
- Multi-process / clustered scale-out (single API process + async worker pool for v1).
- Native mobile app testing (iOS Simulator, Android Emulator).
- Test result analytics / trend dashboards beyond per-run scoring.
- AI-generated test maintenance (updating specs when PRs change the UI).
- Custom LLM provider (only Anthropic/Claude at launch).

---

## Open Questions / Risks

- **Phase 3 needs its own spec** before implementation — the runtime port is the highest-risk,
  highest-effort piece, and the deterministic gates (D1–D5, UV-1–6) must be ported without losing
  the hard-won edge-case behavior they encode.
- **Headless-browser fidelity** — some document-lifecycle flows may depend on behaviors that differ
  between the current local Playwright and a remote headless service; validate early in Phase 3.
- **`ScriptDeployAdapter` is a remote-code-execution surface** by design — it runs operator-supplied
  shell. Sandboxing and admin-only configuration are mandatory (Phase 7).
- **Concurrency model** — if a team needs more than a single API process, SSE and the worker pool
  must move to a shared bus (Postgres LISTEN/NOTIFY or Redis); deferred until there's demand.
