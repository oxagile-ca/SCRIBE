import { useState } from 'react'
import { CouncilStatus, CouncilVerdict, CouncilOverride } from '../types'

interface Props {
  status: CouncilStatus
  verdict?: CouncilVerdict | null
  overrideInfo?: CouncilOverride | null
  onOverride: (reason: string) => Promise<void>
}

export function CouncilPanel({ status, verdict, overrideInfo, onOverride }: Props) {
  const [showOverride, setShowOverride] = useState(false)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (!status) return null

  const reviewers = verdict?.reviewers ?? []

  return (
    <div className="council-panel" data-status={status}>
      <div className="council-header">
        <strong>Council Review</strong>
        <span className={`council-status council-status-${status}`}>
          {status === 'pending' ? 'Running…' :
           status === 'pass'    ? 'PASS' :
           status === 'block'   ? 'BLOCKED' :
                                  'OVERRIDDEN'}
        </span>
      </div>

      {reviewers.length === 0 && status === 'pending' && (
        <div className="council-pending">Reviewers running…</div>
      )}

      <ul className="council-reviewers">
        {reviewers.map(r => (
          <li key={r.name} className={`council-reviewer council-reviewer-${r.verdict.toLowerCase()}`}>
            <span className="reviewer-name">{r.name}</span>
            <span className="reviewer-verdict">{r.verdict}</span>
            {r.reason && <span className="reviewer-reason">{r.reason}</span>}
            {r.usage && (r.usage.cost_usd != null || r.usage.input_tokens != null) && (
              <span className="reviewer-usage" style={{ color: 'var(--text-dim)', fontSize: 10, marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>
                {r.model ?? 'default'} · {(r.usage.input_tokens ?? 0)}/{(r.usage.output_tokens ?? 0)} tok · ${(r.usage.cost_usd ?? 0).toFixed(4)}
              </span>
            )}
          </li>
        ))}
      </ul>

      {status === 'block' && (
        <div className="council-block-actions">
          <div className="council-block-banner">
            ⛔ BLOCKED — {verdict?.rationale}
          </div>
          {!showOverride && (
            <button onClick={() => setShowOverride(true)}>Override</button>
          )}
          {showOverride && (
            <div className="council-override-form">
              <input
                type="text"
                placeholder="Reason required (e.g., flake, see slack thread)"
                value={reason}
                onChange={e => setReason(e.target.value)}
                disabled={submitting}
              />
              <button
                disabled={submitting || !reason.trim()}
                onClick={async () => {
                  setSubmitting(true)
                  try { await onOverride(reason.trim()) } finally { setSubmitting(false) }
                }}
              >
                {submitting ? 'Submitting…' : 'Confirm Override'}
              </button>
              <button onClick={() => { setShowOverride(false); setReason('') }} disabled={submitting}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {status === 'overridden' && overrideInfo && (
        <div className="council-overridden-banner">
          Overridden by {overrideInfo.user}: "{overrideInfo.reason}"
        </div>
      )}
    </div>
  )
}
