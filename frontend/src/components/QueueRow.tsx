import { useState, useEffect } from 'react'
import { Ticket } from '../types'
import type { EnvLockMap, PipelineStateEntry } from './Queue'
import { evidenceIsComplete } from '../laneStatus'
import { queueActionLabel, retestNeedsEnvPicker } from '../queueActions'
import { redactCredentials } from '../redact'
import { extractTicketTestCases, caseCount } from '../testCases'
import TestCasesModal from './TestCases/TestCasesModal'
import { fetchTestCases } from '../api'

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
  /** Re-test an already-QAed ticket: QA stage only, no build/deploy. */
  onReTest: (ticket: Ticket, env: string) => void
  /** False for already-deployed apps — a re-test then needs no env picker. */
  needsBuildDeploy: boolean
  disabled: boolean
  environments: string[]
  envLocks: EnvLockMap
  pipelineState?: PipelineStateEntry
  onRetryProvision?: (ticketKey: string) => void
}

export default function QueueRow({ ticket, onStart, onReTest, needsBuildDeploy, disabled, environments, envLocks, pipelineState, onRetryProvision }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [showEnvPicker, setShowEnvPicker] = useState(false)
  const [customEnv, setCustomEnv] = useState('')
  const priColor = PRIORITY_COLORS[ticket.priority] || 'var(--text-dim)'
  const isBlocked = ticket.flagged
  const isQAed = isTicketQAed(ticket)
  const rowClass = isBlocked ? 'queue-row queue-row--blocked' :
    ticket.staleDays >= 3 ? 'queue-row queue-row--stale' : 'queue-row'

  // A QAed ticket is re-tested (QA stage only), not started from scratch. Already-
  // deployed apps resolve their env server-side, so that path needs no picker.
  const fireAction = (env: string) => (isQAed ? onReTest : onStart)(ticket, env)
  const skipEnvPicker = isQAed && !retestNeedsEnvPicker(needsBuildDeploy)

  const acs = extractACs(ticket.description)
  const ticketCases = extractTicketTestCases(ticket.description)

  const [showCases, setShowCases] = useState(false)
  const [addedCount, setAddedCount] = useState(0)

  // The badge needs the added-case count without opening the modal. Same per-row
  // fetch the inline block already did — see the spec's "Known cost".
  useEffect(() => {
    let alive = true
    fetchTestCases(ticket.key).then((cs) => { if (alive) setAddedCount(cs.length) })
    return () => { alive = false }
  }, [ticket.key])

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
        {isQAed && (() => {
          const ev = ticket.evidence
          const parts: string[] = []
          if (ev?.score != null) parts.push(`${ev.score}/100`)
          if (ev?.claudeCost != null) parts.push(`$${ev.claudeCost.toFixed(2)}`)
          if (ev?.time) parts.push(ev.time)
          return (
            <span
              className="queue-row__metrics"
              title="QA score \u00b7 Claude token cost \u00b7 run time (from OTEL telemetry + score step)"
            >
              {parts.length
                ? parts.join('  \u00b7  ')
                : <span className="queue-row__metrics--missing">cost/time not tracked</span>}
            </span>
          )
        })()}
        <span className="queue-row__assignee">{ticket.assignee || '\u2014'}</span>
        {isQAed && (
          ticket.evidence?.reportUrl ? (
            <a
              className="queue-row__badge"
              href={ticket.evidence.reportUrl}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              title={`QA'd by SCRIBE${ticket.evidence.score != null ? ` \u2014 score ${ticket.evidence.score}/100` : ''}${ticket.evidence.latestRun ? ` (${ticket.evidence.latestRun})` : ''} \u2014 not yet moved to Done. Click to open evidence.`}
              style={{ color: 'var(--success, #4ade80)', background: 'rgba(74,222,128,0.15)', textDecoration: 'none' }}
            >
              {'\u2713'} QAed{ticket.evidence.score != null ? ` ${ticket.evidence.score}` : ''}
            </a>
          ) : (
            <span
              className="queue-row__badge"
              title={`QA'd by SCRIBE${ticket.evidence?.score != null ? ` \u2014 score ${ticket.evidence.score}/100` : ''} \u2014 not yet moved to Done`}
              style={{ color: 'var(--success, #4ade80)', background: 'rgba(74,222,128,0.15)' }}
            >
              {'\u2713'} QAed{ticket.evidence?.score != null ? ` ${ticket.evidence.score}` : ''}
            </span>
          )
        )}
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
        <button
          className="btn btn--ghost btn--small"
          title="View the ticket's test cases and add your own"
          onClick={(e) => { e.stopPropagation(); setShowCases(true) }}
        >
          Test cases ({caseCount(ticketCases.length, addedCount)})
        </button>
        <div style={{ position: 'relative' }}>
          <button
            className="queue-row__start"
            disabled={disabled || isBlocked}
            title={isQAed
              ? 'Re-run QA on this already-tested ticket (test stage only)'
              : 'Run the full pipeline for this ticket'}
            onClick={() => (skipEnvPicker ? fireAction('') : setShowEnvPicker(!showEnvPicker))}
          >
            {queueActionLabel(isQAed)}
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
                      fireAction(env)
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
                        fireAction(customEnv.trim())
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
                      fireAction(customEnv.trim())
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
      {expanded && (
        <div style={{
          padding: '8px 14px 10px 34px',
          background: 'var(--bg)',
          borderBottom: '1px solid var(--border)',
          fontSize: 11,
          color: 'var(--text-muted)',
        }}>
          {acs.length > 0 && (
            <>
              <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 10, color: 'var(--text-dim)' }}>
                Acceptance Criteria:
              </div>
              {acs.map((ac, i) => (
                <div key={i} style={{ marginBottom: 2 }}>- {redactCredentials(ac)}</div>
              ))}
            </>
          )}
        </div>
      )}
      {showCases && (
        <TestCasesModal
          ticket={ticket}
          onClose={() => setShowCases(false)}
          onCountChange={(n) => setAddedCount(Math.max(0, n - ticketCases.length))}
        />
      )}
    </>
  )
}

/** A ticket whose Linear state is a terminal/closed state. */
export function isTicketDone(t: Ticket): boolean {
  return /^(done|completed|complete|cancell?ed|closed|shipped|released)$/i.test((t.status || '').trim())
}

/** SCRIBE has captured QA evidence (tested/published) but the ticket has not been
 *  moved to Done yet — i.e. it's awaiting closure. Drives the "QAed" badge + filter. */
export function isTicketQAed(t: Ticket): boolean {
  // Use evidenceIsComplete (not just status==='tested'): the backend flips a ticket
  // to 'tested' the instant a run dir is created — at the START of a run — so a
  // run that is still in progress would otherwise show a "✓ QAed" badge. Require a
  // real finished result (score or report).
  return evidenceIsComplete(t.evidence) && !isTicketDone(t)
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
