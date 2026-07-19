"""Cross-tracker status normalization.

Different issue trackers (and different teams) name their workflow statuses
differently — Jira "Ready for QA", Linear "Ready for testing", Azure "Ready for QA".
The dashboard branches on a small set of canonical categories instead of raw names:

  - "ready_for_qa"  — picked up by the QA queue / coverage / auto-provision
  - "in_qa"         — currently being tested
  - "other"         — everything else

Mapping = per-provider defaults, overridable per instance via
config.issueTracker.statusMapping (captured during onboarding).
"""

DEFAULT_STATUS_MAP = {
    "jira": {"ready_for_qa": ["Ready for QA"], "in_qa": ["In QA"]},
    "linear": {
        "ready_for_qa": ["Ready for Testing", "Ready for QA", "QA Ready", "Ready for Test"],
        "in_qa": ["In QA", "In Testing", "Testing", "QA"],
    },
    "azure": {"ready_for_qa": ["Ready for QA"], "in_qa": ["In QA", "Testing"]},
    "github": {"ready_for_qa": [], "in_qa": []},
}

CATEGORIES = ("ready_for_qa", "in_qa")


def categorize_status(native: str, mapping: dict) -> str:
    """Map a native status name to a canonical category, or "other"."""
    if not native:
        return "other"
    n = native.strip().lower()
    for category in CATEGORIES:
        for name in mapping.get(category, []) or []:
            if name.strip().lower() == n:
                return category
    return "other"


def resolve_status_mapping(config: dict | None, provider: str) -> dict:
    """Return the effective {category: [names]} map for a provider: the instance config
    override when present, otherwise the provider default. Missing categories in an
    override fall back to the provider default."""
    default = DEFAULT_STATUS_MAP.get(provider, {"ready_for_qa": [], "in_qa": []})
    cfg_map = ((config or {}).get("issueTracker") or {}).get("statusMapping") or {}
    if not any(cfg_map.get(c) for c in CATEGORIES):
        return default
    return {c: (cfg_map.get(c) or default.get(c, [])) for c in CATEGORIES}
