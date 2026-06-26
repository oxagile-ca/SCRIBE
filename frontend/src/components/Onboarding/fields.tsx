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
