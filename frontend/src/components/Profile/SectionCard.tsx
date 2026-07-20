import { useState, type ReactNode } from 'react'
import type { OnboardingAnswers } from '../../onboardingSchema'

export type SetSection = <K extends keyof OnboardingAnswers>(
  section: K,
  patch: Partial<OnboardingAnswers[K]>,
) => void

/** Replace a whole section value (not a merge) — used by the raw-JSON qaTargets editor
 *  so deleting a key actually removes it. */
export type ReplaceSection = <K extends keyof OnboardingAnswers>(
  section: K,
  value: OnboardingAnswers[K],
) => void

/** Read-first section: shows a readable summary; "Edit" flips just this section into an
 *  inline form over a local draft of the whole answers object. Save posts the ENTIRE
 *  answers (PUT /api/config is a full replace — omitting a section would blank it);
 *  Cancel discards the draft. Shared shell for every Application Profile domain. */
export default function SectionCard({
  title, subtitle, answers, secretsSet, editable = true, renderView, renderEdit, onSave,
}: {
  title: string
  subtitle?: ReactNode
  answers: OnboardingAnswers
  secretsSet: Record<string, boolean>
  editable?: boolean
  renderView: (a: OnboardingAnswers) => ReactNode
  renderEdit: (
    draft: OnboardingAnswers,
    setSection: SetSection,
    secretsSet: Record<string, boolean>,
    replaceSection: ReplaceSection,
  ) => ReactNode
  onSave: (draft: OnboardingAnswers) => Promise<{ ok: boolean; errors?: string[] }>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<OnboardingAnswers>(answers)
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)

  const setSection: SetSection = (section, patch) =>
    setDraft((d) => ({ ...d, [section]: { ...(d[section] as object), ...patch } } as OnboardingAnswers))
  const replaceSection: ReplaceSection = (section, value) =>
    setDraft((d) => ({ ...d, [section]: value }))

  function startEdit() {
    setDraft(JSON.parse(JSON.stringify(answers)))  // fresh, isolated draft
    setStatus('')
    setEditing(true)
  }

  async function save() {
    setSaving(true)
    setStatus('Saving…')
    const res = await onSave(draft)
    setSaving(false)
    if (res.ok) { setEditing(false); setStatus('') }
    else setStatus(`Error: ${(res.errors || []).join('; ') || 'save failed'}`)
  }

  return (
    <section className="profile-card">
      <div className="profile-card__head">
        <h3>{title}</h3>
        {editable && !editing && (
          <button className="btn btn--ghost btn--small" onClick={startEdit}>Edit</button>
        )}
      </div>
      {subtitle && <p className="profile-card__subtitle">{subtitle}</p>}
      {editing ? (
        <div className="profile-card__body">
          {renderEdit(draft, setSection, secretsSet, replaceSection)}
          <div className="profile-card__actions">
            <span className="profile-dim" style={{ marginRight: 'auto' }}>{status}</span>
            <button className="btn btn--ghost btn--small" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
            <button className="btn btn--primary btn--small" onClick={save} disabled={saving}>Save</button>
          </div>
        </div>
      ) : (
        <div className="profile-card__body">{renderView(answers)}</div>
      )}
    </section>
  )
}

/** Labeled read-only value used across section summaries. */
export function ReadRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="profile-row">
      <span className="profile-row__label">{label}</span>
      <span className="profile-row__value">{children}</span>
    </div>
  )
}

/** Render a string list as chips, or an em-dash when empty. */
export function Chips({ items }: { items?: string[] }) {
  if (!items || items.length === 0) return <span className="profile-dim">—</span>
  return (
    <span className="profile-chips">
      {items.map((it, i) => <span key={i} className="profile-chip">{it}</span>)}
    </span>
  )
}
