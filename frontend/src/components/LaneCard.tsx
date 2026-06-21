import { useState, useEffect } from 'react'
import { Lane, AgentName, TicketUsage } from '../types'
import AgentDetail from './AgentDetail'
import { CouncilPanel } from './CouncilPanel'
import { getTicketUsage } from '../api'
import { UsageBreakdown } from './UsageBreakdown'

const FULL_AGENT_ORDER: AgentName[] = ['quartermaster', 'builder', 'shipper', 'inspector', 'scribe']
// Apps that are already deployed (static / local / deployed modes) skip build & deploy:
// the run is just analyze-PR → test → report.
const TEST_ONLY_ORDER: AgentName[] = ['inspector', 'scribe']
const STAGE_LABELS: Record<AgentName, string> = {
  quartermaster: 'Provision',
  builder: 'Build',
  shipper: 'Deploy',
  inspector: 'Test',
  scribe: 'Report',
}

interface Props {
  lane: Lane
  onCancel: (laneId: string) => void
  onCheckEvidence: (laneId: string) => void
  onCheckDeploy: (laneId: string) => void
  onRunCommand: (laneId: string, command: string) => void
  onGenerateReport: (laneId: string) => void
  onResume: (laneId: string) => void
  onOverrideCouncil: (laneId: string, reason: string) => Promise<void>
  onStartFromQuartermaster: (lane: Lane) => void
  needsBuildDeploy?: boolean
}

