import { useState } from 'react'
import { Ticket } from '../types'
import QueueRow, { isTicketQAed } from './QueueRow'
import { groupTickets } from '../ticketGroups'

type QueueFilter = 'Ready for QA' | 'In QA' | 'QAed' | 'All'
type SortKey = 'priority' | 'difficulty' | 'created' | 'stale' | 'score' | 'key' | 'summary'
type SortDir = 'asc' | 'desc'

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
  /** Re-test an already-QAed ticket: QA stage only, no build/deploy. */
  onReTest: (ticket: Ticket, env: string) => void
  /** False for already-deployed apps — a re-test then needs no env picker. */
  needsBuildDeploy: boolean
  environments: string[]
  envLocks: EnvLockMap
  pipelineByTicket?: Record<string, PipelineStateEntry>
  onRetryProvision?: (ticketKey: string) => void
}

const PRI_ORDER: Record<string, number> = { Highest: 0, High: 1, Medium: 2, Low: 3, Lowest: 4 }

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'priority', label: 'Priority' },
  { key: 'difficulty', label: 'Difficulty (Easy→Hard)' },
  { key: 'created', label: 'Oldest first' },
  { key: 'stale', label: 'Stale days' },
  { key: 'score', label: 'QA score' },
  { key: 'key', label: 'Ticket key' },
  { key: 'summary', label: 'Summary' },
]

// Sensible default direction per sort field (e.g. most-stale / highest-score first).
const DEFAULT_DIR: Record<SortKey, SortDir> = {
  priority: 'asc', difficulty: 'asc', created: 'asc', stale: 'desc', score: 'desc', key: 'asc', summary: 'asc',
}

export default function Queue({ tickets, activeLaneKeys, lanesAreFull, onStart, onReTest, needsBuildDeploy, environments, envLocks, pipelineByTicket, onRetryProvision }: Props) {
  const [filter, setFilter] = useState<QueueFilter>('All')
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('priority')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [groupBy, setGroupBy] = useState<'none' | 'epic' | 'label'>('none')

  const inQueue = tickets.filter(t => !activeLaneKeys.includes(t.key))
  const qaedCount = inQueue.filter(isTicketQAed).length

  const q = query.trim().toLowerCase()
  const queueTickets = inQueue
    .filter(t => {
      if (filter === 'All') return true
      if (filter === 'QAed') return isTicketQAed(t)
      const cat = filter === 'Ready for QA' ? 'ready_for_qa' : 'in_qa'
      return t.statusCategory === cat
    })
    .filter(t => !q
      || t.key.toLowerCase().includes(q)
      || t.summary.toLowerCase().includes(q)
      || (t.assignee || '').toLowerCase().includes(q))
    .sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      switch (sortKey) {
        case 'difficulty':
          return ((a.difficultyScore ?? 0) - (b.difficultyScore ?? 0)) * dir
        case 'created': {
          const ca = a.createdAt ? Date.parse(a.createdAt) : Infinity
          const cb = b.createdAt ? Date.parse(b.createdAt) : Infinity
          return (ca - cb) * dir
        }
        case 'stale':
          return (a.staleDays - b.staleDays) * dir
        case 'score': {
          const sa = a.evidence?.score ?? -1
          const sb = b.evidence?.score ?? -1
          return (sa - sb) * dir
        }
        case 'key':
          return a.key.localeCompare(b.key, undefined, { numeric: true }) * dir
        case 'summary':
          return a.summary.localeCompare(b.summary) * dir
        case 'priority':
        default: {
          // Prefer Linear's numeric priority when present (None/0 sorts last); else the
          // label order. Ties fall back to most-stale first.
          const pv = (t: Ticket) => (t.priorityValue == null || t.priorityValue === 0) ? 99 : t.priorityValue
          const usePv = a.priorityValue != null || b.priorityValue != null
          const priDiff = usePv
            ? (pv(a) - pv(b))
            : ((PRI_ORDER[a.priority] ?? 2) - (PRI_ORDER[b.priority] ?? 2))
          const base = priDiff !== 0 ? priDiff : b.staleDays - a.staleDays
          return base * dir
        }
      }
    })

  const filters: QueueFilter[] = ['Ready for QA', 'In QA', 'QAed', 'All']

  return (
    <div className="queue">
      <div className="queue__header">
        <span className="queue__title">
          Queue ({queueTickets.length})
          {qaedCount > 0 && (
            <span className="queue__qaed-hint" title="Tickets QA'd by SCRIBE but not yet moved to Done">
              {' · '}{qaedCount} QAed awaiting Done
            </span>
          )}
        </span>
        <div className="queue__filters">
          {filters.map(f => (
            <button
              key={f}
              className={`queue__filter${filter === f ? ' queue__filter--active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f}{f === 'QAed' && qaedCount > 0 ? ` (${qaedCount})` : ''}
            </button>
          ))}
        </div>
      </div>

      <div className="queue__controls">
        <input
          className="queue__search"
          type="text"
          value={query}
          placeholder="Search key, summary, assignee…"
          onChange={e => setQuery(e.target.value)}
        />
        {query && (
          <button className="queue__search-clear" title="Clear search" onClick={() => setQuery('')}>×</button>
        )}
        <div className="queue__sort">
          <label className="queue__sort-label">Sort</label>
          <select
            className="queue__sort-select"
            value={sortKey}
            onChange={e => { const k = e.target.value as SortKey; setSortKey(k); setSortDir(DEFAULT_DIR[k]) }}
          >
            {SORTS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button
            className="queue__sort-dir"
            title={sortDir === 'asc' ? 'Ascending — click for descending' : 'Descending — click for ascending'}
            onClick={() => setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))}
          >
            {sortDir === 'asc' ? '↑' : '↓'}
          </button>
        </div>
        <div className="queue__group-by">
          <label className="queue__sort-label">Group</label>
          <select
            className="queue__sort-select"
            value={groupBy}
            onChange={e => setGroupBy(e.target.value as 'none' | 'epic' | 'label')}
          >
            <option value="none">None</option>
            <option value="epic">Epic</option>
            <option value="label">Label</option>
          </select>
        </div>
      </div>

      <div className="queue__list">
        {queueTickets.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-dim)', fontSize: 13 }}>
            {inQueue.length === 0 ? 'No tickets in queue' : 'No tickets match the current filters'}
          </div>
        ) : (
          (() => {
            const renderRow = (t: Ticket) => (
              <QueueRow
                key={t.key}
                ticket={t}
                onStart={onStart}
                onReTest={onReTest}
                needsBuildDeploy={needsBuildDeploy}
                disabled={lanesAreFull}
                environments={environments}
                envLocks={envLocks}
                pipelineState={pipelineByTicket?.[t.key]}
                onRetryProvision={onRetryProvision}
              />
            )
            if (groupBy === 'none') return queueTickets.map(renderRow)
            return groupTickets(queueTickets, groupBy).map(g => (
              <div key={g.key} className="queue__group">
                <div className="queue__group-header">
                  {g.title} <span className="queue__group-count">({g.tickets.length})</span>
                </div>
                {g.tickets.map(renderRow)}
              </div>
            ))
          })()
        )}
      </div>
    </div>
  )
}
