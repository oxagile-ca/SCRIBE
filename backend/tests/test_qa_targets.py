"""Unit tests for qa_targets — the deterministic Phase-2 target resolver.

All pure logic; no network. The live shell (Cognito mint + booking search +
invoice fetch) is exercised only by the live verification, never here.
"""
import json

import config
import qa_targets


# --- ticket-type classification -------------------------------------------

def test_classify_invoice():
    assert qa_targets.classify_ticket_type(
        "Invoice render — Hotel Room charge shows $0.00, Balance never reconciles"
    ) == "invoice"


def test_classify_folio():
    assert qa_targets.classify_ticket_type("Folio render line items SD5.3.0") == "folio"


def test_classify_payment():
    assert qa_targets.classify_ticket_type("Payment Tab radio rewrite") == "payment"


def test_classify_deposit():
    assert qa_targets.classify_ticket_type("Deposit support on booking payment") == "deposit"


def test_classify_filter():
    assert qa_targets.classify_ticket_type("Filter popover auto-apply standardization") == "filter"


def test_classify_config():
    assert qa_targets.classify_ticket_type("SO Configs settings endpoint") == "config"


def test_classify_other_default():
    assert qa_targets.classify_ticket_type("Refactor internal VP-label helper") == "other"


def test_classify_none_text_is_other():
    assert qa_targets.classify_ticket_type(None) == "other"
    assert qa_targets.classify_ticket_type("") == "other"


def test_inv662_is_display_not_invoice():
    """Regression guard for the scoping bug: INV-662 is a Location-Info display
    ticket. Its OWN text must classify as display (non-booking), never invoice —
    that misclassification (via a Related: link) is exactly what this prevents."""
    t = qa_targets.classify_ticket_type(
        "Location Info Card should show the property email and website"
    )
    assert t == "display"
    assert qa_targets.is_booking_dependent(t) is False


def test_invoice_keyword_wins_over_checkin():
    # "Check In Review Invoice Summary" — the surface is the invoice, still booking-dependent.
    t = qa_targets.classify_ticket_type("Check In Review Invoice Summary panel")
    assert t == "invoice"
    assert qa_targets.is_booking_dependent(t) is True


# --- booking-dependence ----------------------------------------------------

def test_booking_dependent_types():
    for t in ("invoice", "folio", "payment", "deposit", "checkin", "checkout"):
        assert qa_targets.is_booking_dependent(t) is True


def test_non_booking_dependent_types():
    for t in ("display", "filter", "config", "dashboard", "nav", "other"):
        assert qa_targets.is_booking_dependent(t) is False


# --- seed-booking selection ------------------------------------------------

def _booking(num, bid, status="CONFIRMED"):
    return {"booking_number": num, "booking_id": bid, "status": status}


def test_select_picks_confirmed_with_renderable_invoice():
    bookings = [_booking("BK_AAA", 10)]
    invoices = {10: {"total_due": 13685}}
    got = qa_targets.select_seed_booking(bookings, invoices.get, preferred_numbers=[])
    assert got == {"booking_id": 10, "booking_number": "BK_AAA", "invoice_total_due": 13685}


def test_select_prefers_seed_qaseed_numbers():
    bookings = [_booking("BK_OTHER", 1), _booking("BK_X077IUKO", 4)]
    invoices = {1: {"total_due": 100}, 4: {"total_due": 200}}
    got = qa_targets.select_seed_booking(bookings, invoices.get,
                                         preferred_numbers=["BK_X077IUKO"])
    assert got["booking_number"] == "BK_X077IUKO"
    assert got["booking_id"] == 4


def test_select_skips_booking_without_renderable_invoice():
    bookings = [_booking("BK_NOINV", 1), _booking("BK_OK", 2)]
    invoices = {2: {"total_due": 50}}  # booking 1 has no invoice → None
    got = qa_targets.select_seed_booking(bookings, invoices.get, preferred_numbers=[])
    assert got["booking_number"] == "BK_OK"


def test_select_returns_none_when_nothing_renderable():
    bookings = [_booking("BK_A", 1), _booking("BK_B", 2)]
    got = qa_targets.select_seed_booking(bookings, lambda _bid: None, preferred_numbers=[])
    assert got is None


def test_select_ignores_non_confirmed():
    bookings = [_booking("BK_CANCELLED", 1, status="CANCELLED")]
    invoices = {1: {"total_due": 999}}
    got = qa_targets.select_seed_booking(bookings, invoices.get, preferred_numbers=[])
    assert got is None


def test_invoice_total_due_extraction():
    assert qa_targets._invoice_total_due({"total_due": 13685}) == 13685
    assert qa_targets._invoice_total_due({"balance_due": 100}) == 100
    assert qa_targets._invoice_total_due({"total": 7}) == 7
    assert qa_targets._invoice_total_due({}) is None


def test_invoice_total_due_nested_response():
    # Live GET /so/invoice nests the invoice object under "invoice".
    resp = {"invoice": {"so_id": 4, "total_due": 13685, "subtotal": 11900},
            "terms": "...", "customer": {}, "location": {}, "booking": {}}
    assert qa_targets._invoice_total_due(resp) == 13685


def test_seed_booking_numbers_present():
    # The five QA-seed bookings from the plan must be the preferred defaults.
    for n in ("BK_X077IUKO", "BK_9IRRVC4P", "BK_CP8WAXM4", "BK_CRKCYYNI", "BK_J755DICT"):
        assert n in qa_targets.SEED_BOOKING_NUMBERS


# --- Linear ticket fetch (backend token, no OAuth MCP) ---------------------

