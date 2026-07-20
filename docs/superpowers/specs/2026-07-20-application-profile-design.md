# Application Profile — Design Spec

**Date:** 2026-07-20
**Status:** Approved (backend section approved interactively; frontend finalized in auto mode)
**Repo:** `~/SCRIBE` (oxagile-ca/SCRIBE) — React/Vite frontend + FastAPI backend

## Problem

Onboarding collects 9 steps of knowledge about the product under test, but after
onboarding the only edit surface is the `Settings.tsx` "Config Center" modal, which
exposes just 6 partial sections ("the basic stuff"). Several things captured during
onboarding are not viewable or editable afterward, and the richest one is not even
persisted:

- **Product QA knowledge** (`productQA`: critical flows, save/publish semantics, key
  pages, risk areas, always-check) — captured at onboarding, used once to generate the
  QA skill, then **discarded** (never written to `instance.config.json`).
- **Advanced QA taxonomy** (`qaTargets`: seed entities, entity-dependent types, classify
  rules) — only settable by hand-editing the JSON; the wizard never captures it.
- Also missing from Settings: environment `mode`/URLs/build/deploy, `publish.*`,
  `issueTracker.statusMapping`, the tracker/VCS/knowledge `type` selectors, and the
  Anthropic key.

## Goal

An **Application Profile** page that shows a clean, readable view of everything VERDIKT
knows about the app, and lets the user add/edit all of it — including the currently
discarded `productQA` and the advanced `qaTargets`. Editing knowledge that feeds the QA
skill is applied to real QA runs via an explicit **Rebuild skill** action.

## Decisions (locked)

1. **Scope:** Full — all config domains **+ `productQA` (now persisted) + `qaTargets`**.
2. **Placement:** New dedicated **Application Profile** full-page view **and** keep the
   Settings modal. Both share the same section components and the same
   `GET/PUT /api/config` API so they cannot structurally drift.
3. **Skill:** Persist on Save. A separate explicit **Rebuild skill** action regenerates
   the skill bundle. A **staleness indicator** flags when knowledge that feeds the skill
   has changed since the last rebuild.
4. **View:** Read-first per section — a readable summary with a per-section "Edit" that
   flips just that section into an inline form (Save/Cancel).

## Approach (chosen: extend the single-config model)

Persist the missing knowledge into the existing `instance.config.json`, reuse the
existing `PUT /api/config` full-answers round-trip for all saves, and add exactly one
new endpoint for skill rebuild. Rejected alternatives: a separate knowledge store with
per-domain PATCH endpoints (diverges from the one-config model, reintroduces drift);
a documentation-only view (fails the chosen scope — productQA/qaTargets stay inert).

## Backend design

### 1. Persist the missing knowledge
- `build_instance_config(answers)` (`backend/onboarding.py`) writes `productQA` and
  `qaTargets` blocks into the config (today it drops `productQA`; `qaTargets` is
  hand-edit-only). `productQA` and `qaTargets` contain no secrets.
- `config_to_answers(config)` (`backend/config_io.py:69-72`) hydrates the real persisted
  `productQA`/`qaTargets` instead of returning empty stubs. This single change makes them
  viewable, editable, **and** available to the skill rebuild.
- `merge_and_build` needs no new logic — it rebuilds config from answers, so once both
  ends round-trip the new blocks, edits persist automatically. Secret handling
  (blank = keep) is unchanged.

### 2. New endpoint `POST /api/skill/rebuild`
- Loads current config → `config_to_answers` (now incl. productQA/qaTargets) →
  `render_skill` + `build_patterns` → writes `SKILL.md` / `patterns.yml` (+ the live-API
  helper when `api.baseUrl` is set) to both `qa-evidence-<slug>/` and the repo
  `instances/<slug>/` copy.
- Implemented as a focused `rebuild_skill(config_dir, skills_root, repo_instances_root)`
  helper in `onboarding.py` next to `run_onboarding` (reusing `render_skill` /
  `build_patterns` / base-skill reader). It **does not** touch `.secrets.env` or rewrite
  tracker/VCS config — a rebuild can never blank a token. (We do **not** call
  `run_onboarding` for rebuild, because it rebuilds config from blanked secrets and would
  lose tokens.)
- Returns `{ ok, skillBuiltAt, patternRules }`.

### 3. Staleness tracking
- Add a `skillMeta` block to config: `{ builtAt: ISO8601, inputsHash: str }`.
- `_skill_input_signature(answers) -> str`: a canonical (stable, sorted) JSON of exactly
  the answer fields `render_skill`/`build_patterns` consume — `productQA`, `qaTargets`,
  `description`, `urls`, `productType`, `api`, `knowledge`, and the skill-relevant
  `environments` fields. Hashed (sha256) to produce `inputsHash`.
- `GET /api/config` computes the current signature hash and returns
  `skillStale: bool` (current hash ≠ stored `skillMeta.inputsHash`) and `skillBuiltAt`.
