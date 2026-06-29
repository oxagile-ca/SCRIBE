// Framework-free tests for src/laneStatus.ts — run via `npm test` (esbuild → node).
// Each test throws on failure; main() exits non-zero if any failed.
import {
  shouldPassivelyCheckEvidence,
  waitingLaneKey,
  classifyBlocker,
  streamLostUpdate,
} from '../src/laneStatus'
import type { Lane, AgentName, AgentStatus, CouncilStatus } from '../src/types'

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

function ok(cond: boolean, label: string) {
  eq(cond, true, label)
}

// --- builders ---------------------------------------------------------------
function agent(state: AgentStatus['state']): AgentStatus {
  return { name: 'inspector', state, progress: 0, eta: '', message: '' }
}

function makeLane(over: Partial<Lane> = {}): Lane {
  const agents: Record<AgentName, AgentStatus> = {
    quartermaster: agent('idle'),
    builder: agent('idle'),
    shipper: agent('idle'),
    inspector: agent('active'),
    scribe: agent('idle'),
  }
  return {
    id: 'lane-1',
    ticket: { key: 'INV-1' } as Lane['ticket'],
    agents,
    currentAgent: 'inspector',
    streamId: null,
    pipelineId: null,
    logs: [],
    startedAt: '',
    waitingForEvidence: true,
    ...over,
  }
}

// --- shouldPassivelyCheckEvidence ------------------------------------------
ok(shouldPassivelyCheckEvidence(makeLane()) === true,
  'polls a lane genuinely waiting at the inspector stage')

ok(shouldPassivelyCheckEvidence(makeLane({ waitingForEvidence: false })) === false,
  'does NOT poll a lane that is not waiting for evidence')

for (const cs of ['pending', 'pass', 'block', 'overridden'] as CouncilStatus[]) {
  ok(shouldPassivelyCheckEvidence(makeLane({ councilStatus: cs })) === false,
    `does NOT poll a lane the council owns (councilStatus=${cs}) — council SSE drives status`)
}

ok(shouldPassivelyCheckEvidence(makeLane({ currentAgent: 'scribe' })) === false,
  'does NOT poll a lane that has advanced to the scribe stage')

ok(shouldPassivelyCheckEvidence(makeLane({
  agents: { ...makeLane().agents, inspector: agent('done') },
})) === false,
  'does NOT poll a lane whose inspector already finished')

// --- waitingLaneKey ---------------------------------------------------------
eq(
  waitingLaneKey([
    makeLane({ id: 'b' }),
    makeLane({ id: 'a' }),
    makeLane({ id: 'c', waitingForEvidence: false }), // excluded
  ]),
  'a,b',
  'waitingLaneKey returns sorted ids of only the lanes that should be polled',
)

eq(waitingLaneKey([]), '', 'waitingLaneKey is empty when no lanes')
eq(
  waitingLaneKey([makeLane({ id: 'x', councilStatus: 'pending' })]),
  '',
  'waitingLaneKey excludes council-owned lanes (so the poll effect does not churn)',
)

// stable across reordering — the whole point: the effect key must not change
// just because the lanes array was rebuilt in a different order.
eq(
  waitingLaneKey([makeLane({ id: 'a' }), makeLane({ id: 'b' })]),
  waitingLaneKey([makeLane({ id: 'b' }), makeLane({ id: 'a' })]),
  'waitingLaneKey is order-independent',
)

// --- classifyBlocker --------------------------------------------------------
eq(classifyBlocker('Session expired — please log in again').kind, 'login',
  'classifies an auth/session message as a login blocker')
eq(classifyBlocker('401 Unauthorized').kind, 'login',
  'classifies a 401 as a login blocker')
eq(classifyBlocker('No repo/branch info — set dev info in Jira').kind, 'data',
  'classifies missing repo/branch as a data blocker')
eq(classifyBlocker('QA run captured no evidence (summary.json missing)').kind, 'runner',
  'classifies a missing-evidence/browser failure as a runner blocker')
eq(classifyBlocker('Browser already in use').kind, 'runner',
  'classifies a browser-profile conflict as a runner blocker')
eq(classifyBlocker('Connection lost').kind, 'connection',
  'classifies a dropped stream as a connection blocker')
eq(classifyBlocker('something exploded').kind, 'generic',
  'falls back to a generic blocker for unrecognised messages')

// every blocker must carry a user-facing label + actionable hint
for (const msg of ['log in', 'no branch', 'chrome', 'connection lost', 'boom']) {
  const b = classifyBlocker(msg)
  ok(!!b.label && !!b.hint, `blocker for "${msg}" has a label and a hint`)
}

// --- streamLostUpdate (dropped live stream != failed run) -------------------
{
  const found = streamLostUpdate(true)
  eq(found.state, 'done', 'stream lost + evidence present -> done (not failed)')
  eq(found.completed, true, 'stream lost + evidence present -> completed')
  eq(found.waitingForEvidence, false, 'completed run stops waiting for evidence')
  ok(/reconnect lost/i.test(found.message), 'completed message notes the lost reconnect')

  const watching = streamLostUpdate(false)
  eq(watching.state, 'active', 'stream lost + no evidence yet -> active/watching (not failed)')
  eq(watching.completed, false, 'no evidence yet -> not completed')
  eq(watching.waitingForEvidence, true, 'no evidence yet -> keep polling so it self-reconciles')
}
ok((streamLostUpdate(true).state as string) !== 'failed' && (streamLostUpdate(false).state as string) !== 'failed',
  'a dropped live stream is NEVER reported as failed')

// --- report -----------------------------------------------------------------
console.log(`\nlaneStatus: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
