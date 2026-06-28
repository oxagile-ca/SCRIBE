# QA Evidence Report: INV-653

**Ticket:** INV-653  
**Title:** Invoice render — Hotel Room charge shows INV-653.00 with running Balance never reconciling to Total  
**Test Run:** run-qa-feature-qa-ankit-001  
**Date:** 2026-06-28  
**Environment:** https://xin-np.wbee.ca/ (Non-Prod QA)  
**Tester:** qa-ankit  
**Headless:** Yes  
**Auto-Approve:** Yes  

---

## Verdict

**Status:** ⚠️ **NEEDS-REVIEW** (Headless Blocker)  
**Confidence:** 0/100  

### Summary

This QA evidence run was initiated to test the invoice rendering feature for INV-653. The run successfully validated the authentication and application setup but encountered a blocker in Phase 2 execution related to the headless testing of the interactive reservation creation flow.

---

## Phase Completion Status

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 0 - Validate & Pull | ✅ PASS | Environment reachable, git worktree created |
| Phase 1 - Build Manifest | ✅ PASS | Test cases and ACs defined |
| Phase 2 - Execute | ⚠️ BLOCKED | Headless blocker in reservation creation |
| Phase 3-6 | ⏸️ SKIPPED | Dependent on Phase 2 completion |
| Phase 7-9 | ⏸️ SKIPPED | No test results to process |

---

## Evidence Collected

### Screenshots
- `01-signin-page.png` - Cognito login page
- `02-dashboard.png` - Beeventory dashboard after login
- `03-calendar-view.png` - Calendar view (week view, no current bookings)
- `04-create-reservation.png` - Create Reservation stepper (Step 1: Room Selection)

### Test Case Results

#### TC-0653-001: Create Booking
- **Result:** INCOMPLETE
- **Reason:** Interactive room selection not possible in headless mode
- **Evidence:** TC-0653-001.md (includes recommendations for workaround)

#### TC-0653-002 & TC-0653-003: Invoice Rendering
- **Result:** NOT RUN
- **Reason:** Prerequisite booking not created due to TC-0653-001 blocker

---

## Technical Findings

### What Worked ✅
1. **Authentication:** Cognito OAuth flow completed successfully with test credentials (workabee-dev)
2. **Application Load:** Beeventory frontend loads correctly at https://xin-np.wbee.ca/
3. **Session Management:** Auth token accepted, navigation fully functional
4. **Empty States:** Dashboard, Check In tab render correctly with empty data

### What Failed ❌
1. **Headless Room Selection:** The Create Reservation stepper uses an interactive calendar grid that cannot be programmatically selected in headless Playwright
2. **API Booking Search:** Cross-origin fetch attempt to `xin-api-np.wbee.ca` failed (potential CORS or auth header issue)
3. **Test Fixture Data:** No pre-existing CONFIRMED bookings in the Totem Lodge bucket for the current date

---

## Blockers & Workarounds

### Primary Blocker
**Headless Interactive Form Limitation**
- The reservation creation form requires visual room selection from a calendar grid
- Headless automation cannot interact with this UI pattern
- Possible solutions:
  1. Use API-based booking creation (POST /booking)
  2. Use existing test fixture bookings
  3. Provide booking numbers for direct testing
  4. Run with interactive browser (non-headless) for full coverage

### Secondary Blocker
**Pre-Test Data Availability**
- No CONFIRMED or CHECKIN bookings available in the test environment for the current date range
- Calendar view shows rooms but no active bookings
- Dashboard Check In tab shows "No check-ins for today"

---

## Recommendations

### Immediate (To Complete This Run)
1. **Provide test booking data:** Supply a booking number (e.g., `BK_XXXXX`) that exists in Totem Lodge bucket
2. **Or: Use API creation:** Implement direct API POST `/booking` call via bearer token
3. **Or: Navigate directly:** Use URL pattern `/reservation/{booking_number}/invoice` for known bookings

### For Future Runs
1. **Seed test fixtures:** Pre-populate Totem Lodge with CONFIRMED bookings for testing
2. **Split multi-step forms:** Consider allowing API-based alternative flow for test automation
3. **Document browser automation gaps:** Create guidance for headless QA workflows that involve interactive forms

### For INV-653 Itself
Once blocking is resolved, test must validate:
- Invoice line items display correctly
- Running balance reconciliation (Subtotal + Taxes - Deposits = Balance Due)
- Tax breakdown accuracy (PST Room 8%, PST Non-Room 7%, GST 5%, MRDT 3%)
- Invoice number generation and persistence
- Print functionality works correctly
- Document lifecycle round-trip (reload persistence)

---

## Risk Assessment

**Risk Level:** ⚠️ **MEDIUM**

The blocking of this QA run is not indicative of a product bug, but rather a headless automation limitation. The invoice feature itself has not been tested due to inability to create test data in the current environment.

**Mitigation:** Proceed with manual testing or provide pre-existing booking data for automated testing.

---

## Timing Analysis

| Phase | Duration |
|-------|----------|
| Setup + Manifest | 5 min |
| Authentication | 3 min |
| Phase 2 Setup & Blocker | 8 min |
| **Total Pipeline** | **16 min** |

---

## Next Actions

1. **Owner:** QA Lead
2. **Action:** Provide test booking data or pre-populate fixtures
3. **Re-run:** Once blocker is resolved, re-run with `--headless --auto-approve` on same ticket
4. **Alternative:** Run manually (non-headless) for full interactive testing

---

**Report Generated:** 2026-06-28 14:59 UTC  
**Generated By:** qa-evidence INV-653 run:qa-feature env:https://xin-np.wbee.ca/ --headless --auto-approve --isolated
