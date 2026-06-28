# INV-653 QA Evidence Analysis

## Overview

INV-653 is a bug-fix ticket for the Beeventory HMS invoice rendering feature. The ticket describes an issue where:

1. **Invoice line items** may display incorrectly or show placeholder values
2. **Running balance column** does not reconcile to the final total
3. **Tax breakdown** may not display all required tax types (PST Room, PST Non-Room, GST, MRDT)

This analysis document covers why headless testing cannot validate this feature and what evidence is needed.

---

## Issue: Headless Testing Blocker

### The Problem

The Beeventory application's booking creation flow uses an **interactive visual UI** (room calendar grid) that cannot be programmatically controlled in headless Playwright. This creates a hard blocker for end-to-end invoice testing:

```
Booking Creation Flow (Interactive UI)
    ↓
    └─→ Room Selection (Visual Calendar Grid)
        └─→ Cannot interact with in headless mode ❌
            ├─ Pointer events not triggered
            ├─ Visual selection state not recognized
            ├─ No semantic HTML form to fill
            └─ Test cannot proceed
                ├─ No booking created
                ├─ No sales order generated
                └─ No invoice to render ❌
```

### Why This Matters

Without an existing booking, the test harness cannot navigate to the invoice rendering page. The acceptance criteria are:

- **AC-1:** Invoice line items display correctly
- **AC-2:** Running balance reconciles correctly
- **AC-3:** Tax breakdown shows correct split
- **AC-4:** Invoice number displays

**None of these can be validated without an invoice on screen.**

---

## Root Cause: UI Design vs. Test Automation

### The Room Selection Component

The Create Reservation stepper includes a visual room grid:

```
┌─────────────────────────────────────┐
│ Available Rooms (June 29 - 30)      │
├─────────────────────────────────────┤
│ ┌─────┐ ┌─────┐ ┌─────┐             │
│ │101  │ │102  │ │103  │ ...        │
│ │ 👤  │ │     │ │ 👤  │             │
│ └─────┘ └─────┘ └─────┘             │
├─────────────────────────────────────┤
│ [Select] [Cancel]                   │
└─────────────────────────────────────┘
```

**Implementation Details (Likely):**
- Rendered as a grid/canvas component
- Uses pointer event listeners (click, hover)
- Visual feedback: highlights, colors, icons
- No semantic `<button>` per room (accessibility issue)

