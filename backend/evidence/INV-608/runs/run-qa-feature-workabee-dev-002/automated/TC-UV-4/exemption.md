# TC-UV-4 Document Lifecycle Smoke — Exemption

**Ticket:** INV-608  
**Run:** run-qa-feature-workabee-dev-002  
**Exemption type:** Read-only renders

Folio and Invoice are read-only print renders. They have no Save, Edit, or Publish actions.  
D1–D5 gates (save-persist, publish-persist, preview) do not apply.

**Verification performed instead:**  
- GET /so/folio?booking_id=8 → 200 ✅  
- GET /so/invoice?booking_id=8 → 200 ✅  
- Both render correctly in the browser dialogs with correct persisted data.
