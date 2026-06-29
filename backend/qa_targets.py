"""Deterministic Phase-2 target resolver for the headless qa-evidence run.

Run by the agent at the start of Phase 2:

    python qa_targets.py <KEY> <env_url> [--text "<ticket summary/description>"]

Prints ONE JSON object to stdout: the login URL + username (and the secret NAME
of the password — never its value), the resolved ticket type, whether the ticket
is booking-dependent, the canonical evidence_root the dashboard reads, the API
base, and — for booking-dependent tickets — an existing seed booking with a
renderable invoice to navigate to.

The point is to strip the headless agent's guesswork: it should never have to
decide where to log in, what kind of ticket this is, or which existing booking
has a real invoice. The agent still does the per-TC assertions.

Security: the password value and the minted id_token never leave this process —
nothing here prints them.
"""
import argparse
import asyncio
import json
import os
import re
import sys

import config
import instance_config as ic
import qa_auth


# The five QA-seed bookings on Totem (see memory beeventory-seed-bookings-via-api).
SEED_BOOKING_NUMBERS = [
    "BK_X077IUKO", "BK_9IRRVC4P", "BK_CP8WAXM4", "BK_CRKCYYNI", "BK_J755DICT",
]

# Ticket types that require an EXISTING booking's invoice/folio/payment surface.
# These must navigate to a seed booking — never the Create-Reservation room grid.
BOOKING_DEPENDENT_TYPES = frozenset(
    {"invoice", "folio", "payment", "deposit", "checkin", "checkout"}
)

# Keyword → type, most specific first. First matching rule wins. Keywords are kept
# tight on purpose: a stray broad word (e.g. "label") must not pull an unrelated
# ticket into a wrong bucket.
_CLASSIFY_RULES = [
    ("folio",     ("folio",)),
    ("invoice",   ("invoice",)),
    ("deposit",   ("deposit",)),
    ("payment",   ("payment", "refund")),
    ("checkout",  ("check out", "check-out", "checkout")),
    ("checkin",   ("check in", "check-in", "checkin")),
    ("filter",    ("filter",)),
    ("config",    ("config", "setting")),
    ("nav",       ("navigation", "navbar", "nav item", "breadcrumb")),
    ("dashboard", ("dashboard",)),
    ("display",   ("display", "render", "email", "website", "card",
                   "tooltip", "badge", "icon", "logo")),
]


def classify_ticket_type(text):
    """Classify a ticket from its OWN summary+description text. 'other' if unknown.

    Deliberately ignores any Related/linked-ticket text — the caller passes only
    this ticket's own scope (see the Phase 0/1 scoping guard)."""
    if not text:
        return "other"
    low = text.lower()
    for ttype, keywords in _CLASSIFY_RULES:
        if any(k in low for k in keywords):
            return ttype
    return "other"


def is_booking_dependent(ticket_type):
    return ticket_type in BOOKING_DEPENDENT_TYPES


def _invoice_total_due(invoice):
    """Pull a total_due-ish number out of a /so/invoice response. None if absent.

    The live response nests the invoice object under an "invoice" key; accept
    either that shape or a flat invoice object."""
    if not isinstance(invoice, dict):
        return None
    candidates = [invoice]
    sub = invoice.get("invoice")
    if isinstance(sub, dict):
        candidates.append(sub)
    for obj in candidates:
        for k in ("total_due", "totalDue", "balance_due", "balanceDue", "total"):
            v = obj.get(k)
            if isinstance(v, (int, float)):
                return v
    return None


def select_seed_booking(bookings, get_invoice, preferred_numbers=SEED_BOOKING_NUMBERS):
    """Pick a CONFIRMED booking whose invoice renders (get_invoice(id) non-None).

    Prefers the QA-seed booking numbers. ``get_invoice`` is injected so this core
    stays pure and offline-testable. Returns
    {booking_id, booking_number, invoice_total_due} or None.
    """
    preferred = set(preferred_numbers or [])
    confirmed = [b for b in bookings if b.get("status") == "CONFIRMED"]
    confirmed.sort(key=lambda b: 0 if b.get("booking_number") in preferred else 1)
    for b in confirmed:
        invoice = get_invoice(b.get("booking_id"))
        if invoice is not None:
            return {
                "booking_id": b.get("booking_id"),
                "booking_number": b.get("booking_number"),
                "invoice_total_due": _invoice_total_due(invoice),
            }
    return None


def _secret_name(value):
    """The NAME inside a ${secret:NAME} reference, or None. Never the value."""
    if value is None:
        return None
    m = re.match(r"^\$\{secret:([^}]+)\}$", str(value).strip())
    return m.group(1) if m else None


def default_evidence_root():
    """The evidence root the dashboard actually reads (config.EVIDENCE_DIR).

    The skill MUST write runs here, not to ``backend/evidence`` — that mismatch is
    why earlier headless runs produced output the dashboard never registered."""
    return config.EVIDENCE_DIR


def _api_base(instance_cfg):
    api = instance_cfg.get("api") or {}
    base = (api.get("baseUrl") or "").rstrip("/")
    if not base:
        return None
    return base + (api.get("prefix") or "")


