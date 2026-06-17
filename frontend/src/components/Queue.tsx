import { useState } from 'react'
import { Ticket } from '../types'
import QueueRow from './QueueRow'

type QueueFilter = 'Ready for QA' | 'In QA' | 'All'

export type EnvLockMap = Record<string, { pipelineId: string; ticketKey: string; stage: string; status: string }>

export type PipelineStateEntry = {
  ticketKey: string
  env: string
  stage: string
  status: string
  logs: string[]
  provisionFailures?: number
  provisionBlocked?: boolean
}

interface Props {
  tickets: Ticket[]
  activeLaneKeys: string[]
  lanesAreFull: boolean
  onStart: (ticket: Ticket, env: string) => void
  environments: string[]
  envLocks: EnvLockMap
  pipelineByTicket?: Record<string, PipelineStateEntry>
  onRetryProvision?: (ticketKey: string) => void
}

export default function Queue({ tickets, activeLaneKeys, lanesAreFull, onStart, environments, envLocks, pipelineByTicket, onRetryProvision }: Props) {
  const [filter, setFilter] = useState<QueueFilter>('All')

  const queueTickets = tickets
    .filter(t => !activeLaneKeys.includes(t.key))
    .filter(t => {
      if (filter === 'All') return true
      const cat = filter === 'Ready for QA' ? 'ready_for_qa' : 'in_qa'
      return t.statusCategory === cat
    })
    .sort((a, b) => {
      const priOrder: Record<string, number> = { Highest: 0, High: 1, Medium: 2, Low: 3, Lowest: 4 }
      const priDiff = (priOrder[a.priority] ?? 2) - (priOrder[b.priority] ?? 2)
      if (priDiff !== 0) return priDiff
      return b.staleDays - a.staleDays
    })

  const filters: QueueFilter[] = ['Ready for QA', 'In QA', 'All']

  return (
    <div className="queue">
      <div className="queue__header">
        <span className="queue__title">Queue ({queueTickets.length})</span>
        <div className="queue__filters">
          {filters.map(f => (
            <button
              key={f}
              className={`queue__filter${filter === f ? ' queue__filter--active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
      <div className="queue__list">
        {queueTickets.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-dim)', fontSize: 13 }}>
            No tickets in queue
          </div>
        ) : (
          queueTickets.map(t => (
            <QueueRow
              key={t.key}
              ticket={t}
              onStart={onStart}
              disabled={lanesAreFull}
              environments={environments}
              envLocks={envLocks}
              pipelineState={pipelineByTicket?.[t.key]}
              onRetryProvision={onRetryProvision}
            />
          ))
        )}
      </div>
    </div>
  )
}
