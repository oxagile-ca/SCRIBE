"""Onboarding generator: turn wizard answers into instance config + secrets,
a product-customized /qa-evidence skill, and a seeded patterns.yml.

Secrets are split out of the non-secret instance config and referenced from it as
``${secret:KEY}`` so the adapter registry can resolve them from the secret store.
"""
import copy
import json
import os
import re

import yaml

# Which secret key holds the token for each provider, so the adapter registry
# (see multi-provider-adapters design) can resolve ${secret:KEY}.
ISSUE_SECRET_KEY = {
    "jira": "JIRA_TOKEN",
    "linear": "LINEAR_TOKEN",
    "azure": "AZURE_DEVOPS_PAT",
    "github": "GITHUB_TOKEN",
}
VCS_SECRET_KEY = {
    "github": "GITHUB_TOKEN",
    "bitbucket": "BITBUCKET_TOKEN",
    "azure": "AZURE_DEVOPS_PAT",
}
KNOWLEDGE_SECRET_KEY = {
    "notion": "NOTION_TOKEN",
    "confluence": "CONFLUENCE_TOKEN",
}


def _ref(secret_key: str) -> str:
    return "${secret:" + secret_key + "}"


def app_slug(product_name: str) -> str:
    """A filesystem/command-safe slug for the product, used to name its dedicated skill
    folder (qa-evidence-<slug>) and its /qa-evidence-<slug> command."""
    slug = re.sub(r"[^a-z0-9]+", "-", (product_name or "").strip().lower()).strip("-")
    return slug or "app"


ISSUE_TYPES = {"jira", "linear", "azure", "github"}
VCS_TYPES = {"github", "bitbucket", "azure"}
ENV_MODES = {"static", "script", "local", "deployed"}


