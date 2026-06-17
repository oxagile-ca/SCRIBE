import { useState } from 'react'
import { EvidenceHistoryItem } from '../api'

interface Props {
  items: EvidenceHistoryItem[]
  onGenerateReport: (key: string) => void
}

function coerceScore(raw: unknown): number | null {
  if (typeof raw === 'number') return raw
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>
    const pct = obj.pct
    if (typeof pct === 'number') return Math.round(pct)
    const total = typeof obj.total === 'number' ? obj.total : 0
    const passed = typeof obj.pass === 'number' ? obj.pass : 0
    if (total > 0) return Math.round((100 * passed) / total)
  }
  return null
}

function scoreColor(score: number | null): string {
  if (score === null) return 'var(--text-dim)'
  if (score >= 80) return 'var(--success)'
  if (score >= 60) return 'var(--warning)'
  return 'var(--danger, #fc8181)'
}

export default function EvidenceHistory({ items, onGenerateReport }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [filter, setFilter] = useState('')

  if (items.length === 0) return null

  const filtered = filter.trim()
    ? items.filter(i => i.key.toLowerCase().includes(filter.toLowerCase()))
    : items

  return (
    <div style={{ padding: '0 20px 20px' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '10px 14px',
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: expanded ? '8px 8px 0 0' : '8px',
          cursor: 'pointer',
          fontSize: 13,
          fontWeight: 600,
        }}
        onClick={() => setExpanded(e => !e)}
      >
        <span>Evidence History ({items.length} tickets)</span>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          {expanded ? '▲' : '▼'}
        </span>
      </div>
      {expanded && (
        <div style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderTop: 'none',
          borderRadius: '0 0 8px 8px',
        }}>
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)' }}>
            <input
              type="text"
              placeholder="Filter by ticket key…"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              onClick={e => e.stopPropagation()}
              style={{
                width: '100%',
                background: 'var(--bg)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                padding: '4px 8px',
                fontSize: 12,
                color: 'var(--text)',
                boxSizing: 'border-box',
              }}
            />
          </div>
          {filtered.length === 0 && (
            <div style={{ padding: '12px 14px', color: 'var(--text-dim)', fontSize: 12 }}>
              No matching tickets
            </div>
          )}
          {filtered.map(item => {
            const runDate = item.latestMtime
              ? new Date(item.latestMtime * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
              : ''
            const score = coerceScore(item.score as unknown)
            return (
              <div
                key={item.key}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '8px 14px',
                  borderBottom: '1px solid var(--border)',
                  fontSize: 12,
                  gap: 10,
                }}
              >
                <a
                  href={`https://acme.atlassian.net/browse/${item.key}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--accent)', fontWeight: 700, minWidth: 100, textDecoration: 'none' }}
                >
                  {item.key}
                </a>
                <span style={{ flex: 1, color: 'var(--text-dim)', fontSize: 11 }}>
                  {item.latestRun}
                </span>
                {score !== null && (
                  <span style={{ color: scoreColor(score), fontWeight: 700, minWidth: 48 }}>
                    {score}/100
                  </span>
                )}
                {item.claudeCost != null && (
                  <span
                    title="Claude token cost for this ticket's evidence runs"
                    style={{
                      color: 'var(--text-dim)',
                      fontSize: 10,
                      minWidth: 48,
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    ${item.claudeCost.toFixed(2)}
                  </span>
                )}
                {runDate && (
                  <span style={{ color: 'var(--text-dim)', fontSize: 10, minWidth: 52 }}>
                    {runDate}
                  </span>
                )}
                {item.reportUrl ? (
                  <a
                    className="btn btn--primary btn--small"
                    href={item.reportUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={e => e.stopPropagation()}
                  >
                    View Report
                  </a>
                ) : item.needsReport ? (
                  <button
                    className="btn btn--secondary btn--small"
                    onClick={e => { e.stopPropagation(); onGenerateReport(item.key) }}
                  >
                    Generate
                  </button>
                ) : (
                  <span style={{ color: 'var(--text-dim)', fontSize: 10, minWidth: 72 }}>
                    no report
                  </span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
