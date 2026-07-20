// Framework-free tests for src/testCases.ts — run via `npm test` (esbuild → node).
// These pin the EXISTING behavior of a parser that shipped untested for months.
import { extractTicketTestCases, caseCount } from '../src/testCases'

let passed = 0
let failed = 0

function eq<T>(actual: T, expected: T, label: string) {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a === e) {
    passed++
  } else {
    failed++
    console.error(`  FAIL ${label}\n    expected ${e}\n    got      ${a}`)
  }
}

// --- extractTicketTestCases -------------------------------------------------
{
  eq(extractTicketTestCases(''), [], 'empty description -> no cases')
  eq(extractTicketTestCases('Just a plain description with no section.'), [],
    'no Test Cases section -> no cases')

  const markdown = [
    '## Summary',
    'Fix the invoice total.',
    '',
    '## Test Cases',
    '- Open an invoice and confirm the total matches the line items',
    '- Change a line item and confirm the total recalculates',
    '',
    '## Notes',
    '- This note is NOT a test case',
  ].join('\n')
  eq(extractTicketTestCases(markdown), [
    'Open an invoice and confirm the total matches the line items',
    'Change a line item and confirm the total recalculates',
  ], 'markdown heading section, terminated by the next heading')

  // The real NOR shape: checkbox items under a bold label.
  const checkboxes = [
    '**Test Cases:**',
    '- [ ] As `manager@northstar.demo`, GET /api/orders returns only CA orders',
    '- [x] Selecting tenant US returns no US orders',
  ].join('\n')
  eq(extractTicketTestCases(checkboxes), [
    'As `manager@northstar.demo`, GET /api/orders returns only CA orders',
    'Selecting tenant US returns no US orders',
  ], 'checkbox items under a bold heading, checked or not')

  const trailing = ['## Test Cases', '- Only case here'].join('\n')
  eq(extractTicketTestCases(trailing), ['Only case here'],
    'section running to end of description')

  eq(extractTicketTestCases(['## Test Cases', '- ab'].join('\n')), [],
    'items of 2 chars or fewer are dropped as noise')
}

// --- caseCount --------------------------------------------------------------
{
  eq(caseCount(0, 0), 0, 'no cases -> 0')
  eq(caseCount(2, 1), 3, 'ticket cases + added cases')
}

// --- report -----------------------------------------------------------------
console.log(`\ntestCases: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
