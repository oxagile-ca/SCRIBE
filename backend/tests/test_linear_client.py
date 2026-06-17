"""Tests for the Linear issue adapter — maps Linear GraphQL issues to the dashboard's
ticket shape (matching jira_client.get_tickets)."""
from linear_client import tickets_from_response, build_variables, ACTIVE_STATE_TYPES

SAMPLE = {
    "data": {
        "issues": {
            "nodes": [
                {
                    "identifier": "BEE-12",
                    "title": "Add SKU search",
                    "description": "Search inventory by SKU.",
                    "priorityLabel": "High",
                    "updatedAt": "2026-06-10T00:00:00.000Z",
                    "state": {"name": "In QA", "type": "started"},
                    "assignee": {"displayName": "Jane Doe", "name": "jane"},
                },
                {
                    "identifier": "BEE-13",
                    "title": "Fix cart total",
                    "description": None,
                    "priorityLabel": None,
                    "state": {"name": "Todo", "type": "unstarted"},
                    "assignee": None,
                },
            ]
        }
    }
}


def test_tickets_from_response_maps_to_ticket_shape():
    tickets = tickets_from_response(SAMPLE)
    assert len(tickets) == 2

    t = tickets[0]
    assert t["key"] == "BEE-12"
    assert t["summary"] == "Add SKU search"
    assert t["status"] == "In QA"
    assert t["priority"] == "High"
    assert "Doe" in t["assignee"]  # name shortened to "J. Doe"
    assert t["description"] == "Search inventory by SKU."
    # shape parity with the frontend Ticket type
    assert t["flagged"] is False
    assert t["qaAssignee"] == ""
    assert t["devInfo"] == []
    assert t["evidence"]["status"] == "none"


def test_tickets_from_response_tolerates_missing_fields():
    t = tickets_from_response(SAMPLE)[1]
    assert t["key"] == "BEE-13"
    assert t["priority"] == "Medium"  # default when null
    assert t["assignee"] == ""  # no assignee
    assert t["description"] == ""


def test_tickets_from_response_handles_empty_or_garbage():
    assert tickets_from_response({"data": {"issues": {"nodes": []}}}) == []
    assert tickets_from_response({}) == []
    assert tickets_from_response({"errors": [{"message": "bad token"}]}) == []


def test_build_variables_filters_active_states_and_team():
    v = build_variables(["INV"], after="cur123")
    f = v["filter"]
    assert f["team"]["key"]["in"] == ["INV"]
    # only active (non-done) states, so QA-relevant tickets aren't crowded out by Done
    assert f["state"]["type"]["in"] == ACTIVE_STATE_TYPES
    assert "completed" not in ACTIVE_STATE_TYPES and "canceled" not in ACTIVE_STATE_TYPES
    assert v["after"] == "cur123"


def test_build_variables_without_team_omits_team_filter():
    v = build_variables([], after=None)
    assert "team" not in v["filter"]
    assert v["after"] is None
