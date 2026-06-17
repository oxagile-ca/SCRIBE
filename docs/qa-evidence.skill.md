# /qa-evidence — Unified QA System

## What this skill does

End-to-end QA system with three integrated components:

1. **QA Dashboard** (`~/qa-dashboard.html`) — local HTML dashboard showing
   all Jira tickets, priority, status, dev info (repos/branches from PRs),
   evidence status, daily huddle & weekly 3x3 generators.
2. **QA Pipeline** (`~/qa-pipeline.py`) — automated build→deploy→test script
   that chains `deploycli build`, deploy, env polling, and qa-evidence.
3. **QA Evidence** (this skill) — the test execution pipeline that navigates
   the app, verifies ACs, captures screenshots, and publishes reports.

## System files

- `~/qa-dashboard.html` — QA dashboard (single HTML, pre-loaded Jira data)
- `~/qa-pipeline.py` — automated pipeline script
- `~/.claude/skills/qa-evidence.md` — this skill
- `~/evidence/` — evidence packages per ticket
- `~/docs/superpowers/specs/2026-04-27-qa-dashboard-design.md` — dashboard spec

## Plugin context

This skill ships as part of the `qa-evidence` plugin. Scripts are invoked by
name (`qa-markup`, `qa-score`, `qa-evidence-capture`) and resolved by the
plugin runtime to the correct paths.

Plugin configuration keys (read via the plugin config API, with defaults):

- `evidence_root` (default: `evidence`)
- `confluence_space_key` (default: `QA`)
- `min_confidence_gate` (default: `60`)
- `auto_publish_threshold` (default: `75`)
- `gif_level` (default: `p0`)
- `slack_channel` (default: `#qa-reports`)

## Invocation
/qa-evidence <JIRA-KEY>
run:<dev-local|qa-feature|qa-main|baseline-stable|ad-hoc>
env:<env-url | auto>
[--env-name <deploy-env>]
[pr:<pr-url>]
[compare:<prior-run-id>]
[markup-hint:"<annotation guidance>"]
[skip-publish]
[--headless]
[--auto-approve]

### Dashboard commands
/qa-evidence dashboard refresh    — re-fetch Jira data and rebuild dashboard
/qa-evidence dashboard open       — open ~/qa-dashboard.html in browser
/qa-evidence huddle               — generate daily huddle notes
/qa-evidence 3x3                  — generate weekly 3x3 report

### Pipeline commands
/qa-evidence pipeline <JIRA-KEY> [--env <deploy-env>]
  — full pipeline: build → deploy → wait → test → report
/qa-evidence pipeline --all-ready [--env <deploy-env>]
  — batch: process all Ready for QA tickets

## Run kinds

| Kind | Who | When | Env |
|---|---|---|---|
| `dev-local` | Dev | Before pushing PR | localhost |
| `baseline-stable` | Anyone | Any time | k8s-stable (pre-feature) |
| `qa-feature` | QA | After PR deployed | feature QA env |
| `qa-main` | QA | After PR merges | k8s-stable (post-feature) |
| `ad-hoc` | Anyone | Release validation, PM smoke | any |

## Phase -1 — Build & Deploy (only when `env:auto`)

Skip this phase entirely if `env:` is a URL. Only run when `env:auto`.

1. Fetch dev info from Jira dev-status API:
   `GET /rest/dev-status/1.0/issue/detail?issueId={id}&applicationType=bitbucket&dataType=pullrequest`
   Extract all repos and branches from linked PRs.

2. Classify repos: **deployable services** (service-cms, service-a, service-b,
   service-assets, service-assets-b, service-rel-mgr,
   service-config-mgr, service-user-mgmt) vs **libraries**
   (lib-framework, lib-rules). Build all, deploy only services.

3. Build snapshots for each repo:
   `deploycli build -r acme/<repo> -b <branch>`
   Note: snapshot names are UPPERCASE (branch `feature/PROJ-355` →
   snapshot `FEATURE-PROJ-355`).

4. Wait for builds to complete. Poll Jenkins API every 30s, timeout 25 min.
   If Jenkins URL not reachable, wait fixed 20 min.

