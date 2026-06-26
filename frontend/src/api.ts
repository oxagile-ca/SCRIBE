import { Ticket, SSEEvent, CouncilStatus, CouncilVerdict, CouncilOverride, TicketUsage, UsageSummary } from './types'

const BASE = '/api'

export async function fetchVersion(): Promise<{ version: string; startedAt: number; uptimeSec: number }> {
  const res = await fetch(`${BASE}/version`)
  if (!res.ok) throw new Error(`Failed to fetch version: ${res.status}`)
  return res.json()
}

export async function fetchTickets(project: string): Promise<Ticket[]> {
  const res = await fetch(`${BASE}/tickets?project=${project}`)
  if (!res.ok) throw new Error(`Failed to fetch tickets: ${res.status}`)
  return res.json()
}

export async function fetchDevInfo(key: string): Promise<Ticket['devInfo']> {
  const res = await fetch(`${BASE}/dev-info/${key}`)
  if (!res.ok) throw new Error(`Failed to fetch dev info: ${res.status}`)
  return res.json()
}

export async function fetchEvidence(key: string): Promise<Ticket['evidence']> {
  const res = await fetch(`${BASE}/evidence/${key}`)
  if (!res.ok) throw new Error(`Failed to fetch evidence: ${res.status}`)
  return res.json()
}

export type ScoreTally = {
  pass?: number
  fail?: number
  blocked?: number
  total?: number
  pct?: number
  verdict?: string
}

export interface EvidenceHistoryItem {
  key: string
  status: string
  // Backend normalises to number | null, but tolerate a tally dict in case an
  // old summary.json slips through.
  score: number | ScoreTally | null
  time: string
  reportUrl: string
  needsReport: boolean
  latestRun: string
  latestMtime: number
  claudeCost: number | null
}

export async function fetchEvidenceHistory(): Promise<EvidenceHistoryItem[]> {
  const res = await fetch(`${BASE}/evidence-history`)
  if (!res.ok) return []
  return res.json()
}

export interface OnboardingStatus {
  configured: boolean
  productName?: string
  issueTracker?: string
  vcs?: string
  envMode?: string
}

export async function getOnboardingStatus(): Promise<OnboardingStatus> {
  const res = await fetch(`${BASE}/onboarding/status`)
  if (!res.ok) throw new Error(`Failed to fetch onboarding status: ${res.status}`)
  return res.json()
}

export interface OnboardingResult {
  ok: boolean
  errors?: string[]
  error?: string
  summary?: Record<string, unknown>
  paths?: Record<string, string>
}

export async function submitOnboarding(answers: unknown): Promise<OnboardingResult> {
  const res = await fetch(`${BASE}/onboarding`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(answers),
  })
  return res.json().catch(() => ({ ok: false, error: `status ${res.status}` }))
}

export async function startBuild(repo: string, branch: string): Promise<string> {
  const res = await fetch(`${BASE}/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, branch }),
  })
  if (!res.ok) throw new Error(`Failed to start build: ${res.status}`)
  const data = await res.json()
  return data.streamId
}

export async function startDeploy(env: string, service: string, snapshot: string): Promise<string> {
  const res = await fetch(`${BASE}/deploy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ env, service, snapshot }),
  })
  if (!res.ok) throw new Error(`Failed to start deploy: ${res.status}`)
  const data = await res.json()
  return data.streamId
}

export async function startTest(ticketKey: string, envUrl: string): Promise<string> {
  const res = await fetch(`${BASE}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticketKey, envUrl }),
  })
  if (!res.ok) throw new Error(`Failed to start test: ${res.status}`)
  const data = await res.json()
  return data.streamId
}

export async function startQaRun(ticketKey: string, envUrl = ''): Promise<string> {
  const res = await fetch(`${BASE}/qa-run/${ticketKey}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ envUrl }),
  })
  if (!res.ok) throw new Error(`Failed to start QA run: ${res.status}`)
  return (await res.json()).streamId
}

