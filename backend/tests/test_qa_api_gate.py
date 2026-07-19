"""Tests for qa_api_gate — heuristic 'is this an API ticket, and which endpoints?'.

Pure classification (no I/O); the diff fallback parses a supplied diff string.
Spec §3.2 / §8.
"""
import qa_api_gate

GROUPS = ["User", "SalesOrder", "Fee", "Booking"]


def test_label_hit_marks_api_by_label():
    g = qa_api_gate.classify("backend", [], "", GROUPS)
    assert g["is_api"] is True
    assert g["source"] == "label"
    # list-form labels also accepted
    assert qa_api_gate.classify(["Frontend", "API"], [], "", GROUPS)["is_api"] is True


def test_api_path_in_ac_marks_api_by_text_and_extracts_endpoint():
    g = qa_api_gate.classify("", ["GET /api/v1/so/invoice returns the invoice"], "", GROUPS)
    assert g["is_api"] is True
    assert g["source"] == "text"
    assert "/api/v1/so/invoice" in g["endpoints"]


def test_verb_plus_path_in_description_marks_api():
    g = qa_api_gate.classify("", [], "The POST /api/v1/so/lineitem endpoint validates SN24.", GROUPS)
    assert g["is_api"] is True
    assert "/api/v1/so/lineitem" in g["endpoints"]


def test_group_name_in_text_marks_api():
    g = qa_api_gate.classify("", ["Return all SalesOrder charges"], "", GROUPS)
    assert g["is_api"] is True
    assert g["source"] == "text"


def test_ui_ticket_is_not_api_and_not_unclear():
    g = qa_api_gate.classify("frontend", ["The filter popover applies on change"],
                             "Standardize the filter UI across pages", GROUPS)
    assert g["is_api"] is False
    assert g["unclear"] is False          # obvious UI -> don't bother with the diff fallback


def test_ambiguous_ticket_is_unclear_for_diff_fallback():
    g = qa_api_gate.classify("", ["The total reconciles correctly"], "Fix rounding", GROUPS)
    assert g["is_api"] is False
    assert g["unclear"] is True           # no API and no UI signal -> orchestrator tries the diff


def test_endpoints_from_diff_extracts_route_paths():
    diff = (
        "diff --git a/src/so/invoice.controller.ts b/src/so/invoice.controller.ts\n"
        "+@Get('/api/v1/so/invoice')\n"
        "+  async getInvoice() {}\n"
        "+router.post('/api/v1/so/lineitem', handler)\n"
    )
    eps = qa_api_gate.endpoints_from_diff(diff)
    assert "/api/v1/so/invoice" in eps
    assert "/api/v1/so/lineitem" in eps


def test_endpoints_from_diff_empty_when_no_paths():
    assert qa_api_gate.endpoints_from_diff("+const x = 1\n-const y = 2\n") == []
