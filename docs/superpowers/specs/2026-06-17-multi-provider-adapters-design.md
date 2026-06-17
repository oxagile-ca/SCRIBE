# Multi-Provider Adapter Abstraction — Design Spec

**Date:** 2026-06-17
**Status:** Draft (design)
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)
**Related:** `2026-06-16-global-qa-pipeline-design.md` (Plugin Interface — the 5 ABCs),
`docs/superpowers/specs/2026-06-17-onboarding-wizard-design.md` (wizard that configures adapters)

## Scope

This spec details three of the plugin-layer abstractions from the global design:

1. **Issue-tracker adapters** — Jira, Linear, Azure DevOps Boards (`IssueAdapter`).
2. **VCS adapters** — GitHub, Bitbucket, Azure DevOps Repos (`VCSAdapter`).
3. **Test-target modes** — DEPLOYED vs STATIC vs LOCAL, and how the runner resolves a URL,
   checks readiness, and short-circuits build/deploy/env-acquire per mode
   (`DeployAdapter` + `EnvAdapter` involvement).

It does **not** redesign the 5 ABCs (taken as given) or the runner internals. It is grounded in
the current reference clients: `backend/jira_client.py` (→ `IssueAdapter`) and
`backend/bitbucket_client.py` (→ `VCSAdapter`), plus the deploy/env/readiness logic in
`backend/agents.py` and `backend/quartermaster.py`.

## Reference-implementation facts (what we are generalizing)

These behaviors exist today and the adapters must preserve them:

- **Jira linked-PR discovery is two-tier.** `jira_client._get_dev_info(issue_id)` calls the
  Atlassian **dev-status** API (`/rest/dev-status/1.0/issue/detail?applicationType=bitbucket&dataType=pullrequest`).
  `server.api_dev_info` resolves the issue **id** first, calls dev-status, and **if it returns
  empty, falls back** to `_bb_dev_info_fallback` → `bb.find_prs_for_ticket(repo, key)` for every
  repo in `REPO_LIST` (branch-name `~` search). This primary-then-fallback shape is the model for
  every provider's `get_pr_list`.
- **Bitbucket branch search** uses `q=source.branch.name~"{ticket_key}"` and returns
  `{id,title,state,source,destination,links}`. `get_file` resolves slashed branch names to a commit
  hash first (a Bitbucket quirk) before fetching `src/{ref}/{path}`.
- **Auth today** is HTTP Basic for both (`base64(email:token)` for Jira, `base64(user:token)` for
  Bitbucket), read from env or `~/.claude/mcp.json`. Under the product this moves to the encrypted
  secret store, decrypted in-memory at adapter instantiation.
- **Deploy/env today** shells out to a deploy CLI (`run_build`, `run_deploy`, `_poll_snapshot`,
  `quartermaster.ensure_env` / `provision_env`), and resolves the test URL from the deployed
  service via `agents._resolve_test_env_url` (`check_snapshot` returns a URL). This is the
  DEPLOYED mode; STATIC and LOCAL do not exist yet and are new.

---

## 0. Common adapter model

### 0.1 Shared DTOs (provider-agnostic)

```python
@dataclass
class Ticket:
    key: str            # provider-native id the user pastes (PROJ-123, ENG-456, 1789)
    title: str
    description: str    # plaintext, provider markup flattened
    status: str
    url: str
    raw: dict           # provider payload, for adapters that need extra fields

@dataclass
class PullRequest:
    repo: str           # bare repo name or full slug, normalized to what VCSAdapter expects
    pr_id: str
    branch: str         # source branch
    dest_branch: str
    status: str         # OPEN | MERGED | DECLINED | UNKNOWN  (normalized, upper)
    url: str
    source: str         # "devstatus" | "branch-search" | "native-link" — provenance for debugging
```

`PullRequest.source` is new and deliberate: every provider has a "clean" link path and a fallback
branch-search path, and operators need to see which one produced a result.

### 0.2 The `type` discriminator + registry

