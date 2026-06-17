import { useState } from 'react'
import { Ticket } from '../types'

interface DoneTicket {
  ticket: Ticket
  score: number | null
  time: string
}

interface Props {
  items: DoneTicket[]
}

export default function DoneToday({ items }: Props) {
  const [expanded, setExpanded] = useState(false)

  if (items.length === 0) return null

  return (
    <div className="done-today">
      <div className="done-today__header" onClick={() => setExpanded(!expanded)}>
        <span>Done Today ({items.length})</span>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          {expanded ? '\u25B2' : '\u25BC'}
        </span>
      </div>
      {expanded && (
        <div className="done-today__list">
          {items.map(({ ticket, score, time }) => (
            <div key={ticket.key} className="done-today__row">
              <a
                href={`https://acme.atlassian.net/browse/${ticket.key}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: 'var(--accent)', fontWeight: 700, minWidth: 90, textDecoration: 'none' }}
              >
                {ticket.key}
              </a>
              <span style={{ flex: 1, color: 'var(--text-muted)' }}>{ticket.summary}</span>
              {score !== null && (
                <span className="pill" style={{
                  background: score >= 80 ? 'rgba(72,187,120,0.2)' : 'rgba(246,173,85,0.2)',
                  color: score >= 80 ? 'var(--success)' : 'var(--warning)',
                }}>
                  {score}/100
                </span>
              )}
              {time && (
                <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{time}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