def validate_answers(answers: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: list[str] = []

    company = answers.get("company") or {}
    if not (company.get("productName") or "").strip():
        errors.append("company.productName is required")

    issue_type = (answers.get("issueTracker") or {}).get("type") or ""
    if issue_type not in ISSUE_TYPES:
        errors.append("issue tracker type is required (jira | linear | azure | github)")

    vcs_type = (answers.get("vcs") or {}).get("type") or ""
    if vcs_type not in VCS_TYPES:
        errors.append("vcs type is required (github | bitbucket | azure)")

    env = answers.get("environments") or {}
    mode = env.get("mode") or ""
    if mode not in ENV_MODES:
        errors.append("environment mode is required (static | script | local | deployed)")
    elif mode == "static" and not (env.get("staticUrls") or []):
        errors.append("static environment mode requires at least one staging URL")
    elif mode == "script" and (
        not (env.get("buildCmd") or "").strip() or not (env.get("deployCmd") or "").strip()
    ):
        errors.append("script environment mode requires buildCmd and deployCmd")

    return errors


def build_instance_config(answers: dict) -> tuple[dict, dict]:
    """Return (config, secrets).

    config is the non-secret instance config (safe to write to instance.config.json),
    with every credential replaced by a ``${secret:KEY}`` reference. secrets maps those
    KEYs to the real values (written to the gitignored .secrets.env).
    """
    secrets: dict = {}

    company = answers.get("company", {})
    env = copy.deepcopy(answers.get("environments", {}))
    issue = copy.deepcopy(answers.get("issueTracker", {}))
    vcs = copy.deepcopy(answers.get("vcs", {}))
    publish = copy.deepcopy(answers.get("publish", {}))
    knowledge = copy.deepcopy(answers.get("knowledge", {}))

    def extract(container: dict, field: str, key: str):
        """Move container[field] into secrets[key], leaving a ${secret:KEY} ref."""
        value = container.get(field)
        if value:
            secrets[key] = value
            container[field] = _ref(key)

    # issue tracker
    extract(issue, "token", ISSUE_SECRET_KEY.get(issue.get("type"), "ISSUE_TOKEN"))
    # vcs
    extract(vcs, "token", VCS_SECRET_KEY.get(vcs.get("type"), "VCS_TOKEN"))
    # knowledge source
    extract(knowledge, "token", KNOWLEDGE_SECRET_KEY.get(knowledge.get("provider"), "KNOWLEDGE_TOKEN"))

    # test-env login password
    auth = env.get("testAuth")
    if isinstance(auth, dict):
        extract(auth, "password", "TEST_LOGIN_PASSWORD")

    # publish targets
    extract(publish, "slackWebhook", "SLACK_WEBHOOK")
    conf = publish.get("confluence")
    if isinstance(conf, dict):
        extract(conf, "token", "CONFLUENCE_TOKEN")

    # runner key — secret only, not referenced from config
    if answers.get("anthropicKey"):
        secrets["ANTHROPIC_API_KEY"] = answers["anthropicKey"]

    slug = app_slug(company.get("productName"))
    config = {
        "orgName": company.get("orgName"),
        "productName": company.get("productName"),
        "productType": company.get("productType"),
        "description": company.get("description"),
        "urls": company.get("urls", []),
        "appSlug": slug,
        "skillCommand": f"/qa-evidence-{slug}",
        "environments": env,
        "issueTracker": issue,
        "vcs": vcs,
        "publish": publish,
        "knowledge": knowledge,
        "api": copy.deepcopy(answers.get("api", {})),
    }
    return config, secrets


PRODUCT_CONTEXT_MARKER = "<!-- PRODUCT_CONTEXT -->"
API_SURFACE_MARKER = "<!-- API_SURFACE -->"

# In-page live-API helper, shipped beside each generated skill. __BASE_URL__ is
# replaced with the product's API base URL at generation time. Reads the in-browser
# bearer token (the SPA stores it) and calls the API from the page so the token never
# leaves the browser. See Phase 2.7 in the skill.
LIVE_API_HELPER_TEMPLATE = """// scribe-live-api.js (generated) — in-page live API verification helper.
// Paste into the Claude-in-Chrome javascript_tool on an authenticated app tab, then:
//   await scribeApi('GET','<endpoint>',{query:{...}})   /   {body:{...}} for POST/PUT
// Returns {status, ok, shape, data}. Surface only status/ok/shape — never the token,
// and avoid dumping r.data (may carry PII).
globalThis.scribeApi = async function (method, path, opts = {}) {
  const BASE = '__BASE_URL__';
  // Find a bearer token the SPA stashed (Cognito oidc.user, or a *token* key).
  const pick = (store) => {
    const k = Object.keys(store).find(x => /oidc\\.user|id_token|access_token|auth/i.test(x));
    if (!k) return null;
    const raw = store.getItem(k);
    try { const o = JSON.parse(raw); return o.id_token || o.access_token || o.token || raw; }
    catch (e) { return raw; }
  };
  const tok = pick(sessionStorage) || pick(localStorage);
  if (!tok) return { error: 'no in-browser token (expired? not signed in on this tab)' };
  let url = BASE + (path.startsWith('/') ? path : '/' + path);
  if (opts.query) {
    const qs = new URLSearchParams();
    for (const [a, b] of Object.entries(opts.query)) { Array.isArray(b) ? b.forEach(v => qs.append(a, v)) : qs.append(a, b); }
    url += '?' + qs.toString();
  }
  const init = { method, headers: { Authorization: 'Bearer ' + tok } };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  let resp, data;
  try { resp = await fetch(url, init); } catch (e) { return { error: 'fetch failed: ' + e.message }; }
  const t = await resp.text();
  try { data = JSON.parse(t); } catch (e) { data = t; }
  const sum = (d) => Array.isArray(d)
    ? { type: 'array', length: d.length, itemKeys: (d[0] && typeof d[0] === 'object') ? Object.keys(d[0]) : null }
    : (d && typeof d === 'object') ? { type: 'object', keys: Object.keys(d) } : { type: typeof d };
  return { status: resp.status, ok: resp.ok, shape: sum(data), data };
};
'scribeApi ready';
"""


def _parse_postman_endpoints(path: str) -> dict:
    """Parse a Postman v2.1 collection into {group_name: [(METHOD, /path), ...]}.
    Returns {} on any error (missing file / bad JSON) so generation never fails on it."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            coll = json.load(fh)
    except Exception:
        return {}

    def url_path(req: dict) -> str:
        u = req.get("url")
        raw = u if isinstance(u, str) else ((u or {}).get("raw", "") if isinstance(u, dict) else "")
        raw = (raw or "").split("?")[0]
        return re.sub(r"\{\{[^}]+\}\}", "", raw)  # strip {{BASE_URL}} etc.

    groups: dict = {}

    def walk(items, group):
        for it in items:
            if "item" in it:
                walk(it["item"], it.get("name", group))
            else:
                req = it.get("request", {}) or {}
                p = url_path(req)
                if p:
                    groups.setdefault(group, []).append((req.get("method", "?"), p))

    walk(coll.get("item", []), (coll.get("info", {}) or {}).get("name", "API"))
    return groups


def _api_surface_block(answers: dict) -> str:
    """Generate the '## API Surface (generated)' section from answers['api'].
    Returns '' when no API is configured (so the marker renders empty)."""
    api = answers.get("api") or {}
    base_url = (api.get("baseUrl") or "").strip()
    coll_path = (api.get("postmanCollectionPath") or "").strip()
    if not (base_url or coll_path):
        return ""

    product = (answers.get("company", {}) or {}).get("productName") or "the product"
    lines = ["## API Surface (generated)", ""]
    lines.append(
        f"{product}'s UI is a thin client over its REST API. Many tickets are API-shaped "
        "(a response field, validation rule, math, scope/permission), so QA must verify the "
        "live API contract, not just the rendered UI — see **Phase 2.7 — Live API Verification**."
    )
    if base_url:
        line = f"- **Base URL:** `{base_url}`"
        if api.get("prefix"):
            line += f" · prefix `{api['prefix']}`"
        lines.append(line)
        auth = api.get("authType", "bearer")
        authline = f"- **Auth:** {auth}"
        if api.get("tokenLocation"):
            authline += f" — in-browser token at `{api['tokenLocation']}`"
        lines.append(authline)
    if api.get("scopeParam"):
        lines.append(f"- **Scope:** most calls require/accept `{api['scopeParam']}`; cross-scope access must be denied.")
    ref_bits = []
    if coll_path:
        ref_bits.append(f"raw collection `{coll_path}`")
    if api.get("referencePath"):
        ref_bits.append(f"catalog `{api['referencePath']}`")
    if ref_bits:
        lines.append("- **Full reference:** " + "; ".join(ref_bits) + " (private — keep out of the public repo).")

    groups = _parse_postman_endpoints(coll_path) if coll_path else {}
    if groups:
        total = sum(len(v) for v in groups.values())
        lines.append("")
        lines.append(f"Endpoint catalog — {total} requests / {len(groups)} groups:")
        lines.append("")
        for g, eps in groups.items():
            shown = ", ".join(f"{m} `{p}`" for m, p in eps[:24])
            if len(eps) > 24:
                shown += f", … (+{len(eps) - 24})"
            lines.append(f"- **{g}:** {shown}")
    lines.append("")
    lines.append(
        "When a TC maps to a backend change, cite the matching endpoint(s) in its `notes` "
        "and attach a live API assertion (Phase 2.7)."
    )
    return "\n".join(lines)


def _live_api_helper_js(base_url: str) -> str:
    """Render the per-app scribe-live-api.js with the product's API base URL baked in."""
    return LIVE_API_HELPER_TEMPLATE.replace("__BASE_URL__", base_url or "")


def _yaml_str(s: str) -> str:
    return '"' + (s or "").replace('"', "'").replace("\n", " ").strip() + '"'


def skill_frontmatter(answers: dict) -> str:
    """YAML frontmatter so Claude Code registers the per-app skill + slash command.
    Without this (and the SKILL.md filename) the skill is 'unknown'."""
    company = answers.get("company", {})
    slug = app_slug(company.get("productName"))
    name = f"qa-evidence-{slug}"
    product = (company.get("productName") or "the product").strip()
    desc = _yaml_str(
        f"QA evidence pipeline for {product}: reads the PR diff, generates and runs "
        "tests, captures screenshots + markup, runs smoke/sanity, scores, and writes an "
        "HTML evidence report."
    )
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        "version: 1.0.0\n"
        "triggers:\n"
        f"  - /{name}\n"
        "---\n\n"
    )


