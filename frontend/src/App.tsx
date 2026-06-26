import { useState, useEffect, useCallback, useRef } from 'react'
import { Ticket, Lane, AgentName, AgentStatus } from './types'
import { fetchTickets, startPipeline, fetchDevInfo, subscribeSSE, fetchPipelineStates, resumePipeline, checkEvidence, checkDeploy, runCommand, releaseEnv, fetchEnvLocks, generateReport, fetchEvidenceHistory, EvidenceHistoryItem, subscribeCouncil, overrideCouncil, retryAutoProvision, getOnboardingStatus, startQaRun, attachToLinear, getAutomation, setAutomation } from './api'
import type { EnvInUseError } from './api'
import { loadLanes, dumpLanes, reconcileLanesWithBackend } from './laneSchema'

type EnvLocks = Record<string, { pipelineId: string; ticketKey: string; stage: string; status: string }>
import TopBar from './components/TopBar'
import ActiveLanes from './components/ActiveLanes'
import Queue from './components/Queue'
import DoneToday from './components/DoneToday'
import EvidenceHistory from './components/EvidenceHistory'
import HuddleModal from './components/HuddleModal'
import ThreeByThreeModal from './components/ThreeByThreeModal'
import CleanupEnvModal from './components/CleanupEnvModal'
import Toast from './components/Toast'
import ChatPanel from './components/ChatPanel'
import Settings from './components/Settings'

const POLL_INTERVAL = 60_000

function makeAgentStatuses(): Record<AgentName, AgentStatus> {
  return {
    quartermaster: { name: 'quartermaster', state: 'idle', progress: 0, eta: '', message: '' },
    builder: { name: 'builder', state: 'idle', progress: 0, eta: '', message: '' },
    shipper: { name: 'shipper', state: 'idle', progress: 0, eta: '', message: '' },
    inspector: { name: 'inspector', state: 'idle', progress: 0, eta: '', message: '' },
    scribe: { name: 'scribe', state: 'idle', progress: 0, eta: '', message: '' },
  }
}

