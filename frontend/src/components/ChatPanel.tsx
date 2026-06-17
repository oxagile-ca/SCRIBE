import { useEffect, useRef, useState } from 'react'
import { chatSend } from '../api'

type ToolCall = {
  id: string
  name: string
  input: Record<string, unknown>
  result?: string
  is_error?: boolean
}

type Message = {
  id: string
  role: 'user' | 'assistant'
  text: string
  tools: ToolCall[]
  cost?: number
  pending?: boolean
  streamId?: string
  /**
   * Connection state for the in-flight reply:
   *   undefined  — terminal (done) or never connected (user message)
   *   'open'     — EventSource is connected and streaming
   *   'reconnecting' — lost connection, auto-retrying
   *   'failed'   — exhausted auto-retries; user can manually retry
   */
  connState?: 'open' | 'reconnecting' | 'failed'
  retryAttempt?: number
}

const MAX_RETRY_ATTEMPTS = 3
const RETRY_BACKOFF_MS = [800, 1600, 3200]

type ChatEvent =
  | { type: 'session'; session_id: string }
  | { type: 'text'; data: string }
  | { type: 'tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; tool_use_id: string; content: string; is_error: boolean }
  | { type: 'result'; session_id: string; cost: number; duration_ms: number; is_error: boolean; result: string }
  | { type: 'error'; msg: string }

const SESSION_KEY = 'qa-dash-chat-session'
const MESSAGES_KEY = 'qa-dash-chat-messages'

function loadMessages(): Message[] {
  try {
    return JSON.parse(localStorage.getItem(MESSAGES_KEY) ?? '[]')
  } catch {
    return []
  }
}

function summarizeToolInput(name: string, input: Record<string, unknown>): string {
  if (name === 'Bash') return String(input.command ?? '').slice(0, 120)
  if (name === 'Read') return String(input.file_path ?? '')
  if (name === 'Write') return String(input.file_path ?? '')
  if (name === 'Edit') return String(input.file_path ?? '')
  if (name === 'Glob') return String(input.pattern ?? '')
  if (name === 'Grep') return String(input.pattern ?? '')
  if (name === 'WebFetch' || name === 'WebSearch') return String(input.url ?? input.query ?? '')
  return ''
}

export default function ChatPanel() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>(loadMessages)
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string>(() => localStorage.getItem(SESSION_KEY) ?? '')
  const [busy, setBusy] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(messages.slice(-200)))
  }, [messages])

  useEffect(() => {
    if (sessionId) localStorage.setItem(SESSION_KEY, sessionId)
    else localStorage.removeItem(SESSION_KEY)
  }, [sessionId])

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages, open])

  useEffect(() => () => sourceRef.current?.close(), [])

  /**
   * Open an SSE to the given streamId and pipe events into the named message.
   * On connection drop (uvicorn reload, network blip), auto-reconnects up to
   * MAX_RETRY_ATTEMPTS — since the stream is persisted to disk, replay+tail
   * is correct: any events emitted while we were disconnected are still
   * delivered by the dedup-via-`_seq` logic in /api/stream/{id}.
   */
  const subscribeWithRetry = (assistantId: string, streamId: string, attempt = 0) => {
    setMessages(prev => prev.map(m => m.id === assistantId
      ? { ...m, streamId, connState: 'open', retryAttempt: attempt }
      : m))

    const source = new EventSource(`/api/stream/${streamId}`)
    sourceRef.current = source

    let terminal = false

    source.onmessage = (e) => {
      let event: ChatEvent
      try { event = JSON.parse(e.data) } catch { return }

      setMessages(prev => prev.map(m => {
        if (m.id !== assistantId) return m
        switch (event.type) {
          case 'text':
            return { ...m, text: m.text + event.data, connState: 'open' }
          case 'tool_use':
            return { ...m, tools: [...m.tools, { id: event.id, name: event.name, input: event.input }] }
          case 'tool_result':
            return {
              ...m,
              tools: m.tools.map(t => t.id === event.tool_use_id
                ? { ...t, result: event.content, is_error: event.is_error }
                : t),
            }
          case 'result':
            return {
              ...m,
              cost: event.cost,
              pending: false,
              connState: undefined,
              text: event.is_error && !m.text ? `[error] ${event.result || 'request failed'}` : m.text,
            }
          case 'error':
            return {
              ...m,
              text: (m.text ? m.text + '\n' : '') + `[error] ${event.msg}`,
              pending: false,
              connState: undefined,
            }
          default:
            return m
        }
      }))

      if (event.type === 'session' && event.session_id) {
        setSessionId(event.session_id)
      } else if (event.type === 'result' || event.type === 'error') {
        terminal = true
        if ((event.type === 'result' && event.is_error) || event.type === 'error') {
          // Drop a session that just errored so the next message starts fresh.
          setSessionId('')
        }
        source.close()
        sourceRef.current = null
        setBusy(false)
      }
    }

    source.onerror = () => {
      source.close()
      sourceRef.current = null
      if (terminal) {
        // Already wrapped up cleanly — the error is the post-close native event.
        return
      }
      if (attempt + 1 >= MAX_RETRY_ATTEMPTS) {
        // Auto-retry exhausted: mark failed, surface a manual retry button.
        setMessages(prev => prev.map(m => m.id === assistantId
          ? { ...m, pending: false, connState: 'failed', retryAttempt: attempt + 1 }
          : m))
        setBusy(false)
        return
      }
      const delay = RETRY_BACKOFF_MS[attempt] ?? 3200
      setMessages(prev => prev.map(m => m.id === assistantId
        ? { ...m, connState: 'reconnecting', retryAttempt: attempt + 1 }
        : m))
      window.setTimeout(() => {
        subscribeWithRetry(assistantId, streamId, attempt + 1)
      }, delay)
    }
  }

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)

    const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', text, tools: [] }
    const assistantId = `a-${Date.now()}`
    const assistantMsg: Message = { id: assistantId, role: 'assistant', text: '', tools: [], pending: true }
    setMessages(prev => [...prev, userMsg, assistantMsg])

    let streamId: string
    try {
      const res = await chatSend(text, sessionId)
      streamId = res.streamId
    } catch (err) {
      setMessages(prev => prev.map(m => m.id === assistantId
        ? { ...m, text: `Error: ${err}`, pending: false }
        : m))
      setBusy(false)
      return
    }

    subscribeWithRetry(assistantId, streamId, 0)
  }

  const retryMessage = (assistantId: string) => {
    const msg = messages.find(m => m.id === assistantId)
    if (!msg?.streamId) return
    setBusy(true)
    setMessages(prev => prev.map(m => m.id === assistantId
      ? { ...m, pending: true, connState: 'reconnecting', retryAttempt: 0 }
      : m))
    subscribeWithRetry(assistantId, msg.streamId, 0)
  }

  const newChat = () => {
    sourceRef.current?.close()
    sourceRef.current = null
    setSessionId('')
    setMessages([])
    setBusy(false)
  }

  if (!open) {
    return (
      <button
        className="chat-fab"
        onClick={() => setOpen(true)}
        title="Chat with FRIDAY"
      >
        ✦ FRIDAY
      </button>
    )
  }

  return (
    <div className="chat-panel">
      <div className="chat-panel__header">
        <span>FRIDAY</span>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn--ghost btn--small" onClick={newChat} disabled={busy}>
            New
          </button>
          <button className="btn btn--ghost btn--small" onClick={() => setOpen(false)}>
            ▾
          </button>
        </div>
      </div>
      <div className="chat-panel__messages" ref={listRef}>
        {messages.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12, padding: 12, textAlign: 'center' }}>
            Ask FRIDAY anything — has access to your skills, MCP servers, and ~ files.
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} className={`chat-msg chat-msg--${m.role}`}>
            <div className="chat-msg__role">{m.role === 'user' ? 'You' : 'FRIDAY'}</div>
            {m.tools.length > 0 && (
              <div className="chat-msg__tools">
                {m.tools.map(t => (
                  <div key={t.id} className={`chat-tool${t.is_error ? ' chat-tool--error' : ''}`}>
                    <span className="chat-tool__name">→ {t.name}</span>
                    {summarizeToolInput(t.name, t.input) && (
                      <span className="chat-tool__arg"> {summarizeToolInput(t.name, t.input)}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {m.text && <div className="chat-msg__text">{m.text}</div>}
            {m.pending && m.connState !== 'reconnecting' && <div className="chat-msg__pending">…</div>}
            {m.connState === 'reconnecting' && (
              <div style={{ fontSize: 11, color: 'var(--warning, #f5a524)', marginTop: 4 }}>
                Connection lost — reconnecting{m.retryAttempt ? ` (attempt ${m.retryAttempt}/${MAX_RETRY_ATTEMPTS})` : ''}…
              </div>
            )}
            {m.connState === 'failed' && (
              <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--danger, #fc8181)' }}>
                  Disconnected after {MAX_RETRY_ATTEMPTS} attempts.
                </span>
                <button className="btn btn--ghost btn--small" onClick={() => retryMessage(m.id)} disabled={busy}>
                  Retry
                </button>
              </div>
            )}
            {m.cost != null && (
              <div className="chat-msg__cost">${m.cost.toFixed(4)}</div>
            )}
          </div>
        ))}
      </div>
      <div className="chat-panel__input">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder={busy ? 'Working…' : 'Ask FRIDAY…'}
          disabled={busy}
          rows={2}
        />
        <button className="btn btn--primary btn--small" onClick={send} disabled={busy || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  )
}