export default function LaneCard({ lane, onCancel, onCheckEvidence, onCheckDeploy, onRunCommand, onGenerateReport, onResume, onOverrideCouncil, onStartFromQuartermaster, needsBuildDeploy = true }: Props) {
  const [expandedAgent, setExpandedAgent] = useState<AgentName | null>(null)
  const [cmdInput, setCmdInput] = useState('')
  const [usage, setUsage] = useState<TicketUsage | null>(null)
  const { ticket, agents, currentAgent } = lane

  useEffect(() => {
    let alive = true
    getTicketUsage(ticket.key).then(u => { if (alive) setUsage(u) }).catch(() => {})
    return () => { alive = false }
  }, [ticket.key])

  const AGENT_ORDER: AgentName[] = needsBuildDeploy ? FULL_AGENT_ORDER : TEST_ONLY_ORDER

  const overallProgress = AGENT_ORDER.reduce((sum, name) => {
    const agentProgress = agents[name]?.progress ?? 0
    return sum + (agentProgress / 100) * (100 / AGENT_ORDER.length)
  }, 0)

  const councilGate = lane.councilStatus == null || lane.councilStatus === 'pass' || lane.councilStatus === 'overridden'
  const isComplete = agents.scribe.state === 'done' && councilGate
  const scribeMsg = (agents.scribe.message ?? '').toLowerCase()
  const scribeHasRealEvidence = /score:|evidence collected|report|generated/.test(scribeMsg)
  const scribeStuck = currentAgent === 'scribe' && agents.scribe.state === 'done' && !scribeHasRealEvidence

  // Report URL: prefer lane.reportUrl (set after check/generate), fall back to ticket evidence
  const reportUrl = lane.reportUrl || ticket.evidence?.reportUrl || ''
  const needsReport = !reportUrl && (ticket.evidence?.needsReport || ticket.evidence?.status === 'tested')

  // Show "Re-check Evidence" at inspector stage OR when scribe is done without real evidence
  const showCheckEvidence = (currentAgent === 'inspector' && agents.inspector.state !== 'done') || scribeStuck
  // Show "Re-check Evidence" button at scribe stage too (allows retrying after pipeline completes)
  const showRecheckAtScribe = currentAgent === 'scribe' && agents.scribe.state === 'done'

  return (
    <div className={`lane-card${isComplete ? ' lane-card--complete' : ''}`}>
      <div className="lane-card__header">
        <div style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
          <a
            className="lane-card__key"
            href={`https://acme.atlassian.net/browse/${ticket.key}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {ticket.key}
          </a>
          <span style={{ fontSize: 10, color: 'var(--text-dim)', marginLeft: 8 }}>
            {ticket.assignee}
          </span>
          {usage && usage.total_cost_usd > 0 && (
            <span className="lane-card__cost" title="AI spend on this ticket (council + chat + evidence)"
                  style={{ marginLeft: 'auto', color: 'var(--text-dim)', fontSize: 10, fontVariantNumeric: 'tabular-nums' }}>
              ${usage.total_cost_usd.toFixed(2)}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {reportUrl && (
            <a
              className="btn btn--primary btn--small"
              href={reportUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              View Report
            </a>
          )}
          <button className="btn btn--ghost btn--small" onClick={() => onCancel(lane.id)}>
            {isComplete ? 'Dismiss' : 'Cancel'}
          </button>
        </div>
      </div>
      <div className="lane-card__summary">{ticket.summary}</div>
      <div className="stage-strip">
        {AGENT_ORDER.map(name => {
          const state = agents[name]?.state ?? 'pending'
          const message = agents[name]?.message
          return (
            <button
              key={name}
              type="button"
              className={`stage-chip stage-chip--${state}`}
              onClick={() => setExpandedAgent(expandedAgent === name ? null : name)}
              title={`${STAGE_LABELS[name]} — ${state}${message ? ': ' + message : ''}`}
            >
              <span className="stage-chip__dot" />
              <span>{STAGE_LABELS[name]}</span>
            </button>
          )
        })}
      </div>
      <div className="lane-card__progress">
        <div className="lane-card__progress-fill" style={{ width: `${overallProgress}%` }} />
      </div>
      {usage && usage.tasks.length > 0 && (
        <details className="lane-card__usage">
          <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--text-dim)' }}>AI usage</summary>
          <UsageBreakdown usage={usage} />
        </details>
      )}
      {lane.connectionLost && lane.pipelineId && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', alignSelf: 'center' }}>Connection lost</span>
          <button className="btn btn--primary btn--small" onClick={() => onResume(lane.id)}>
            Resume
          </button>
        </div>
      )}
      {currentAgent === 'quartermaster' && agents.quartermaster.state === 'done' && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8 }}>
          <button className="btn btn--primary btn--small" onClick={() => onStartFromQuartermaster(lane)}>
            Ready to test — Start
          </button>
        </div>
      )}
      {agents[currentAgent]?.eta && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 6, textAlign: 'center' }}>
          {STAGE_LABELS[currentAgent]}: {agents[currentAgent]?.message || agents[currentAgent]?.eta}
        </div>
      )}
      {(currentAgent === 'shipper' && agents.shipper.state !== 'done') && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8 }}>
          <button className="btn btn--primary btn--small" onClick={() => onCheckDeploy(lane.id)}>
            Check Deploy
          </button>
        </div>
      )}
      {(showCheckEvidence || showRecheckAtScribe) && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8 }}>
          <button className="btn btn--primary btn--small" onClick={() => onCheckEvidence(lane.id)}>
            {showRecheckAtScribe ? 'Re-check Evidence' : scribeStuck ? 'Re-check Evidence' : 'Check Evidence'}
          </button>
          {lane.qaCommand && (
            <button
              className="btn btn--ghost btn--small"
              onClick={() => navigator.clipboard.writeText(lane.qaCommand!)}
              title={lane.qaCommand}
            >
              Copy QA Cmd
            </button>
          )}
        </div>
      )}
      {lane.qaCommand && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--muted, #9aa3af)', marginBottom: 4 }}>Run this in Claude Code:</div>
          <code
            onClick={() => navigator.clipboard.writeText(lane.qaCommand!)}
            title="Click to copy"
            style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: 'var(--bg, #15171c)', border: '1px solid var(--border, #2c313a)', borderRadius: 6, padding: '8px 10px', fontSize: 12, cursor: 'pointer', userSelect: 'all' }}
          >
            {lane.qaCommand}
          </code>
        </div>
      )}
      {/* Generate Report button — shown when evidence exists but index.html is missing */}
      {(needsReport || (isComplete && !reportUrl)) && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8 }}>
          <button
            className="btn btn--secondary btn--small"
            onClick={() => onGenerateReport(lane.id)}
            title="Build index.html with screenshots, markups, and diffs"
          >
            Generate Report
          </button>
        </div>
      )}
      {(currentAgent === 'shipper' || currentAgent === 'inspector') && (
        <div style={{ display: 'flex', gap: 4, marginTop: 8 }}>
          <input
            type="text"
            className="lane-card__cmd-input"
            placeholder="deploycli ls qa-env ..."
            value={cmdInput}
            onChange={e => setCmdInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && cmdInput.trim()) {
                onRunCommand(lane.id, cmdInput.trim())
                setCmdInput('')
              }
            }}
            style={{
              flex: 1,
              background: 'var(--bg-card)',
              border: '1px solid var(--border)',
              borderRadius: 4,
              padding: '4px 8px',
              fontSize: 11,
              color: 'var(--text)',
              fontFamily: 'monospace',
            }}
          />
          <button
            className="btn btn--ghost btn--small"
            onClick={() => { if (cmdInput.trim()) { onRunCommand(lane.id, cmdInput.trim()); setCmdInput('') } }}
          >
            Run
          </button>
        </div>
      )}
      {lane.councilStatus && (
        <CouncilPanel
          status={lane.councilStatus}
          verdict={lane.councilVerdict}
          overrideInfo={lane.councilOverride}
          onOverride={async (reason) => { await onOverrideCouncil(lane.id, reason) }}
        />
      )}
      {expandedAgent && agents[expandedAgent] && (
        <AgentDetail
          agentName={expandedAgent}
          agentStatus={agents[expandedAgent]}
          logs={lane.logs}
          onClose={() => setExpandedAgent(null)}
          onCheckEvidence={
            expandedAgent === 'inspector' || expandedAgent === 'scribe'
              ? () => onCheckEvidence(lane.id)
              : undefined
          }
          qaCommand={lane.qaCommand}
        />
      )}
    </div>
  )
}
