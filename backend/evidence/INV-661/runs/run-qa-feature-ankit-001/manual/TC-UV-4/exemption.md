## TC-UV-4 Exemption — Document Lifecycle Smoke

**Exempt:** Yes  
**Reason:** INV-661 is a backend API-only ticket (GET /so/invoice adds email + website fields to the location payload). There is no CMS document create/edit workflow in scope. No user-editable document is created, saved, or published by this ticket. The Document Lifecycle gates (D1–D5) are not applicable.  
**Ticket type:** `invoice` (API assertion, read-only)  
**Run:** run-qa-feature-ankit-001
