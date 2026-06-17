import { useState } from 'react'
import { Ticket } from '../types'
import type { EnvLockMap, PipelineStateEntry } from './Queue'

const PRIORITY_COLORS: Record<string, string> = {
  Highest: 'var(--pri-highest)',
  High: 'var(--pri-high)',
  Medium: 'var(--pri-medium)',
  Low: 'var(--pri-low)',
  Lowest: 'var(--pri-low)',
}

interface Props {
  ticket: Ticket
  onStart: (ticket: Ticket, env: string) => void
  disabled: boolean
  environments: string[]
  envLocks: EnvLockMap
  pipelineState?: PipelineStateEntry
  onRetryProvision?: (ticketKey: string) => void
}

export default function QueueRow({ ticket, onStart, disabled, environments, envLocks, pipelineState, onRetryProvision }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [showEnvPicker, setShowEnvPicker] = useState(false)
  const [customEnv, setCustomEnv] = useState('')
  const priColor = PRIORITY_COLORS[ticket.priority] || 'var(--text-dim)'
  const isBlocked = ticket.flagged
  const rowClass = isBlocked ? 'queue-row queue-row--blocked' :
    ticket.staleDays >= 3 ? 'queue-row queue-row--stale' : 'queue-row'

  const acs = extractACs(ticket.description)

  return (
    <>
      <div className={rowClass}>
        <div className="queue-row__priority" style={{ background: priColor }} />
        <span
          className="queue-row__key"
          style={{ cursor: 'pointer' }}
          onClick={() => setExpanded(!expanded)}
        >
          {ticket.key}
        </span>
        <span className="queue-row__summary">{ticket.summary}</span>
        <span className="queue-row__assignee">{ticket.assignee || '\u2014'}</span>
        {ticket.staleDays >= 3 && (
          <span className="queue-row__badge" style={{ color: 'var(--warning)', background: 'rgba(246,173,85,0.15)' }}>
            {ticket.staleDays}d
          </span>
        )}
        {isBlocked && (
          <span className="queue-row__badge" style={{ color: 'var(--danger)', background: 'rgba(252,129,129,0.15)' }}>
            Blocked
          </span>
        )}
        {pipelineState?.provisionBlocked ? (
          <span
            className="queue-row__badge"
            title="Auto-provision failed twice — click to retry"
            onClick={(e) => { e.stopPropagation(); onRetryProvision?.(ticket.key) }}
            style={{
              color: 'var(--danger)',
              background: 'rgba(252,129,129,0.15)',
              cursor: onRetryProvision ? 'pointer' : 'default',
            }}
          >
            Provision blocked - retry
          </span>
        ) : pipelineState?.stage === 'quartermaster' && pipelineState?.status === 'running' ? (
          <span
            className="queue-row__badge"
            title={`Auto-provisioning env ${pipelineState.env || ticket.key.toLowerCase()} — building snapshots and deploying`}
            style={{
              color: 'var(--accent, #4f9eff)',
              background: 'rgba(79,158,255,0.15)',
            }}
          >
            Auto-provisioning…
          </span>
        ) : pipelineState?.stage === 'quartermaster' && pipelineState?.status === 'done' ? (
          <span
            className="queue-row__badge"
            title={`Env ${pipelineState.env || ticket.key.toLowerCase()} provisioned with snapshots — click Start to begin testing`}
            style={{
              color: 'var(--success, #4ade80)',
              background: 'rgba(74,222,128,0.15)',
            }}
          >
            Env ready
          </span>
        ) : null}
        <div style={{ position: 'relative' }}>
          <button
            className="queue-row__start"
            disabled={disabled || isBlocked}
            onClick={() => setShowEnvPicker(!showEnvPicker)}
          >
            Start
          </button>
          {showEnvPicker && (
            <div style={{
              position: 'absolute',
              right: 0,
              top: '100%',
              marginTop: 4,
              background: 'var(--bg-card)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: 6,
              zIndex: 50,
              minWidth: 160,
              boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            }}>
              <div style={{ fontSize: 10, color: 'var(--text-dim)', padding: '4px 8px', fontWeight: 700 }}>
                Select environment:
              </div>
              {environments.map(env => {
                const lock = envLocks[env]
                const held = !!lock
                const tooltip = held
                  ? `Held by ${lock.ticketKey || lock.pipelineId} (${lock.stage || 'running'}). Dismiss that lane to release.`
                  : `Start on ${env}`
                return (
                  <button
                    key={env}
                    disabled={held}
                    title={tooltip}
                    onClick={() => {
                      if (held) return
                      setShowEnvPicker(false)
                      onStart(ticket, env)
                    }}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      width: '100%',
                      textAlign: 'left',
                      padding: '6px 8px',
                      background: 'none',
                      border: 'none',
                      color: held ? 'var(--text-dim)' : 'var(--text)',
                      fontSize: 12,
                      cursor: held ? 'not-allowed' : 'pointer',
                      borderRadius: 4,
                      opacity: held ? 0.55 : 1,
                    }}
                    onMouseEnter={e => { if (!held) e.currentTarget.style.background = 'var(--bg)' }}
                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                  >
                    <span>{env}</span>
                    {held && (
                      <span style={{
                        fontSize: 9,
                        color: 'var(--warning, #f5a524)',
                        marginLeft: 8,
                        whiteSpace: 'nowrap',
                        fontFamily: 'monospace',
                      }}>
                        {lock.ticketKey || 'in use'}
                      </span>
                    )}
                  </button>
                )
              })}
              <div style={{ borderTop: '1px solid var(--border)', marginTop: 6, paddingTop: 6 }}>
                <div style={{ fontSize: 10, color: 'var(--text-dim)', padding: '0 8px 4px', fontWeight: 700 }}>
                  Or use already-deployed env:
                </div>
                <div style={{ display: 'flex', gap: 4, padding: '0 4px' }}>
                  <input
                    type="text"
                    value={customEnv}
                    placeholder="e.g. proj-shared"
                    onChange={e => setCustomEnv(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && customEnv.trim()) {
                        setShowEnvPicker(false)
                        onStart(ticket, customEnv.trim())
                        setCustomEnv('')
                      }
                    }}
                    style={{
                      flex: 1,
                      background: 'var(--bg)',
                      border: '1px solid var(--border)',
                      borderRadius: 4,
                      color: 'var(--text)',
                      fontSize: 11,
                      padding: '4px 6px',
                      fontFamily: 'monospace',
                      minWidth: 0,
                    }}
                  />
                  <button
                    disabled={!customEnv.trim()}
                    onClick={() => {
                      setShowEnvPicker(false)
                      onStart(ticket, customEnv.trim())
                      setCustomEnv('')
                    }}
                    style={{
                      background: customEnv.trim() ? 'var(--accent)' : 'var(--bg)',
                      border: '1px solid var(--border)',
                      borderRadius: 4,
                      color: customEnv.trim() ? '#fff' : 'var(--text-dim)',
                      fontSize: 11,
                      padding: '4px 8px',
                      cursor: customEnv.trim() ? 'pointer' : 'not-allowed',
                    }}
                  >
                    Go
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      {expanded && acs.length > 0 && (
        <div style={{
          padding: '8px 14px 8px 34px',
          background: 'var(--bg)',
          borderBottom: '1px solid var(--border)',
          fontSize: 11,
          color: 'var(--text-muted)',
        }}>
          <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 10, color: 'var(--text-dim)' }}>
            Acceptance Criteria:
          </div>
          {acs.map((ac, i) => <div key={i} style={{ marginBottom: 2 }}>- {ac}</div>)}
        </div>
      )}
    </>
  )
}

function extractACs(description: string): string[] {
  if (!description) return []
  const lines = description.split('\n')
  const acs: string[] = []
  let inAcSection = false
  for (const line of lines) {
    const trimmed = line.trim()
    if (/^ac[:\s]/i.test(trimmed) || /acceptance\s*criteria/i.test(trimmed)) {
      inAcSection = true
      continue
    }
    if (inAcSection || /^\*\s/.test(trimmed) || /^-\s/.test(trimmed)) {
      const clean = trimmed.replace(/^[\*\-]\s*/, '').trim()
      if (clean.length > 10) acs.push(clean)
    }
    if (inAcSection && trimmed === '') inAcSection = false
  }
  return acs
}
