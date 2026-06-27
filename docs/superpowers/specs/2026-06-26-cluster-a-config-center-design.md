# Cluster A — Config Center (View / Edit / Attach) — Design Spec

**Date:** 2026-06-26
**Status:** Draft (design) — pending user approval
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)
**Branch:** `feat/cluster-a-config-center` (stacked on `feat/cluster-c-automation` @ dd00fbf)
**Target:** web version (desktop wrap is a later, separate step — unaffected)
**Demo:** Beeventory client demo, week of 2026-06-29
**Related:** `2026-06-25-cluster-c-automation-design.md`, `2026-06-17-onboarding-wizard-design.md`

## Scope

The second of three demo clusters. Five of the eleven improvements:

- **#1 — View saved configs without secrets.**
- **#2 — Edit configs after onboarding.**
- **#3 — Attach files (Postman collection): upload + re-parse.**
- **#5 — Manage multiple GitHub automation-repo URLs** (config-management only).
- **#11 — Read/Write permission toggles per integration** (config-management only).

Out of scope (separate clusters): Cluster C (automation — already built), Cluster B (ticket UX). Also explicitly out of scope for A per decisions below: building `github_client` to actually consume the repos, and enforcing access flags beyond Cluster C's existing Linear write-gate.

## Goal

After onboarding, the user can open a **Settings (Config Center)** screen from the dashboard, see the current configuration with secrets masked, edit any persisted field (including the multiple repo URLs and the per-integration read/write toggles), upload a Postman collection that refreshes the API surface, and save — all without re-walking the onboarding wizard and without a backend restart.

## Why this is feasible (reuse, not rebuild)

The onboarding subsystem already does almost everything:

- The wizard models every field as `OnboardingAnswers` (`frontend/src/onboardingSchema.ts:10-69`) with `access:{read,write}` (`AccessChecks`) and `vcs.repos[]` (`ListTextarea`) already present.
- `onboarding.build_instance_config(answers)` (`backend/onboarding.py:79-140`) splits into `(config, secrets)`, replacing each credential with a `${secret:KEY}` ref. The on-disk `instance.config.json` therefore **already contains no real secrets** (refs only) — so "view without secrets" is inherent.
- `onboarding.write_outputs(...)` (`onboarding.py:427-475`) writes `instance.config.json` + `.secrets.env`; `POST /api/onboarding` (`server.py:240-259`) then calls `load_secrets_env()` so new tokens take effect **with no restart**.
- Config is read live per request via `load_instance_config()`, so non-secret edits apply immediately.

Cluster A adds a **merge-aware edit path** (so a blank secret field means "keep") plus a Settings UI that reuses these pieces. No new config store, no new config format.

## Decisions (locked during brainstorming)

