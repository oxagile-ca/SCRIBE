import { useState } from 'react'
import { Ticket } from '../types'
import { topFeatures } from '../ticketGroups'

/** HERO breakdown of the features (epics) being worked on: top 3 by ticket count,
 *  "Show N more" expands to 5; each row shows a QA-coverage bar. */
export default function FeatureBreakdown({ tickets }: { tickets: Ticket[] }) {
  const [expanded, setExpanded] = useState(false)
  const features = topFeatures(tickets, 5)
  if (features.length === 0) return null
  const shown = expanded ? features : features.slice(0, 3)
  const more = features.length - 3
  return (
    <div className="feature-breakdown">
      <div className="feature-breakdown__title">Features in progress</div>
      <div className="feature-breakdown__rows">
        {shown.map(f => {
          const pct = f.total ? Math.round((f.qaed / f.total) * 100) : 0
          return (
            <div key={f.key} className="feature-breakdown__row">
              <span className="feature-breakdown__name" title={f.title}>{f.title}</span>
              <span className="feature-breakdown__count">{f.total} ticket{f.total === 1 ? '' : 's'}</span>
              <div className="feature-breakdown__bar" title={`${pct}% QAed`}>
                <div className="feature-breakdown__bar-fill" style={{ width: `${pct}%` }} />
              </div>
              <span className="feature-breakdown__cov">{f.qaed}/{f.total} QAed</span>
            </div>
          )
        })}
      </div>
      {more > 0 && (
        <button className="feature-breakdown__toggle" onClick={() => setExpanded(e => !e)}>
          {expanded ? 'Show less' : `Show ${more} more`}
        </button>
      )}
    </div>
  )
}