export async function attachToLinear(ticketKey: string): Promise<string> {
  const res = await fetch(`${BASE}/attach/${ticketKey}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to start attach: ${res.status}`)
  return (await res.json()).streamId
}

export interface AutomationState {
  writeAllowed: boolean
  autoMode: { enabled: boolean; armed: boolean }
}

export async function getAutomation(): Promise<AutomationState> {
  const res = await fetch(`${BASE}/automation`)
  if (!res.ok) throw new Error(`getAutomation failed: ${res.status}`)
  return res.json()
}

export async function setAutomation(patch: { enabled?: boolean; armed?: boolean }): Promise<AutomationState> {
  const res = await fetch(`${BASE}/automation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`setAutomation failed: ${res.status}`)
  return res.json()
}

export type EnvInUseError = {
  kind: 'env_in_use'
  env: string
  heldBy: { pipelineId: string; ticketKey: string; stage: string; status: string }
  message: string
}

export async function startPipeline(params: {
  repo: string
  branch: string
  env?: string
  service?: string
  snapshot?: string
  ticketKey: string
  envUrl?: string
}): Promise<{ streamId: string; pipelineId: string }> {
  const res = await fetch(`${BASE}/pipeline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (res.status === 409) {
    const body = await res.json()
    const err = new Error(body.message || 'env in use') as Error & { conflict: EnvInUseError }
    err.conflict = { kind: 'env_in_use', env: body.env, heldBy: body.heldBy, message: body.message }
    throw err
  }
  if (!res.ok) throw new Error(`Failed to start pipeline: ${res.status}`)
  const data = await res.json()
  return { streamId: data.streamId, pipelineId: data.pipelineId }
}

export async function fetchEnvLocks(): Promise<Record<string, {
  pipelineId: string
  ticketKey: string
  stage: string
  status: string
}>> {
  const res = await fetch(`${BASE}/env-locks`)
  if (!res.ok) return {}
  return res.json()
}

export async function releaseEnv(pipelineId: string): Promise<void> {
  await fetch(`${BASE}/release-env`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pipelineId }),
  }).catch(() => {})
}

export async function fetchPipelineStates(): Promise<Record<string, {
  ticketKey: string
  env: string
  stage: string
  status: string
  logs: string[]
  provisionFailures?: number
  provisionBlocked?: boolean
}>> {
  const res = await fetch(`${BASE}/pipeline-states`)
  if (!res.ok) return {}
  return res.json()
}

export async function retryAutoProvision(key: string): Promise<{ streamId: string; ticketKey: string }> {
  const res = await fetch(`${BASE}/auto-provision/retry/${key}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to retry auto-provision: ${res.status}`)
  return res.json()
}

export async function resumePipeline(pipelineId: string): Promise<{ streamId: string; resumedFrom: string }> {
  const res = await fetch(`${BASE}/pipeline/resume/${pipelineId}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to resume: ${res.status}`)
  return res.json()
}

export async function chatSend(message: string, sessionId: string = ''): Promise<{ streamId: string }> {
  const res = await fetch(`${BASE}/chat/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(`Failed to send chat: ${res.status}`)
  return res.json()
}

export async function checkEvidence(key: string, baselineRuns: string[] = []): Promise<{
  found: boolean
  run?: string
  score?: number | null
  time?: string
  reportUrl?: string
  in_progress?: string
  evidence: { status: string; score: number | null; time: string; reportPath: string; reportUrl?: string }
}> {
  const res = await fetch(`${BASE}/check-evidence/${key}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ baseline_runs: baselineRuns }),
  })
  if (!res.ok) throw new Error(`Failed to check evidence: ${res.status}`)
  return res.json()
}