1. **UI:** a single-page **Settings screen** (sectioned), opened from a header **⚙ gear** as a full-screen modal (consistent with the app's existing modal pattern — there is no router). It reuses the wizard's field components, pre-fills from current config, and saves in one action.
2. **#5 / #11 depth:** **config-management only.** Edit the repo URL list and the read/write toggles; they persist (Cluster C's Linear gate already honors `write`). Do **not** build `github_client` and do **not** add broad access-flag enforcement in this cluster.
3. **#3 Postman:** a file picker **uploads + stores** the collection, sets `api.postmanCollectionPath`, and **re-parses** it (`_parse_postman_endpoints`) to validate the JSON and report the discovered endpoint count. It does **not** rewrite `SKILL.md` on upload — the API Surface refreshes on the next full skill regeneration. (Rationale: `render_skill` rebuilds the whole skill from `productQA`, which is not persisted to the config, so an on-upload regen would blank the live instance's hand-tuned Product Context. Avoided.)
4. **Secret editing:** fields show **"•••• set" / "not set"** (never the real value). **Blank = keep** the existing secret; a non-blank value **replaces** it in `.secrets.env` (hot-reloaded, no restart).
5. **`productQA` excluded:** it is not persisted to `instance.config.json` (it only seeds `patterns.yml` at onboarding), so it is not round-trippable and the Settings screen omits it.
6. **Invariant preserved:** real secrets never land in `instance.config.json` — only `${secret:KEY}` refs.

## Architecture (units)

### Backend

**Unit 1 — `instance_config.read_secrets_file(path=None) -> dict` (new).**
A pure reader of `.secrets.env` (KEY=VALUE) that returns a dict **without** mutating `os.environ`. (`load_secrets_env` mutates `os.environ`; the merge needs a side-effect-free read.)

**Unit 2 — `config_io.py` (new module) — config↔form mapping + merge.**
- `config_to_answers(config: dict) -> dict` — reshape the on-disk config into the `OnboardingAnswers` form shape (config's top-level `orgName`/`productName`/… → `company.*`; `environments`/`issueTracker`/`vcs`/`publish`/`knowledge`/`api` pass through; secret fields blanked to `""`). Omits `productQA`.
- `secrets_set_map(config: dict, secrets: dict) -> dict` — `{secretKey: bool}` for which secrets currently have a value, so the UI renders masked state. Secret keys derived from the same maps `build_instance_config` uses (`ISSUE_SECRET_KEY`/`VCS_SECRET_KEY`/`KNOWLEDGE_SECRET_KEY` + `TEST_LOGIN_PASSWORD`/`SLACK_WEBHOOK`/`CONFLUENCE_TOKEN`).
- `merge_and_build(answers, existing_config, existing_secrets) -> (config, secrets)` — runs `build_instance_config(answers)`; then:
  - **Secret merge:** for **each known secret field**, if the incoming value was blank (so `build_instance_config` left `""` and produced no secret), **restore** the `${secret:KEY}` ref in config and keep the existing secret value. Non-blank values flow through as new secrets. The known secret-field locations mirror `build_instance_config`'s `extract` calls (issueTracker.token, vcs.token, knowledge.token, environments.testAuth.password, publish.slackWebhook, publish.confluence.token).
  - **Identity preserve:** `build_instance_config` recomputes `appSlug` and `skillCommand` from `productName`. On an EDIT this must NOT silently rename the skill command / orphan the generated skill, so `merge_and_build` **carries over `appSlug` and `skillCommand` from `existing_config`** (the originally onboarded identity) rather than the recomputed values.

**Unit 3 — `onboarding.write_config_and_secrets(config, secrets, config_dir)` (new, factored out).**
Writes only `instance.config.json` + `.secrets.env` (the config-write half of `write_outputs`). `write_outputs` is refactored to call it (DRY). The edit endpoint uses it, then `load_secrets_env()` to hot-reload.

**Unit 4 — Postman upload handler.**
Saves the uploaded `.json` to `default_config_dir()/{appSlug}.postman_collection.json`, validates it parses as JSON (reject otherwise without changing the path), sets `config.api.postmanCollectionPath`, writes config via Unit 3, and re-parses with `_parse_postman_endpoints` to return the discovered endpoint count. It does **not** rewrite `SKILL.md` (see Decision 3).

**Unit 5 — `server.py` endpoints.**
- `GET /api/config` → `{ answers: config_to_answers(cfg), secretsSet: secrets_set_map(...) }` (secret-safe; pre-fills the form).
- `PUT /api/config` (JSON body = edited answers) → `merge_and_build` → `write_config_and_secrets` → `load_secrets_env` → `{ok: true}`. Validation allows a blank secret field when that secret already exists (blank=keep).
- `POST /api/config/upload-postman` (multipart) → Unit 4 → `{ok, endpointCount, path}`.

### Frontend

**Unit 6 — extract shared field components.**
Move `AccessChecks` and `ListTextarea` from `OnboardingWizard.tsx` into `frontend/src/components/Onboarding/fields.tsx` and import them in both the wizard and the Settings screen (small, in-scope refactor).

**Unit 7 — `Settings.tsx` (new).**
Single-page sectioned form (Company, Environments, Issue Tracker, Version Control, Publish, Knowledge, API/Postman), pre-filled from `getConfig()`. Secret inputs render masked using `secretsSet` (placeholder "•••• set" / "not set"; blank submit = keep). Repos via `ListTextarea`; R/W via `AccessChecks`; a Postman file `<input type=file>` calling `uploadPostman`. One **Save** → `updateConfig(answers)`.

**Unit 8 — header entry + api client.**
A **⚙** button in `TopBar.tsx` opens `Settings` as a full-screen modal (state in `App.tsx`, like the other modals). `api.ts`: `getConfig()`, `updateConfig(answers)`, `uploadPostman(file)` (the app's first `FormData`/multipart call).

## Data flow

```
Open Settings → GET /api/config → pre-fill (non-secrets from config; secrets masked via secretsSet)
  → user edits fields / toggles R/W / edits repo list
  → Save → PUT /api/config → merge_and_build (blank secret = keep existing ref+value)
         → write_config_and_secrets → load_secrets_env (hot reload) → ok
Postman: pick .json → POST /api/config/upload-postman → store + set path + write config + re-parse
         → { endpointCount } shown in the form (SKILL.md not rewritten on upload)
```

## Error handling

- **Edit validation:** a blank token/password is valid **iff** the corresponding secret already exists; otherwise it's a missing-credential error. Never write a real secret into `instance.config.json`.
- **Postman upload:** reject non-`.json` / unparseable files with a clear message and **do not** change `api.postmanCollectionPath`.
- **Write failures** surface to the form; partial writes avoided by building the full config in memory before writing.
- **Hot reload:** after writing secrets, `load_secrets_env()` updates `os.environ` so edited tokens take effect without a restart.

## Testing

- **Backend units:** `read_secrets_file` (parses, no `os.environ` mutation); `config_to_answers` (reshape + secrets blanked + productQA omitted); `secrets_set_map`; `merge_and_build` truth cases — (a) blank token keeps existing ref+value, (b) new token replaces, (c) new secret added, (d) config never contains a real secret, (e) editing `productName` preserves the original `appSlug`/`skillCommand`; `write_config_and_secrets` writes both files and config has only `${secret:}` refs; Postman handler (valid → path set + count; invalid → unchanged).
- **Endpoint tests** (FastAPI TestClient): `GET /api/config` shape + masking; `PUT /api/config` round-trip with blank-keep; `POST /api/config/upload-postman` multipart with a tiny valid collection.
- **Frontend:** `npm run build` (tsc) + manual — open Settings, see masked secrets, toggle a repo/flag, upload a small Postman file, Save, confirm persistence.

## Build order

1. Unit 1 (`read_secrets_file`) + Unit 3 (`write_config_and_secrets` refactor) — the write/read primitives.
2. Unit 2 (`config_io.py`: `config_to_answers` / `secrets_set_map` / `merge_and_build`) — the crux, TDD.
3. Unit 5 `GET /api/config` + `PUT /api/config` — **#1 + #2 + #5 + #11 land** (view/edit/repos/toggles all flow through the form).
4. Unit 6 (extract fields) + Unit 7 (`Settings.tsx`) + Unit 8 (gear + api client) — the UI for the above.
5. Unit 4 + `POST /api/config/upload-postman` + the file picker — **#3 lands**.

## Open risks

- **Edit validation reuse.** `validate_answers` (onboarding.py:49-76) is already **token-agnostic** — it only enforces structural rules (productName, issueTracker.type, vcs.type, environments.mode + mode-specific URL/cmd requirements) and never requires any secret. So the edit path can reuse `validate_answers` verbatim; the blank-secret = keep behavior is handled entirely in `merge_and_build`, not in validation. (Resolved — no separate validator needed.)
- **`python-multipart` dependency.** FastAPI `UploadFile`/`File` requires `python-multipart`, which is NOT currently in `requirements.txt`. The Postman-upload task adds it (`python-multipart==0.0.9`) and the `UploadFile, File` imports to `server.py`.
- **Changing an integration's type with a blank token.** If the user switches, e.g., issueTracker.type jira→linear and leaves the token blank, the secret key changes (JIRA_TOKEN→LINEAR_TOKEN) and there is no existing LINEAR_TOKEN to restore, so the token is left empty (no ref) — correct behavior (a new tracker needs a new token), documented for the implementer.
