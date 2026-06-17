import re
import json
import os
import base64
from datetime import datetime

import httpx

from config import (
    JIRA_BASE_URL, JIRA_EMAIL, JIRA_TOKEN,
    QA_ASSIGNEE_FIELD, STALE_DAYS, TEAM, REPO_MAP,
)


def _auth():
    email = JIRA_EMAIL
    token = JIRA_TOKEN
    if not token:
        mcp_path = os.path.expanduser("~/.claude/mcp.json")
        if os.path.exists(mcp_path):
            with open(mcp_path) as f:
                data = json.load(f)
            # Check top-level env first (Claude MCP format)
            env = data.get("env", {})
            if "JIRA_API_TOKEN" in env:
                token = env["JIRA_API_TOKEN"]
            if "JIRA_USERNAME" in env:
                email = env["JIRA_USERNAME"]
            # Fallback: check mcpServers
            if not token:
                for server in data.get("mcpServers", {}).values():
                    srv_env = server.get("env", {})
                    if "JIRA_API_TOKEN" in srv_env:
                        token = srv_env["JIRA_API_TOKEN"]
                    if "JIRA_EMAIL" in srv_env:
                        email = srv_env["JIRA_EMAIL"]
    return email, token


def _headers():
    email, token = _auth()
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Accept": "application/json"}


def _shorten_name(name):
    if not name:
        return ""
    parts = name.strip().split()
    if len(parts) < 2:
        return name
    return f"{parts[0][0]}. {parts[-1]}"


def _resolve_repo(summary):
    if not summary:
        return ""
    match = re.match(r"^\[([^\]]+)\]", summary)
    if not match:
        return ""
    tag = match.group(1).strip().lower()
    if tag in REPO_MAP:
        return REPO_MAP[tag]
    for key, repo in REPO_MAP.items():
        if tag in key or key in tag:
            return repo
    return tag.replace(" ", "-")


def _days_since(date_str):
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return (datetime.now(dt.tzinfo) - dt).days
    except Exception:
        return 0


def _extract_description_text(desc):
    if desc is None:
        return ""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        text_parts = []
        for block in desc.get("content", []):
            for inline in block.get("content", []):
                if inline.get("type") == "text":
                    text_parts.append(inline.get("text", ""))
            text_parts.append("\n")
        return "\n".join(text_parts)
    return str(desc)