def render_skill(answers: dict, base_skill: str) -> str:
    """Render the per-app skill: YAML frontmatter (so Claude Code registers it) + the
    base skill with a generated 'Product Context' block injected at the marker."""
    block = _product_context_block(answers)
    api_block = _api_surface_block(answers)
    if PRODUCT_CONTEXT_MARKER in base_skill:
        body = base_skill.replace(PRODUCT_CONTEXT_MARKER, block)
    else:
        body = block + "\n\n" + base_skill
    # Inject the per-app API Surface at its marker (empty when no API configured).
    if API_SURFACE_MARKER in body:
        body = body.replace(API_SURFACE_MARKER, api_block)
    elif api_block:
        body = body.replace(block, block + "\n\n" + api_block, 1) if block in body else api_block + "\n\n" + body
    return skill_frontmatter(answers) + body


def _product_context_block(answers: dict) -> str:
    company = answers.get("company", {})
    env = answers.get("environments", {})
    qa = answers.get("productQA", {})
    knowledge = answers.get("knowledge", {})

    lines = ["## Product Context (generated)", ""]

    name = company.get("productName", "")
    desc = company.get("description", "")
    lines.append(f"**Product:** {name}{(' — ' + desc) if desc else ''}")
    if company.get("productType"):
        lines.append(f"**Type:** {company['productType']}")
    if company.get("urls"):
        lines.append(f"**Primary URLs:** {', '.join(company['urls'])}")

    if env.get("mode"):
        lines.append(f"**Test target mode:** {env['mode']}")
    if env.get("staticUrls"):
        lines.append(f"**Staging/QA URLs:** {', '.join(env['staticUrls'])}")
    auth = env.get("testAuth") or {}
    if auth.get("required"):
        login = f"**Login:** {auth.get('loginUrl', '')} (user: {auth.get('username', '')})"
        if auth.get("notes"):
            login += f" — {auth['notes']}"
        lines.append(login)

    def bullets(title, items):
        if items:
            lines.append("")
            lines.append(f"**{title}:**")
            lines.extend(f"- {it}" for it in items)

    bullets("Critical flows to exercise", qa.get("criticalFlows"))
    if qa.get("saveSemantics"):
        lines.append("")
        lines.append(f"**Save semantics:** {qa['saveSemantics']}")
    if qa.get("publishSemantics"):
        lines.append(f"**Publish semantics:** {qa['publishSemantics']}")
    pages = qa.get("keyPages") or []
    if pages:
        lines.append("")
        lines.append("**Key pages:**")
        lines.extend(f"- {p.get('name', '')}: `{p.get('route', '')}`" for p in pages)
    bullets("Known risk areas", qa.get("riskAreas"))
    bullets("Always check", qa.get("alwaysCheck"))

    provider = knowledge.get("provider")
    access = knowledge.get("access") or {}
    if provider and provider != "none" and knowledge.get("link") and access.get("read", True):
        lines.append("")
        lines.append(
            f"**Knowledge source ({provider}):** {knowledge['link']} — "
            "read this to gather additional product context before testing."
        )

    return "\n".join(lines)