5. Deploy each service to the Deploy env (`--env-name` or default
   `qa-env-364`):
   `deploycli deploy <env>/<service> --snapshot <BRANCH-UPPERCASE> -y`

6. Poll the env URL every 60s until HTTP 200 (timeout 25 min).
   Construct URL from pattern: `https://<service>-<env>-qa.qa.example.com`

7. Mark the env as `busy` in `~/qa-env-pool.json`. Set `env:` to the
   live URL and proceed to Phase 0.

### Environment pool

When `env:auto`, the pipeline picks an env from a pool rather than
hardcoding one. Pool config in `~/qa-env-pool.json`:

```json
{
  "envs": [
    {"name": "qa-env-1", "status": "free", "ticket": null},
    {"name": "qa-env-2", "status": "free", "ticket": null},
    {"name": "qa-env-3", "status": "free", "ticket": null}
  ]
}
```

**Allocation logic:**
1. Read pool file. Pick first env with `status: "free"`.
2. Set `status: "busy"`, `ticket: "<JIRA-KEY>"`, `since: "<timestamp>"`.
3. Deploy to that env.
4. After Phase 10 cleanup, set env back to `status: "free"`.

**If all envs are busy:**
- Check `since` timestamps. If any env has been busy > 2 hours, force-free
  it (stale lock from a crashed run).
- If still all busy, wait 5 min and retry (up to 3 times).
- If still blocked, STOP and ask user which env to use.

**Parallel testing:** With 3 envs, you can run 3 tickets simultaneously
via `--all-ready`, each on its own env.

**Timings**: Build ~16-20 min, Deploy ~7-25 min. Total Phase -1: ~25-45 min.

## Phase 0 — Validate & pull

1. Parse ticket, run kind, env, optional flags. If required fields missing, STOP.
2. Check reachability of `env:`. If non-200 and looks like a Deploy env,
   run `deploycli wake --env <extracted-from-url>` and retry.
3. `git fetch origin`. If `test-evidence/<JIRA-KEY>` exists remotely:
   `git worktree add .claude/worktrees/<JIRA-KEY> test-evidence/<JIRA-KEY>`.
   Else: `git worktree add -b test-evidence/<JIRA-KEY> .claude/worktrees/<JIRA-KEY>`.
4. Chdir into the worktree. All subsequent file writes happen there.

## Phase 1 — Build the manifest (first run only)

Skip if `<evidence_root>/<JIRA-KEY>/manifest.yml` exists.

1. Fetch Jira ticket via MCP. Capture summary, description, assignee, ALL
   comments, linked issues.

2. **PR analysis is MANDATORY before generating test cases.** Acquire a diff
   via, in order:
   - `pr:` URL passed explicitly, OR
   - The first OPEN PR from the Phase -1 Jira dev-info lookup whose
     destination is `main`/`master`/`develop`/`release-*` (skip e2e or
     stacked PRs), OR
   - Bitbucket search by ticket key:
     `GET /repositories/<workspace>/<repo>/pullrequests?q=source.branch.name~"<TICKET>"`.

   If no diff is reachable, STOP. Print:
   `Cannot build manifest without PR context — supply pr: <url> or fix the
   Jira dev info link for <TICKET>.`
   Never invent test cases blind. A test plan with no diff anchor is noise.

3. **Map every non-trivial change to a TC.** Walk the diff. For each
   file with behavior changes (exclude: pure refactor with identical
   AST, dep-version bumps, doc-only, test-only):
   - Identify the user-facing surface — component name, page route,
     API endpoint, schema field.
   - Generate ≥1 TC that exercises that surface end-to-end.
   - In the TC's `notes`, record `file:lines-touched` so reviewers can
     trace each test back to the diff hunk.
   A TC that doesn't cite a diff hunk is invalid — kick it back.

4. Extract ACs into an array. Each gets ID `AC-N`, captures `text` and
   `source` (description / comment-N / derived). If no structured AC section,
   derive from description bullets and flag for review.