- `POST /api/skill/rebuild` recomputes and stores `skillMeta.builtAt` + `inputsHash`.
- Same helper on both sides → correct by construction; staleness self-clears if an edit
  is reverted. `run_onboarding` also stamps `skillMeta` so a freshly onboarded app starts
  "up to date".

### Backend API surface (net new / changed)
| Method / Route | Change |
|---|---|
| `GET /api/config` | Add `skillStale`, `skillBuiltAt` to the response. |
| `PUT /api/config` | Unchanged (already round-trips full answers incl. new blocks). |
| `POST /api/skill/rebuild` | **New.** Regenerate skill artifacts + stamp `skillMeta`. |

## Frontend design

No router (state-driven views). Settings stays a modal (`showSettings` in `App.tsx`).

### Entry point & page
- New `showProfile` state in `App.tsx`; a TopBar entry point (button) opens the
  full-page `ApplicationProfile` view (with a Back/Close control). Settings modal is
  untouched.

### Components (new, under `frontend/src/components/Profile/`)
- `ApplicationProfile.tsx` — page container. Loads `getConfig()` once; holds full
  `answers`, `secretsSet`, `skillStale`, `skillBuiltAt`. Renders a header
  (product identity + staleness banner + Rebuild button) then one SectionCard per domain.
- `SectionCard.tsx` — read/edit wrapper: shows a readable summary (children in "view"
  mode); "Edit" flips to an inline form (children in "edit" mode); Save/Cancel. Save calls
  a passed `onSave()` that posts the **entire** answers object via `updateConfig` and
  refetches; Cancel reverts the section's local edits.
- `sections/*` — one component per domain rendering both view and edit modes, built on the
  existing `Field` / `AccessChecks` / `ListTextarea` primitives from `Onboarding/fields`:
  Company & product, Environments, Issue tracker (incl. `type` + `statusMapping`),
  Version control (incl. `type`), Publish targets, Knowledge source (incl. `provider`),
  **Product QA knowledge**, API / Postman, **Advanced — qaTargets**, Anthropic key.
- `SecretInput` extracted from `Settings.tsx` into a shared module
  (`Profile/SecretInput.tsx` or `components/SecretInput.tsx`) and imported by both the
  page and the modal (blank = keep semantics preserved).

### Data flow & the full-replace constraint
`PUT /api/config` is a full replace of the answers object. The page holds the complete
`answers` in state; editing a section mutates a copy; Save posts the **whole** object.
Omitting a section would blank it — so SectionCard.onSave always submits the full answers.
This mirrors how `Settings.tsx` already works (`updateConfig(a)`).

### Staleness UI
Header shows "Skill up to date ✓ (built <time>)" or a warning banner
"Knowledge changed — Rebuild skill to apply" with the **Rebuild skill** button
(`POST /api/skill/rebuild`, then refetch). Disabled/among a spinner while rebuilding.

### qaTargets editor
`qaTargets` is structured (seedEntities, entityDependentTypes, classifyRules). Render it
under an "Advanced" section: readable summary in view mode; edit mode offers structured
list editors where practical and a validated raw-JSON textarea fallback for the nested
`classifyRules` (parse on Save; surface parse errors inline). Empty `qaTargets` falls back
to the generic ruleset (existing `qa_targets.py` behavior) — the page shows "using
defaults" when unset.

## Error handling
- `PUT` 400 (`{ ok:false, errors:[] }`) → surface inline in the editing section; stay in
  edit mode.
- Secret fields use `SecretInput` (blank keeps existing).
- Rebuild failure → banner/toast with the error; staleness stays flagged.
- `GET /api/config` 404 (not onboarded) → the OnboardingGate already handles this; the
  Profile entry point is only shown when configured.

## Testing
- **Backend (pytest):**
  - `productQA` + `qaTargets` round-trip: `build_instance_config` → `config_to_answers`
    returns the same values.
  - `merge_and_build` preserves `productQA`/`qaTargets` and still keeps secrets
    (blank = keep) and `appSlug`/`skillCommand`.
  - `_skill_input_signature` stable across dict ordering; changes when a skill-affecting
    field changes; unchanged for a non-skill field (e.g. issueTracker.email).
  - `rebuild_skill` writes `SKILL.md`/`patterns.yml` to install + repo dirs, updates
    `skillMeta`, and leaves `.secrets.env` untouched.
  - `GET /api/config` reports `skillStale=false` right after onboarding, `true` after a
    knowledge edit via `PUT`, `false` again after rebuild.
- **Frontend (esbuild+node, pure helpers):**
  - Full-answers round-trip helper: editing one section and serializing yields an object
    with all other sections intact (guards the full-replace footgun).
  - Staleness/label helper (e.g. `skillStatusLabel(stale, builtAt)`).
  - Section "completeness" summary helper if added.
- **Live E2E (manual, this session):** onboard NorthStar, open the Profile page, confirm
  all domains render, edit a `productQA` field + Save, confirm `skillStale` flips, click
  Rebuild, confirm it clears and the generated `SKILL.md` reflects the edit.

## Out of scope
- Per-domain PATCH endpoints (full-replace PUT is sufficient).
- Reworking onboarding wizard steps.
- Multi-instance profile switching.
