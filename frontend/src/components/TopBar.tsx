import { useEffect, useState } from 'react'
import { Ticket, UsageSummary } from '../types'
import { fetchVersion, getUsageSummary } from '../api'

interface Props {
  project: string
  projects: string[]
  onProjectChange: (project: string) => void
  tickets: Ticket[]
  theme: 'dark' | 'light'
  onToggleTheme: () => void
  onHuddle: () => void
  on3x3: () => void
  onCleanupEnv: () => void
  lastRefresh: string
  onRefresh: () => void
  isRefreshing: boolean
}

function useBackendVersion() {
  const [version, setVersion] = useState<string>('')
  const [startedAt, setStartedAt] = useState<number>(0)
  const [restartedSinceMount, setRestartedSinceMount] = useState(false)
  useEffect(() => {
    let cancelled = false
    let initialStartedAt: number | null = null
    const poll = async () => {
      try {
        const v = await fetchVersion()
        if (cancelled) return
        setVersion(v.version)
        setStartedAt(v.startedAt)
        if (initialStartedAt == null) {
          initialStartedAt = v.startedAt
        } else if (v.startedAt !== initialStartedAt) {
          setRestartedSinceMount(true)
        }
      } catch {
        if (!cancelled) setVersion('offline')
      }
    }
    poll()
    const handle = setInterval(poll, 15000)
    return () => { cancelled = true; clearInterval(handle) }
  }, [])
  return { version, startedAt, restartedSinceMount }
}

export default function TopBar({
  project, projects, onProjectChange, tickets,
  theme, onToggleTheme, onHuddle, on3x3, onCleanupEnv, lastRefresh,
  onRefresh, isRefreshing,
}: Props) {
  const { version, restartedSinceMount } = useBackendVersion()
  const [spend, setSpend] = useState<UsageSummary | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => getUsageSummary().then(s => { if (alive) setSpend(s) }).catch(() => {})
    load()
    const handle = setInterval(load, 30000)
    return () => { alive = false; clearInterval(handle) }
  }, [])
  const readyTickets = tickets.filter(t => t.statusCategory === 'ready_for_qa')
  const withEvidence = readyTickets.filter(t =>
    t.evidence.status === 'tested' || t.evidence.status === 'published'
  )
  const coveragePct = readyTickets.length > 0
    ? Math.round((withEvidence.length / readyTickets.length) * 100)
    : 0

  return (
    <div className="top-bar">
      <div className="top-bar__title">
        <span>Agent Squad</span>
        <select
          className="select"
          value={project}
          onChange={e => onProjectChange(e.target.value)}
        >
          {projects.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 12 }}>
          <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>QA Coverage:</span>
          <div className="coverage-bar">
            <div className="coverage-bar__fill" style={{ width: `${coveragePct}%` }} />
          </div>
          <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--success)' }}>
            {withEvidence.length}/{readyTickets.length}
          </span>
        </div>
      </div>
      <div className="top-bar__actions">
        {spend && (
          <span className="top-bar__spend" title="AI spend — today / all-time"
                style={{ fontSize: 12, color: 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>
            ${spend.today.cost_usd.toFixed(2)} today · ${spend.allTime.cost_usd.toFixed(2)} all-time
          </span>
        )}
        <span
          style={{
            fontSize: 10,
            color: restartedSinceMount ? 'var(--warning, #f5a524)' : 'var(--text-dim)',
            fontFamily: 'ui-monospace, monospace',
          }}
          title={restartedSinceMount
            ? 'Backend restarted since this tab loaded — reload the page to be safe.'
            : 'Backend git SHA'}
        >
          be: {version || '…'}{restartedSinceMount ? ' ⟳' : ''}
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{lastRefresh}</span>
        <button
          className="btn btn--ghost btn--small"
          onClick={onRefresh}
          disabled={isRefreshing}
          title="Fetch latest tickets from Jira"
        >
          <span className={isRefreshing ? 'spin' : ''} style={{ display: 'inline-block' }}>
            {'↻'}
          </span>
          {' '}{isRefreshing ? 'Refreshing…' : 'Refresh'}
        </button>
        <button className="btn btn--ghost btn--small" onClick={onCleanupEnv} title="Reset stale snapshots back to k8s-stable / projd-stable">
          Clean Env
        </button>
        <button className="btn btn--accent" onClick={onHuddle}>Daily Huddle</button>
        <button className="btn btn--warning" onClick={on3x3}>Weekly 3x3</button>
        <button className="btn btn--ghost btn--small" onClick={onToggleTheme}>
          {theme === 'dark' ? '☽ Dark' : '☀ Light'}
        </button>
      </div>
    </div>
  )
}
