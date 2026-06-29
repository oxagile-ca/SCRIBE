# TC-UV-4 Exemption — Document Lifecycle Smoke

**Exempt because:** Folio and Invoice are read-only display documents. The app renders them from existing booking/SO data via GET endpoints. There is no save, edit, or publish action available to the user — the Print button only calls `window.print()`. D1–D5 gates (save→reload→publish→reload→preview) do not apply to this document type.

**Run:** run-qa-feature-ankit-002
**Date:** 2026-06-29
