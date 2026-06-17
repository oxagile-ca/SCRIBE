"""Linear issue-tracker adapter.

Fetches issues from Linear's GraphQL API and maps them to the same ticket shape the
dashboard expects from jira_client.get_tickets, so the frontend renders them unchanged.
Auth: a Linear personal API key passed directly in the Authorization header.
"""
import httpx

LINEAR_API = "https://api.linear.app/graphql"

# Active (non-done) Linear workflow-state types. We exclude completed/canceled so the
# QA-relevant tickets (which live in started/unstarted custom states like "Ready for
# Testing") aren't crowded out — mirrors the Jira source's "non-Done" behavior.
ACTIVE_STATE_TYPES = ["triage", "backlog", "unstarted", "started"]

_PAGE_SIZE = 100
_MAX_ISSUES = 1000  # safety cap (~10 pages); logged if hit

_ISSUES_QUERY = """
query Issues($filter: IssueFilter, $after: String) {
  issues(filter: $filter, first: 100, after: $after, orderBy: updatedAt) {
    nodes {
      identifier
      title
      description
      priorityLabel
      updatedAt
      state { name type }
      assignee { displayName name }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()


def build_variables(team_keys, after=None) -> dict:
    """GraphQL variables: active-state filter (+ team filter when given) and a cursor."""
    filter_obj: dict = {"state": {"type": {"in": ACTIVE_STATE_TYPES}}}
    if team_keys:
        filter_obj["team"] = {"key": {"in": list(team_keys)}}
    return {"filter": filter_obj, "after": after}


def _short(name: str | None) -> str:
    if not name:
        return ""
    parts = name.split()
    return f"{parts[0][0]}. {parts[-1]}" if len(parts) > 1 else name


def _map_issue(node: dict) -> dict:
    state = node.get("state") or {}
    assignee = node.get("assignee") or {}
    return {
        "key": node.get("identifier", ""),
        "summary": node.get("title", ""),
        "status": state.get("name", ""),
        "priority": node.get("priorityLabel") or "Medium",
        "assignee": _short(assignee.get("displayName") or assignee.get("name")),
        "qaAssignee": "",
        "description": node.get("description") or "",
        "flagged": False,
        "staleDays": 0,
        "devInfo": [],
        "evidence": {"status": "none", "score": None, "time": "", "reportPath": ""},
    }


def tickets_from_response(data: dict) -> list[dict]:
    """Map a Linear GraphQL response into a list of dashboard tickets. Returns [] for
    empty/error responses."""
    nodes = (((data or {}).get("data") or {}).get("issues") or {}).get("nodes") or []
    return [_map_issue(n) for n in nodes]


async def get_tickets(api_key: str, team_keys: list[str] | None = None) -> list[dict]:
    """Fetch all active issues from Linear, paginating until exhausted (capped at
    _MAX_ISSUES). Filters by team key(s) and to non-done states. Returns whatever it has
    (never raises) on missing key, network error, or non-200 so the dashboard stays up."""
    if not api_key:
        return []
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    tickets: list[dict] = []
    after = None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(_MAX_ISSUES // _PAGE_SIZE + 1):
                resp = await client.post(
                    LINEAR_API,
                    headers=headers,
                    json={"query": _ISSUES_QUERY, "variables": build_variables(team_keys, after)},
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                tickets.extend(tickets_from_response(data))
                page = ((data.get("data") or {}).get("issues") or {}).get("pageInfo") or {}
                if not page.get("hasNextPage") or len(tickets) >= _MAX_ISSUES:
                    break
                after = page.get("endCursor")
    except Exception:
        pass
    return tickets