def gather(key, env_url, *, ticket_text, seed_booking, instance_cfg, evidence_root):
    """Assemble the target JSON. Pure: no network, never reads/echoes the password."""
    testauth = (instance_cfg.get("environments") or {}).get("testAuth") or {}
    ticket_type = classify_ticket_type(ticket_text)
    return {
        "key": key,
        "login_url": testauth.get("loginUrl") or env_url,
        "username": testauth.get("username"),
        "password_secret": _secret_name(testauth.get("password")),
        "ticket_type": ticket_type,
        "booking_dependent": is_booking_dependent(ticket_type),
        "evidence_root": evidence_root,
        "runs_dir": os.path.join(evidence_root, key, "runs"),
        "api_base": _api_base(instance_cfg),
        "seed_booking": seed_booking,
    }


# --- live shell (only runs in main(); unit tests never reach here) ----------

def _ticket_text_from_manifest(evidence_root, key):
    """Read this ticket's own summary+description from the Phase-1 manifest."""
    import yaml
    path = os.path.join(evidence_root, key, "manifest.yml")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            m = yaml.safe_load(f) or {}
    except Exception:
        return None
    t = m.get("ticket") if isinstance(m.get("ticket"), dict) else {}
    parts = [
        t.get("summary") or t.get("title") or m.get("summary") or m.get("title") or "",
        t.get("description") or m.get("description") or "",
    ]
    return "\n".join(p for p in parts if p) or None


def _user_buckets(client, api_base, headers):
    """Bucket ids the test user can see, from GET /user/profile.bucket_to_location."""
    try:
        r = client.get(f"{api_base}/user/profile", headers=headers)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    prof = r.json() if isinstance(r.json(), dict) else {}
    out = []
    for entry in (prof.get("bucket_to_location") or []):
        bucket = (entry or {}).get("bucket") or {}
        bid = bucket.get("id")
        if isinstance(bid, int):
            out.append(bid)
    return out


def _normalize_booking(b):
    return {
        "booking_number": b.get("booking_number") or b.get("bookingNumber"),
        "booking_id": b.get("id") or b.get("booking_id") or b.get("bookingId"),
        "status": b.get("status"),
    }


def _fetch_bookings(api_base, id_token):
    """POST /booking/search across the user's buckets. Returns normalized rows.

    The search response holds rows under "bookings"; an empty search_payload
    returns every booking in the bucket (which is what we want — pick a seed)."""
    import httpx
    headers = {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}
    out = []
    try:
        with httpx.Client(timeout=20) as c:
            buckets = _user_buckets(c, api_base, headers) or []
            for bucket_id in buckets:
                body = {"bucket_id": bucket_id, "search_payload": {}}
                try:
                    r = c.post(
                        f"{api_base}/booking/search"
                        "?sort_order=id&sort_dir=desc&page=1&size=100",
                        headers=headers, json=body,
                    )
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                data = r.json()
                rows = data.get("bookings") if isinstance(data, dict) else data
                out.extend(_normalize_booking(b) for b in (rows or []))
    except Exception:
        return out
    return out


def _fetch_invoice(api_base, id_token, booking_id):
    import httpx
    if booking_id is None:
        return None
    headers = {"Authorization": f"Bearer {id_token}"}
    url = f"{api_base}/so/invoice?booking_id={booking_id}"
    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(url, headers=headers)
    except Exception:
        return None
    return r.json() if r.status_code == 200 else None


def _resolve_seed_booking(api_base):
    """Live: mint token → search bookings → pick one with a renderable invoice.

    Returns (seed_booking|None, error_message|None). Never raises; never leaks the
    token. The agent treats a None seed on a booking-dependent ticket as 'blocked'.
    """
    if not api_base:
        return None, "no api.baseUrl configured"
    try:
        tokens = asyncio.run(qa_auth.mint_tokens())
    except Exception as e:
        return None, f"auth failed: {e}"
    id_token = tokens.get("id_token")
    bookings = _fetch_bookings(api_base, id_token)
    if not bookings:
        return None, "booking search returned no rows"
    seed = select_seed_booking(
        bookings, lambda bid: _fetch_invoice(api_base, id_token, bid)
    )
    if seed is None:
        return None, "no CONFIRMED booking had a renderable invoice"
    return seed, None


def main(argv=None):
    p = argparse.ArgumentParser(description="Resolve headless QA Phase-2 targets.")
    p.add_argument("key")
    p.add_argument("env_url")
    p.add_argument("--text", default=None,
                   help="ticket summary/description (else read manifest.yml)")
    p.add_argument("--no-network", action="store_true",
                   help="skip the live seed-booking lookup")
    args = p.parse_args(argv)

    cfg = ic.load_instance_config() or {}
    evidence_root = default_evidence_root()
    ticket_text = args.text or _ticket_text_from_manifest(evidence_root, args.key)
    ttype = classify_ticket_type(ticket_text)

    seed_booking, seed_error = None, None
    if is_booking_dependent(ttype) and not args.no_network:
        seed_booking, seed_error = _resolve_seed_booking(_api_base(cfg))

    out = gather(args.key, args.env_url, ticket_text=ticket_text,
                 seed_booking=seed_booking, instance_cfg=cfg,
                 evidence_root=evidence_root)
    if seed_error:
        out["seed_error"] = seed_error
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
