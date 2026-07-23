"""Tests for the pure prompt/parse helpers behind 'Generate test cases from ticket'.

The Claude spawn is the integration layer; the prompt building and the parsing of
the model's reply are pure so they can be unit-tested without invoking the CLI.
"""
import test_case_gen as g


# ── build_generation_prompt ──────────────────────────────────────────────────
def test_prompt_includes_ticket_text_and_asks_for_json_array():
    p = g.build_generation_prompt("As a manager, GET /api/orders returns only CA orders")
    assert "GET /api/orders returns only CA orders" in p
    assert "JSON array" in p


def test_prompt_is_safe_on_empty_ticket():
    p = g.build_generation_prompt("")
    assert isinstance(p, str) and "Ticket:" in p


# ── parse_generated_cases ────────────────────────────────────────────────────
def test_parses_a_plain_json_array():
    out = '["Log in as admin", "Filter by tenant US returns nothing"]'
    assert g.parse_generated_cases(out) == ["Log in as admin", "Filter by tenant US returns nothing"]


def test_parses_json_array_inside_a_code_fence_and_prose():
    out = 'Here are the cases:\n```json\n["Case A", "Case B"]\n```\nHope this helps!'
    assert g.parse_generated_cases(out) == ["Case A", "Case B"]


def test_falls_back_to_bullet_lines_when_not_json():
    out = "- Log in as admin\n- [ ] Filter by tenant US\n* Direct GET is denied with 403"
    assert g.parse_generated_cases(out) == [
        "Log in as admin", "Filter by tenant US", "Direct GET is denied with 403",
    ]


def test_falls_back_to_numbered_lines():
    out = "1. First case here\n2. Second case here"
    assert g.parse_generated_cases(out) == ["First case here", "Second case here"]


def test_drops_blank_and_trivial_lines_and_dedupes():
    out = '["  ", "ok", "ok", "a real test case"]'
    assert g.parse_generated_cases(out) == ["ok", "a real test case"]


def test_respects_limit():
    out = json_list = "[" + ",".join(f'"case {i}"' for i in range(50)) + "]"
    assert len(g.parse_generated_cases(out, limit=8)) == 8


def test_empty_or_garbage_is_safe():
    assert g.parse_generated_cases("") == []
    assert g.parse_generated_cases("no cases here, sorry") == []
