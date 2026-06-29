# TC-UV-4 Exemption

**Ticket:** INV-506 — Folio & Invoice Support, Framework

**Reason:** Folio and Invoice are display-only documents rendered from live API data via `GET /so/folio` and `GET /so/invoice`. There are no editable fields, no Save action, no draft state, and no publish flow. Document Lifecycle Gates D1–D5 do not apply.

**Evidence:** See `automated/TC-UV-4/publish-skipped.txt`. API calls verified in `TC-0506-008/api-folio.json` and `TC-0506-009/api-invoice.json`.
