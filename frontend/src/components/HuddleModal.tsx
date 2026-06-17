import { useState, useEffect } from 'react'
import Modal from './Modal'
import { showToast } from './Toast'
import { fetchHuddle } from '../api'

interface Props {
  project: string
  onClose: () => void
}

export default function HuddleModal({ project, onClose }: Props) {
  const [text, setText] = useState('Generating...')
  const [notes, setNotes] = useState(() => localStorage.getItem('qa-dash-huddle-notes') || '')

  const generate = (userNotes: string) => {
    setText('Generating...')
    fetchHuddle(project, userNotes)
      .then(setText)
      .catch(() => setText('Failed to generate huddle.'))
  }

  useEffect(() => {
    generate(notes)
  }, [project])

  const handleNotesChange = (val: string) => {
    setNotes(val)
    localStorage.setItem('qa-dash-huddle-notes', val)
  }

  const copy = () => {
    navigator.clipboard.writeText(text).then(() => showToast('Copied to clipboard!'))
  }

  return (
    <Modal
      title="Daily Huddle"
      onClose={onClose}
      actions={
        <>
          <button className="btn btn--accent" onClick={copy}>Copy to Clipboard</button>
          <button className="btn btn--ghost" onClick={() => generate(notes)}>Regenerate</button>
          <button className="btn btn--ghost" onClick={onClose}>Close</button>
        </>
      }
    >
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
          Add notes (included in report):
        </label>
        <textarea
          value={notes}
          onChange={e => handleNotesChange(e.target.value)}
          placeholder="e.g., Sprint review tomorrow, deployment freeze Friday..."
          style={{
            width: '100%',
            minHeight: 60,
            background: 'var(--bg-input)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: 8,
            fontSize: 12,
            resize: 'vertical',
            fontFamily: 'inherit',
          }}
        />
      </div>
      <pre className="modal__content">{text}</pre>
    </Modal>
  )
}