export async function checkDeploy(env: string, services: { service: string; snapshot: string }[]): Promise<{
  allDeployed: boolean
  anyFailed: boolean
  services: {
    service: string
    snapshot: string
    deployed: boolean
    failed: boolean
    failureReason: string
    currentVersion: string
    url: string
    status: string
    buildStatus: string
    scaleCurrent: number
    scaleTarget: number
    buildUrl: string
  }[]
}> {
  const res = await fetch(`${BASE}/check-deploy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ env, services }),
  })
  if (!res.ok) throw new Error(`Failed to check deploy: ${res.status}`)
  return res.json()
}

export async function generateReport(key: string, runName = ''): Promise<{ success: boolean; message: string; reportUrl: string }> {
  const res = await fetch(`${BASE}/generate-report/${key}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_name: runName }),
  })
  if (!res.ok) throw new Error(`Failed to generate report: ${res.status}`)
  return res.json()
}

export async function runCommand(command: string): Promise<{ exit_code: number; output: string[]; error?: string }> {
  const res = await fetch(`${BASE}/run-command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command }),
  })
  if (!res.ok) throw new Error(`Failed to run command: ${res.status}`)
  return res.json()
}

export async function cleanupEnv(env: string, keep: string[] = []): Promise<string> {
  const res = await fetch(`${BASE}/cleanup-env`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ env, keep }),
  })
  if (!res.ok) throw new Error(`Failed to start cleanup: ${res.status}`)
  const data = await res.json()
  if (data.error) throw new Error(data.error)
  return data.streamId
}

export async function fetchHuddle(project: string, notes = ''): Promise<string> {
  const res = await fetch(`${BASE}/huddle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, notes }),
  })
  if (!res.ok) throw new Error(`Failed to fetch huddle: ${res.status}`)
  const data = await res.json()
  return data.text
}

export async function fetch3x3(project: string, notes = ''): Promise<string> {
  const res = await fetch(`${BASE}/3x3`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, notes }),
  })
  if (!res.ok) throw new Error(`Failed to fetch 3x3: ${res.status}`)
  const data = await res.json()
  return data.text
}

export async function getCouncil(pipelineId: string): Promise<{
  councilStatus: CouncilStatus
  councilPayload: CouncilVerdict | null
  councilOverride: CouncilOverride | null
}> {
  const res = await fetch(`${BASE}/council/${pipelineId}`)
  if (!res.ok) throw new Error(`getCouncil failed: ${res.status}`)
  return res.json()
}

export async function overrideCouncil(pipelineId: string, reason: string): Promise<void> {
  const res = await fetch(`${BASE}/council/override/${pipelineId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || `override failed: ${res.status}`)
  }
}

export function subscribeCouncil(
  streamId: string,
  onEvent: (event: any) => void,
  onError: (err: Event) => void = () => {},
): () => void {
  return subscribeSSE(streamId, onEvent as (e: SSEEvent) => void, onError)
}

export async function getTicketUsage(key: string): Promise<TicketUsage> {
  const res = await fetch(`${BASE}/usage/ticket/${key}`)
  if (!res.ok) throw new Error(`getTicketUsage failed: ${res.status}`)
  return res.json()
}

export async function getUsageSummary(): Promise<UsageSummary> {
  const res = await fetch(`${BASE}/usage/summary`)
  if (!res.ok) throw new Error(`getUsageSummary failed: ${res.status}`)
  return res.json()
}

export function subscribeSSE(
  streamId: string,
  onEvent: (event: SSEEvent) => void,
  onError: (err: Event) => void
): () => void {
  let source: EventSource
  let retries = 0
  let cancelled = false
  const MAX_RETRIES = 5

  function connect() {
    source = new EventSource(`${BASE}/stream/${streamId}`)
    source.onmessage = (e) => {
      retries = 0
      const event: SSEEvent = JSON.parse(e.data)
      if (event.type === 'ping') return
      onEvent(event)
    }
    source.onerror = (e) => {
      source.close()
      if (cancelled) return
      if (retries < MAX_RETRIES) {
        retries++
        setTimeout(connect, 1500 * retries)
      } else {
        onError(e)
      }
    }
  }

  connect()
  return () => {
    cancelled = true
    source?.close()
  }
}