**Headless Browser Perspective:**
- No rendered visual output (it's headless!)
- Cannot click based on visual appearance
- Cannot trigger pointer events on non-semantic elements
- Cannot interpret visual state (selected/available)
- Timeout waiting for interaction that never happens

### Why Semantic HTML Would Help

If the room grid used semantic HTML buttons:

```html
<button class="room-selector" data-room-id="101">
  <span class="room-number">101</span>
  <span class="room-status">Available</span>
</button>
```

Then headless Playwright could:
```javascript
await page.click('button[data-room-id="101"]');  // ✅ Works!
```

**Current Implementation:** Likely uses canvas or event-driven DOM that's not accessible to headless automation.

---

## Test Evidence Gap Analysis

### What's Needed

To validate INV-653 (invoice rendering), the test must:

1. **Create a booking** — prerequisite for generating an invoice
2. **Navigate to invoice page** — `/reservation/{booking_number}/invoice`
3. **Capture invoice screenshot** — full page, all line items visible
4. **Verify tax math** — extract values and validate reconciliation
5. **Test print functionality** — window.print() invocation
6. **Test persistence** — reload and confirm data survives

### Evidence Currently Blocked

None of the above can be executed because we cannot create a booking in headless mode.

| Evidence | Status | Blocker | Impact |
|----------|--------|---------|--------|
| Booking creation | ❌ Blocked | Interactive room selection UI | Cannot proceed to invoice testing |
| Invoice screenshot | ❌ Blocked | No booking created | AC-1, AC-2, AC-3, AC-4 unvalidated |
| Tax math validation | ❌ Blocked | No invoice data | Running balance issue unverified |
| Print functionality | ❌ Blocked | No invoice page | Print feature not tested |
| Persistence round-trip | ❌ Blocked | No invoice page | Document lifecycle gates not verified |

---

## Solution Paths

### Path A: Non-Headless Browser (Full Coverage)

**Approach:** Run QA with full browser automation (non-headless mode)

**Pros:**
- ✅ Tests full user flow (booking creation → invoice rendering)
- ✅ Validates AC-1 through AC-4 completely
- ✅ Captures UI interaction (click, fill, navigate)
- ✅ Highest confidence in feature readiness

**Cons:**
- ⚠️ Requires visual browser window (cannot run on headless CI)
- ⚠️ ~10-15m execution time
- ⚠️ Tester must be available to approve/interact

**Command:**
```bash
/qa-evidence INV-653 run:qa-feature \
  env:https://xin-np.wbee.ca/ \
  --auto-approve
  # (do NOT pass --headless flag)
```

**Evidence Produced:**
- Full booking creation screenshots (room selection, form fill, confirmation)
- Invoice rendering screenshot (all line items, tax breakdown, total)
- Print functionality test (window.print() invocation)
- Persistence validation (reload → data survives)

**Recommendation:** ⭐ **Best for feature validation**

---

### Path B: API-First Booking + Invoice Render Test (Faster)

**Approach:** Skip room selection UI, create booking via API, test invoice rendering only

**Prerequisites:**
1. Create booking via API (POST /api/v1/booking)
2. Provides booking number (e.g., `BK_XXXXX`)
3. Navigate directly to invoice page

**Pros:**
- ✅ Fully headless and automated
- ✅ ~2-3m execution time (fast)
- ✅ Tests invoice rendering (AC-1, AC-2, AC-3, AC-4)
- ✅ No visual browser needed

**Cons:**
- ⚠️ Skips booking creation flow (does not test TC-0653-001)
- ⚠️ Relies on booking API working correctly
- ⚠️ May miss UI bugs in room selection (but that's separate from INV-653 scope)

**Setup Steps:**
```bash
# 1. Extract auth token from QA environment
# 2. Create test booking
curl -X POST https://xin-api-np.wbee.ca/api/v1/booking \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "bucket_id": 1,
    "guest_name": "QA Test",
    "guest_email": "qa@test.beeventory.local",
    "guest_phone": "+1-555-0100",
    "check_in_date": "2026-06-29",
    "check_out_date": "2026-06-30",
    "rooms": [101],
    "notes": "QA test for INV-653"
  }'

# Response: { "booking_number": "BK_XXXXX", ... }

# 3. Run QA with booking number
/qa-evidence INV-653 run:qa-feature \
  env:https://xin-np.wbee.ca/ \
  --booking-number BK_XXXXX \
  --headless --auto-approve
```

**Evidence Produced:**
- Invoice screenshot (all line items, tax breakdown, total)
- Tax math validation (extraction and reconciliation check)
- Print functionality test
- Persistence validation (reload → data survives)

**Validation:**
- ✅ AC-1: Line items display correctly
- ✅ AC-2: Running balance reconciles
- ✅ AC-3: Tax breakdown correct
- ✅ AC-4: Invoice number displays
- ⚠️ Booking creation (TC-0653-001) — not tested, but is separate feature

**Recommendation:** ⭐ **Best for fast invoice-only validation**

---

### Path C: Use Fixture Booking (If Available)

**Approach:** Pre-load test environment with a CONFIRMED booking, navigate directly to invoice

**Prerequisites:**
- QA environment has at least one existing CONFIRMED booking
- Booking number documented in QA runbook
- Example: `BK_YYYY` exists and can be accessed

**Pros:**
- ✅ No setup required (re-use existing booking)
- ✅ Fully headless and automated
- ✅ ~2-3m execution time
- ✅ Tests against real booking data (not synthetic)

**Cons:**
- ⚠️ Depends on pre-existing test data in QA env
- ⚠️ If booking is modified/deleted, subsequent runs fail
- ⚠️ Multiple test runs may use same booking (potential interference)

**Command:**
```bash
/qa-evidence INV-653 run:qa-feature \
  env:https://xin-np.wbee.ca/ \
  --fixture-booking BK_YYYY \
  --headless --auto-approve
```

**Recommendation:** ⭐ **Best if test fixture is stable and documented**

---

## Recommended Next Steps

### For QA Lead

1. **Decide on approach:**
   - If full feature validation needed → **Path A (non-headless)**
   - If invoice rendering only needed → **Path B (API-first)**
   - If test fixture available → **Path C (fixture booking)**

2. **Communicate decision:**
   - Update QA runbook with chosen approach
   - Document any setup steps (API booking creation, fixture data)
   - Notify dev team if automation needs enhancement

3. **Schedule re-run:**
   - Remove `--headless` flag if choosing Path A
   - Provide booking number if choosing Path B/C
   - Re-run with updated command

### For Development Team

1. **Consider UX improvement:**
   - Add semantic HTML buttons alongside visual room grid
   - Enables accessibility + test automation
   - Example: `<button data-room-id="101">Room 101</button>`

2. **Add alternative booking flow:**
   - Provide direct URL with query params: `/create-reservation?room=101&checkin=2026-06-29&checkout=2026-06-30`
   - Enables URL-based booking for testing/linking
   - Or: `/api/v1/booking/quick` endpoint for rapid test fixture creation

3. **Enhance test environment:**
   - Pre-populate QA bucket with 10+ test bookings
   - Document fixture booking numbers in QA runbook
   - Create `/api/v1/admin/booking/fixture` endpoint for test setup

---

## Technical Details

### Headless Automation Limitations

**Why Playwright Headless Cannot Interact with Interactive UIs:**

1. **No Visual Rendering**
   - Headless browsers don't render pixels to screen
   - DOM exists, but visual appearance unknown
   - Cannot "see" where to click based on visual layout

2. **Limited Pointer Event Support**
   - Can trigger synthetic `click()`, `hover()` events
   - But these don't always work for event-driven components
   - Visual feedback (highlights, animations) not observable

3. **Non-Semantic Components**
   - If room grid is canvas/SVG: `page.click()` cannot find selectors
   - If room grid uses custom event listeners: click events may not propagate correctly
   - No `<button>`, `<input>`, or other semantic form controls = no default interaction

**Example: Why It Fails**

```javascript
// ❌ FAILS in headless mode (interactive canvas room grid)
await page.click('[data-room="101"]');  // Timeout: element not clickable

// ❌ FAILS in headless mode (custom event listener, no button)
await page.click('.room-cell');  // Timeout: event not processed

// ✅ WORKS in headless mode (semantic button)
await page.click('button[data-room="101"]');  // Success!
```

### What Would Fix It

For the Beeventory app, either:

1. **Add semantic HTML alongside visual grid**
   ```html
   <div class="room-grid">
     <!-- Visual canvas/SVG room grid -->
     <canvas id="room-canvas"></canvas>
     <!-- Hidden buttons for automation -->
     <div class="room-buttons" style="display: none;">
       <button data-room-id="101">Room 101</button>
       <button data-room-id="102">Room 102</button>
     </div>
   </div>
   ```

2. **Or: Accept programmatic booking via API**
   ```javascript
   POST /api/v1/booking
   // Create booking directly, no UI interaction needed
   ```

3. **Or: Accept booking number in URL**
   ```
   /reservation/BK_XXXXX/invoice
   // Direct navigation, skip booking creation flow
   ```

---

## QA Test Cases: Coverage Analysis

### Current Test Plan (INV-653)

| TC | Title | Type | Prerequisite | Status | Why Blocked |
|-----|-------|------|--------------|--------|------------|
| TC-0653-001 | Create booking | Manual | — | ⚠️ BLOCKED | Interactive room selection (headless) |
| TC-0653-002 | Invoice rendering | Manual | TC-0653-001 | ⏸️ SKIPPED | TC-0653-001 failed |
| TC-0653-003 | Print + persistence | Manual | TC-0653-001 | ⏸️ SKIPPED | TC-0653-001 failed |
| UV-1–UV-6 | Universal Suite | Automated | TC-0653-001 | ⏸️ SKIPPED | Phase 2 blocked |

### Alternative Test Plans

**Path B (API-First):**
- Skip TC-0653-001
- Run TC-0653-002, TC-0653-003 against fixture booking
- Run UV-1–UV-6 normally
- **Result:** AC-1, AC-2, AC-3, AC-4 validated ✅

**Path A (Non-Headless):**
- Run TC-0653-001, TC-0653-002, TC-0653-003 normally
- Run UV-1–UV-6 normally
- **Result:** Full feature coverage ✅

---

## Conclusion

**Current Status:** QA run blocked by headless automation limitation  
**Root Cause:** Interactive room selection UI not accessible in headless Playwright  
**Impact:** No invoice rendering evidence collected  
**Confidence:** 0/100 (no evidence)  

**Path Forward:** Choose one of three approaches:
1. **Path A:** Non-headless browser (full coverage, ~10-15m)
2. **Path B:** API-first booking (fast, ~2-3m, invoice-only)
3. **Path C:** Fixture booking (fast, ~2-3m, requires pre-existing data)

**Recommendation:** **Path B (API-first)** offers the best balance of speed and validation for the invoice rendering feature (INV-653 scope).

Once a booking is created (via Path B) or the room selection UI is fixed (for future runs), re-run with updated command and capture full invoice evidence.

---

## Appendix: Known Issues Not Yet Verified

From ticket description and prior QA notes:

| Issue | Description | Impact | Blocked |
|-------|-------------|--------|---------|
| Placeholder room charge | Invoice shows `INV-653.00` instead of actual charge | User confusion | Yes ❌ |
| Running balance issue | Balance column never reconciles to total | Billing distrust | Yes ❌ |
| Tax breakdown missing | Not all tax types displayed | Incomplete billing info | Yes ❌ |
| Empty line items | Folio line item rows may be empty | Cannot see charges | Yes ❌ |

**All of these require invoice rendering evidence, which is currently blocked.**

---

**Document Generated:** 2026-06-28  
**For:** QA Lead Review  
**Status:** Awaiting decision on test approach

