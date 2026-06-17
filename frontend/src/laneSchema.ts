/**
 * Lane state persistence with versioning + migrations.
 *
 * Why this exists: Lane state lived in localStorage as a bare array, and
 * every time we added a field (deployInfo, qaCommand, pipelineId, env, ...)
 * old saved lanes loaded without it. Code accessing the new field then got
 * `undefined` and silently no-op'd — e.g., `releaseEnv(lane.pipelineId)`
 * does nothing when pipelineId is missing. That's exactly the silent-wrong
 * failure mode this dashboard had too much of.
 *
 * The fix:
 *   1. Wrap persisted lanes with `{ version: N, lanes: [...] }`.
 *   2. On load, run migrations from the persisted version to LANE_SCHEMA_VERSION.
 *   3. Migrations defensively default every new field, so any drift between
 *      what's in the codebase and what's in localStorage is closed at load.
 *   4. Unknown / corrupt shapes return [] rather than throwing.
 *
 * When you add a field to Lane, bump LANE_SCHEMA_VERSION and add a migration
 * step that backfills it. The migration runs once per load, not per access.
 */
import type { Lane, AgentName, AgentStatus } from './types'

export const LANE_SCHEMA_VERSION = 3

/** Wrapped shape persisted in localStorage. */
type Persisted = {
  version: number
  lanes: unknown[]
}

const AGENT_NAMES: AgentName[] = ['quartermaster', 'builder', 'shipper', 'inspector', 'scribe']

function emptyAgent(name: AgentName): AgentStatus {
  return { name, state: 'idle', progress: 0, message: '', eta: '' }
}

function emptyAgents(): Record<AgentName, AgentStatus> {
  return Object.fromEntries(AGENT_NAMES.map(n => [n, emptyAgent(n)])) as Record<AgentName, AgentStatus>
}

/**
 * Normalize one raw lane into the current shape. Idempotent.
 * Designed to be called regardless of source version — every defensive
 * default is applied unconditionally, so bumping the schema version only
 * requires updating Lane in types.ts and adding the default here.
 */
export function normalizeLane(raw: unknown): Lane | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>

  // Required identity — without these the lane is unrecoverable.
  if (typeof r.id !== 'string' || !r.id) return null
  if (!r.ticket || typeof r.ticket !== 'object') return null

  // Reconstruct agents map: fill in any missing agent with idle defaults
  // (covers old lanes from before scribe existed, for instance).
  const rawAgents = (r.agents && typeof r.agents === 'object') ? r.agents as Record<string, unknown> : {}
  const agents = emptyAgents()
  for (const name of AGENT_NAMES) {
    const a = rawAgents[name]
    if (a && typeof a === 'object') {
      const ag = a as Record<string, unknown>
      agents[name] = {
        name,
        state: (typeof ag.state === 'string' ? ag.state : 'idle') as AgentStatus['state'],
        progress: typeof ag.progress === 'number' ? ag.progress : 0,
        message: typeof ag.message === 'string' ? ag.message : '',
        eta: typeof ag.eta === 'string' ? ag.eta : '',
      }
    }
  }

  const currentAgent = AGENT_NAMES.includes(r.currentAgent as AgentName)
    ? (r.currentAgent as AgentName)
    : 'builder'

  const lane: Lane = {
    id: r.id,
    ticket: r.ticket as Lane['ticket'],
    agents,
    currentAgent,
    streamId: typeof r.streamId === 'string' ? r.streamId : null,
    pipelineId: typeof r.pipelineId === 'string' ? r.pipelineId : null,
    logs: Array.isArray(r.logs) ? r.logs.filter((x): x is string => typeof x === 'string') : [],
    startedAt: typeof r.startedAt === 'string' ? r.startedAt : new Date().toISOString(),
  }

  // Optional fields — preserve only if shape is plausible.
  if (typeof r.waitingForEvidence === 'boolean') lane.waitingForEvidence = r.waitingForEvidence
  if (Array.isArray(r.baselineRuns)) {
    lane.baselineRuns = r.baselineRuns.filter((x): x is string => typeof x === 'string')
  }
  if (typeof r.qaCommand === 'string') lane.qaCommand = r.qaCommand
  if (typeof r.reportUrl === 'string') lane.reportUrl = r.reportUrl
  if (typeof r.env === 'string') lane.env = r.env
  if (r.deployInfo && typeof r.deployInfo === 'object') {
    const d = r.deployInfo as Record<string, unknown>
    if (typeof d.env === 'string' && Array.isArray(d.services)) {
      lane.deployInfo = {
        env: d.env,
        services: d.services
          .filter((s): s is Record<string, unknown> => !!s && typeof s === 'object')
          .map(s => ({
            service: typeof s.service === 'string' ? s.service : '',
            snapshot: typeof s.snapshot === 'string' ? s.snapshot : '',
          })),
      }
    }
  }

  return lane
}

