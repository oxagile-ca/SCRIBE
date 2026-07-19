import { useEffect, useState } from 'react'
import { Ticket } from '../types'
import { fetchVersion } from '../api'

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
  autoMode: { enabled: boolean; armed: boolean }
  writeAllowed: boolean
  onToggleAutoMode: (enabled: boolean) => void
  onToggleArm: (armed: boolean) => void
  onOpenSettings: () => void
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
  autoMode, writeAllowed, onToggleAutoMode, onToggleArm, onOpenSettings,
}: Props) {
  const { version, restartedSinceMount } = useBackendVersion()
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
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}
                 title="Continuously QA Ready-for-QA tickets in the background">
            <input type="checkbox" checked={autoMode.enabled}
                   onChange={e => onToggleAutoMode(e.target.checked)} />
            Auto Mode
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4,
                          cursor: writeAllowed ? 'pointer' : 'not-allowed',
                          color: autoMode.armed ? 'var(--warning, #f5a524)' : 'var(--text-dim)' }}
                 title={writeAllowed ? 'When ON, auto mode attaches evidence to the live Linear board'
                                     : 'Write permission is off for this instance'}>
            <input type="checkbox" checked={autoMode.armed} disabled={!writeAllowed}
                   onChange={e => {
                     if (e.target.checked && !window.confirm('Arm auto-publish? Auto mode will attach evidence to the LIVE Linear board.')) return
                     onToggleArm(e.target.checked)
                   }} />
            Auto-publish
          </label>
        </span>
        <button className="btn btn--accent" onClick={onHuddle}>Daily Huddle</button>
        <button className="btn btn--warning" onClick={on3x3}>Weekly 3x3</button>
        <button className="btn btn--ghost btn--small" onClick={onToggleTheme}>
          {theme === 'dark' ? '☽ Dark' : '☀ Light'}
        </button>
        <button className="btn btn--ghost btn--small" onClick={onOpenSettings} title="Settings — Config Center">⚙</button>
      </div>
    </div>
  )
}