5. For each AC and each diff-derived requirement, propose 1+ test cases:
   - `id`: `TC-<last-4-of-ticket>-<seq>` (zero-padded, e.g. `TC-0364-001`)
   - `title`, `type`, `priority`
   - `evidence_required`: MUST include `screenshot` for any TC with a
     user-visible surface (so Phase 6 markup runs unconditionally).
     Pull from `[screenshot, video, network, console, manual-note,
     accessibility-scan, markup]`.
   - `spec`: path for automated, or `TBD`
   - `steps[]`: required for manual
   - `tags[]`: include `@<JIRA-KEY>`
   - `notes`: cite the diff hunk(s) this TC covers
   - `annotations_hint`: optional, but recommended for every screenshot
     TC since markup runs on all of them

6. **Append the Universal Validation Suite (Phase 2.6 spec)** to the manifest
   with TC ids `TC-UV-1` … `TC-UV-6`. These run on every non-baseline run
   regardless of PR scope. Do NOT skip them when the diff looks small —
   "basic validations regardless of code change" is the floor.

7. Write to `<evidence_root>/<JIRA-KEY>/manifest.yml` per
   `@qa-evidence/manifest-schema`.

8. **PAUSE — tester approval.** Show the manifest, ask "Approved to proceed?"
   Wait for `approved` / `yes`. This is the only built-in pause.
   - If `--auto-approve` is set, skip this pause and proceed immediately.

## Phase 2 — Execute

1. Generate run ID: `run-<kind>-<user-slug>-<seq>` (increment `seq` if prior
   runs of same kind+user exist).
2. Create `<evidence_root>/<JIRA-KEY>/runs/<run-id>/` with subfolders
   `automated/`, `manual/`, `markup/`, `diffs/`.
3. Append a `runs[]` entry to the manifest with the run metadata (kind, env,
   executor, started, etc.).
4. Export env vars the reporter needs:
   - `QA_EVIDENCE_RUN_ID=<run-id>`
   - `QA_EVIDENCE_TICKET=<JIRA-KEY>`
   - `QA_EVIDENCE_ROOT=<evidence_root>`
5. Per run kind:
   - `dev-local`, `qa-feature`, `qa-main`, `ad-hoc`: run
     `pnpm playwright test --project=evidence --grep @<JIRA-KEY>`.
   - `baseline-stable`: skip automated test execution. For each TC with
     `markup` in `evidence_required`, navigate to the relevant URL on the
     stable env (read-only) and capture `baseline.png` into
     `automated/<TC-ID>/`. No interactions, no assertions.
6. The reporter files artifacts automatically as tests complete.
7. On failure, retry up to 3x per TC: read trace + error + relevant source,
   apply test-only fixes (never product code), re-run the single spec. After
   3 attempts still failing, mark `status: fail` and continue.
   - If `--headless` is set: do NOT pause for human input on failures.
     Mark `status: fail`, capture error screenshot, and continue to next TC.

## Phase 2.5 — Document Lifecycle Gates (HARD REQUIREMENT)

Any TC that touches a CMS document (creates one, edits a field, toggles a
flag, attaches a relation, etc.) MUST exercise the full save → reload →
publish → reload → preview loop. A test that only verifies the in-memory
edit before saving is NOT acceptable evidence — it does not prove the
change survives the trip to the persisted store.

Persistence is checked TWICE: once after Save (the draft round-trip) and
once after Publish (the live round-trip). Both stores can lose data
independently; both must be proven.

For every document edited in a run, the test must capture evidence for ALL
five gates below. Missing any gate fails Phase 8 (gap gate), regardless of
the headline score.

### Gate D1 — Save persists (action)
- Click Save. Wait for the success indicator (toast, "saved" badge,
  status field flipping to `saved`). No silent saves — the assertion
  must be on an observable post-save signal.
- Evidence: screenshot AND network-tab capture of the save request + 2xx
  response, stored under `automated/<TC>/save-success.{png,har}`.
- If the save call returns non-2xx, the TC is `fail` — do not proceed.

### Gate D2 — Value persistence after Save (draft round-trip)
- Hard-reload (`page.reload({waitUntil: 'networkidle'})`, NOT a soft
  client-side route change). Re-read every field that was changed in
  this TC and assert each value matches the value submitted before save.
- This catches drafts that "look saved" client-side but never reached
  the server — the most common silent-loss class.
- Evidence: `automated/<TC>/before-save.png`,
  `automated/<TC>/after-save-reload.png`, and
  `automated/<TC>/save-persistence.json`:
  ```json
  [{"field": "headline", "expected": "...", "actual": "...", "match": true}, ...]
  ```
