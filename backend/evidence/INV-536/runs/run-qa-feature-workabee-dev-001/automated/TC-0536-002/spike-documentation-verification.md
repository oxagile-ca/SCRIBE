# TC-0536-002: Spike Approach Documentation Verification

## AC-3: Chosen approach is documented on this ticket before implementation begins

**Status: PASS**

### Evidence: Linear Ticket INV-536 Comments (fetched 2026-06-29)

#### Comment 1 — sang.nguyen@xpventurelabs.com (2026-06-04)

> **Hi @becca here is my investigate**
>
> ## The acceptance criterion is unachievable on FE alone
>
> Per the ticket, on timeout the flow must do **one of two things**:
> (A) recover to a valid booking state, OR
> (B) keep the user on the create form with clear feedback so they can retry
>
> Under current BE constraints, **both paths are blocked**.

**Why path A (recover) is blocked:**
- No `booking_number` returned on timeout → no handle to navigate to created booking
- No `customer_number` returned (new customer flow) → cannot identify which customer
- Booking may exist server-side (confirmed: rooms become unavailable on retry), but client has no identifier

**Why path B (retry) is blocked:**
- BE has **no idempotency-key support**
- Booking, booking_items, sale_order may already be persisted server-side
- Every retry is a duplicate write → the form is effectively poisoned after first timeout

#### Comment 2 — Becca Manning (2026-06-04)

> @sang.nguyen @nhan.nguyen Added booking idempotency key in db v1.6 — INV-552

### Conclusion

AC-3 is **SATISFIED**:
1. sang.nguyen documented the investigation findings showing both FE-only paths are blocked
2. The chosen approach (add idempotency key to DB via INV-552, then FE timeout recovery via INV-558) is documented before implementation began
3. The implementation ticket INV-558 was subsequently created and tested

**Note:** The actual implementation of AC-1 and AC-2 (user not silently left in broken state; user only leaves with valid booking) is covered by INV-558 which has its own evidence run.