async def get_tickets(project):
    jql = (
        f"project = {project} "
        f"AND status not in (Done, \"Won't Do\", Backlog) "
        f"ORDER BY updated DESC"
    )
    fields = ["summary", "status", "priority", "assignee", "description", "flagged",
              "statuscategorychangedate", QA_ASSIGNEE_FIELD]
    body = {"jql": jql, "maxResults": 100, "fields": fields}

    async with httpx.AsyncClient(timeout=30) as client:
        headers = _headers()
        headers["Content-Type"] = "application/json"
        resp = await client.post(
            f"{JIRA_BASE_URL}/rest/api/3/search/jql",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    tickets = []
    for issue in data.get("issues", []):
        f = issue["fields"]
        status = f.get("status", {}).get("name", "")
        stale_days = _days_since(f.get("statuscategorychangedate")) if status == "Ready for QA" else 0
        flagged = f.get("flagged")
        is_flagged = flagged == "impediment" or (flagged is not None and flagged != "false" and bool(flagged))

        tickets.append({
            "key": issue["key"],
            "summary": f.get("summary", ""),
            "status": status,
            "priority": f.get("priority", {}).get("name", "Medium"),
            "assignee": _shorten_name(f.get("assignee", {}).get("displayName") if f.get("assignee") else None),
            "qaAssignee": _shorten_name(
                f.get(QA_ASSIGNEE_FIELD, {}).get("displayName")
                if isinstance(f.get(QA_ASSIGNEE_FIELD), dict) else None
            ),
            "description": _extract_description_text(f.get("description")),
            "flagged": is_flagged,
            "staleDays": stale_days,
            "devInfo": [],  # fetched on-demand via /api/dev-info/{key}
            "evidence": {"status": "none", "score": None, "time": "", "reportPath": ""},
        })
    return tickets


async def get_ticket_text(key: str) -> str:
    """Return 'summary\\n\\ndescription' for one ticket. Empty on any failure."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{JIRA_BASE_URL}/rest/api/3/issue/{key}",
                params={"fields": "summary,description"},
                headers=_headers(),
            )
            resp.raise_for_status()
            f = resp.json().get("fields", {})
        summary = f.get("summary", "") or ""
        desc = _extract_description_text(f.get("description"))
        return f"{summary}\n\n{desc}".strip()
    except Exception:
        return ""


async def _get_dev_info(issue_id):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.get(
                f"{JIRA_BASE_URL}/rest/dev-status/1.0/issue/detail",
                params={
                    "issueId": issue_id,
                    "applicationType": "bitbucket",
                    "dataType": "pullrequest",
                },
                headers=_headers(),
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = []
            for detail in data.get("detail", []):
                for pr in detail.get("pullRequests", []):
                    source = pr.get("source", {})
                    destination = pr.get("destination", {}) or {}
                    branch = source.get("branch", "")
                    dest_branch = destination.get("branch", "")
                    # repositoryName is at PR top level (e.g. "acme/service-assets")
                    repo_name = pr.get("repositoryName", "")
                    if repo_name and branch:
                        results.append({
                            "repo": repo_name,
                            "branch": branch,
                            "destBranch": dest_branch,
                            "prStatus": (pr.get("status") or "").upper(),
                            "prId": pr.get("id", ""),
                        })
            return results
    except Exception:
        return []


async def _search_jira(jql, max_results=50):
    """Run a JQL search and return list of {key, summary}."""
    fields = ["summary"]
    body = {"jql": jql, "maxResults": max_results, "fields": fields}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = _headers()
            headers["Content-Type"] = "application/json"
            resp = await client.post(
                f"{JIRA_BASE_URL}/rest/api/3/search/jql",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return [{"key": i["key"], "summary": i["fields"].get("summary", "")} for i in data.get("issues", [])]
    except Exception:
        return []


async def _get_done_today(project):
    """Fetch tickets moved to Done today."""
    return await _search_jira(
        f"project = {project} AND status changed to Done AFTER startOfDay() ORDER BY updated DESC"
    )


async def _get_done_this_week(project):
    """Fetch tickets moved to Done this week."""
    return await _search_jira(
        f"project = {project} AND status changed to Done AFTER -7d ORDER BY updated DESC"
    )


async def get_huddle_data(project, notes=""):
    tickets = await get_tickets(project)
    done_today = await _get_done_today(project)

    groups = {"inQA": [], "readyQA": [], "inProgress": [], "blocked": [], "stale": []}
    for t in tickets:
        status = t["status"]
        if t["flagged"]:
            groups["blocked"].append(t)
        if t["staleDays"] >= STALE_DAYS:
            groups["stale"].append(t)
        if status in ("In QA", "QA"):
            groups["inQA"].append(t)
        elif status == "Ready for QA":
            groups["readyQA"].append(t)
        elif status in ("In Progress", "In Review"):
            groups["inProgress"].append(t)

    date_str = datetime.now().strftime("%b %d, %Y")
    text = f"{project} Daily Huddle — {date_str}\n\n"

    def bullets(items):
        return "\n".join(f"• {i['key']} {i['summary'][:50]}" for i in items)

    def section(title, items, fallback="None"):
        if not items:
            return f"{title}\n{fallback}\n\n"
        return f"{title}\n{bullets(items)}\n\n"

    text += section("Done today", done_today)
    text += section("In QA", groups["inQA"])
    text += section("Ready for QA", groups["readyQA"])
    if groups["blocked"]:
        text += section("Blocked", groups["blocked"])
    if groups["stale"]:
        text += "Stale\n" + "\n".join(
            f"• {s['key']} — {s['staleDays']}d in Ready for QA"
            for s in groups["stale"]
        ) + "\n\n"
    if notes.strip():
        text += f"Notes\n{notes.strip()}\n"
    return text.strip()


async def get_3x3_data(project, notes=""):
    tickets = await get_tickets(project)
    released = await _get_done_this_week(project)
    date_str = datetime.now().strftime("%b %d, %Y")

    in_progress = [t for t in tickets if t["status"] in ("In QA", "QA", "Ready for QA")]
    ready_prod = [t for t in tickets if t["status"] == "Ready for Prod"]
    blocked = [t for t in tickets if t["flagged"]]

    def bullets(items):
        return "\n".join(f"• {i['key']} {i['summary'][:50]}" for i in items)

    def section(title, items):
        if not items:
            return f"*{title}*\nNone\n\n"
        return f"*{title}*\n{bullets(items)}\n\n"

    text = f"*{project} 3x3*\n_Week of {date_str}_\n\n"
    text += section("Released", released)
    text += section("In Progress", in_progress)
    text += section("Ready for Prod", ready_prod)
    if blocked:
        text += section("Blocked", blocked)
    text += f"*Summary*\n{len(released)} released · {len(ready_prod)} queued"
    if blocked:
        text += f" · {len(blocked)} blocked"
    if notes.strip():
        text += f"\n\n*Notes*\n{notes.strip()}"
    return text
