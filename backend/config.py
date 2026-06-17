import os

JIRA_BASE_URL = "https://acme.atlassian.net"
JIRA_CLOUD_ID = "00000000-0000-0000-0000-000000000000"
DEFAULT_PROJECT = "PROJ"
PROJECTS = ["PROJ", "PROJC", "PROJB"]
STALE_DAYS = 3
TEAM = [
    {"name": "QA Lead", "email": "qa.lead@example.com"},
    {"name": "QA Engineer", "email": "qa.engineer@example.com"},
    {"name": "QA Analyst", "email": None},
]
QA_ASSIGNEE_FIELD = "customfield_10000"
EVIDENCE_DIR = os.path.expanduser("~/evidence")
STREAMS_DIR = os.path.expanduser("~/qa-dashboard/streams")
STREAMS_RETENTION_DAYS = 7
PIPELINE_DB = os.path.expanduser("~/qa-dashboard/pipeline-state.db")
PIPELINE_RETENTION_DAYS = 30
POLL_INTERVAL = 60
def _envs_from_env_var() -> list[str]:
    """Deploy envs are per-user (each teammate has their own proj-<name>-* set).
    Read from QA_DASH_ENVS as a comma-separated list; fall back to maintainer's set
    so the maintainer's local install behaves identically without setup."""
    raw = os.environ.get("QA_DASH_ENVS", "").strip()
    if raw:
        return [e.strip() for e in raw.split(",") if e.strip()]
    return ["qa-env", "qa-env-1", "qa-env-2", "qa-env-3"]


ENVIRONMENTS = _envs_from_env_var()
DEFAULT_ENV = os.environ.get("QA_DASH_DEFAULT_ENV") or ENVIRONMENTS[0]
REPO_LIST = [
    "service-a", "service-a-config", "service-cms-core", "service-cms", "service-cms-config",
    "service-assets", "service-base-config", "service-config-mgr",
    "service-assets-b", "service-b", "service-c", "lib-framework",
    "service-rel-mgr", "deploy-cli-repo", "service-cms-plugin-b", "service-user-mgmt",
]
REPO_MAP = {
    "service-cms": "service-cms", "corecms": "service-cms", "core cms": "service-cms",
    "service-assets-b": "service-assets-b",
    "service-assets": "service-assets", "assets": "service-assets",
    "service-rel-mgr": "service-rel-mgr",
    "service-a": "service-a", "service-b": "service-b", "lib-framework": "lib-framework",
    "service-config-mgr": "service-config-mgr",
    "service-base-config": "service-base-config",
    "service-a-config": "service-a-config", "service-cms-config": "service-cms-config",
    "service-cms-core": "service-cms-core", "service-cms-plugin-b": "service-cms-plugin-b",
    "deploy-cli-repo": "deploy-cli-repo", "ww": "service-cms", "service-c": "service-c",
    "service-user-mgmt": "service-user-mgmt",
}
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "qa.engineer@example.com")
JIRA_TOKEN = os.environ.get("JIRA_TOKEN", "")

# Default reference each service uses on stable. Most are k8s-stable.
DEFAULT_REFERENCE = "k8s-stable"
SERVICE_REFERENCE_MAP = {
    "service-assets-b": "projd-stable",
}


def reference_for(service: str) -> str:
    """Return the stable-reference name a given service should reset to."""
    return SERVICE_REFERENCE_MAP.get(service, DEFAULT_REFERENCE)


# CMS plugins are deployed as their own Deploy services but they don't expose
# a meaningful UI of their own — they get loaded into service-cms at runtime via
# /api/plugin/module/<plugin-name>. So when a ticket only changes a plugin we
# still deploy that plugin's snapshot, but the QA target (env_url) must point at
# service-cms on the same env, not at the plugin host. Otherwise tests hit the
# plugin's own host and exercise nothing of the integration.
SERVICE_TEST_HOST_MAP = {
    "service-cms-base-plugin": "service-cms",
    "service-cms-plugin-b": "service-cms",
    "service-cms-plugin-c": "service-cms",
    "service-tools-plugin-d": "service-cms",
    "service-cms-plugin-e": "service-cms",
    "service-cms-plugin-f": "service-cms",
    "service-cms-plugin-g": "service-cms",
}


def qa_target_host_for(service: str) -> str:
    """Return the service whose URL QA should actually exercise for a given
    deployed service. Defaults to the service itself; plugins redirect to the
    host app they plug into."""
    return SERVICE_TEST_HOST_MAP.get(service, service)


# Auto-provision on Ready for QA
AUTO_PROVISION_ENABLED = True
AUTO_PROVISION_POLL_SEC = 60
AUTO_PROVISION_LEASE_HOURS = 48
AUTO_PROVISION_MAX_FAILURES = 2
AUTO_PROVISION_OWNER = os.environ.get("QA_DASH_OWNER", "devops")
# Parent env for new auto-provisions. qa-env-1 has ~44 services and the
# refs/concrete mix we actually exercise — cloning it costs ~$1.20/hr vs
# service-cms-beta-testing's ~$12/hr (which has 66 real services). The trade-off
# is we have to keep qa-env-1's lease alive ourselves (see
# AUTO_PROVISION_PARENT_KEEPALIVE_*). Override with QA_DASH_PARENT_ENV.
AUTO_PROVISION_PARENT_ENV = os.environ.get("QA_DASH_PARENT_ENV", "qa-env-1")
# Keep-alive: the parent env must outlive its own lease so clones can keep
# happening. Renew on backend startup and then once per interval below.
AUTO_PROVISION_PARENT_KEEPALIVE_HOURS = 168
AUTO_PROVISION_PARENT_KEEPALIVE_INTERVAL_SEC = 24 * 60 * 60


# --- Instance config overrides (onboarding-generated) -------------------------
# When an instance.config.json exists (written by the onboarding wizard), use it to
# reconfigure the dashboard to the deployed product instead of these built-in defaults.
# Silently falls back to the defaults above when absent.
try:
    from instance_config import load_instance_config as _load_instance_config
    _instance = _load_instance_config()
except Exception:
    _instance = None

if _instance:
    _it = _instance.get("issueTracker") or {}
    if _it.get("projects"):
        PROJECTS = _it["projects"]
        DEFAULT_PROJECT = PROJECTS[0]
    if _it.get("baseUrl"):
        JIRA_BASE_URL = _it["baseUrl"]
    if _it.get("email"):
        JIRA_EMAIL = _it["email"]
    _env_cfg = _instance.get("environments") or {}
    _static_urls = _env_cfg.get("staticUrls") or []
    if _static_urls:
        ENVIRONMENTS = _static_urls
        DEFAULT_ENV = _static_urls[0]
    _repos = (_instance.get("vcs") or {}).get("repos")
    if _repos:
        REPO_LIST = _repos