- Any `match: false` → TC is `fail`. Do not proceed to publish on a
  document whose draft can't round-trip.

### Gate D3 — Publish persists (action)
- Trigger publish and wait for the published state. Capture the
  published timestamp / version where exposed.
- Evidence: screenshot AND network capture of the publish endpoint
  returning 2xx, under `automated/<TC>/publish-success.{png,har}`.
- If the document type has no publish step, log this explicitly in the
  TC notes ("no publish step for type X") AND in
  `automated/<TC>/publish-skipped.txt`. Do not silently skip the gate.

### Gate D4 — Value persistence after Publish (live round-trip)
- Hard-reload again. Re-read every field that was changed. Assert each
  value matches the value submitted before publish.
- Publishing can transform values (e.g., URL slug regeneration, image
  ID re-pointing). If a value legitimately differs, the TC must encode
  the expected-after-publish value, not just the pre-publish value.
- Evidence: `automated/<TC>/after-publish-reload.png` and
  `automated/<TC>/publish-persistence.json` (same schema as D2).
- Any `match: false` → TC is `fail`.

### Gate D5 — Preview renders
- Open the document's published preview (preview URL, "View on site"
  button, or staging URL — whichever the doc type supports). Verify the
  page loads (HTTP 200) AND the changed values appear in the rendered
  output. If the doc type has multiple preview surfaces (web, AMP,
  AppleNews, app), exercise at least the primary web preview; note the
  others as `not-verified` rather than skipping silently.
- Evidence: screenshot of the rendered preview under
  `automated/<TC>/preview.png` AND the preview URL captured in
  `automated/<TC>/preview-url.txt` AND the HTTP status code in
  `automated/<TC>/preview-status.txt`.
- A non-200 preview, or preview missing the changed values, fails the TC.

### When a TC is exempt from these gates
Only the following kinds of tests skip D1–D5:
- Read-only TCs (smoke / accessibility audits / link checks that never
  edit a document). These still run the Universal Validation Suite
  (Phase 2.6).
- `baseline-stable` runs (no edits permitted by definition).
- Tests of CMS infrastructure that don't involve documents (auth flows,
  service-user-mgmt screens, settings panels).

If you exempt a TC, write the reason in `manual/<TC>/exemption.md` with
the doc-type or flow it covers. The gap gate (Phase 8) reads this file
and accepts the exemption — without the file, the gate fails.

## Phase 2.6 — Universal Validation Suite (HARD REQUIREMENT)

These TCs run on every `qa-feature`, `qa-main`, `dev-local`, and `ad-hoc`
run, regardless of what the PR changed. Diffs that look small often miss
regressions in shared infrastructure (asset pipeline, auth, persistence
layer). The Universal Suite is the floor — basic validations regardless
of code change.

Added to the manifest automatically with prefix `TC-UV-` during Phase 1
step 6. Each is non-skippable; an exemption requires
`manual/<TC>/exemption.md` with the reason.

### UV-1 — Console error scan
- Throughout the run, listen for `console.error` and uncaught `pageerror`
  events on every page visited.
- Evidence: `automated/TC-UV-1/console.log` (all messages) and
  `automated/TC-UV-1/console-errors.json` (just the failures).
- Fail the TC if any non-allowlisted error fires. Allowlist file:
  `<evidence_root>/<TICKET>/console-allowlist.txt`, one regex per line.

### UV-2 — Network error scan
- Record HAR for the full session.
- Any 4xx/5xx response on a request the page authored (vs third-party
  beacons that are in the noise allowlist) → fail.
- Evidence: `automated/TC-UV-2/network.har` AND
  `automated/TC-UV-2/non-2xx.json`:
  ```json
  [{"url": "...", "status": 500, "method": "POST", "phase": "save"}, ...]
  ```

### UV-3 — Broken image / asset scan
- After every significant navigation, walk the DOM: collect every
  `<img src>`, `<source srcset>`, and CSS `background-image` URL.
