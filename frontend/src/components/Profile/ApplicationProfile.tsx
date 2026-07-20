import { useCallback, useEffect, useState } from 'react'
import type { OnboardingAnswers } from '../../onboardingSchema'
import { getConfig, updateConfig, rebuildSkill } from '../../api'
import {
  CompanySection, EnvironmentsSection, IssueTrackerSection, VcsSection, PublishSection,
  ProductQaSection, KnowledgeSection, ApiSection, QaTargetsSection, AnthropicSection,
  type SaveFn,
} from './sections'
import './profile.css'

function fmtTime(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : d.toLocaleString()
}

/** Full-page view of everything VERDIKT knows about the app, with per-section inline
 *  editing and an explicit "Rebuild skill" when Product QA knowledge changes. */
export default function ApplicationProfile({ onClose }: { onClose: () => void }) {
  const [answers, setAnswers] = useState<OnboardingAnswers | null>(null)
  const [secretsSet, setSecretsSet] = useState<Record<string, boolean>>({})
  const [skillStale, setSkillStale] = useState(false)
  const [skillBuiltAt, setSkillBuiltAt] = useState<string | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMsg, setRebuildMsg] = useState('')

  const reload = useCallback(async () => {
    try {
      const r = await getConfig()
      setAnswers(r.answers)
      setSecretsSet(r.secretsSet)
      setSkillStale(!!r.skillStale)
      setSkillBuiltAt(r.skillBuiltAt ?? null)
    } catch (e) {
      setLoadErr(String(e))
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  // Every section saves the full answers (PUT is full-replace); on success we reload so
  // secret masks + skill staleness reflect the change.
  const onSave: SaveFn = async (draft) => {
    const res = await updateConfig(draft)
    if (res.ok) await reload()
    return res
  }

  async function rebuild() {
    setRebuilding(true); setRebuildMsg('')
    const r = await rebuildSkill()
    setRebuilding(false)
    if (r.ok) { setRebuildMsg(`Rebuilt · ${r.patternRules ?? 0} pattern rule(s)`); await reload() }
    else setRebuildMsg(`Error: ${r.error || 'rebuild failed'}`)
  }

  const sectionProps = answers ? { answers, secretsSet, onSave } : null

  return (
    <div className="profile-page">
      <div className="profile-page__header">
        <div>
          <h2>Application Profile</h2>
          <p className="profile-dim">Everything VERDIKT knows about {answers?.company.productName || 'your app'} — view and edit.</p>
        </div>
        <button className="btn btn--ghost btn--small" onClick={onClose}>✕ Close</button>
      </div>

      <div className={`profile-banner ${skillStale ? 'profile-banner--warn' : 'profile-banner--ok'}`}>
        <span>
          {skillStale
            ? '⚠ Product QA knowledge changed — the QA skill is out of date.'
            : `✓ QA skill up to date${skillBuiltAt ? ` — built ${fmtTime(skillBuiltAt)}` : ''}`}
        </span>
        <span className="profile-banner__actions">
          {rebuildMsg && <span className="profile-dim">{rebuildMsg}</span>}
          <button className="btn btn--primary btn--small" onClick={rebuild} disabled={rebuilding || !answers}>
            {rebuilding ? 'Rebuilding…' : 'Rebuild skill'}
          </button>
        </span>
      </div>

      {loadErr && <div className="profile-json-err">{loadErr}</div>}
      {!answers && !loadErr && <p className="profile-dim">Loading…</p>}

      {sectionProps && (
        <div className="profile-grid">
          <CompanySection {...sectionProps} />
          <ProductQaSection {...sectionProps} />
          <EnvironmentsSection {...sectionProps} />
          <IssueTrackerSection {...sectionProps} />
          <VcsSection {...sectionProps} />
          <PublishSection {...sectionProps} />
          <KnowledgeSection {...sectionProps} />
          <ApiSection {...sectionProps} />
          <QaTargetsSection {...sectionProps} />
          <AnthropicSection {...sectionProps} />
        </div>
      )}
    </div>
  )
}