export default function App() {
  const [project, setProject] = useState(
    () => localStorage.getItem('qa-dash-project') || 'PROJ'
  )
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('qa-dash-theme') as 'dark' | 'light') || 'dark'
  )
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [lanes, setLanes] = useState<Lane[]>(() => loadLanes(localStorage.getItem('qa-dash-lanes')))
  const today = new Date().toISOString().split('T')[0]
  const [doneToday, _setDoneToday] = useState<{ ticket: Ticket; score: number | null; time: string }[]>(() => {
    try {
      const saved = localStorage.getItem(`qa-dash-done-${today}`)
      return saved ? JSON.parse(saved) : []
    } catch { return [] }
  })
  const [showHuddle, setShowHuddle] = useState(false)
  const [show3x3, setShow3x3] = useState(false)
  const [showCleanup, setShowCleanup] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [lastRefresh, setLastRefresh] = useState('')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [environments, setEnvironments] = useState<string[]>([])
  const [projects, setProjects] = useState<string[]>([])
  // Already-deployed apps (static/local/deployed modes) skip build & deploy — only
  // 'script' mode needs them. Drives which stages the lane shows.
  const [needsBuildDeploy, setNeedsBuildDeploy] = useState(true)
  // Ref mirror so the start callbacks read the current value (not a stale closure).
  const needsBuildDeployRef = useRef(true)
  useEffect(() => { needsBuildDeployRef.current = needsBuildDeploy }, [needsBuildDeploy])
  const [writeAllowed, setWriteAllowed] = useState(false)
  const [autoMode, setAutoMode] = useState<{ enabled: boolean; armed: boolean }>({ enabled: false, armed: false })
  useEffect(() => {
    getAutomation().then(a => { setWriteAllowed(a.writeAllowed); setAutoMode(a.autoMode) }).catch(() => {})
  }, [])
  const handleToggleAutoMode = useCallback(async (enabled: boolean) => {
    const a = await setAutomation({ enabled }); setAutoMode(a.autoMode)
  }, [])
  const handleToggleArm = useCallback(async (armed: boolean) => {
    const a = await setAutomation({ armed }); setAutoMode(a.autoMode)
  }, [])
  const [envLocks, setEnvLocks] = useState<EnvLocks>({})
  const [pipelineStates, setPipelineStates] = useState<Record<string, {
    ticketKey: string
    env: string
    stage: string
    status: string
    logs: string[]
    provisionFailures?: number
    provisionBlocked?: boolean
  }>>({})
  const [evidenceHistory, setEvidenceHistory] = useState<EvidenceHistoryItem[]>([])
  const sseCleanups = useRef<Record<string, () => void>>({})

  // Theme sync
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('qa-dash-theme', theme)
  }, [theme])

  useEffect(() => {
    localStorage.setItem('qa-dash-project', project)
  }, [project])

  // Persist lanes and done-today
  useEffect(() => {
    localStorage.setItem('qa-dash-lanes', dumpLanes(lanes))
  }, [lanes])

  useEffect(() => {
    localStorage.setItem(`qa-dash-done-${today}`, JSON.stringify(doneToday))
  }, [doneToday, today])

  // Load environments + projects from backend. Backend is the source of
  // truth so the dropdown can't drift if config.py changes.
  useEffect(() => {
    fetch('/api/environments').then(r => r.json()).then(setEnvironments).catch(() => {})
    fetch('/api/projects')
      .then(r => r.json())
      .then(d => setProjects(d.projects || []))
      .catch(() => {})
    getOnboardingStatus()
      .then(s => setNeedsBuildDeploy(s.envMode === 'script'))
      .catch(() => {})
  }, [])

  // Poll env locks so the picker can grey out envs already held by another
  // pipeline — avoids the wasted-click of submitting and getting a 409 back.
  // Also refresh pipeline-states so the Queue can show the provision-blocked
  // badge keyed by ticket.
  useEffect(() => {
    const poll = () => {
      fetchEnvLocks().then(setEnvLocks).catch(() => {})
      fetchPipelineStates().then(setPipelineStates).catch(() => {})
    }
    poll()
    const handle = setInterval(poll, 10000)
    return () => clearInterval(handle)
  }, [])

  // Load evidence history (all-time, from ~/evidence/)
  const loadEvidenceHistory = useCallback(() => {
    fetchEvidenceHistory().then(setEvidenceHistory).catch(() => {})
  }, [])

  useEffect(() => {
    loadEvidenceHistory()
    const h = setInterval(loadEvidenceHistory, 30_000)
    return () => clearInterval(h)
  }, [loadEvidenceHistory])

  // Polling
  const loadTickets = useCallback(async () => {
    setIsRefreshing(true)
    try {
      const data = await fetchTickets(project)
      setTickets(data)
      setLastRefresh(new Date().toLocaleTimeString())
    } catch (err) {
      console.error('Failed to load tickets:', err)
    } finally {
      setIsRefreshing(false)
    }
  }, [project])

  useEffect(() => {
    loadTickets()
    const interval = setInterval(loadTickets, POLL_INTERVAL)
    return () => clearInterval(interval)
  }, [loadTickets])

  // Resume running pipelines after page load
  const resumeAttempted = useRef(false)
  useEffect(() => {
    if (resumeAttempted.current) return
    resumeAttempted.current = true

    // Backend pipeline-states is authoritative. We rehydrate two ways:
    //   1. Reconcile existing localStorage lanes against the backend (stage/env).
    //   2. Adopt any *running* backend pipeline that has no matching local lane
    //      so users who refresh in a fresh tab / cleared storage / different
    //      browser still see their work-in-progress.
    fetchPipelineStates().then(states => {
      const runningEntries = Object.entries(states).filter(([, s]) => s.status === 'running')

      setLanes(prev => {
        const reconciled = reconcileLanesWithBackend(prev, states)
        const adopted: Lane[] = []
        for (const [pipelineId, s] of runningEntries) {
          const already = reconciled.some(l =>
            l.pipelineId === pipelineId || l.ticket.key === s.ticketKey
          )
          if (already) continue
          const stage = (['quartermaster','builder','shipper','inspector','scribe'] as AgentName[]).includes(s.stage as AgentName)
            ? (s.stage as AgentName) : 'builder'
          const agents = makeAgentStatuses()
          agents[stage].state = 'active'
          const known = tickets.find(t => t.key === s.ticketKey)
          const ticket: Ticket = known ?? {
            key: s.ticketKey,
            summary: s.ticketKey,
            status: 'In Progress',
            priority: '',
            assignee: '',
            qaAssignee: '',
            description: '',
            flagged: false,
            staleDays: 0,
            devInfo: [],
            evidence: { status: 'none', score: null, time: '', reportPath: '' },
          }
          adopted.push({
            id: `lane-adopted-${pipelineId}`,
            ticket,
            agents,
            currentAgent: stage,
            streamId: null,
            pipelineId,
            env: s.env,
            logs: [],
            startedAt: new Date().toISOString(),
          })
        }
        return adopted.length ? [...reconciled, ...adopted] : reconciled
      })

      // Resume every running pipeline — both pre-existing lanes (reconciled)
      // and ones we just adopted. Resolve lane id/ticket by pipelineId or
      // ticketKey against the snapshot we have. The synthetic id matches the
      // one we used above for adoption, so SSE updates land on the same lane.
      if (runningEntries.length === 0) return Promise.resolve()
      for (const [pipelineId, s] of runningEntries) {
        const existing = lanes.find(l => l.pipelineId === pipelineId || l.ticket.key === s.ticketKey)
        const known = tickets.find(t => t.key === s.ticketKey)
        const lane = existing ?? {
          id: `lane-adopted-${pipelineId}`,
          ticket: known ?? ({
            key: s.ticketKey,
            summary: s.ticketKey,
            status: 'In Progress',
            priority: '', assignee: '', qaAssignee: '', description: '',
            flagged: false, staleDays: 0, devInfo: [],
            evidence: { status: 'none', score: null, time: '', reportPath: '' },
          } as Ticket),
        }
        {
          // Resume and resubscribe to SSE
          resumePipeline(pipelineId).then(({ streamId, resumedFrom }) => {
            // Record the pipelineId so handleCancel can release the env lock.
            setLanes(prev => prev.map(l => l.id === lane.id ? { ...l, pipelineId, streamId } : l))
            laneCurrentAgent.current[lane.id] = resumedFrom as AgentName
            const cleanup = subscribeSSE(
              streamId,
              (event) => {
                const currentAgent = laneCurrentAgent.current[lane.id] || 'builder'
                if (event.type === 'stage_change' && event.stage) {
                  updateLaneAgent(lane.id, currentAgent, { state: 'done', progress: 100 })
                  laneCurrentAgent.current[lane.id] = event.stage
                  updateLaneAgent(lane.id, event.stage, { state: 'active', progress: 0, message: '' })
                  setLanes(prev => prev.map(l => l.id === lane.id ? { ...l, currentAgent: event.stage! } : l))
                } else if (event.type === 'progress') {
                  updateLaneAgent(lane.id, currentAgent, { progress: event.pct ?? 0, eta: event.eta ?? '' })
                } else if (event.type === 'log') {
                  updateLaneAgent(lane.id, currentAgent, { message: event.data ?? '' })
                  appendLog(lane.id, event.data ?? '')
                } else if (event.type === 'shipper_ready') {
                  setLanes(prev => prev.map(l => l.id === lane.id ? {
                    ...l,
                    deployInfo: { env: event.env ?? '', services: event.services ?? [] },
                  } : l))
                } else if (event.type === 'inspector_ready') {
                  setLanes(prev => prev.map(l => l.id === lane.id ? {
                    ...l,
                    qaCommand: event.data ?? '',
                    baselineRuns: event.baseline_runs ?? [],
                  } : l))
                } else if (event.type === 'done') {
                  if (event.success && event.waiting_for_deploy) {
                    updateLaneAgent(lane.id, currentAgent, {
                      state: 'active',
                      progress: 10,
                      eta: '~20 min',
                      message: 'Deploys triggered. Click "Check Deploy" when ready.',
                    })
                  } else if (event.success && event.waiting_for_evidence) {
                    setLanes(prev => prev.map(l => l.id === lane.id ? {
                      ...l,
                      waitingForEvidence: true,
                    } : l))
                    updateLaneAgent(lane.id, currentAgent, {
                      state: 'active',
                      progress: 10,
                      eta: 'waiting for test run',
                      message: 'Click "Check Evidence" when tests are complete',
                    })
                  } else if (event.success) {
                    updateLaneAgent(lane.id, currentAgent, { state: 'done', progress: 100 })
                    const ev = event as unknown as { evidence?: { score?: number | null } }
                    _setDoneToday(prev => [...prev, {
                      ticket: lane.ticket,
                      score: ev.evidence?.score ?? null,
                      time: new Date().toLocaleTimeString(),
                    }])
                  } else {
                    updateLaneAgent(lane.id, currentAgent, { state: 'failed', message: event.msg ?? 'Failed' })
                  }
                }
              },
              () => {}
            )
            sseCleanups.current[lane.id] = cleanup
          }).catch(() => {
            // Resume failed — pipeline state is stale, just show last known state
          })
        }
      }
    }).catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Track current agent per lane for SSE event routing
  const laneCurrentAgent = useRef<Record<string, AgentName>>({})

  // Start ticket in a lane — runs full pipeline (build → deploy → test → evidence)
  const handleStart = useCallback(async (ticket: Ticket, env = '') => {
    if (lanes.length >= 3) return

    const laneId = `lane-${Date.now()}`
    const agents = makeAgentStatuses()
    // Already-deployed apps skip build/deploy — the lane starts at the test stage.
    const firstAgent: AgentName = needsBuildDeployRef.current ? 'builder' : 'inspector'
    agents[firstAgent].state = 'active'

    const newLane: Lane = {
      id: laneId,
      ticket,
      agents,
      currentAgent: firstAgent,
      streamId: null,
      pipelineId: null,
      env,
      logs: [],
      startedAt: new Date().toISOString(),
    }
    setLanes(prev => [...prev, newLane])
    laneCurrentAgent.current[laneId] = firstAgent

    try {
      // Fetch dev info on-demand for this ticket
      let devInfo = ticket.devInfo
      if (devInfo.length === 0) {
        try {
          devInfo = await fetchDevInfo(ticket.key)
        } catch {
          // Ignore — will fail gracefully below
        }
      }

      const repo = devInfo[0]?.repo || ''
      const branch = devInfo[0]?.branch || ''
      // repo/branch are only needed to build & deploy. Already-deployed apps test an
      // existing env, so missing dev-info is fine — the backend goes straight to test.
      if ((!repo || !branch) && needsBuildDeployRef.current) {
        updateLaneAgent(laneId, 'builder', { state: 'failed', message: 'No repo/branch info — set dev info in Jira' })
        return
      }

      // Compute snapshot name (branch uppercase, slashes to dashes)
      const snapshot = branch.toUpperCase().replace(/\//g, '-')

      // Start the full pipeline
      let streamId: string
      let pipelineId: string
      try {
        const result = await startPipeline({
          repo,
          branch,
          env,
          service: repo,
          snapshot,
          ticketKey: ticket.key,
          envUrl: '',
        })
        streamId = result.streamId
        pipelineId = result.pipelineId
      } catch (err) {
        const conflict = (err as Error & { conflict?: EnvInUseError }).conflict
        if (conflict) {
          updateLaneAgent(laneId, 'builder', {
            state: 'failed',
            message: `${env} is in use by ${conflict.heldBy.ticketKey || conflict.heldBy.pipelineId} (${conflict.heldBy.stage || 'running'}) — pick another env or dismiss that lane.`,
          })
        } else {
          updateLaneAgent(laneId, 'builder', { state: 'failed', message: `Pipeline start failed: ${err}` })
        }
        return
      }
      setLanes(prev => prev.map(l => l.id === laneId ? { ...l, streamId, pipelineId } : l))

      // Subscribe to SSE — single stream handles all 4 stages
      const cleanup = subscribeSSE(
        streamId,
        (event) => {
          const currentAgent = laneCurrentAgent.current[laneId] || 'builder'

          if (event.type === 'stage_change' && event.stage) {
            // Mark previous agent as done, activate new one
            updateLaneAgent(laneId, currentAgent, { state: 'done', progress: 100 })
            laneCurrentAgent.current[laneId] = event.stage
            updateLaneAgent(laneId, event.stage, { state: 'active', progress: 0, message: '' })
            setLanes(prev => prev.map(l => l.id === laneId ? { ...l, currentAgent: event.stage! } : l))
          } else if (event.type === 'progress') {
            updateLaneAgent(laneId, currentAgent, {
              progress: event.pct ?? 0,
              eta: event.eta ?? '',
            })
          } else if (event.type === 'log') {
            updateLaneAgent(laneId, currentAgent, { message: event.data ?? '' })
            appendLog(laneId, event.data ?? '')
          } else if (event.type === 'shipper_ready') {
            // Shipper triggered deploys — store info for recheck
            setLanes(prev => prev.map(l => l.id === laneId ? {
              ...l,
              deployInfo: { env: event.env ?? '', services: event.services ?? [] },
            } : l))
          } else if (event.type === 'inspector_ready') {
            // Inspector emitted the QA command and baseline — store for retry
            setLanes(prev => prev.map(l => l.id === laneId ? {
              ...l,
              qaCommand: event.data ?? '',
              baselineRuns: event.baseline_runs ?? [],
            } : l))
          } else if (event.type === 'done') {
            if (event.success && event.waiting_for_deploy) {
              // Shipper triggered deploys — show recheck button
              updateLaneAgent(laneId, currentAgent, {
                state: 'active',
                progress: 10,
                eta: '~20 min',
                message: 'Deploys triggered. Click "Check Deploy" when ready.',
              })
            } else if (event.success && event.waiting_for_evidence) {
              // Inspector finished but evidence not yet collected — show retry button
              setLanes(prev => prev.map(l => l.id === laneId ? {
                ...l,
                waitingForEvidence: true,
              } : l))
              updateLaneAgent(laneId, currentAgent, {
                state: 'active',
                progress: 10,
                eta: 'waiting for test run',
                message: 'Click "Check Evidence" when tests are complete',
              })
            } else if (event.success) {
              updateLaneAgent(laneId, currentAgent, { state: 'done', progress: 100 })
              // Pipeline complete — move to done today
              const ev = event as unknown as { evidence?: { score?: number | null } }
              _setDoneToday(prev => [...prev, {
                ticket,
                score: ev.evidence?.score ?? null,
                time: new Date().toLocaleTimeString(),
              }])
            } else {
              updateLaneAgent(laneId, currentAgent, { state: 'failed', message: event.msg ?? 'Failed' })
            }
          }
        },
        () => {
          const currentAgent = laneCurrentAgent.current[laneId] || 'builder'
          updateLaneAgent(laneId, currentAgent, { state: 'failed', message: 'Connection lost' })
          setLanes(prev => prev.map(l => l.id === laneId ? { ...l, connectionLost: true } : l))
        }
      )
      sseCleanups.current[laneId] = cleanup
    } catch (err) {
      updateLaneAgent(laneId, 'builder', { state: 'failed', message: String(err) })
    }
  }, [lanes])

  const handleRunQa = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return
    laneCurrentAgent.current[laneId] = 'inspector'
    updateLaneAgent(laneId, 'inspector', { state: 'active', progress: 10, message: 'Running QA server-side…' })
    try {
      const streamId = await startQaRun(lane.ticket.key, lane.env || '')
      const cleanup = subscribeSSE(streamId, (event) => {
        if (event.type === 'log') { appendLog(laneId, event.data ?? ''); updateLaneAgent(laneId, 'inspector', { message: event.data ?? '' }) }
        else if (event.type === 'progress') updateLaneAgent(laneId, 'inspector', { progress: event.pct ?? 0, eta: event.eta ?? '' })
        else if (event.type === 'done') {
          const d = event as unknown as { success?: boolean; report_url?: string }
          if (d.success) {
            updateLaneAgent(laneId, 'inspector', { state: 'done', progress: 100, message: 'QA complete' })
            setLanes(prev => prev.map(l => l.id === laneId ? { ...l, reportUrl: d.report_url || l.reportUrl } : l))
          } else {
            updateLaneAgent(laneId, 'inspector', { state: 'failed', message: 'QA run failed — see log' })
          }
        }
      }, () => updateLaneAgent(laneId, 'inspector', { state: 'failed', message: 'Connection lost' }))
      sseCleanups.current[laneId] = cleanup
    } catch (err) {
      updateLaneAgent(laneId, 'inspector', { state: 'failed', message: String(err) })
    }
  }, [lanes])

  const handleAttachLinear = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return
    try {
      const streamId = await attachToLinear(lane.ticket.key)
      subscribeSSE(streamId, (event) => {
        if (event.type === 'log') appendLog(laneId, event.data ?? '')
        else if (event.type === 'done') {
          const d = event as unknown as { attached?: boolean; skipped_reason?: string }
          appendLog(laneId, d.attached ? 'Attached to Linear ✓' : `Not attached: ${d.skipped_reason || 'error'}`)
        }
      }, () => {})
    } catch (err) {
      appendLog(laneId, `Attach failed: ${err}`)
    }
  }, [lanes])

  const appendLog = (laneId: string, line: string) => {
    setLanes(prev => prev.map(lane => {
      if (lane.id !== laneId) return lane
      return { ...lane, logs: [...lane.logs.slice(-200), line] } // keep last 200 lines
    }))
  }

  const updateLaneAgent = (laneId: string, agentName: AgentName, updates: Partial<AgentStatus>) => {
    setLanes(prev => prev.map(lane => {
      if (lane.id !== laneId) return lane
      return {
        ...lane,
        agents: {
          ...lane.agents,
          [agentName]: { ...lane.agents[agentName], ...updates },
        },
      }
    }))
  }

  const handleCheckDeploy = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return

    // Use stored deployInfo, or derive from ticket's dev info / API
    let env = lane.deployInfo?.env ?? ''
    let services = lane.deployInfo?.services ?? []

    if (services.length === 0) {
      // Try ticket devInfo first
      let devInfo = lane.ticket.devInfo ?? []
      // If empty, fetch from API
      if (devInfo.length === 0) {
        try {
          devInfo = await fetchDevInfo(lane.ticket.key)
        } catch { /* ignore */ }
      }
      if (devInfo.length > 0) {
        // Drop PRs whose branch doesn't reference this ticket — Jira dev-info
        // sometimes links unrelated PRs.
        const key = lane.ticket.key.toUpperCase()
        const relevant = devInfo.filter(pr => pr.branch.toUpperCase().includes(key))
        const useList = relevant.length > 0 ? relevant : devInfo
        if (relevant.length < devInfo.length) {
          appendLog(laneId, `[Shipper] Ignoring ${devInfo.length - relevant.length} unrelated PR(s) not referencing ${lane.ticket.key}`)
        }
        services = useList.map(pr => ({
          service: pr.repo.includes('/') ? pr.repo.split('/').pop()! : pr.repo,
          snapshot: pr.branch.toUpperCase().replace(/\//g, '-'),
        }))
      }
    }

    // Try to get env from logs
    if (!env) {
      for (const line of lane.logs) {
        const m = line.match(/(qa-env(?:-\d)?)/)
        if (m) { env = m[1]; break }
      }
    }

    if (!env || services.length === 0) {
      updateLaneAgent(laneId, 'shipper', { message: 'No deploy info — use command input below' })
      return
    }

    updateLaneAgent(laneId, 'shipper', { state: 'active', message: 'Checking deploy status...', progress: 50, eta: 'checking' })

    try {
      const result = await checkDeploy(env, services)
      for (const svc of result.services) {
        const scale = `${svc.scaleCurrent}/${svc.scaleTarget}`
        let status: string
        if (svc.deployed) {
          status = `LIVE (${svc.status} ${scale})`
        } else if (svc.failed) {
          status = `FAILED (${svc.status} build=${svc.buildStatus} scale=${scale})`
        } else {
          status = `pending (${svc.currentVersion || 'not found'}${svc.status ? ` ${svc.status} ${scale}` : ''})`
        }
        appendLog(laneId, `  ${svc.service}: ${status}`)
        if (svc.failed && svc.failureReason) {
          appendLog(laneId, `    Reason: ${svc.failureReason}`)
        }
        if (svc.failed && svc.buildUrl) {
          appendLog(laneId, `    Build log: ${svc.buildUrl}`)
        }
      }

      if (result.anyFailed) {
        const failed = result.services.filter(s => s.failed)
        appendLog(laneId, `[Shipper] Deploy FAILED on ${failed.length} service(s). Possible missing dependency — check the build log above, fix, and redeploy.`)
        updateLaneAgent(laneId, 'shipper', {
          state: 'failed',
          message: `Deploy failed: ${failed.map(s => `${s.service} (${s.buildStatus || s.status})`).join(', ')}`,
          eta: 'check build log',
          progress: 50,
        })
      } else if (result.allDeployed) {
        appendLog(laneId, '[Shipper] All deploys confirmed!')
        updateLaneAgent(laneId, 'shipper', { state: 'done', progress: 100, message: 'All deployed!' })
        // Get env URL from first deployed service
        const urlSvc = result.services.find(s => s.url)
        // Advance to inspector
        laneCurrentAgent.current[laneId] = 'inspector'
        updateLaneAgent(laneId, 'inspector', {
          state: 'active',
          progress: 10,
          eta: 'waiting for test run',
          message: 'Click "Check Evidence" when tests are complete',
        })
        const envUrl = urlSvc?.url ?? ''
        const qaCmd = `/qa-evidence ${lane.ticket.key} run:qa-feature env:${envUrl} --headless --auto-approve`
        setLanes(prev => prev.map(l => l.id === laneId ? {
          ...l,
          currentAgent: 'inspector' as const,
          waitingForEvidence: true,
          qaCommand: qaCmd,
        } : l))
        appendLog(laneId, '')
        appendLog(laneId, 'Paste this in Claude Code:')
        appendLog(laneId, `  ${qaCmd}`)
      } else {
        const pending = result.services.filter(s => !s.deployed)
        updateLaneAgent(laneId, 'shipper', {
          state: 'active',
          message: `${pending.length} service(s) still deploying`,
          eta: 'retry in a few min',
          progress: 30,
        })
      }
    } catch (err) {
      updateLaneAgent(laneId, 'shipper', { state: 'active', message: `Check failed: ${err}`, eta: 'click to retry' })
    }
  }, [lanes])

  const handleRunCommand = useCallback(async (laneId: string, command: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return

    appendLog(laneId, `$ ${command}`)
    try {
      const result = await runCommand(command)
      if (result.error) {
        appendLog(laneId, `Error: ${result.error}`)
      } else {
        for (const line of result.output) {
          appendLog(laneId, line)
        }
        appendLog(laneId, `Exit code: ${result.exit_code}`)
      }
    } catch (err) {
      appendLog(laneId, `Command failed: ${err}`)
    }
  }, [lanes])

  const handleCheckEvidence = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return

    // Reset inspector to active if it was failed (e.g. from timeout)
    updateLaneAgent(laneId, 'inspector', { state: 'active', message: 'Checking for evidence...', progress: 10, eta: 'checking' })

    try {
      const result = await checkEvidence(lane.ticket.key, lane.baselineRuns ?? [])

      if (result.found) {
        // Evidence found — advance to scribe, keep lane visible for review
        const runPath = `~/evidence/${lane.ticket.key}/runs/${result.run}`
        const reportUrl = result.reportUrl || result.evidence?.reportUrl || ''
        appendLog(laneId, `Evidence found: ${result.run}`)
        appendLog(laneId, `Score: ${result.score ?? 'N/A'}`)
        appendLog(laneId, `Path: ${runPath}`)
        if (reportUrl) appendLog(laneId, `Report: ${reportUrl}`)
        updateLaneAgent(laneId, 'inspector', { state: 'done', progress: 100, message: `Evidence: ${result.run}` })

        const r = result as typeof result & { awaitingCouncil?: boolean; councilStreamId?: string }
        if (r.awaitingCouncil && r.councilStreamId) {
          // Hand off to the council: keep Scribe "active" until verdict arrives.
          laneCurrentAgent.current[laneId] = 'scribe'
          updateLaneAgent(laneId, 'scribe', { state: 'active', progress: 30, message: 'Council reviewing…' })
          setLanes(prev => prev.map(l => l.id === laneId ? {
            ...l,
            currentAgent: 'scribe',
            waitingForEvidence: false,
            reportUrl: reportUrl || l.reportUrl,
            councilStreamId: r.councilStreamId,
            councilStatus: 'pending',
          } : l))
          _subscribeCouncil(laneId, r.councilStreamId!)
        } else {
          // Legacy / no-council path (no pipeline-id match): complete as before.
          laneCurrentAgent.current[laneId] = 'scribe'
          updateLaneAgent(laneId, 'scribe', { state: 'done', progress: 100, message: result.score != null ? `Score: ${result.score}` : 'Evidence collected' })
          setLanes(prev => prev.map(l => l.id === laneId ? {
            ...l,
            currentAgent: 'scribe',
            waitingForEvidence: false,
            reportUrl: reportUrl || l.reportUrl,
          } : l))
          // Add to done today but keep lane visible — user dismisses with Cancel
          _setDoneToday(prev => [...prev, {
            ticket: lane.ticket,
            score: result.score ?? null,
            time: new Date().toLocaleTimeString(),
          }])
          loadEvidenceHistory()
        }
      } else if (result.in_progress) {
        updateLaneAgent(laneId, 'inspector', {
          message: `Test running: ${result.in_progress}`,
          eta: 'testing in progress',
          progress: 50,
        })
        appendLog(laneId, `Test in progress: ${result.in_progress}`)
      } else {
        updateLaneAgent(laneId, 'inspector', {
          message: 'No new evidence found yet',
          eta: 'waiting for test run',
        })
        appendLog(laneId, 'No new evidence found')
      }
    } catch (err) {
      updateLaneAgent(laneId, 'inspector', { message: `Check failed: ${err}` })
    }
  }, [lanes, loadEvidenceHistory])

  // Passive evidence poll — auto-check every 60s while any lane is waiting.
  useEffect(() => {
    const waiting = lanes.filter(l => l.waitingForEvidence)
    if (waiting.length === 0) return
    const handle = setInterval(() => {
      for (const lane of waiting) {
        handleCheckEvidence(lane.id)
      }
    }, 60_000)
    return () => clearInterval(handle)
  }, [lanes, handleCheckEvidence])

  const _subscribeCouncil = useCallback((laneId: string, streamId: string) => {
    subscribeCouncil(streamId, (event) => {
      if (event.type === 'reviewer_started') {
        appendLog(laneId, `Council: ${event.reviewer} started`)
      } else if (event.type === 'reviewer_done') {
        appendLog(laneId, `Council: ${event.reviewer} → ${event.verdict}${event.reason ? ` (${event.reason})` : ''}`)
      } else if (event.type === 'verdict') {
        const verdict = { verdict: event.verdict, rationale: event.rationale, reviewers: event.reviewers }
        setLanes(prev => prev.map(l => l.id === laneId ? {
          ...l,
          councilStatus: event.verdict === 'PASS' ? 'pass' : 'block',
          councilVerdict: verdict,
        } : l))
        if (event.verdict === 'PASS') {
          updateLaneAgent(laneId, 'scribe', { state: 'done', progress: 100, message: 'Council PASS' })
          const lane = lanes.find(l => l.id === laneId)
          if (lane) {
            _setDoneToday(prev => [...prev, { ticket: lane.ticket, score: null, time: new Date().toLocaleTimeString() }])
          }
          loadEvidenceHistory()
        } else {
          updateLaneAgent(laneId, 'scribe', { state: 'active', message: `BLOCKED: ${event.rationale}` })
        }
      }
    })
  }, [lanes, loadEvidenceHistory])

  const handleOverrideCouncil = useCallback(async (laneId: string, reason: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane?.pipelineId) return
    await overrideCouncil(lane.pipelineId, reason)
    setLanes(prev => prev.map(l => l.id === laneId ? {
      ...l,
      councilStatus: 'overridden',
      councilOverride: { reason, user: 'you', at: new Date().toISOString() },
    } : l))
    updateLaneAgent(laneId, 'scribe', { state: 'done', progress: 100, message: `Overridden: ${reason}` })
  }, [lanes])

  // Reconnect to council SSE for lanes that are still pending after reload.
  useEffect(() => {
    for (const lane of lanes) {
      if (lane.pipelineId && lane.councilStatus === 'pending' && lane.councilStreamId) {
        _subscribeCouncil(lane.id, lane.councilStreamId)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lanes.length])

  const handleGenerateReport = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return
    appendLog(laneId, 'Generating HTML report…')
    updateLaneAgent(laneId, 'scribe', { message: 'Generating report…', state: 'active' })
    try {
      const result = await generateReport(lane.ticket.key)
      if (result.success) {
        appendLog(laneId, `Report ready: ${result.message}`)
        setLanes(prev => prev.map(l => l.id === laneId ? { ...l, reportUrl: result.reportUrl } : l))
        updateLaneAgent(laneId, 'scribe', { state: 'done', message: result.message })
        loadEvidenceHistory()
      } else {
        appendLog(laneId, `Report generation failed: ${result.message}`)
        updateLaneAgent(laneId, 'scribe', { state: 'failed', message: result.message })
      }
    } catch (err) {
      appendLog(laneId, `Generate report failed: ${err}`)
      updateLaneAgent(laneId, 'scribe', { state: 'failed', message: `${err}` })
    }
  }, [lanes, loadEvidenceHistory])

  const handleResume = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane?.pipelineId) return
    try {
      setLanes(prev => prev.map(l => l.id === laneId ? { ...l, connectionLost: false } : l))
      const { streamId, resumedFrom } = await resumePipeline(lane.pipelineId)
      setLanes(prev => prev.map(l => l.id === laneId ? { ...l, streamId } : l))
      laneCurrentAgent.current[laneId] = resumedFrom as AgentName
      updateLaneAgent(laneId, resumedFrom as AgentName, { state: 'active', message: 'Reconnected' })
      const cleanup = subscribeSSE(
        streamId,
        (event) => {
          const currentAgent = laneCurrentAgent.current[laneId] || 'builder'
          if (event.type === 'stage_change' && event.stage) {
            updateLaneAgent(laneId, currentAgent, { state: 'done', progress: 100 })
            laneCurrentAgent.current[laneId] = event.stage
            updateLaneAgent(laneId, event.stage, { state: 'active', progress: 0, message: '' })
            setLanes(prev => prev.map(l => l.id === laneId ? { ...l, currentAgent: event.stage! } : l))
          } else if (event.type === 'progress') {
            updateLaneAgent(laneId, currentAgent, { progress: event.pct ?? 0, eta: event.eta ?? '' })
          } else if (event.type === 'log') {
            updateLaneAgent(laneId, currentAgent, { message: event.data ?? '' })
            appendLog(laneId, event.data ?? '')
          } else if (event.type === 'done') {
            if (event.success) {
              updateLaneAgent(laneId, currentAgent, { state: 'done', progress: 100 })
            } else {
              updateLaneAgent(laneId, currentAgent, { state: 'failed', message: event.msg ?? 'Failed' })
            }
          }
        },
        () => {
          updateLaneAgent(laneId, laneCurrentAgent.current[laneId] || 'builder', { state: 'failed', message: 'Connection lost' })
          setLanes(prev => prev.map(l => l.id === laneId ? { ...l, connectionLost: true } : l))
        }
      )
      sseCleanups.current[laneId] = cleanup
    } catch {
      updateLaneAgent(laneId, laneCurrentAgent.current[laneId] || 'builder', { state: 'failed', message: 'Resume failed — pipeline may have ended' })
    }
  }, [lanes])

  const handleCancel = useCallback((laneId: string) => {
    if (sseCleanups.current[laneId]) {
      sseCleanups.current[laneId]()
      delete sseCleanups.current[laneId]
    }
    // Best-effort: free the env so another lane can grab it. Backend release
    // is idempotent; if the pipeline already completed and self-released,
    // this is a no-op.
    const lane = lanes.find(l => l.id === laneId)
    if (lane?.pipelineId) {
      releaseEnv(lane.pipelineId)
    }
    setLanes(prev => prev.filter(l => l.id !== laneId))
  }, [lanes])

  // Transition an existing Quartermaster lane into the Builder stage and start the pipeline.
  // This avoids creating a duplicate lane — the QM lane already exists with lane.env provisioned.
  const handleStartFromQuartermaster = useCallback(async (lane: Lane) => {
    if (lane.pipelineId) return
    const laneId = lane.id
    // Mark quartermaster done (should already be), activate builder
    setLanes(prev => prev.map(l => {
      if (l.id !== laneId) return l
      return {
        ...l,
        currentAgent: 'builder' as const,
        agents: {
          ...l.agents,
          quartermaster: { ...l.agents.quartermaster, state: 'done', progress: 100 },
          builder: { ...l.agents.builder, state: 'active', progress: 0, message: '' },
        },
      }
    }))
    laneCurrentAgent.current[laneId] = 'builder'

    const env = lane.env || ''
    const ticket = lane.ticket

    try {
      let devInfo = ticket.devInfo
      if (devInfo.length === 0) {
        try {
          devInfo = await fetchDevInfo(ticket.key)
        } catch {
          // Ignore — will fail gracefully below
        }
      }

      const repo = devInfo[0]?.repo || ''
      const branch = devInfo[0]?.branch || ''
      // repo/branch are only needed to build & deploy. Already-deployed apps test an
      // existing env, so missing dev-info is fine — the backend goes straight to test.
      if ((!repo || !branch) && needsBuildDeployRef.current) {
        updateLaneAgent(laneId, 'builder', { state: 'failed', message: 'No repo/branch info — set dev info in Jira' })
        return
      }

      const snapshot = branch.toUpperCase().replace(/\//g, '-')

      let streamId: string
      let pipelineId: string
      try {
        const result = await startPipeline({
          repo,
          branch,
          env,
          service: repo,
          snapshot,
          ticketKey: ticket.key,
          envUrl: '',
        })
        streamId = result.streamId
        pipelineId = result.pipelineId
      } catch (err) {
        const conflict = (err as Error & { conflict?: EnvInUseError }).conflict
        if (conflict) {
          updateLaneAgent(laneId, 'builder', {
            state: 'failed',
            message: `${env} is in use by ${conflict.heldBy.ticketKey || conflict.heldBy.pipelineId} (${conflict.heldBy.stage || 'running'}) — pick another env or dismiss that lane.`,
          })
        } else {
          updateLaneAgent(laneId, 'builder', { state: 'failed', message: `Pipeline start failed: ${err}` })
        }
        return
      }
      setLanes(prev => prev.map(l => l.id === laneId ? { ...l, streamId, pipelineId } : l))

      const cleanup = subscribeSSE(
        streamId,
        (event) => {
          const currentAgent = laneCurrentAgent.current[laneId] || 'builder'

          if (event.type === 'stage_change' && event.stage) {
            updateLaneAgent(laneId, currentAgent, { state: 'done', progress: 100 })
            laneCurrentAgent.current[laneId] = event.stage
            updateLaneAgent(laneId, event.stage, { state: 'active', progress: 0, message: '' })
            setLanes(prev => prev.map(l => l.id === laneId ? { ...l, currentAgent: event.stage! } : l))
          } else if (event.type === 'progress') {
            updateLaneAgent(laneId, currentAgent, {
              progress: event.pct ?? 0,
              eta: event.eta ?? '',
            })
          } else if (event.type === 'log') {
            updateLaneAgent(laneId, currentAgent, { message: event.data ?? '' })
            appendLog(laneId, event.data ?? '')
          } else if (event.type === 'shipper_ready') {
            setLanes(prev => prev.map(l => l.id === laneId ? {
              ...l,
              deployInfo: { env: event.env ?? '', services: event.services ?? [] },
            } : l))
          } else if (event.type === 'inspector_ready') {
            setLanes(prev => prev.map(l => l.id === laneId ? {
              ...l,
              qaCommand: event.data ?? '',
              baselineRuns: event.baseline_runs ?? [],
            } : l))
          } else if (event.type === 'done') {
            if (event.success && event.waiting_for_deploy) {
              updateLaneAgent(laneId, currentAgent, {
                state: 'active',
                progress: 10,
                eta: '~20 min',
                message: 'Deploys triggered. Click "Check Deploy" when ready.',
              })
            } else if (event.success && event.waiting_for_evidence) {
              setLanes(prev => prev.map(l => l.id === laneId ? {
                ...l,
                waitingForEvidence: true,
              } : l))
              updateLaneAgent(laneId, currentAgent, {
                state: 'active',
                progress: 10,
                eta: 'waiting for test run',
                message: 'Click "Check Evidence" when tests are complete',
              })
            } else if (event.success) {
              updateLaneAgent(laneId, currentAgent, { state: 'done', progress: 100 })
              const ev = event as unknown as { evidence?: { score?: number | null } }
              _setDoneToday(prev => [...prev, {
                ticket,
                score: ev.evidence?.score ?? null,
                time: new Date().toLocaleTimeString(),
              }])
            } else {
              updateLaneAgent(laneId, currentAgent, { state: 'failed', message: event.msg ?? 'Failed' })
            }
          }
        },
        () => {
          const currentAgent = laneCurrentAgent.current[laneId] || 'builder'
          updateLaneAgent(laneId, currentAgent, { state: 'failed', message: 'Connection lost' })
          setLanes(prev => prev.map(l => l.id === laneId ? { ...l, connectionLost: true } : l))
        }
      )
      sseCleanups.current[laneId] = cleanup
    } catch (err) {
      updateLaneAgent(laneId, 'builder', { state: 'failed', message: String(err) })
    }
  }, [lanes])

  const handleStartNext = useCallback(() => {
    const activeLaneKeys = lanes.map(l => l.ticket.key)
    const nextTicket = tickets
      .filter(t => !activeLaneKeys.includes(t.key) && t.statusCategory === 'ready_for_qa' && !t.flagged)
      .sort((a, b) => {
        const priOrder: Record<string, number> = { Highest: 0, High: 1, Medium: 2, Low: 3, Lowest: 4 }
        return (priOrder[a.priority] ?? 2) - (priOrder[b.priority] ?? 2)
      })[0]
    if (!nextTicket) return
    // Pick the first env that isn't already held.
    const freeEnv = environments.find(e => !envLocks[e])
    if (!freeEnv) return
    handleStart(nextTicket, freeEnv)
  }, [lanes, tickets, environments, envLocks, handleStart])

  const activeLaneKeys = lanes.map(l => l.ticket.key)

  // Map pipeline-state entries by ticket key so Queue rows can show the
  // provision-blocked badge without re-querying the backend per render.
  const pipelineByTicket: Record<string, typeof pipelineStates[string]> = {}
  for (const s of Object.values(pipelineStates)) {
    pipelineByTicket[s.ticketKey] = s
  }

  const handleRetryProvision = useCallback(async (ticketKey: string) => {
    try {
      await retryAutoProvision(ticketKey)
      // Refresh immediately so the badge clears as soon as the backend
      // resets failures; the 10s poll catches subsequent updates.
      fetchPipelineStates().then(setPipelineStates).catch(() => {})
    } catch (err) {
      console.error('Failed to retry auto-provision:', err)
    }
  }, [])

  return (
    <>
      <TopBar
        project={project}
        projects={projects}
        onProjectChange={setProject}
        tickets={tickets}
        theme={theme}
        onToggleTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
        onHuddle={() => setShowHuddle(true)}
        on3x3={() => setShow3x3(true)}
        onCleanupEnv={() => setShowCleanup(true)}
        lastRefresh={lastRefresh}
        onRefresh={loadTickets}
        isRefreshing={isRefreshing}
        autoMode={autoMode}
        writeAllowed={writeAllowed}
        onToggleAutoMode={handleToggleAutoMode}
        onToggleArm={handleToggleArm}
        onOpenSettings={() => setShowSettings(true)}
      />
      <ActiveLanes
        lanes={lanes}
        onCancel={handleCancel}
        onStartNext={handleStartNext}
        onCheckEvidence={handleCheckEvidence}
        onCheckDeploy={handleCheckDeploy}
        onRunCommand={handleRunCommand}
        onGenerateReport={handleGenerateReport}
        onResume={handleResume}
        onOverrideCouncil={handleOverrideCouncil}
        onStartFromQuartermaster={handleStartFromQuartermaster}
        onRunQa={handleRunQa}
        onAttachLinear={handleAttachLinear}
        writeAllowed={writeAllowed}
        evidenceHistory={evidenceHistory}
        needsBuildDeploy={needsBuildDeploy}
      />
      <Queue
        tickets={tickets}
        activeLaneKeys={activeLaneKeys}
        lanesAreFull={lanes.length >= 3}
        onStart={handleStart}
        environments={environments}
        envLocks={envLocks}
        pipelineByTicket={pipelineByTicket}
        onRetryProvision={handleRetryProvision}
      />
      <DoneToday items={doneToday} />
      <EvidenceHistory items={evidenceHistory} onGenerateReport={(key) => {
        generateReport(key).then(() => loadEvidenceHistory()).catch(() => {})
      }} />
      {showSettings && <Settings onClose={() => setShowSettings(false)} />}
      {showHuddle && <HuddleModal project={project} onClose={() => setShowHuddle(false)} />}
      {show3x3 && <ThreeByThreeModal project={project} onClose={() => setShow3x3(false)} />}
      {showCleanup && (
        <CleanupEnvModal
          environments={environments}
          activeEnvs={lanes.map(l => l.deployInfo?.env ?? '').filter(Boolean)}
          onClose={() => setShowCleanup(false)}
        />
      )}
      <Toast />
      <ChatPanel />
    </>
  )
}
