import { useState, useEffect, FormEvent, CSSProperties } from 'react'
import Modal from '../Modal'
import { Ticket } from '../../types'
import { extractTicketTestCases } from '../../testCases'
import { redactCredentials } from '../../redact'
import {
  fetchTestCases, addTestCase, deleteTestCase, updateTestCase, TestCase,
} from '../../api'

interface Props {
  ticket: Ticket
  /** A stage of this ticket's lane is currently working — added cases land in the NEXT run. */
  runActive?: boolean
  onClose: () => void
  /** Report the ticket+added total so the card badge stays in sync without refetching. */
  onCountChange?: (n: number) => void
}

const TAG: CSSProperties = { fontSize: 9, marginLeft: 6 }
const ROW: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
  borderBottom: '1px solid var(--border)',
}
const GROUP_TITLE: CSSProperties = {
  fontSize: 10, fontWeight: 700, color: 'var(--text-dim)', margin: '14px 0 4px',
  textTransform: 'uppercase', letterSpacing: 0.4,
}

export default function TestCasesModal({ ticket, runActive = false, onClose, onCountChange }: Props) {
  const ticketCases = extractTicketTestCases(ticket.description)

  const [added, setAdded] = useState<TestCase[]>([])
  const [loadError, setLoadError] = useState('')
  const [newCase, setNewCase] = useState('')
  const [addError, setAddError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [editError, setEditError] = useState('')

  function load() {
    setLoadError('')
    fetchTestCases(ticket.key)
      .then(setAdded)
      .catch(() => setLoadError("Couldn't load your added cases."))
  }

  useEffect(() => {
    let alive = true
    fetchTestCases(ticket.key)
      .then((cs) => { if (alive) setAdded(cs) })
      .catch(() => { if (alive) setLoadError("Couldn't load your added cases.") })
    return () => { alive = false }
  }, [ticket.key])

  // Keep the card's badge in step with what's in the modal.
  useEffect(() => {
    onCountChange?.(ticketCases.length + added.length)
  }, [ticketCases.length, added.length])

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    const text = newCase.trim()
    if (!text) return
    setBusy(true)
    setAddError('')
    const created = await addTestCase(ticket.key, text)
    setBusy(false)
    if (created) {
      setAdded((prev) => [...prev, created])
      setNewCase('')
    } else {
      setAddError("Couldn't save that case — it wasn't added.")
    }
  }

  async function handleDelete(id: string) {
    if (await deleteTestCase(ticket.key, id)) {
      setAdded((prev) => prev.filter((c) => c.id !== id))
    } else {
      load()  // already gone, or a write failed — resync rather than leave a phantom row
    }
  }

  function startEdit(c: TestCase) {
    setEditingId(c.id)
    setEditText(c.text)
    setEditError('')
  }

  async function saveEdit(id: string) {
    const text = editText.trim()
    if (!text) return
    setBusy(true)
    const res = await updateTestCase(ticket.key, id, text)
    setBusy(false)
    if (res.ok && res.case) {
      const saved = res.case
      setAdded((prev) => prev.map((c) => (c.id === id ? saved : c)))
      setEditingId(null)
      setEditError('')
    } else {
      setEditError(res.error || 'could not save')  // stay in edit mode, keep their text
    }
  }

  const total = ticketCases.length + added.length

  return (
    <Modal title={`Test cases — ${ticket.key}`} onClose={onClose}>
      {runActive && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 10 }}>
          Run in progress — cases added now apply to the next run.
        </div>
      )}

      <div style={GROUP_TITLE}>From the ticket ({ticketCases.length})</div>
      {ticketCases.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          No test cases in the ticket description.
        </div>
      ) : (
        ticketCases.map((tc, i) => (
          <div key={`t${i}`} style={ROW}>
            <span style={{ fontSize: 12 }}>
              {redactCredentials(tc)}
              <span style={{ ...TAG, color: 'var(--text-dim)' }}>from ticket</span>
            </span>
          </div>
        ))
      )}

      <div style={GROUP_TITLE}>Added in Verdikt ({added.length})</div>
      {loadError && (
        <div style={{ fontSize: 11, color: 'var(--danger)' }}>
          {loadError}{' '}
          <button type="button" className="btn btn--ghost btn--small" onClick={load}>Retry</button>
        </div>
      )}
      {!loadError && added.length === 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>None yet — add one below.</div>
      )}
      {added.map((c) => (
        <div key={c.id} style={ROW}>
          {editingId === c.id ? (
            <>
              <input
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                style={{ flex: 1, fontSize: 12, padding: '4px 6px' }}
                autoFocus
              />
              <button
                type="button"
                className="btn btn--primary btn--small"
                disabled={busy || !editText.trim()}
                onClick={() => saveEdit(c.id)}
              >
                Save
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => { setEditingId(null); setEditError('') }}
              >
                Cancel
              </button>
              {editError && (
                <span style={{ fontSize: 10, color: 'var(--danger)' }}>{editError}</span>
              )}
            </>
          ) : (
            <>
              <span style={{ flex: 1, fontSize: 12 }}>
                {c.text}
                <span style={{ ...TAG, color: 'var(--accent, #5b8cff)' }}>added</span>
              </span>
              <button type="button" className="btn btn--ghost btn--small" onClick={() => startEdit(c)}>
                Edit
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                title="Remove this test case"
                onClick={() => handleDelete(c.id)}
              >
                {'✕'}
              </button>
            </>
          )}
        </div>
      ))}

      <form onSubmit={handleAdd} style={{ display: 'flex', gap: 6, marginTop: 12 }}>
        <input
          value={newCase}
          onChange={(e) => setNewCase(e.target.value)}
          placeholder="Add a test case — it'll be tested on the next run"
          style={{ flex: 1, fontSize: 12, padding: '5px 7px' }}
        />
        <button type="submit" className="btn btn--primary btn--small" disabled={busy || !newCase.trim()}>
          Add
        </button>
      </form>
      {addError && (
        <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4 }}>{addError}</div>
      )}
      <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 10 }}>
        {total} case{total === 1 ? '' : 's'} in scope. Added cases stay in Verdikt — they are
        never written back to the tracker.
      </div>
    </Modal>
  )
}