Each adapter slot in instance config carries a `type` string. A single registry maps
`type → adapter class` and instantiates it with its validated config + decrypted secrets. There is
**one registry per ABC** (issue, vcs, deploy, env, publish) so a `type` value is only ever
interpreted within its slot.

```python
ISSUE_ADAPTERS = {
    "jira":          JiraIssueAdapter,
    "linear":        LinearIssueAdapter,
    "azure_boards":  AzureBoardsIssueAdapter,
    "github_issues": GitHubIssuesAdapter,   # from the global design; out of scope here
}
VCS_ADAPTERS = {
    "github":     GitHubVCSAdapter,
    "bitbucket":  BitbucketVCSAdapter,
    "azure_repos": AzureReposVCSAdapter,
}

def build_issue_adapter(cfg: dict, secrets: SecretStore) -> IssueAdapter:
    cls = ISSUE_ADAPTERS[cfg["type"]]        # KeyError → 400 "unknown issue adapter type"
    return cls(IssueConfig.parse(cfg), secrets)
```

Selection is **pure config**: the onboarding wizard writes `issueTracker.type` / `vcs.type` /
`environments.mode`, and the registry resolves classes at startup. No code change to swap providers
(global design success criterion #4). Each adapter validates its own config block and exposes a
`check_auth()` for the wizard's (future) "test connection" button — `bitbucket_client.check_auth`
is the existing template.

---

## 1. Issue-tracker adapters

### 1.1 Capability matrix

| Method | Jira (Cloud) | Linear | Azure DevOps Boards |
|---|---|---|---|
| **Auth** | Basic `email:api_token` (current). OAuth 3LO optional. | API key (`Authorization: <key>`) or OAuth; header on GraphQL endpoint. | PAT via Basic `:{pat}` (blank user). Azure AD optional. |
| **API style** | REST v3 + **dev-status 1.0** | **GraphQL** (`https://api.linear.app/graphql`) | REST 7.x + **PAT**; WIQL for queries |
| **ID the user pastes** | `PROJ-123` | `ENG-456` (team-prefixed identifier) | numeric work-item id `1789` |
| `get_ticket` | `GET /rest/api/3/issue/{key}` → summary/description (ADF flatten, current `_extract_description_text`) | GraphQL `issue(id)` — but id is a UUID; resolve `identifier` "ENG-456" via `issueSearch`/`issues(filter:{number,team})` first | `GET /_apis/wit/workitems/{id}?fields=System.Title,System.Description` (HTML desc → text) |
| `get_pr_list` | **dev-status** `applicationType={bitbucket\|github}&dataType=pullrequest`; fallback = VCS branch search | **No dev-status API.** `attachments` (GitHub/GitLab integration writes PR URLs as attachments) → parse; fallback = VCS branch search on `git-branch-format` | Work-item **relations** of type `ArtifactLink` (`PullRequestId` / `Branch` artifact links); fallback = VCS branch search |
| `post_comment` | `POST /issue/{key}/comment` (ADF body) | GraphQL `commentCreate(input:{issueId, body})` (markdown) | `POST /_apis/wit/workItems/{id}/comments?api-version=7.1-preview` |
| `attach_file` | `POST /issue/{key}/attachments` (`X-Atlassian-Token: no-check`, multipart) | 2-step: `fileUpload` mutation → PUT to signed URL → `attachmentCreate`/comment link | `POST .../attachments?fileName=` (upload) then PATCH work item `AttachedFile` relation |
| `transition` | `GET /issue/{key}/transitions` → match name → `POST .../transitions` | GraphQL `issueUpdate(input:{stateId})`; resolve state id by name within team | `PATCH /workitems/{id}` set `System.State` (must be a legal state for the work-item type) |

### 1.2 Provider specifics & gaps

**Jira (reference impl — least new work).**
`JiraIssueAdapter` is a near-direct lift of `jira_client.py`. Generalize the hardcoded
`JIRA_BASE_URL`, `QA_ASSIGNEE_FIELD`, and the `applicationType="bitbucket"` literal into config —
dev-status takes `bitbucket` **or** `github`, so the value must follow the configured VCS provider.
Keep the two-tier `get_pr_list`: dev-status first, branch-search fallback (today's
`server.api_dev_info` + `_bb_dev_info_fallback`).

**Linear (biggest gap: no first-class dev-info).**
Linear has no equivalent of Jira dev-status. Linked PRs surface two ways, in priority order:
1. **Attachments.** Linear's native GitHub/GitLab integration writes the PR as an *attachment* on
   the issue (the attachment `url` is the PR URL, `metadata` carries source branch / status). This
   is the closest "clean" path — query `issue.attachments` and filter `url` by the VCS host.
2. **Branch-name convention.** Linear's GitHub integration uses a deterministic branch format,
   default `username/eng-456-short-title` (the issue identifier, lowercased, is embedded). So the
   **fallback is the same branch search the Bitbucket fallback already does**, matching on the
   lowercased identifier (`eng-456`) rather than the upper key. Config must let the operator
   override the branch-token pattern because teams customize Linear's `branchNameTemplate`.

   Gap/risk: if neither integration is installed and the team doesn't follow the branch
   convention, `get_pr_list` returns `[]` and the run can only proceed in STATIC/LOCAL mode (no
   diff-driven manifest). Surface this clearly rather than failing the run.

**Azure DevOps Boards.**
Work-item → PR links are **relations** on the work item, not a separate API. `GET workitems/{id}?$expand=relations`
returns relations whose `rel` is `ArtifactLink` and whose `url` is a `vstfs:///Git/PullRequestId/{project}/{repo}/{prId}`
(or `.../Git/Ref/...` for a branch). Parse the artifact URI to get `{repo, prId}`. Fallback = VCS
branch search via Azure Repos. **Shared-PAT note:** the same PAT typically authorizes Boards and
Repos (see §2.3), so the fallback needs no extra credential.

**Universal fallback contract.** Every `get_pr_list` is `primary_link_lookup(key) or branch_search(key)`,
where `branch_search` is delegated to the configured `VCSAdapter.find_prs_for_ticket`. This keeps
PR discovery working even when the issue↔VCS integration is absent — exactly today's Jira→Bitbucket
fallback, generalized. The token searched is provider-specific: Jira/Azure use the raw key; Linear
uses the lowercased identifier.

### 1.3 Config schemas (YAML)

Non-secret fields live in instance config; `*_token` / `*_key` are **references** into the
encrypted secret store (the wizard already splits these into `.secrets.env`).

```yaml
# Jira
issue:
  type: jira
  base_url: https://acme.atlassian.net
  email: qa.engineer@example.com
  api_token: ${secret:JIRA_TOKEN}
  projects: [PROJ, PROJC, PROJB]
  dev_status_application: bitbucket   # bitbucket | github — must match the VCS provider
  qa_assignee_field: customfield_10000   # optional, Jira-only custom field
  stale_days: 3
```

```yaml
# Linear
issue:
  type: linear
  api_key: ${secret:LINEAR_API_KEY}
  teams: [ENG, PLAT]                  # team keys; used to scope identifier lookups
  pr_discovery:
    use_attachments: true             # GitHub/GitLab integration attachments
    branch_token_pattern: "{identifier_lower}"   # fallback token, e.g. eng-456
```

```yaml
# Azure DevOps Boards
issue:
  type: azure_boards
  organization: acme                  # https://dev.azure.com/acme
  project: Platform
  pat: ${secret:AZURE_DEVOPS_PAT}     # shared with azure_repos (see §2.3)
  api_version: "7.1"
```

Registry selection: `ISSUE_ADAPTERS[issue.type]`. Validation rejects unknown `type` (400) and a
missing required field (e.g. Jira without `base_url`) with a field-level message the wizard renders.

---

## 2. VCS adapters

### 2.1 Capability matrix

| Method | GitHub | Bitbucket Cloud (reference impl) | Azure DevOps Repos |
|---|---|---|---|
| **Auth** | PAT/fine-grained token **or** GitHub App installation token (`Authorization: Bearer`) | App password or API token, Basic `user:token` (current) | PAT, Basic `:{pat}` |
| **API style** | REST v3 (`api.github.com`) | REST 2.0 (`api.bitbucket.org/2.0`) | REST 7.x (`dev.azure.com/{org}/{project}/_apis/git`) |
| **repo identity** | `owner/repo` | `repo` under configured `workspace` | `repo` under `{org}/{project}` |
| `get_diff` | `GET /repos/{o}/{r}/pulls/{n}` with `Accept: application/vnd.github.v3.diff` | `GET .../pullrequests/{id}/diff` (follow 302→S3; current) | `GET .../pullRequests/{id}/iterations/{i}/changes` then per-file diffs, or commits-diff API; no single unified-diff endpoint — assemble |
| `post_pr_comment` | `POST /repos/{o}/{r}/issues/{n}/comments` (PR-level) | `POST .../pullrequests/{id}/comments` `{content:{raw}}` (current) | `POST .../pullRequests/{id}/threads` (comment = a thread with one comment) |
| `get_branch` | `GET /repos/{o}/{r}/branches/{branch}` | `GET .../refs/branches/{enc}` (current) | `GET .../refs?filter=heads/{branch}` |
| `find_prs_for_ticket` | `GET /search/issues?q=type:pr+repo:{o}/{r}+head:{key}` or list-PRs + filter `head.ref` contains key | `q=source.branch.name~"{key}"` (current) | `GET .../pullrequests?searchCriteria.sourceRefName=refs/heads/...` (no substring filter → list + client-side filter on branch name) |
| `get_file` | `GET /repos/{o}/{r}/contents/{path}?ref={branch}` (base64) or raw media type | `src/{ref}/{path}`, slashed-branch→commit-hash hop first (current quirk) | `GET .../items?path={path}&versionDescriptor.version={branch}` |

### 2.2 Provider specifics & gaps

- **GitHub** is the cleanest: diff via the diff media type, PR-level comments via the issues
  endpoint, branch search via the search API. Two auth modes — a simple PAT (parity with Bitbucket)
  and **GitHub App** (per-install token, needed for org-wide installs and higher rate limits). The
  App path adds a token-minting step (JWT → installation token) but the adapter surface is identical;
  model it as an auth strategy inside the adapter, selected by config (`auth.mode: token | app`).
- **Bitbucket** is the existing `bitbucket_client.py` lifted to a class. Preserve: the **302→S3 diff
  redirect** (`follow_redirects=True`), the **slashed-branch→commit-hash** resolution in `get_file`,
  and the `~` substring branch search. De-hardcode `BB_WORKSPACE = "acme"` to config.
- **Azure DevOps Repos** has the most impedance mismatch:
  - **No single unified-diff endpoint.** `get_diff` must assemble from the PR iteration `changes`
    list (added/edited/deleted files) plus per-file content diffs, or use the commit-diff API
    between base and head. This is the main implementation cost; cache the assembled diff per PR.
  - **Comments are threads.** A "PR comment" is a thread containing one comment
    (`pullRequests/{id}/threads`). `post_pr_comment` wraps that.
  - **Branch search has no substring operator.** List PRs by `sourceRefName` prefix or list-all and
    filter client-side on the branch name containing the ticket key — same shape as the existing
    Bitbucket fallback but without server-side `~`.

### 2.3 Shared-credential case (Azure PAT spans Boards + Repos)

A single Azure DevOps PAT (scoped `Work Items: R/W` + `Code: R/W`) authorizes **both**
`azure_boards` (IssueAdapter) and `azure_repos` (VCSAdapter). Design:

- The secret is stored **once** as `AZURE_DEVOPS_PAT`; both config blocks reference the same secret
  key (`pat: ${secret:AZURE_DEVOPS_PAT}`).
- The onboarding wizard, when issue=`azure_boards` **and** vcs=`azure_repos`, collects **one** PAT
  and one `organization`, prefills both blocks, and the validation/"test connection" check exercises
  both scopes with that single token.
- Adapters remain independent classes (no shared base required); they just happen to read the same
  secret key. This avoids a special "combined Azure adapter" while giving the operator a one-token
  experience. Document the **minimum scopes** so a too-narrow PAT fails fast at the wizard, not
  mid-run.

### 2.4 Config schemas (YAML)

```yaml
# GitHub (token mode)
vcs:
  type: github
  owner: acme
  repos: [service-cms, service-assets, service-a]
  auth:
    mode: token                       # token | app
    token: ${secret:GITHUB_TOKEN}
    # app mode: app_id, installation_id, private_key: ${secret:GITHUB_APP_KEY}
```

```yaml
# Bitbucket Cloud
vcs:
  type: bitbucket
  workspace: acme
  repos: [service-cms, service-assets, service-a]
  username: ci-bot
  token: ${secret:BITBUCKET_TOKEN}
```

```yaml
# Azure DevOps Repos (shares PAT with azure_boards)
vcs:
  type: azure_repos
  organization: acme
  project: Platform
  repos: [service-cms, service-assets]
  pat: ${secret:AZURE_DEVOPS_PAT}     # SAME secret key as issue.pat
  api_version: "7.1"
```

Registry selection: `VCS_ADAPTERS[vcs.type]`. The `IssueAdapter.get_pr_list` fallback calls
`VCSAdapter.find_prs_for_ticket` on each repo in `vcs.repos` — exactly today's `REPO_LIST` loop in
`_bb_dev_info_fallback`, generalized over the configured VCS adapter.

---

## 3. Test-target modes: DEPLOYED vs STATIC vs LOCAL

The runner needs a URL to point Playwright at and a way to know it's healthy. Today only the
DEPLOYED path exists (build snapshot → deploy → resolve URL from the deployed service). We define
three **target modes**, selected once per instance in onboarding and overridable per run.

### 3.1 Mode definitions

| Mode | DeployAdapter | EnvAdapter | URL source | Use case |
|---|---|---|---|---|
| **DEPLOYED** | yes (build+deploy PR snapshot) | yes (acquire/release from pool) | resolved from the deployed service (`check_snapshot` → URL) | full pipeline parity with today; per-PR isolated env |
| **STATIC** | **no** | optional (fixed URL registry) | a fixed staging URL from config | team already has a shared staging env; zero deploy infra — the fast onboarding path |
| **LOCAL** | **no** | **no** | a developer's local dev server, e.g. `http://localhost:3000` | run against uncommitted/local changes; no CI; fastest inner loop |

The mode is a discriminated `test_target` block (the `environments.mode` the wizard already
collects: `static | script`, extended with `local`).

### 3.2 How the runner resolves the URL and checks readiness

A small `TargetResolver` sits in front of the pipeline and returns `(env_url, teardown_callable)`:

```text
resolve_target(run) -> (url, teardown):
  DEPLOYED:
    env   = EnvAdapter.acquire(ticket_key)          # pool lease (today: env = ticket_key.lower())
    DeployAdapter.build(repo, branch) -> handle     # only if PRs are deployable
    DeployAdapter.deploy(env, service, handle)
    DeployAdapter.poll_ready(env_url, timeout)      # today: _poll_snapshot / _wait_env_settled
    url = resolve_service_url(env, services)         # today: _resolve_test_env_url -> check_snapshot
    teardown = lambda: EnvAdapter.release(env)
  STATIC:
    url = config.test_target.url                     # or pick by service from a small map
    wait_http_ok(url, readiness_path)                # GET, expect 2xx/3xx within timeout
    teardown = noop
  LOCAL:
    url = config.test_target.url (default http://localhost:3000)
    wait_http_ok(url, readiness_path, short_timeout) # fail fast with a clear "is your dev server up?" msg
    teardown = noop
```

**Readiness per mode.** DEPLOYED keeps the existing deep readiness — snapshot-deployed +
service-healthy + `_wait_env_settled` (no in-flight deploys) before the URL is considered live.
STATIC/LOCAL collapse readiness to a single **HTTP health probe**: `GET {url}{readiness_path}` until
2xx/3xx or timeout. LOCAL uses a short timeout and a human error ("dev server not reachable at
localhost:3000 — is it running?") because the cause is almost always a stopped local process.

### 3.3 How LOCAL short-circuits build/deploy/env-acquire

In the global design, Phase -1 (Build & Deploy) runs "only if a DeployAdapter is configured." We make
that conditional explicit and mode-driven:

- **STATIC & LOCAL bind a `NullDeployAdapter` and `NullEnvAdapter`** (no-op `build/deploy/poll_ready`
  / `acquire` returns the static or local `Env`, `release` is a no-op). So Phase -1 and env-acquire
  become no-ops without special-casing the pipeline — the runner still "calls adapters only" (global
  success criterion #4).
- The pipeline checks `run.test_target.requires_deploy`; when false it skips straight to Phase 0
  (preflight) with the configured URL. **LOCAL additionally skips PR-snapshot resolution** — there is
  no build, so `resolve_deployables` / `quartermaster` logic is never entered. (Diff-driven manifest
  generation in Phase 1 still uses the VCS diff if a PR exists; LOCAL works even with no PR, in which
  case the manifest is ticket-text-only.)

This is the cheapest correct design: modes are realized as **adapter bindings**, not `if mode ==`
branches scattered through the runner.

### 3.4 Auth/login into the test env per mode

Login is orthogonal to URL resolution and handled by a `test_auth` block (the wizard already
collects `testAuth {required, loginUrl, username, password, notes}`). The runner performs a
Playwright login pre-step when `test_auth.required`:

| Mode | Typical auth handling |
|---|---|
| DEPLOYED | Per-env seeded test account; credentials from secret store; login via `loginUrl` before Phase 0. Ephemeral envs may share a known seed user. |
| STATIC | Shared staging account from secret store; same Playwright login pre-step. Risk: shared state across runs — note it, don't solve in v1. |
| LOCAL | Often **no auth** (dev server with a logged-in/dev-bypass session) → `test_auth.required: false`. If required, use the developer's local creds from config; never the production secret store. |

Password is always a secret reference (`TEST_LOGIN_PASSWORD`), never in `instance.config.json`
(matches the wizard's secret split). The login step is a deterministic Playwright routine, not an AI
phase.

### 3.5 Config schemas (YAML)

```yaml
# STATIC — the fast onboarding path
test_target:
  mode: static
  url: https://staging.acme.com
  readiness_path: /healthz
  # optional per-service routing if one instance tests several apps:
  service_urls:
    service-cms: https://cms.staging.acme.com
  auth:
    required: true
    login_url: https://staging.acme.com/login
    username: qa@acme.com
    password: ${secret:TEST_LOGIN_PASSWORD}
```

```yaml
# LOCAL — point at the developer's machine
test_target:
  mode: local
  url: http://localhost:3000
  readiness_path: /
  readiness_timeout_sec: 20            # short; fail fast
  auth:
    required: false
```

```yaml
# DEPLOYED — full build/deploy/env pool (parity with today)
test_target:
  mode: deployed
  deploy:
    type: script                       # ScriptDeployAdapter (or acme/custom plugin)
    build_cmd: "deploycli build -r {repo} -b {branch}"
    deploy_cmd: "deploycli deploy {env}/{service} --snapshot {snapshot}"
    readiness_url_pattern: "https://{env}.acme.com"
  env:
    type: pool                         # EnvAdapter; static list or auto-provision plugin
    envs: [qa-env-1, qa-env-2, qa-env-3]
  auth:
    required: true
    login_url: "https://{env}.acme.com/login"
    username: qa@acme.com
    password: ${secret:TEST_LOGIN_PASSWORD}
```

### 3.6 Onboarding-wizard interaction

The wizard's Environments step (step 2) becomes a **3-way choice** (currently `static | script`):

1. **"I have a staging URL"** → `mode: static` (collect URL + readiness path + test_auth). One-screen,
   zero deploy infra — the <15-min onboarding path.
2. **"Test against my local dev server"** → `mode: local` (collect localhost URL; default auth off).
   Great for first-touch/demo without any infra.
3. **"Build & deploy a snapshot per PR"** → `mode: deployed` (collect `build_cmd`, `deploy_cmd`,
   `readiness_url_pattern`, env list; or select the bundled deploy plugin). The advanced path.

The wizard writes `test_target` into instance config; `requires_deploy` is derived
(`mode == deployed`). DEPLOYED/STATIC/LOCAL map to adapter bindings at registry build time, so the
rest of the system needs no awareness of the mode beyond the resolver.

---

## 4. Unified registry & selection (summary)

- Five per-ABC registries keyed by `type` (issue/vcs) or `mode` (test_target → deploy/env binding).
- Onboarding writes discriminators (`issueTracker.type`, `vcs.type`, `environments.mode`); the
  registry resolves classes at startup; **no code change to switch providers**.
- Secrets are references resolved from the encrypted store at instantiation; config is validated
  per-block with field-level errors the wizard can render.
- Every `IssueAdapter.get_pr_list` is `primary_link_lookup(key) or VCSAdapter.find_prs_for_ticket(...)`
  — the existing Jira→Bitbucket fallback, generalized over whichever VCS is configured.
- Shared-credential Azure case: one PAT secret key referenced by both `azure_boards` and
  `azure_repos`, with the wizard collecting it once and validating both scopes.

---

## 5. Gaps, risks, open questions

**Gaps / fallbacks**
- **Linear has no dev-status API.** Linked-PR discovery relies on integration *attachments*, then a
  branch-name convention fallback. If neither is present, `get_pr_list` is empty → no diff-driven
  manifest; the run degrades to ticket-text-only (still valid in STATIC/LOCAL). Must be surfaced,
  not silently empty.
- **Azure Repos has no unified-diff endpoint** and **no substring branch search.** `get_diff` must
  be assembled from iteration changes (extra cost; cache per PR); branch search is list+client-filter.
- **Linear/Azure id shapes differ** from Jira keys (Linear UUID-vs-identifier; Azure numeric). Each
  adapter normalizes "what the user pastes" → native id in `get_ticket`.

**Risks**
- **Shared Azure PAT scope:** a too-narrow PAT (missing `Code` or `Work Items`) fails mid-run. Mitigate
  with a wizard-time scope check across both adapters.
- **STATIC shared-env state bleed:** concurrent runs hit the same staging URL/account; ordering and
  data collisions are possible. Out of scope for v1 beyond a documented warning.
- **LOCAL reachability from the runner:** if the runner is containerized (Docker Compose), `localhost`
  is the container, not the host. LOCAL likely needs `host.docker.internal` (or host networking) —
  call this out in onboarding copy and default the doc/example accordingly.
- **GitHub App auth** adds a token-minting path and key handling distinct from a PAT; more surface to
  test.

**Open questions**
1. Do we support **mixed providers** (e.g. Linear issues + GitHub VCS, or Jira issues + Azure Repos)?
   The design allows it (independent registries), but the dev-status/attachment link path assumes the
   issue provider knows about the VCS host. Confirm we only guarantee the **branch-search fallback**
   across mismatched pairs, and that "clean" linked-PR discovery is best-effort.
2. Is `test_target.mode` **per-instance only**, or **per-run overridable** (e.g. a developer triggers a
   LOCAL run on an otherwise-DEPLOYED instance)? Per-run override is cheap given the resolver design;
   confirm it's wanted.
3. For DEPLOYED, do we keep the bundled deploy/env plugin (today's CLI-based auto-provisioning) as the
   only "real" DeployAdapter at launch, with `ScriptDeployAdapter` as the generic path? (Consistent
   with the global design; just confirming scope here.)
4. Linear branch-token pattern: ship a sensible default (`{identifier_lower}`) but should the wizard
   expose it, given teams customize Linear's `branchNameTemplate`?
5. Azure DevOps **Server (on-prem)** vs **Services (cloud)** base-URL differences — in scope for v1 or
   cloud-only first?