_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "into", "when", "then", "does", "will"}


def _keywords_from(text: str, limit: int = 6) -> list[str]:
    """Salient lowercased words (len>=4, non-stopword) to use as classifier keyword regexes."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text):
        lw = word.lower()
        if lw in _STOPWORDS or lw in out:
            continue
        out.append(lw)
        if len(out) >= limit:
            break
    return out


def build_patterns(answers: dict) -> dict:
    """Seed a patterns.yml config (qa_patterns schema) from the product's risk areas
    and always-check items. Each risk area becomes a keyword-triggered rule that injects
    a regression TC; each always-check item becomes an always-on baseline line.
    """
    qa = answers.get("productQA", {})
    rules = []
    for i, risk in enumerate(qa.get("riskAreas") or [], start=1):
        rules.append(
            {
                "id": f"PAT-{i:03d}",
                "name": risk,
                "description": f"Known risk area flagged during onboarding: {risk}",
                "triggers": {"keywords": _keywords_from(risk)},
                "inject_tcs": [
                    {
                        "id_suffix": "001",
                        "title": f"Regression check: {risk}",
                        "type": "manual",
                        "priority": "P1",
                        "evidence_required": ["screenshot"],
                        "notes": "Flagged as a known risk area during onboarding.",
                    }
                ],
            }
        )
    return {"rules": rules, "baseline_always_on": list(qa.get("alwaysCheck") or [])}


def write_config_and_secrets(config: dict, secrets: dict, config_dir: str) -> dict:
    """Write instance.config.json + .secrets.env to config_dir; return their paths.
    Shared by write_outputs (full onboarding) and the Config Center edit path."""
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "instance.config.json")
    secrets_path = os.path.join(config_dir, ".secrets.env")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    with open(secrets_path, "w", encoding="utf-8") as fh:
        for key, value in secrets.items():
            fh.write(f"{key}={value}\n")
    return {"config": config_path, "secrets": secrets_path}


def write_outputs(
    config: dict,
    secrets: dict,
    skill_text: str,
    patterns: dict,
    *,
    config_dir: str,
    skill_dir: str,
) -> dict:
    """Write all four artifacts to disk and return their paths. Idempotent — re-running
    with the same dirs overwrites in place.

      config_dir/instance.config.json   non-secret instance config
      config_dir/.secrets.env           KEY=VALUE secrets (gitignored)
      skill_dir/qa-evidence.md          product-customized skill
      skill_dir/patterns.yml            seeded classifier patterns
    """
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(skill_dir, exist_ok=True)

    paths = {
        "config": os.path.join(config_dir, "instance.config.json"),
        "secrets": os.path.join(config_dir, ".secrets.env"),
        "skill": os.path.join(skill_dir, "SKILL.md"),
        "patterns": os.path.join(skill_dir, "patterns.yml"),
    }

    # Ship the in-page live-API helper beside the skill when an API base URL is set
    # (referenced by Phase 2.7). Skipped for products with no documented API.
    api_base = (config.get("api") or {}).get("baseUrl")
    if api_base:
        paths["liveApiHelper"] = os.path.join(skill_dir, "scribe-live-api.js")
        with open(paths["liveApiHelper"], "w", encoding="utf-8") as fh:
            fh.write(_live_api_helper_js(api_base))

    write_config_and_secrets(config, secrets, config_dir)

    with open(paths["skill"], "w", encoding="utf-8") as fh:
        fh.write(skill_text)

    with open(paths["patterns"], "w", encoding="utf-8") as fh:
        yaml.safe_dump(patterns, fh, sort_keys=False)

    return paths


def write_skill_bundle(skill_text: str, patterns: dict, dest_dir: str) -> dict:
    """Write just the skill + patterns to dest_dir (used for the repo-local copy)."""
    os.makedirs(dest_dir, exist_ok=True)
    paths = {
        "skill": os.path.join(dest_dir, "SKILL.md"),
        "patterns": os.path.join(dest_dir, "patterns.yml"),
    }
    with open(paths["skill"], "w", encoding="utf-8") as fh:
        fh.write(skill_text)
    with open(paths["patterns"], "w", encoding="utf-8") as fh:
        yaml.safe_dump(patterns, fh, sort_keys=False)
    return paths


DEFAULT_BASE_SKILL = os.path.join(
    os.path.dirname(__file__), "templates", "qa-evidence.skill.base.md"
)
# Generic template stays in the repo; generated per-app skills go to a dedicated folder.
DEFAULT_SKILLS_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "skills")
DEFAULT_INSTANCES_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instances")
_FALLBACK_BASE_SKILL = "# /qa-evidence\n\n" + PRODUCT_CONTEXT_MARKER + "\n"


def _read_base_skill(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return _FALLBACK_BASE_SKILL


def run_onboarding(
    answers: dict,
    *,
    config_dir: str,
    skills_root: str = DEFAULT_SKILLS_ROOT,
    repo_instances_root: str = DEFAULT_INSTANCES_ROOT,
    base_skill_path: str = DEFAULT_BASE_SKILL,
) -> dict:
    """End-to-end: build config+secrets, render the product skill, seed patterns, and
    write everything. The skill installs to a DEDICATED per-app folder
    (skills_root/qa-evidence-<slug>) so apps never collide, with a versioned copy kept
    in the repo at instances/<slug>/. Callers should validate_answers() first.
    """
    # Sticky API config: if the wizard didn't supply answers["api"], reuse the api
    # block already persisted in this instance's config so a regeneration never loses
    # the generated API Surface section / live-verification helper.
    if not (answers.get("api") or {}):
        try:
            with open(os.path.join(config_dir, "instance.config.json"), encoding="utf-8") as fh:
                prior = json.load(fh)
            if prior.get("api"):
                answers = {**answers, "api": prior["api"]}
        except Exception:
            pass

    config, secrets = build_instance_config(answers)
    slug = config["appSlug"]
    skill_text = render_skill(answers, _read_base_skill(base_skill_path))
    patterns = build_patterns(answers)

    install_dir = os.path.join(skills_root, f"qa-evidence-{slug}")
    repo_dir = os.path.join(repo_instances_root, slug)

    paths = write_outputs(
        config, secrets, skill_text, patterns,
        config_dir=config_dir, skill_dir=install_dir,
    )
    repo_paths = write_skill_bundle(skill_text, patterns, repo_dir)

    summary = {
        "productName": config.get("productName"),
        "appSlug": slug,
        "skillCommand": config.get("skillCommand"),
        "issueTracker": (config.get("issueTracker") or {}).get("type"),
        "vcs": (config.get("vcs") or {}).get("type"),
        "envMode": (config.get("environments") or {}).get("mode"),
        "patternRules": len(patterns["rules"]),
        "secretsCount": len(secrets),
        "skillInstallDir": install_dir,
        "skillRepoDir": repo_dir,
    }
    return {"paths": {**paths, "skillRepo": repo_paths["skill"]}, "summary": summary}
