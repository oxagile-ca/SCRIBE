# TC-UV-4 Exemption — Document Lifecycle Smoke

**Ticket:** INV-661
**Run:** run-qa-feature-workabee-dev-002
**Exemption type:** API-only backend ticket

## Reason

INV-661 is a pure backend API change: it adds `email` and `website` fields to
the `location` payload in `GET /so/invoice`. There is no CMS document created,
edited, or published as part of this ticket. No create/edit/save/publish workflow
exists in scope.

The document lifecycle gates (D1–D5) are not applicable because no document
mutation occurs. This ticket is exempt from TC-UV-4 per the Phase 2.5 rule:
"Tests of CMS infrastructure that don't involve documents (auth flows,
service-user-mgmt screens, settings panels)" may skip D1–D5.

## Coverage instead

API contract verified via Phase 2.7 (live API assertions):
- `GET /so/invoice?booking_id=8` returns `location.email` and `location.website`
- All existing location fields confirmed present and unaffected
