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
  // Tracker deep-link for this ticket, built server-side from the onboarded
  // issueTracker config (Linear /issue/<KEY>, Jira /browse/<KEY>). "" when no
  // baseUrl is configured.
  url?: string
  summary: string
  status: string
  statusCategory?: 'ready_for_qa' | 'in_qa' | 'other'
  priority: string
  priorityValue?: number
  assignee: string
  qaAssignee: string
  description: string
  flagged: boolean
  staleDays: number
  createdAt?: string
  parent?: { key: string; title: string } | null
  labels?: string[]
  difficulty?: 'Easy' | 'Medium' | 'Hard'
  difficultyScore?: number
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
  claudeCost?: number | null
}

export type CouncilStatus = 'pending' | 'pass' | 'block' | 'overridden' | null

export interface CouncilReviewer {
  name: string
  verdict: 'PASS' | 'BLOCK' | 'ERROR' | 'UNPARSEABLE'
  reason: string
  model?: string | null
  usage?: { cost_usd?: number; input_tokens?: number; output_tokens?: number }
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

export interface TaskUsage {
  task: string
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number
}

export interface TicketUsage {
  ticket: string
  tasks: TaskUsage[]
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
}

export interface UsageBucket {
  cost_usd: number
  input_tokens: number
  output_tokens: number
}

export interface UsageSummary {
  today: UsageBucket
  allTime: UsageBucket
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
  // Emitted by qa_orchestrator.run_and_finalize on the `done` event so the UI
  // can show WHY a run failed / what was skipped, instead of a generic message.
  error?: string | null
  skipped_reason?: string | null
  report_url?: string
  attached?: boolean
}
