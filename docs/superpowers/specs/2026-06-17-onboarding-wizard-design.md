# Onboarding Wizard — Design Spec

**Date:** 2026-06-17
**Status:** Approved (design)
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)

## Goal

A first-run, multi-step React wizard that collects company + product + stack +
critical-flow info, and on submit generates: (1) a backend **instance config**, and
(2) a **product-customized `/qa-evidence` skill** + seeded **`patterns.yml`**. v1 uses
**deterministic templating** (no AI call) so onboarding works offline and needs no key.

## Architecture (3 isolated units)

1. **Wizard UI** (`frontend/src/components/Onboarding/`) — holds one aggregate `answers`
   object; POSTs to `/api/onboarding` on finish. `App.tsx` gates on configured-state.
2. **Generator** (`backend/onboarding.py`) — pure functions turning `answers` into files.
3. **Templates** (`backend/templates/`) — base skill (with a marker) + patterns base.

## Wizard steps & fields

1. **Company & product** — `orgName`, `productName`, `description` (free text),
   `productType` (cms | webapp | api | ecommerce | other), `urls[]`.
2. **Environments** — `mode` (static | script). static → `staticUrls[]`; script →
   `buildCmd`, `deployCmd`, `readinessUrlPattern`. `testAuth` { `required`, `loginUrl`,
   `username`, `password`, `notes` }.
3. **Issue tracker** — `type` (jira | linear | azure | github); provider creds
   (jira → `baseUrl`, `projects[]`, `email`, `token`; linear/azure/github → their
   equivalents); `access` { `read`, `write` }.
4. **VCS** — `type` (github | bitbucket | azure), `org`, `repos[]`, `token`;
   `access` { `read`, `write` }.
5. **Publish targets** — `jiraComment`, `prComment` (bools); `slackWebhook`;
   `confluence` { `baseUrl`, `spaceKey`, `parentPage`, `token` }.
6. **Product QA knowledge** (drives skill personalization) — `criticalFlows[]`,
   `saveSemantics` (text), `publishSemantics` (text), `keyPages[]` ({name, route}),
   `riskAreas[]`, `alwaysCheck[]`.
7. **Knowledge source** — `provider` (notion | confluence | none), `link`/`baseUrl`,
   `token` (read access, to gather product context/knowledge), `access` { `read`, `write` }.
8. **Anthropic API key** — `anthropicKey` (runner; stored in secrets).
9. **Review & Generate** — summary, then submit.

**Access scoping:** every external connection (issue tracker, VCS, knowledge source)
carries an `access` { `read`, `write` } pair, shown as two checkboxes in the form. The
backend persists these so adapters honor them later (e.g. `write:false` issue tracker →
never post comments/transition; knowledge `read:true` → fetch pages for product context).

## Answer schema (POST /api/onboarding body)

```
{ company, environments, issueTracker, vcs, publish, productQA, knowledge, anthropicKey }
```
(shape per the fields above; `knowledge` = { provider, link, token, access{read,write} })

## Outputs

- **`instance.config.json`** (non-secret, gitignored; ships `instance.config.example.json`):
  orgName, productName, productType, urls, environments (mode + urls/cmds + auth.required/
  loginUrl/notes — **no password**), issueTracker (type, baseUrl, projects, email, **access**),
  vcs (type, org, repos, **access**), publish (flags + non-secret bits), knowledge (provider,
  link, **access** — **no token**), createdAt. **Secrets appear as `${secret:KEY}` references**
  (e.g. `token: "${secret:JIRA_TOKEN}"`) so the adapter registry resolves them from the secret
  store — never raw values.
- **`.secrets.env`** (gitignored): `JIRA_TOKEN`, `GITHUB_TOKEN`/`BITBUCKET_TOKEN`/`AZURE_DEVOPS_PAT`,
  `SLACK_WEBHOOK`, `CONFLUENCE_TOKEN`, `NOTION_TOKEN`, `ANTHROPIC_API_KEY`, `TEST_LOGIN_PASSWORD`.
