"""Heuristic gate: is this an API ticket, and which endpoints does it touch?

Pure (no I/O): classify() looks at the Linear label + ACs + description + the collection's
group names. endpoints_from_diff() is the last-resort fallback the orchestrator calls only
when classify() is `unclear`. No LLM. See
docs/superpowers/specs/2026-06-29-gated-api-smoke-design.md §3.2.
"""
import re

API_LABELS = {"backend", "back-end", "be", "api", "services", "service", "server"}
UI_LABELS = {"frontend", "front-end", "fe", "ui", "ux", "design", "css", "styling"}
_UI_TEXT = ("popover", "button", "modal", "css", "styling", "layout", "screen",
            "page ", "tooltip", "badge", "icon", "navbar", "breadcrumb", "render")

# /api/v1/... style paths, and route decorators / router calls quoting such a path.
_PATH_RE = re.compile(r"/api/v\d+/[A-Za-z0-9_\-/{}]+")
_VERB_PATH_RE = re.compile(r"\b(?:GET|POST|PUT|DELETE|PATCH)\b\s+(/[A-Za-z0-9_\-/{}]+)", re.I)


def _labels(label):
    if label is None:
        return set()
    items = label if isinstance(label, (list, tuple, set)) else [label]
    return {str(x).strip().lower() for x in items if str(x).strip()}


def _extract_paths(text):
    """Normalized /api/v… paths mentioned in text (deduped, order-preserving)."""
    seen, out = set(), []
    for m in _PATH_RE.findall(text or ""):
        p = m.rstrip("/.,)")
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def classify(label, acs, description, collection_groups):
    """{is_api, endpoints, unclear, source}. source ∈ 'label'|'text'|'none'.

    Order: (1) an API label; (2) API signal in ACs+description — an /api/v… path, a
    verb+path, or a collection group name; (3) neither, but no UI signal → unclear (the
    orchestrator then tries the diff); (4) obvious UI/other → skip, not unclear."""
    labelset = _labels(label)
    text = "\n".join([*(acs or []), description or ""])
    low = text.lower()
    endpoints = _extract_paths(text)

    if labelset & API_LABELS:
        return {"is_api": True, "endpoints": endpoints, "unclear": False, "source": "label"}

    has_verb_path = bool(_VERB_PATH_RE.search(text))
    groups_low = {g.lower() for g in (collection_groups or [])}
    group_hit = any(re.search(rf"\b{re.escape(g)}\b", low) for g in groups_low if g)
    if endpoints or has_verb_path or group_hit:
        return {"is_api": True, "endpoints": endpoints, "unclear": False, "source": "text"}

    ui_signal = bool(labelset & UI_LABELS) or any(w in low for w in _UI_TEXT)
    return {"is_api": False, "endpoints": [], "unclear": not ui_signal, "source": "none"}


def endpoints_from_diff(pr_diff):
    """Best-effort API paths in a PR diff — /api/v… paths plus verb+path route decls.
    Last resort when classify() is unclear. Deduped, order-preserving."""
    seen, out = set(), []
    for p in _extract_paths(pr_diff):
        if p not in seen:
            seen.add(p)
            out.append(p)
    for m in _VERB_PATH_RE.findall(pr_diff or ""):
        p = m.rstrip("/.,)'\"")
        if p.startswith("/api/") and p not in seen:
            seen.add(p)
            out.append(p)
    return out
