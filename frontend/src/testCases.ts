/**
 * Ticket-derived and user-added QA test cases.
 *
 * Ticket cases are parsed out of the ticket description and are READ-ONLY — the
 * tracker owns them. User-added cases live in the backend test-case store and are
 * merged into the run scope by qa_targets.
 *
 * This parser was private to QueueRow, which made it untestable and unusable from
 * LaneCard. Moved here verbatim; behavior is unchanged and pinned by tests.
 */

/** Pull the ticket's own test cases from a `Test Cases` section of the description
 *  (the checklist items under a "## Test Cases" heading). Empty when there's none. */
export function extractTicketTestCases(description: string): string[] {
  if (!description) return []
  const out: string[] = []
  let inSection = false
  for (const raw of description.split('\n')) {
    const line = raw.trim()
    const isHeading = /^#{1,6}\s/.test(line) || /^\*{0,2}[\w ]+:?\*{0,2}$/.test(line)
    if (/test\s*cases?/i.test(line) && isHeading) {
      inSection = true
      continue
    }
    if (!inSection) continue
    // A different heading ends the Test Cases section.
    if (/^#{1,6}\s/.test(line) && !/test\s*cases?/i.test(line)) {
      inSection = false
      continue
    }
    // Collect checklist / bullet items: "- [ ] x", "- [x] x", "- x", "* x".
    const m = line.match(/^[-*]\s*(?:\[[ xX]\]\s*)?(.+)$/)
    if (m && m[1].trim().length > 2) out.push(m[1].trim())
  }
  return out
}

/** Badge count for the card button: ticket-derived cases plus the user's own. */
export function caseCount(ticketCount: number, addedCount: number): number {
  return ticketCount + addedCount
}