- **`<skill_dir>/qa-evidence.md`** — base skill with a generated **“## Product Context
  (generated)”** block injected at the `<!-- PRODUCT_CONTEXT -->` marker: product name/
  description, env URLs, login steps, critical flows, save/publish semantics, key pages,
  always-check items, and a **Knowledge sources** subsection (Notion/Confluence link) the
  engine can read to gather product context. Default `skill_dir = ~/.claude/skills/qa-evidence/`.
- **`<skill_dir>/patterns.yml`** — one pattern row per `riskArea` (id `PAT-<n>`, name,
  why, trigger defaulting to keyword-match on the risk text, injected TC hint) +
  `baseline_always_on` from `alwaysCheck`.

## Generator functions (`backend/onboarding.py`)

- `build_instance_config(answers) -> (config: dict, secrets: dict)` — splits non-secret
  config from secrets.
- `render_skill(answers, base_skill: str) -> str` — replaces `<!-- PRODUCT_CONTEXT -->`
  with the generated block (or prepends a Product Context section if marker absent);
  preserves all base content.
- `build_patterns(answers) -> dict` — risk areas → pattern rows; alwaysCheck → baseline.
- `write_outputs(config, secrets, skill_text, patterns, paths) -> dict` — writes files;
  **idempotent** (re-run overwrites). Returns written paths.
- The endpoint is a thin wrapper that validates and calls these.

## Backend wiring

- **`backend/instance_config.py`** — `load_instance_config(path=None) -> dict|None` reads
  `instance.config.json` (path from env `SCRIBE_CONFIG`, else `./instance.config.json`,
  else `~/.scribe/instance.config.json`).
- **`backend/config.py`** — at import, if an instance config exists, override the relevant
  defaults (`PROJECTS`, `ENVIRONMENTS`, `JIRA_BASE_URL`, `JIRA_EMAIL`, `REPO_LIST`) from it;
  otherwise keep current defaults. Backwards compatible.
- **`backend/server.py`** — `GET /api/onboarding/status` → `{configured, productName?}`;
  `POST /api/onboarding` → validate → generate → `{ok, paths, summary}` (400 on missing
  required fields: productName, at least one env URL or script cmds, issue tracker type).

## Frontend wiring

- `frontend/src/onboardingSchema.ts` — TS types + empty-answers factory.
- `frontend/src/components/Onboarding/OnboardingWizard.tsx` + one component per step.
- `frontend/src/api.ts` — `getOnboardingStatus()`, `submitOnboarding(answers)`.
- `frontend/src/App.tsx` — on load, fetch status; if `!configured`, render the wizard;
  else the dashboard. Add a “Re-run setup” affordance (TopBar) to reopen the wizard.

## Testing (TDD; backend-first, matching repo style)

`backend/tests/test_onboarding.py`:
- `build_instance_config` splits secrets out; non-secret config has no tokens/password.
- `render_skill` injects product context at the marker; output contains productName +
  a critical flow; base content retained.
- `build_patterns` makes one rule per risk area; baseline from alwaysCheck.
- `write_outputs` writes all files to tmp paths; re-run overwrites (idempotent).
- endpoint validation: missing productName → 400.

Frontend: minimal (repo has light FE tests) — a render smoke test of the wizard is enough.

## Decisions

- Deterministic skill-gen for v1 (no AI).
- Secrets isolated in gitignored `.secrets.env`; `instance.config.json` gitignored with a
  committed `.example`.
- Skill installs to `~/.claude/skills/qa-evidence/` so it works with today’s runtime.

## Out of scope (v1)

- AI “enhance” pass (clean follow-up).
- Postgres / multi-user auth (later roadmap phases).
- Live token validation / “test connection” buttons.
- Editing config post-onboarding beyond re-running the wizard.
