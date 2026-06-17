import { useState } from 'react'
import Modal from './Modal'
import { cleanupEnv, subscribeSSE } from '../api'

interface Props {
  environments: string[]
  activeEnvs: string[]  // envs currently in use by lanes — protect from cleanup
  onClose: () => void
}

export default function CleanupEnvModal({ environments, activeEnvs, onClose }: Props) {
  const [running, setRunning] = useState<string | null>(null)
  const [logs, setLogs] = useState<string[]>([])
  const [done, setDone] = useState<{ env: string; success: boolean } | null>(null)

  const startCleanup = async (env: string) => {
    if (activeEnvs.includes(env)) return
    setRunning(env)
    setLogs([`Starting cleanup of ${env}...`])
    setDone(null)
    try {
      const streamId = await cleanupEnv(env, [])
      subscribeSSE(
        streamId,
        (event) => {
          if (event.type === 'log') {
            setLogs(prev => [...prev.slice(-200), event.data ?? ''])
          } else if (event.type === 'done') {
            setRunning(null)
            setDone({ env, success: !!event.success })
          }
        },
        () => {
          setRunning(null)
          setLogs(prev => [...prev, 'Connection lost'])
        }
      )
    } catch (err) {
      setLogs(prev => [...prev, `Error: ${err}`])
      setRunning(null)
    }
  }

  return (
    <Modal title="Clean Env — Reset stale snapshots" onClose={onClose}>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
        Resets every snapshot service on the env back to its stable reference
        (k8s-stable / projd-stable). Services tied to an active lane are protected.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
        {environments.map(env => {
          const inUse = activeEnvs.includes(env)
          const isRunning = running === env
          return (
            <div
              key={env}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '8px 12px', background: 'var(--bg-input)', borderRadius: 6,
              }}
            >
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
                {env}
                {inUse && <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--warning)' }}>IN USE</span>}
              </span>
              <button
                className="btn btn--accent btn--small"
                disabled={isRunning || inUse || running !== null}
                onClick={() => startCleanup(env)}
              >
                {isRunning ? 'Cleaning…' : 'Clean'}
              </button>
            </div>
          )
        })}
      </div>
      {(logs.length > 0 || done) && (
        <div
          style={{
            background: '#000', color: '#0f0', padding: 10, borderRadius: 6,
            fontFamily: 'monospace', fontSize: 11, maxHeight: 280, overflowY: 'auto',
            whiteSpace: 'pre-wrap',
          }}
        >
          {logs.join('\n')}
          {done && (
            <div style={{ marginTop: 8, color: done.success ? '#0f0' : '#f88', fontWeight: 700 }}>
              {done.success ? `✓ ${done.env} cleaned` : `✗ ${done.env} cleanup had failures`}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
