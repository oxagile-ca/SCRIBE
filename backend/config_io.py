"""Map the on-disk instance config to/from the onboarding form shape, and merge edits.

The Config Center edits config via the same OnboardingAnswers shape the wizard uses.
On the way in we blank secrets (the on-disk config holds only ${secret:} refs anyway);
on the way out we keep blanked secrets (blank = keep) and preserve the onboarded
appSlug/skillCommand so an edit never renames the generated skill.
"""
import copy

from onboarding import (
    build_instance_config, _ref, skill_signature,
    ISSUE_SECRET_KEY, VCS_SECRET_KEY, KNOWLEDGE_SECRET_KEY,
)
from status_map import DEFAULT_STATUS_MAP


def _secret_specs(config: dict) -> list[tuple[str, tuple, str]]:
    """(field_id, path, secret_key) for every secret field, key resolved vs this config."""
    it = config.get("issueTracker") or {}
    vcs = config.get("vcs") or {}
    kn = config.get("knowledge") or {}
    return [
        ("issueTracker.token", ("issueTracker", "token"), ISSUE_SECRET_KEY.get(it.get("type"), "ISSUE_TOKEN")),
        ("vcs.token", ("vcs", "token"), VCS_SECRET_KEY.get(vcs.get("type"), "VCS_TOKEN")),
        ("knowledge.token", ("knowledge", "token"), KNOWLEDGE_SECRET_KEY.get(kn.get("provider"), "KNOWLEDGE_TOKEN")),
        ("environments.testAuth.password", ("environments", "testAuth", "password"), "TEST_LOGIN_PASSWORD"),
        ("publish.slackWebhook", ("publish", "slackWebhook"), "SLACK_WEBHOOK"),
        ("publish.confluence.token", ("publish", "confluence", "token"), "CONFLUENCE_TOKEN"),
    ]


def _dig(d: dict, path: tuple):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _put(d: dict, path: tuple, value) -> None:
    cur = d
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[path[-1]] = value


def _hydrate_product_qa(pqa) -> dict:
    """Full productQA shape with defaults for any missing field, so the edit form always
    has every key present (and an un-onboarded / lean config yields all-empty values)."""
    pqa = pqa or {}
    return {
        "criticalFlows": pqa.get("criticalFlows") or [],
        "saveSemantics": pqa.get("saveSemantics") or "",
        "publishSemantics": pqa.get("publishSemantics") or "",
        "keyPages": pqa.get("keyPages") or [],
        "riskAreas": pqa.get("riskAreas") or [],
        "alwaysCheck": pqa.get("alwaysCheck") or [],
    }


def config_skill_signature(config: dict) -> str:
    """Signature of the skill inputs for this config (see onboarding.skill_signature).
    GET /api/config compares it to config.skillMeta.inputsHash to report skillStale."""
    return skill_signature(config_to_answers(config or {}))


def config_to_answers(config: dict) -> dict:
    """Reshape on-disk config into the OnboardingAnswers form shape, secrets blanked."""
    cfg = copy.deepcopy(config or {})
    answers = {
        "company": {
            "orgName": cfg.get("orgName", ""),
            "productName": cfg.get("productName", ""),
            "description": cfg.get("description", ""),
            "productType": cfg.get("productType", "webapp"),
            "urls": cfg.get("urls") or [],
        },
        "environments": cfg.get("environments") or {},
        "issueTracker": cfg.get("issueTracker") or {},
        "vcs": cfg.get("vcs") or {},
        "publish": cfg.get("publish") or {},
        "knowledge": cfg.get("knowledge") or {},
        "api": cfg.get("api") or {},
        "productQA": _hydrate_product_qa(cfg.get("productQA")),
        "anthropicKey": "",
    }
    # qaTargets is persisted-but-optional and carries no secrets; round-trip it so an
    # edit through the Application Profile (or Settings) never silently drops it.
    if cfg.get("qaTargets"):
        answers["qaTargets"] = cfg["qaTargets"]
    it = answers["issueTracker"]
    if not it.get("statusMapping"):
        # Default to the provider's own status names (status_map is the source of
        # truth) so a Linear instance gets "Ready for testing" — not a generic Jira
        # placeholder that silently empties the QA queue.
        prov = it.get("type") or "jira"
        default_map = DEFAULT_STATUS_MAP.get(prov) or {"ready_for_qa": [], "in_qa": []}
        it["statusMapping"] = copy.deepcopy(default_map)
    for _id, path, _key in _secret_specs(cfg):
        parent = _dig(answers, path[:-1])
        if isinstance(parent, dict) and path[-1] in parent:
            parent[path[-1]] = ""
    return answers


def secrets_set_map(config: dict, secrets: dict) -> dict:
    secrets = secrets or {}
    out = {field_id: bool(secrets.get(key)) for field_id, _p, key in _secret_specs(config or {})}
    out["anthropicKey"] = bool(secrets.get("ANTHROPIC_API_KEY"))
    return out


def merge_and_build(answers: dict, existing_config: dict, existing_secrets: dict) -> tuple[dict, dict]:
    """Build (config, secrets) from edited answers, keeping blanked secrets and identity."""
    existing_config = existing_config or {}
    existing_secrets = existing_secrets or {}
    new_config, new_secrets = build_instance_config(answers)
    # Preserve onboarded identity — never rename the skill on a productName edit.
    if existing_config.get("appSlug"):
        new_config["appSlug"] = existing_config["appSlug"]
    if existing_config.get("skillCommand"):
        new_config["skillCommand"] = existing_config["skillCommand"]
    # Carry the skill build stamp forward. build_instance_config never emits skillMeta, so
    # without this every edit would wipe it and falsely flag the skill stale. Kept as-is so
    # staleness is driven purely by whether the skill inputs changed (GET recomputes and
    # compares); an explicit Rebuild re-stamps it.
    if existing_config.get("skillMeta"):
        new_config["skillMeta"] = existing_config["skillMeta"]
    # New non-blank secrets override; existing values carried forward.
    final_secrets = {**existing_secrets, **new_secrets}
    # Blank secret field -> restore the ${secret:KEY} ref if a value exists.
    for _id, path, key in _secret_specs(new_config):
        val = _dig(new_config, path)
        if (val is None or val == "") and key in final_secrets:
            _put(new_config, path, _ref(key))
    return new_config, final_secrets