- HEAD-fetch each. Assert status 200 AND `content-length > 0` (or body
  non-empty if HEAD isn't supported).
- Evidence: `automated/TC-UV-3/asset-report.json`:
  ```json
  [{"url": "...", "status": 404, "page": "/edit/123", "context": "<img src>"}]
  ```
- Any non-200 → fail.

### UV-4 — Document lifecycle smoke
- For the primary document type the PR affects (or, for non-document PRs,
  pick a typical document on the env):
  - Open it, make a no-op edit (set one field to its current value to
    mark dirty), then run D1 → D5 from Phase 2.5.
- This is the floor. Even a "just a label change" PR proves the document
  round-trip still survives the deploy.
- Evidence: full D1–D5 artifact set under `automated/TC-UV-4/`.

### UV-5 — Accessibility scan
- Run `@axe-core/playwright` on each unique page touched during the run.
- Evidence: `automated/TC-UV-5/axe-<page-slug>.json` per page.
- Fail on `serious` or `critical` violations. If a `baseline-stable`
  axe report exists for the same pages, only fail on NEW violations
  introduced by this run (diff against baseline).

### UV-6 — Snapshot drift check (visual regression)
- If a `baseline-stable` run exists for this ticket, capture the same
  screens at the start of the qa-feature run and diff against baseline
  via `qa-markup diff` (see Phase 5).
- Any `pixel_delta_pct ≥ 0.5%` outside the PR-affected region → flag
  for manual review (status `needs-review`, not auto-fail).
- Evidence: `diffs/UV-6_<page-slug>.png` per page.
- Skip with explicit exemption only if no baseline exists AND
  `--no-baseline-warning` is passed.

## Phase 3 — Convert videos to GIFs

Plugin config `gif_level` controls which tests get GIFs:
- `p0` (default): every P0 TC + every failed TC
- `all`: every TC with a video
- `failed-only`: only failed TCs

```bash
ffmpeg -i video.webm -vf "fps=10,scale=720:-1:flags=lanczos" -loop 0 video.gif -y
```

Target <5MB. Retry at `fps=8,scale=600` if larger.

## Phase 4 — Manual evidence intake

For each `type: manual` (or `hybrid`) TC:

1. Check `manual/<TC-ID>_<slug>/` for required files.
2. If any required evidence missing, prompt tester:
TC-XXX needs: [screenshot, network]
Missing: [network]
Run: pnpm qa-evidence capture <JIRA-KEY> <TC-ID> --har <url>
Or drop network.har into the folder and reply "done".
3. Wait for `done`, re-check.
   - If `--headless` is set: skip waiting. Log missing evidence as
     `status: incomplete` and continue. Do not block the pipeline.
4. Require `notes.md` with env, browser, tester name, verdict, repro notes.
   - If `--headless` is set: auto-generate `notes.md` with available data.

## Phase 5 — Cross-run comparison (if `compare:` set)

Load prior run from manifest `runs[]`. For each TC present in both:

1. **Status diff**: differences go into `diff_report.verdict_changes`.
2. **Visual diff** — if both have screenshots:
qa-markup diff
--before <prior-run>/automated/<TC>/screenshot.png
--after  <this-run>/automated/<TC>/screenshot.png
--output <this-run>/diffs/<TC>vs<prior>.png
   Record `pixel_delta_pct`. If ≥0.5%, add to `diff_report.visual_diffs`.
3. **Network diff** — diff HAR request URLs, status codes, selected response
   fields. Write `<this-run>/diffs/<TC>_network.diff.md`.
4. **Console diff** — errors in current not in prior.

Populate `diff_report` on the current run.

## Phase 6 — Markup (UNCONDITIONAL for every screenshot)

Every TC with at least one screenshot in `automated/<TC>/` gets a markup
pass. No `evidence_required` opt-in, no "only when diff detected" — if
there's an image, it gets annotated. Reviewers shouldn't have to figure
out where to look on a raw screenshot.

For each `automated/<TC>/<image>.png`:
qa-markup annotate
--image <run>/automated/<TC>/<image>.png
--annotations '<JSON>'
--output <run>/markup/<TC>_<image>_annotated.png

If a baseline-stable run exists for any image, also generate before/after:
qa-markup compare
--before <baseline-run>/automated/<TC>/baseline.png
--after  <this-run>/automated/<TC>/screenshot.png
--label-before "k8s-stable (before)"
--label-after "<this env> (after)"
--output <this-run>/markup/<TC>_before-after.png

Annotation JSON sources, in order of preference:
  1. Explicit `annotations_hint` on the TC.
  2. Auto-detected diff regions from Phase 5 (visual diff).
  3. Auto-detected change anchors from the PR diff (Phase 1 step 3):
     point arrows at the screen regions that correspond to the changed
     code (component bounding box, edited field).
  4. Fallback: a default callout box around the page region the TC's
     `notes` reference. Never produce a bare unannotated copy — if you
     genuinely can't annotate, write `markup/<TC>_<image>_clean.note`
     explaining why so the gap gate sees it.

## Phase 7 — Regenerate matrices

Always write:

1. `traceability.md` — one column per run
2. `summary.md` — verdict, counts, failures, top evidence
3. `index.html` — portal linking everything

## Phase 7.5 — Confidence score
qa-score compute <JIRA-KEY>

Writes `confidence:` block to manifest:
- `headline` (0-100)
- `band` (high / pass-with-issues / needs-review / not-ready)
- Sub-scores: `coverage`, `execution`, `corroboration`
- `explanation` (weakest-dimension reason)

**Scoring rules:**
- Start at 100. Only deduct for **real, actionable gaps** — not theoretical ones.
- If all TCs pass with screenshot evidence, baseline is 95.
- Deduct only for: missing AC coverage, untested edge cases explicitly
  called out in the ticket, single data point when the feature spans
  multiple contexts (e.g. one doc type when multiple exist).
- **When score < 100, ALWAYS write a clear explanation** listing each
  deduction with the reason and how to close the gap.
- **If the gap is testable** (e.g., "only tested BIO, should also test
  STRUCTUREDCONTENT"), **run parallel browser instances** to cover
  additional contexts instead of just noting the gap. Use the Agent tool
  to dispatch parallel test runs on different documents/brands/contexts.
- Do NOT deduct for: programmatic verification (DOM reads are valid
  evidence), single brand when the component is shared, theoretical
  edge cases not mentioned in the ticket.

## Phase 8 — Gap gate

Verify:

- [ ] Every AC has ≥1 TC
- [ ] Every TC in current run has non-pending status
- [ ] Every TC's required evidence files exist
- [ ] `confidence.headline` ≥ `min_confidence_gate` (unless `baseline-stable`)
- [ ] No `status: blocked` TCs
- [ ] **PR citation present (Phase 1 step 3):** every non-UV TC has a
      `notes` field referencing at least one diff hunk
      (`file:lines-touched`). Manifests without PR anchors get rejected
      here — you can't verify a feature you can't trace to code.
- [ ] **Document Lifecycle Gates (Phase 2.5):** for every TC that edits a
      document and is not explicitly exempt via `manual/<TC>/exemption.md`,
      all FIVE artifact sets exist:
      - D1 (save action): `automated/<TC>/save-success.png` AND
        `automated/<TC>/save-success.har`
      - D2 (save persistence): `automated/<TC>/before-save.png`,
        `automated/<TC>/after-save-reload.png`,
        `automated/<TC>/save-persistence.json` with all `match: true`
      - D3 (publish action): `automated/<TC>/publish-success.png` AND
        `automated/<TC>/publish-success.har`, OR
        `automated/<TC>/publish-skipped.txt` for doc types without publish
      - D4 (publish persistence): `automated/<TC>/after-publish-reload.png`
        and `automated/<TC>/publish-persistence.json` with all `match: true`
      - D5 (preview): `automated/<TC>/preview.png`,
        `automated/<TC>/preview-url.txt`, and
        `automated/<TC>/preview-status.txt` reading `200`
      Missing any → TC fails the gate. No headline-confidence override.
- [ ] **Universal Validation Suite (Phase 2.6):** TC-UV-1 through
      TC-UV-6 all present in the manifest AND have evidence:
      - TC-UV-1: `automated/TC-UV-1/console.log` + `console-errors.json`
        (empty list OR all entries allowlist-matched)
      - TC-UV-2: `automated/TC-UV-2/network.har` + `non-2xx.json` empty
        (or only third-party beacons explicitly allowlisted)
      - TC-UV-3: `automated/TC-UV-3/asset-report.json` with no non-200
      - TC-UV-4: full D1-D5 artifact set (same shape as Phase 2.5)
      - TC-UV-5: `automated/TC-UV-5/axe-*.json` for every page visited,
        no new `serious`/`critical` violations vs baseline
      - TC-UV-6: `diffs/UV-6_*.png` for each baseline page (or
        explicit `--no-baseline-warning` invocation logged)
      `baseline-stable` runs skip UV-4 only; all other UV TCs run.
- [ ] **Markup coverage (Phase 6):** every screenshot in
      `automated/**/*.png` has a corresponding annotated file in
      `markup/**` OR a `markup/<TC>_<image>_clean.note` explaining the
      skip. No bare unannotated screenshots ship.

Fail → STOP, print numbered remediation list, exit non-zero.

## Phase 9 — Publish

Per run kind (respect `skip-publish` flag if set):

**`dev-local`** — commit + push to `test-evidence/<JIRA-KEY>`. Post Bitbucket
PR comment: "Dev evidence captured, confidence: N/100, branch:
test-evidence/<JIRA-KEY>". Skip Jira + Confluence.

**`baseline-stable`** — commit + push silently.

**`qa-feature`** — commit + push. Zip the ticket folder. Upload to Jira as
attachment. Post Jira comment with verdict, confidence breakdown, traceability
table, top 3 markup images. Create Confluence page under
`<confluence_space_key>/<confluence_parent_page_title>` in DRAFT if confidence
< `auto_publish_threshold`, else PUBLISHED.

**`qa-main`** — commit + push. Append to existing Jira comment (don't
replace). Promote Confluence page to PUBLISHED if currently DRAFT. Post to
`<slack_channel>`: verdict + confidence + link (skip if config empty).

**`ad-hoc`** — commit + push. Print summary to stdout. Notify runner only.

## Phase 9.5 — Confluence-Ready HTML Report

After Phase 9 publish, generate self-contained HTML evidence files for
Confluence with all screenshots embedded as base64 data URIs.

1. Generate `index.html` with full report (verdict, tables, field audit,
   screenshot gallery). All `<img>` `src` attributes must be base64 data URIs
   (`data:image/png;base64,...`), NOT relative file paths.

2. **Size check**: Confluence pages have a ~5 MB content limit.
   - If `index.html` ≤ 5 MB → copy to clipboard as a single file.
   - If `index.html` > 5 MB → split into multiple parts:
     - Split by dividing screenshots evenly across files.
     - Each part must be a valid standalone HTML document with its own
       `<style>` block.
     - Part 1 gets the tables + first half of screenshots.
     - Part 2+ gets remaining screenshots.
     - Target ≤ 3 MB per part for comfortable Confluence handling.
   - Alternative workaround: create a composite PNG (all screenshots in a
     labeled 2-column grid using PIL/Pillow), copy to clipboard for
     direct paste into Confluence, and keep the Confluence page text-only
     (tables + verdict) via the API.

3. **Confluence page creation**: Use the Atlassian MCP
   `createConfluencePage` tool with `contentFormat: markdown` (not `html`)
   to create the text portion (tables, verdict, field audit). Create as
   DRAFT. Then the user can paste the composite screenshot image or
   attach the self-contained HTML.

4. Copy each part to clipboard via `pbcopy` when user requests, one at a
   time. Always confirm which part before copying.

## Phase 9.7 — Timing Analysis

Track and report wall-clock durations for each phase in the final report.
Include a **Timing Analysis** section in `summary.md`, `index.html`, and
the Confluence page.

1. Record timestamps at the start and end of each phase.
2. Compute durations for:
   - Phase 0–1 (setup + manifest): env check, Jira fetch, manifest build,
     tester approval wait
   - Phase 2 (actual testing): browser navigation, field edits, saves,
     reload verification
   - Phase 3–6 (post-processing): GIFs, manual intake, comparison, markup
   - Phase 7–9 (finalization): matrices, scoring, gap gate, publish
   - Phase 9.5 (Confluence HTML): report generation, splitting, clipboard
   - **Total pipeline wall-clock time**
3. Include the timing table in all report outputs:

```
| Phase | Duration |
|-------|----------|
| Setup + Manifest | X min |
| Actual Testing | X min |
| Post-processing | X min |
| Finalization + Publish | X min |
| Confluence HTML | X min |
| **Total Pipeline** | **X min** |
```

4. The "Actual Testing" row is the most important metric — highlight it
   in the verdict banner or summary.

## Phase 9.9 — Update Dashboard

After publishing, update the QA dashboard so evidence status reflects
the completed run without manual intervention.

1. Read `~/qa-dashboard.html` and inject updated localStorage data via
   a script block, or use Playwright to open the dashboard and set values:
   ```js
   localStorage.setItem('qa-dash-evidence-<JIRA-KEY>',
     JSON.stringify({status:'tested', score:'<confidence>', time:'<minutes>'}));
   ```

2. Re-fetch ticket data from Jira (MCP) and dev-status API for all
   PROJ tickets. Re-embed as PRELOADED_DATA in the dashboard HTML.
   This ensures the dashboard shows the latest ticket statuses, not
   stale pre-loaded data.

3. Open the dashboard in the browser:
   `open ~/qa-dashboard.html`

This way, after every `/qa-evidence` run, the tester sees their
dashboard immediately reflect the new evidence status, score, and
timing without touching any dropdown manually.

## Phase 10 — Cleanup

1. `git worktree remove .claude/worktrees/<JIRA-KEY>`
2. Print final summary with all links.

## Autonomous rules

- No approval between phases except Phase 1 (manifest) and Phase 8 (gap remediation).
- `evidence/**` writes proceed without confirmation.
- Git commits + pushes to `test-evidence/**` proceed.
- Merge conflicts → `git pull --rebase`, retry 3x, then ask user.

## Headless mode (`--headless --auto-approve`)

When both flags are set, the entire pipeline runs without human interaction:

- Phase 1: manifest auto-approved (no pause)
- Phase 2: Playwright runs headless (default). On failure after 3 retries,
  mark `status: fail` and continue — never pause for input.
- Phase 4: skip manual evidence wait. Log missing items as `incomplete`.
- Phase 8: if gap gate fails, log remediation list but do NOT exit.
  Mark run as `needs-review` instead of blocking.
- Phase 9.5: generate HTML report but skip clipboard copy prompts.
  Write files directly without asking which part to copy.
- All phases: no `AskUserQuestion` calls. Log decisions to
  `<run>/headless.log` for post-run review.

Headless invocation example:
```
/qa-evidence PROJ-330 run:qa-feature env:https://... --headless --auto-approve
```

## Guardrails

- Never run against production.
- Never publish when gate fails.
- Only modify Confluence pages under the configured space.
- Scrub HAR bodies for PII regex (email, SSN, credit card) before upload.
- Dev-local evidence never publishes externally by default.
- Refuse `qa-main` before any `qa-feature` exists for this ticket.
- Warn but proceed if `compare:<run-id>` is >30 days old.

## Success criteria

1. Manifest has new run entry
2. Traceability has no empty cells for current run
3. Confidence ≥ `min_confidence_gate`
4. Publish targets succeeded
5. Final stdout ends with:
✅ Evidence published for <JIRA-KEY> (run: <kind>)
Verdict: <verdict>   Confidence: <N>/100
Branch: test-evidence/<JIRA-KEY>
Jira: <url>
Confluence: <url>

## Troubleshooting

| Symptom | Fix |
|---|---|
| Phase 1 finds no ACs | Description lacks structure. Draft manifest manually. |
| Reporter skips tests | Tests missing `testInfo.annotations` for `ticket` and `tc-id`. |
| ffmpeg not found | `/qa-evidence doctor` will tell you install command. |
| Confluence 403 | Token missing Confluence write scope. |
| Gap gate blocks on manual TC | Share `pnpm qa-evidence capture` command with tester. |
| Score stuck low | `qa-score show <JIRA-KEY>` — explanation shows weakest dimension. |
| Merge conflicts repeat | Someone else pushed. Skill rebases 3×; beyond that, ask user. |

## Related skills / commands

- `/qa-evidence install` — scaffold a test repo
- `/qa-evidence doctor` — verify install + env vars
- `/qa-test` — author missing specs (optional sub-agent for Phase 2)
