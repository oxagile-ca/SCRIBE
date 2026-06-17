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

    config = {
        "orgName": company.get("orgName"),
        "productName": company.get("productName"),
        "productType": company.get("productType"),
        "description": company.get("description"),
        "urls": company.get("urls", []),
        "environments": env,
        "issueTracker": issue,
        "vcs": vcs,
        "publish": publish,
        "knowledge": knowledge,
    }
    return config, secrets


PRODUCT_CONTEXT_MARKER = "<!-- PRODUCT_CONTEXT -->"


def render_skill(answers: dict, base_skill: str) -> str:
    """Inject a generated 'Product Context' block into the base /qa-evidence skill.

    Replaces PRODUCT_CONTEXT_MARKER if present; otherwise prepends the block. All base
    content is preserved.
    """
    block = _product_context_block(answers)
    if PRODUCT_CONTEXT_MARKER in base_skill:
        return base_skill.replace(PRODUCT_CONTEXT_MARKER, block)
    return block + "\n\n" + base_skill


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
        "skill": os.path.join(skill_dir, "qa-evidence.md"),
        "patterns": os.path.join(skill_dir, "patterns.yml"),
    }

    with open(paths["config"], "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)

    with open(paths["secrets"], "w", encoding="utf-8") as fh:
        for key, value in secrets.items():
            fh.write(f"{key}={value}\n")

    with open(paths["skill"], "w", encoding="utf-8") as fh:
        fh.write(skill_text)

    with open(paths["patterns"], "w", encoding="utf-8") as fh:
        yaml.safe_dump(patterns, fh, sort_keys=False)

    return paths


DEFAULT_BASE_SKILL = os.path.join(
    os.path.dirname(__file__), "templates", "qa-evidence.skill.base.md"
)
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
    skill_dir: str,
    base_skill_path: str = DEFAULT_BASE_SKILL,
) -> dict:
    """End-to-end: build config+secrets, render the product skill, seed patterns, write
    everything, and return {paths, summary}. Callers should validate_answers() first.
    """
    config, secrets = build_instance_config(answers)
    skill_text = render_skill(answers, _read_base_skill(base_skill_path))
    patterns = build_patterns(answers)
    paths = write_outputs(
        config, secrets, skill_text, patterns,
        config_dir=config_dir, skill_dir=skill_dir,
    )
    summary = {
        "productName": config.get("productName"),
        "issueTracker": (config.get("issueTracker") or {}).get("type"),
        "vcs": (config.get("vcs") or {}).get("type"),
        "envMode": (config.get("environments") or {}).get("mode"),
        "patternRules": len(patterns["rules"]),
        "secretsCount": len(secrets),
    }
    return {"paths": paths, "summary": summary}
