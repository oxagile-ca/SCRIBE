import { useState, type ReactNode } from 'react'
import { Access } from '../../onboardingSchema'

export const linesToArr = (s: string) => s.split('\n').map((l) => l.trim()).filter(Boolean)
export const arrToLines = (a: string[]) => a.join('\n')

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="ob-field">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function AccessChecks({ value, onChange }: { value: Access; onChange: (a: Access) => void }) {
  return (
    <div className="ob-access">
      <span className="ob-access-label">Access:</span>
      <label>
        <input type="checkbox" checked={value.read} onChange={(e) => onChange({ ...value, read: e.target.checked })} /> read
      </label>
      <label>
        <input type="checkbox" checked={value.write} onChange={(e) => onChange({ ...value, write: e.target.checked })} /> write
      </label>
    </div>
  )
}

export function ListTextarea({
  value, onChange, rows = 3, placeholder,
}: {
  value: string[]
  onChange: (v: string[]) => void
  rows?: number
  placeholder?: string
}) {
  const [text, setText] = useState(() => arrToLines(value))
  return (
    <textarea
      rows={rows}
      placeholder={placeholder}
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        onChange(linesToArr(e.target.value))
      }}
    />
  )
}

/** "Name | /route" per line -> [{name, route}]. Shared by the onboarding wizard and the
 *  Application Profile so both edit key pages the same way. */
export function KeyPagesTextarea({
  value, onChange, rows = 3,
}: {
  value: { name: string; route: string }[]
  onChange: (v: { name: string; route: string }[]) => void
  rows?: number
}) {
  const [text, setText] = useState(() => value.map((p) => `${p.name} | ${p.route}`).join('\n'))
  return (
    <textarea
      rows={rows}
      placeholder="Orders | /orders"
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        onChange(
          linesToArr(e.target.value).map((line) => {
            const [name, route] = line.split('|')
            return { name: (name || '').trim(), route: (route || '').trim() }
          })
        )
      }}
    />
  )
}
