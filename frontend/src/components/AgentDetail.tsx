import { useRef, useEffect } from 'react'
import { AgentName, AgentStatus } from '../types'

const STAGE_LABELS: Record<AgentName, string> = {
  quartermaster: 'Provision',
  builder: 'Build',
  shipper: 'Deploy',
  inspector: 'Test',
  scribe: 'Report',
}

const STATE_PHRASES: Record<AgentName, Record<string, string>> = {
  quartermaster: { idle: 'Waiting', active: 'Provisioning', done: 'Provisioned', failed: 'Provision failed' },
  builder:   { idle: 'Waiting',  active: 'Building',  done: 'Built',    failed: 'Build failed' },
  shipper:   { idle: 'Waiting',  active: 'Deploying', done: 'Deployed', failed: 'Deploy failed' },
  inspector: { idle: 'Waiting',  active: 'Testing',   done: 'Tested',   failed: 'Tests failed' },
  scribe:    { idle: 'Waiting',  active: 'Collecting evidence', done: 'Evidence ready', failed: 'Evidence failed' },
}

interface Props {
  agentName: AgentName
  agentStatus: AgentStatus
  logs?: string[]
  onClose: () => void
  onCheckEvidence?: () => void
  qaCommand?: string
}

export default function AgentDetail({ agentName, agentStatus, logs = [], onClose, onCheckEvidence, qaCommand }: Props) {
  const logsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logsRef.current) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div className="agent-detail" style={{ marginTop: 12 }}>
      <div className="agent-detail__header">
        <div>
          <div className="agent-detail__name">{STAGE_LABELS[agentName]}</div>
          <div className="agent-detail__status">
            {STATE_PHRASES[agentName][agentStatus.state]}
          </div>
        </div>
        <button
          className="btn btn--ghost btn--small"
          style={{ marginLeft: 'auto' }}
          onClick={onClose}
        >
          Close
        </button>
      </div>
      <div className="agent-detail__logs" ref={logsRef}>
        {logs.length === 0
          ? <span style={{ color: 'var(--text-dim)' }}>No logs yet...</span>
          : logs.map((line, i) => <div key={i}>{line}</div>)
        }
      </div>
      <div className="agent-detail__metrics">
        <div className="agent-detail__metric">
          <span className="agent-detail__metric-label">Progress</span>
          <span className="agent-detail__metric-value">{agentStatus.progress}%</span>
        </div>
        {agentStatus.eta && (
          <div className="agent-detail__metric">
            <span className="agent-detail__metric-label">ETA</span>
            <span className="agent-detail__metric-value">{agentStatus.eta}</span>
          </div>
        )}
        {agentStatus.message && (
          <div className="agent-detail__metric">
            <span className="agent-detail__metric-label">Status</span>
            <span className="agent-detail__metric-value">{agentStatus.message}</span>
          </div>
        )}
      </div>
      {onCheckEvidence && (
        (agentName === 'inspector' && agentStatus.state !== 'done') ||
        agentName === 'scribe'
      ) && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 10 }}>
          <button
            className="btn btn--primary btn--small"
            onClick={onCheckEvidence}
          >
            {agentName === 'scribe' ? 'Re-check Evidence' : 'Check Evidence'}
          </button>
          {qaCommand && (
            <button
              className="btn btn--ghost btn--small"
              onClick={() => navigator.clipboard.writeText(qaCommand)}
              title={qaCommand}
            >
              Copy QA Cmd
            </button>
          )}
        </div>
      )}
    </div>
  )
}