def test_split_ticket_key():
    assert qa_targets.split_ticket_key("INV-602") == ("INV", 602)
    assert qa_targets.split_ticket_key("abc-12") == ("ABC", 12)
    assert qa_targets.split_ticket_key("nonsense") == (None, None)
    assert qa_targets.split_ticket_key("") == (None, None)
    assert qa_targets.split_ticket_key(None) == (None, None)


def test_parse_linear_issue():
    data = {"data": {"issues": {"nodes": [
        {"identifier": "INV-602", "title": "[SD4.12.0] Apply Payment to Booking",
         "description": "New API to apply a payment...", "state": {"name": "Ready for Testing"}}
    ]}}}
    got = qa_targets.parse_linear_issue(data)
    assert got["summary"] == "[SD4.12.0] Apply Payment to Booking"
    assert got["description"].startswith("New API to apply a payment")
    assert got["state"] == "Ready for Testing"


def test_parse_linear_issue_empty_or_error():
    assert qa_targets.parse_linear_issue({"data": {"issues": {"nodes": []}}}) is None
    assert qa_targets.parse_linear_issue({"errors": [{"message": "deprecated"}]}) is None
    assert qa_targets.parse_linear_issue({}) is None


def test_classify_ticket_prefers_title_over_description():
    # Title says payment; the long description merely name-drops DEPOSIT as an example
    # value — title-first classification must not mis-bucket this as deposit.
    summary = "[SD4.12.0] Apply Payment to Booking"
    description = ("New API to apply a payment ... txn_type maps to values such as "
                   "DEPOSIT and MANUAL_CREDIT ... POST /api/v1/booking/payment")
    assert qa_targets.classify_ticket(summary, description) == "payment"


def test_classify_ticket_falls_back_to_description_when_title_vague():
    assert qa_targets.classify_ticket("SD4.12.0 update", "This reworks the invoice renderer") == "invoice"
    assert qa_targets.classify_ticket("Misc cleanup", None) == "other"


def test_classify_payment_from_real_inv602_description():
    # The real INV-602 scope must classify as payment (booking-dependent), NOT "other".
    text = ("[SD4.12.0] Apply Payment to Booking\n"
            "New API to apply a payment to a booking, executing the payment against "
            "the sales order system. API Call: POST /api/v1/booking/payment")
    t = qa_targets.classify_ticket_type(text)
    assert t == "payment"
    assert qa_targets.is_booking_dependent(t) is True


# --- output assembly + secret hygiene --------------------------------------

_INSTANCE_CFG = {
    "environments": {"testAuth": {
        "loginUrl": "https://xin-np.wbee.ca/",
        "username": "workabee-dev",
        "password": "${secret:TEST_LOGIN_PASSWORD}",
    }},
    "api": {"baseUrl": "https://xin-api-np.wbee.ca", "prefix": "/api/v1"},
}


def test_gather_output_shape():
    out = qa_targets.gather(
        "INV-653", "https://xin-np.wbee.ca/",
        ticket_text="Invoice render bug",
        seed_booking={"booking_id": 4, "booking_number": "BK_X077IUKO", "invoice_total_due": 13685},
        instance_cfg=_INSTANCE_CFG,
        evidence_root="/home/u/evidence",
    )
    assert out["key"] == "INV-653"
    assert out["login_url"] == "https://xin-np.wbee.ca/"
    assert out["username"] == "workabee-dev"
    assert out["password_secret"] == "TEST_LOGIN_PASSWORD"
    assert out["ticket_type"] == "invoice"
    assert out["booking_dependent"] is True
    assert out["evidence_root"] == "/home/u/evidence"
    assert out["api_base"] == "https://xin-api-np.wbee.ca/api/v1"
    assert out["seed_booking"]["booking_number"] == "BK_X077IUKO"
    assert out["runs_dir"].replace("\\", "/").endswith("INV-653/runs")


def test_gather_never_leaks_password(monkeypatch):
    monkeypatch.setenv("TEST_LOGIN_PASSWORD", "sup3r-sekret-value")
    out = qa_targets.gather(
        "INV-662", "https://xin-np.wbee.ca/",
        ticket_text="Location Info Card email and website",
        seed_booking=None,
        instance_cfg=_INSTANCE_CFG,
        evidence_root="/home/u/evidence",
    )
    blob = json.dumps(out)
    assert "sup3r-sekret-value" not in blob
    # the non-booking ticket carries no seed booking
    assert out["seed_booking"] is None
    assert out["booking_dependent"] is False


def test_gather_includes_ticket_scope():
    out = qa_targets.gather(
        "INV-602", "https://xin-np.wbee.ca/",
        ticket_text="Apply Payment to Booking POST /api/v1/booking/payment",
        ticket_summary="[SD4.12.0] Apply Payment to Booking",
        ticket_description="New API to apply a payment to a booking",
        ticket_state="Ready for Testing",
        seed_booking=None, instance_cfg=_INSTANCE_CFG, evidence_root="/home/u/evidence",
    )
    assert out["ticket_type"] == "payment"
    assert out["booking_dependent"] is True
    assert out["ticket_summary"] == "[SD4.12.0] Apply Payment to Booking"
    assert out["ticket_description"] == "New API to apply a payment to a booking"
    assert out["ticket_state"] == "Ready for Testing"


def test_default_evidence_root_is_dashboard_canonical():
    # The dashboard reads config.EVIDENCE_DIR; the resolver must agree so runs land
    # where check_evidence looks (NOT backend/evidence — that was the silent miss).
    assert qa_targets.default_evidence_root() == config.EVIDENCE_DIR


def test_secret_name_extraction():
    assert qa_targets._secret_name("${secret:TEST_LOGIN_PASSWORD}") == "TEST_LOGIN_PASSWORD"
    assert qa_targets._secret_name("plain-literal") is None
    assert qa_targets._secret_name(None) is None
