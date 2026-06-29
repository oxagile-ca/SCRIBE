TC-UV-4 Document Lifecycle Smoke — EXEMPT

Folio and Invoice are read-only render endpoints (/reservation/:booking_number/folio and /invoice).
There is no save/edit/publish action on these pages — they are generated from existing SO data.
D1-D5 document lifecycle gates (save → reload → publish → preview) do not apply.

The "document round-trip" for this ticket is validated instead via:
- API GET /so/invoice?booking_id=8 → 200 (data persisted and retrievable)
- API GET /so/folio?booking_id=8 → 200 (data persisted and retrievable)
- UI: Print Invoice and Print Folio dialogs correctly render the persisted data
