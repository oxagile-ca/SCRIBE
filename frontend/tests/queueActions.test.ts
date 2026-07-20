// Framework-free tests for src/queueActions.ts — run via `npm test` (esbuild → node).
import { queueActionLabel, retestNeedsEnvPicker } from '../src/queueActions'

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

// --- queueActionLabel -------------------------------------------------------
{
  eq(queueActionLabel(false), 'Start', 'a never-tested ticket still says Start')
  eq(queueActionLabel(true), 'Re-test',
    'a QAed ticket says Re-test so it is distinguishable from untested work')
}

// --- retestNeedsEnvPicker ---------------------------------------------------
{
  // Already-deployed app: the backend resolves envUrl from environments.staticUrls,
  // so asking the user is a pointless click — there is exactly one answer.
  eq(retestNeedsEnvPicker(false), false,
    'already-deployed app re-tests in one click, no picker')
  // Build/deploy app: staticUrls may be empty, and resolve_env_url would fall back
  // to "" — a run that silently tests nothing. Make the user choose instead.
  eq(retestNeedsEnvPicker(true), true,
    'build/deploy app must pick an env rather than risk an empty envUrl')
}

// --- report -----------------------------------------------------------------
console.log(`\nqueueActions: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
