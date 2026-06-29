import type { Lane, EvidenceStatus } from './types'

/**
 * Should the passive 60s evidence poll fire for this lane?
 *
 * The poll exists to auto-advance a lane once a test run drops evidence. But it
 * must NOT run for lanes whose status is already owned by something live:
 *  - a council review in flight/resolved (the council SSE drives the chip), or
 *  - a lane that has moved on to the scribe stage, or
 *  - a lane whose inspector already finished.
 * Polling those re-flashes the chip every cycle — the "cards flicker between
 * statuses" bug. Keeping this a pure predicate lets the poll effect key off a
 * stable set (see waitingLaneKey) instead of being torn down on every render.
 */
export function shouldPassivelyCheckEvidence(lane: Lane): boolean {
  if (!lane.waitingForEvidence) return false
  if (lane.councilStatus) return false
  if (lane.currentAgent === 'scribe') return false
  if (lane.agents?.inspector?.state === 'done') return false
  return true
}

/**
 * Stable identity for the set of lanes the passive poll cares about. The poll
 * effect depends on THIS string, not the whole `lanes` array, so it is recreated
 * only when the waiting set actually changes — not on every log line / 10s poll
 * that mutates `lanes`. Sorted so it is order-independent.
 */
export function waitingLaneKey(lanes: Lane[]): string {
  return lanes.filter(shouldPassivelyCheckEvidence).map(l => l.id).sort().join(',')
}

/**
 * Decide a lane's state when its LIVE qa-run stream drops. A dropped stream is a
 * client-side connection loss — the orchestrator keeps running server-side — so it
 * must NOT be shown as a failed run. If evidence already exists for the ticket the
 * run completed (we just lost the live feed): mark it done. Otherwise keep watching
 * so the passive evidence poll reconciles it once evidence lands. Either way: never
 * 'failed'.
 */
export interface StreamLostUpdate {
  state: 'done' | 'active'
  message: string
  waitingForEvidence: boolean
  completed: boolean
}

export function streamLostUpdate(evidenceFound: boolean): StreamLostUpdate {
  if (evidenceFound) {
    return { state: 'done', message: 'QA complete (stream reconnect lost)', waitingForEvidence: false, completed: true }
  }
  return { state: 'active', message: 'Stream lost — watching for evidence…', waitingForEvidence: true, completed: false }
}

/**
 * Has this ticket's QA run actually FINISHED (vs merely started)?
 *
 * The backend marks evidence `status: 'tested'` the moment a run *directory* is
 * created — which happens at the very START of a run (Phase 2 scaffolding), before
 * any verdict is written. So `status === 'tested'` alone is NOT "complete" and was
 * making in-progress runs show a "✓ QAed" badge. A genuinely finished run has a
 * real result: a score (from summary.json) or a generated report (index.html). An
 * in-progress run has neither yet.
 */
export function evidenceIsComplete(ev?: EvidenceStatus | null): boolean {
  if (!ev) return false
  if (ev.status !== 'tested' && ev.status !== 'published') return false
  // Gate on `score`, not `reportUrl`: the backend reads score ONLY from the latest
  // run's summary.json (no fallback), so it's null until THIS run writes its verdict.
  // reportUrl/reportPath can resolve to a PRIOR run's leftover report during a
  // re-run, which would wrongly read as complete while the new run is in progress.
  return ev.score != null
}

export type BlockerKind = 'login' | 'data' | 'runner' | 'connection' | 'generic'

export interface Blocker {
  kind: BlockerKind
  /** Short, user-facing label, e.g. "Login required". */
  label: string
  /** What the user should do about it. */
  hint: string
}

const BLOCKER_RULES: { kind: BlockerKind; label: string; hint: string; test: RegExp }[] = [
  {
    kind: 'login',
    label: 'Login required',
    hint: 'Sign in to the app / environment, then retry.',
    test: /log\s?in|sign[\s-]?in|unauthor|\b401\b|\b403\b|\bauth\b|authenticat|credential|session\s+(?:expired|invalid)|password|cognito|token\s+expired/,
  },
  {
    kind: 'runner',
    label: 'Test runner blocked',
    hint: 'Close stray Chrome/MCP sessions holding the profile, then retry.',
    test: /browser|chrome|profile|already in use|phase\s?2|playwright|\bmcp\b|did not execute|summary\.json|no evidence|captured no evidence/,
  },
  {
    kind: 'connection',
    label: 'Connection lost',
    hint: 'Click Resume to reconnect to the run.',
    test: /connection lost|disconnect|stream closed|timed?\s?out|timeout|network error|econn/,
  },
  {
    kind: 'data',
    label: 'Missing data',
    hint: 'Add the missing dev info / data, then retry.',
    test: /no repo|no branch|dev[\s-]?info|missing data|missing dev|fee_schedule|\bno pr\b|missing (?:snapshot|info)/,
  },
]

/**
 * Classify a failure message into an actionable blocker so the lane card can
 * tell the user WHY a run can't continue — login, missing data, runner, etc. —
 * instead of a generic "Failed". Only call this for a failed/blocked state.
 */
export function classifyBlocker(message: string): Blocker {
  const m = (message || '').toLowerCase()
  for (const rule of BLOCKER_RULES) {
    if (rule.test.test(m)) {
      return { kind: rule.kind, label: rule.label, hint: rule.hint }
    }
  }
  return { kind: 'generic', label: 'Failed', hint: 'See the log below for details.' }
}
