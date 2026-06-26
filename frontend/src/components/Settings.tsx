import { useEffect, useState } from 'react'
import Modal from './Modal'
import { Field, AccessChecks, ListTextarea } from './Onboarding/fields'
import type { OnboardingAnswers } from '../onboardingSchema'
import { getConfig, updateConfig, uploadPostman } from '../api'

// Masked secret input: blank submit keeps the existing secret; typing replaces it.
function SecretInput({ isSet, value, onChange }: { isSet: boolean; value: string; onChange: (v: string) => void }) {
  return (
    <input
      type="password"
      value={value}
      placeholder={isSet ? '•••• set — leave blank to keep' : 'not set'}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export default function Settings({ onClose }: { onClose: () => void }) {
  const [a, setA] = useState<OnboardingAnswers | null>(null)
  const [secretsSet, setSecretsSet] = useState<Record<string, boolean>>({})
  const [status, setStatus] = useState<string>('')
  const [postmanMsg, setPostmanMsg] = useState<string>('')

  useEffect(() => {
    getConfig().then((r) => { setA(r.answers); setSecretsSet(r.secretsSet) }).catch((e) => setStatus(String(e)))
  }, [])

  if (!a) {
    return <Modal title="Settings — Config Center" onClose={onClose}><p>Loading…</p></Modal>
  }

  // section-merge helper mirroring the wizard's set()
  function set<K extends keyof OnboardingAnswers>(section: K, patch: Partial<OnboardingAnswers[K]>) {
    setA((prev) => prev ? { ...prev, [section]: { ...(prev[section] as object), ...patch } as OnboardingAnswers[K] } : prev)
  }

  async function save() {
    setStatus('Saving…')
    const res = await updateConfig(a!)
    setStatus(res.ok ? 'Saved ✓' : `Error: ${(res.errors || []).join('; ')}`)
  }

  async function onPostman(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    setPostmanMsg('Uploading…')
    const r = await uploadPostman(f)
    setPostmanMsg(r.ok ? `Stored — ${r.endpointCount} endpoints parsed` : `Error: ${r.error}`)
  }

  const it = a.issueTracker, vcs = a.vcs, env = a.environments, kn = a.knowledge, api = a.api

  return (
    <Modal
      title="Settings — Config Center"
      onClose={onClose}
      actions={<>
        <span style={{ marginRight: 'auto', fontSize: 12, color: 'var(--text-dim)' }}>{status}</span>
        <button className="btn btn--primary" onClick={save}>Save</button>
      </>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '70vh', overflowY: 'auto' }}>
        <section>
          <h4>Company &amp; product</h4>
          <Field label="Org name"><input value={a.company.orgName} onChange={(e) => set('company', { orgName: e.target.value })} /></Field>
          <Field label="Product name"><input value={a.company.productName} onChange={(e) => set('company', { productName: e.target.value })} /></Field>
          <Field label="Description"><input value={a.company.description} onChange={(e) => set('company', { description: e.target.value })} /></Field>
          <Field label="URLs (one per line)"><ListTextarea rows={2} value={a.company.urls} onChange={(urls) => set('company', { urls })} /></Field>
        </section>

        <section>
          <h4>Issue tracker</h4>
          <Field label="Base URL"><input value={it.baseUrl} onChange={(e) => set('issueTracker', { baseUrl: e.target.value })} /></Field>
          <Field label="Project keys (one per line)"><ListTextarea rows={2} value={it.projects} onChange={(projects) => set('issueTracker', { projects })} /></Field>
          <Field label="Account email"><input value={it.email} onChange={(e) => set('issueTracker', { email: e.target.value })} /></Field>
          <Field label="API token"><SecretInput isSet={!!secretsSet['issueTracker.token']} value={it.token} onChange={(token) => set('issueTracker', { token })} /></Field>
          <AccessChecks value={it.access} onChange={(access) => set('issueTracker', { access })} />
        </section>

        <section>
          <h4>Version control</h4>
          <Field label="Org / workspace"><input value={vcs.org} onChange={(e) => set('vcs', { org: e.target.value })} /></Field>
          <Field label="Repos (one per line)"><ListTextarea rows={3} value={vcs.repos} onChange={(repos) => set('vcs', { repos })} /></Field>
          <Field label="API token"><SecretInput isSet={!!secretsSet['vcs.token']} value={vcs.token} onChange={(token) => set('vcs', { token })} /></Field>
          <AccessChecks value={vcs.access} onChange={(access) => set('vcs', { access })} />
        </section>

        <section>
          <h4>Test login</h4>
          <Field label="Login URL"><input value={env.testAuth?.loginUrl || ''} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, loginUrl: e.target.value } })} /></Field>
          <Field label="Username"><input value={env.testAuth?.username || ''} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, username: e.target.value } })} /></Field>
          <Field label="Password"><SecretInput isSet={!!secretsSet['environments.testAuth.password']} value={env.testAuth?.password || ''} onChange={(v) => set('environments', { testAuth: { ...env.testAuth, password: v } })} /></Field>
        </section>

        <section>
          <h4>Knowledge</h4>
          <Field label="Link"><input value={kn.link} onChange={(e) => set('knowledge', { link: e.target.value })} /></Field>
          <Field label="Token"><SecretInput isSet={!!secretsSet['knowledge.token']} value={kn.token} onChange={(token) => set('knowledge', { token })} /></Field>
          <AccessChecks value={kn.access} onChange={(access) => set('knowledge', { access })} />
        </section>

        <section>
          <h4>API / Postman</h4>
          <Field label="Base URL"><input value={api.baseUrl || ''} onChange={(e) => set('api', { ...api, baseUrl: e.target.value })} /></Field>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>Collection: {api.postmanCollectionPath || '(none)'}</div>
          <input type="file" accept="application/json,.json" onChange={onPostman} />
          {postmanMsg && <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>{postmanMsg}</div>}
        </section>
      </div>
    </Modal>
  )
}
