export type AgentName = 'quartermaster' | 'builder' | 'shipper' | 'inspector' | 'scribe'
export type AgentState = 'idle' | 'active' | 'done' | 'failed'

export interface AgentStatus {
  name: AgentName
  state: AgentState
  progress: number // 0-100
  eta: string // "3 min" or ""
  message: string // latest log line
}

export interface Ticket {
  key: string
  summary: string
  status: string
  priority: string
  assignee: string
  qaAssignee: string
  description: string
  flagged: boolean
  staleDays: number
  devInfo: DevInfo[]
  evidence: EvidenceStatus
}

export interface DevInfo {
  repo: string
  branch: string
  prStatus: string
}

export interface EvidenceStatus {
  status: 'none' | 'manifest' | 'tested' | 'published'
  score: number | null
  time: string
  reportPath: string
  reportUrl?: string
  needsReport?: boolean
  latestRun?: string
}

export type CouncilStatus = 'pending' | 'pass' | 'block' | 'overridden' | null

export interface CouncilReviewer {
  name: string
  verdict: 'PASS' | 'BLOCK' | 'ERROR' | 'UNPARSEABLE'
  reason: string
}

export interface CouncilVerdict {
  verdict: 'PASS' | 'BLOCK'
  rationale: string
  reviewers: CouncilReviewer[]
}

export interface CouncilOverride {
  reason: string
  user: string
  at: string
}

export interface Lane {
  id: string
  ticket: Ticket
  agents: Record<AgentName, AgentStatus>
  currentAgent: AgentName
  streamId: string | null
  pipelineId: string | null
  logs: string[]
  startedAt: string
  waitingForEvidence?: boolean
  baselineRuns?: string[]
  qaCommand?: string
  reportUrl?: string
  deployInfo?: { env: string; services: { service: string; snapshot: string }[] }
  env?: string
  connectionLost?: boolean
  councilStreamId?: string
  councilStatus?: CouncilStatus
  councilVerdict?: CouncilVerdict | null
  councilOverride?: CouncilOverride | null
  provisionFailures?: number
  provisionBlocked?: boolean
}

export interface SSEEvent {
  type: 'log' | 'progress' | 'done' | 'error' | 'stage_change' | 'inspector_ready' | 'shipper_ready' | 'ping'
  data?: string
  pct?: number
  eta?: string
  success?: boolean
  msg?: string
  stage?: AgentName
  waiting_for_evidence?: boolean
  waiting_for_deploy?: boolean
  baseline_runs?: string[]
  env?: string
  services?: { service: string; snapshot: string }[]
}