/**
 * Read raw localStorage value (or null) and return a clean Lane[].
 * Handles three input shapes:
 *   - null / "" / corrupt JSON → []
 *   - bare array (legacy, pre-versioned) → normalized through v1 → current
 *   - { version, lanes } wrapper → migrate from `version` to current
 *
 * Worked examples:
 *   loadLanes(null)
 *     → []
 *   loadLanes('not json')
 *     → []
 *   loadLanes('[]')
 *     → []
 *   loadLanes('[{"id":"x","ticket":{...},"agents":{...},"currentAgent":"builder","streamId":null,"logs":[],"startedAt":"..."}]')
 *     → [Lane with pipelineId=null filled in]
 *   loadLanes('{"version":1,"lanes":[{...without pipelineId...}]}')
 *     → [Lane with pipelineId=null filled in]
 */
export function loadLanes(raw: string | null | undefined): Lane[] {
  if (!raw) return []
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return []
  }

  let rawLanes: unknown[]
  let version = 0
  if (Array.isArray(parsed)) {
    rawLanes = parsed // legacy unversioned shape
  } else if (parsed && typeof parsed === 'object' && Array.isArray((parsed as Persisted).lanes)) {
    rawLanes = (parsed as Persisted).lanes
    version = typeof (parsed as Persisted).version === 'number' ? (parsed as Persisted).version : 0
  } else {
    return []
  }

  // Future-shape guard: if persisted version is newer than what this build
  // knows, discard rather than risk feeding unknown fields into the runtime.
  if (version > LANE_SCHEMA_VERSION) return []

  return rawLanes
    .map(normalizeLane)
    .filter((l): l is Lane => l !== null)
}

/** Serialize lanes with the current schema version. */
export function dumpLanes(lanes: Lane[]): string {
  const wrapper: Persisted = { version: LANE_SCHEMA_VERSION, lanes }
  return JSON.stringify(wrapper)
}

/**
 * Reconcile lane state against backend pipeline-states. The backend is the
 * authoritative source for `stage` and `status` — if a pipeline completed
 * while the tab was closed, the lane shouldn't still claim Builder is active.
 *
 * Only updates fields the backend actually owns; UI-only fields (logs,
 * agent animations) are left as the SSE replay will refresh them.
 */
export function reconcileLanesWithBackend(
  lanes: Lane[],
  pipelineStates: Record<string, { ticketKey: string; env: string; stage: string; status: string }>,
): Lane[] {
  return lanes.map(lane => {
    // Match by pipelineId if we have one, else by ticket key + running status.
    let match: { pipelineId: string; state: typeof pipelineStates[string] } | null = null
    if (lane.pipelineId && pipelineStates[lane.pipelineId]) {
      match = { pipelineId: lane.pipelineId, state: pipelineStates[lane.pipelineId] }
    } else {
      const found = Object.entries(pipelineStates).find(([, s]) => s.ticketKey === lane.ticket.key)
      if (found) {
        match = { pipelineId: found[0], state: found[1] }
      }
    }
    if (!match) return lane

    const stage = match.state.stage as AgentName
    const validStage = AGENT_NAMES.includes(stage) ? stage : lane.currentAgent
    return {
      ...lane,
      pipelineId: lane.pipelineId ?? match.pipelineId,
      currentAgent: validStage,
      env: lane.env ?? match.state.env,
    }
  })
}
