import { useState } from 'react'
import type { OnboardingAnswers } from '../../onboardingSchema'
import { Field, AccessChecks, ListTextarea, KeyPagesTextarea } from '../Onboarding/fields'
import SecretInput from '../SecretInput'
import { uploadPostman } from '../../api'
import SectionCard, { ReadRow, Chips } from './SectionCard'

export type SaveFn = (draft: OnboardingAnswers) => Promise<{ ok: boolean; errors?: string[] }>
export interface SectionProps {
  answers: OnboardingAnswers
  secretsSet: Record<string, boolean>
  onSave: SaveFn
}

const dim = (v?: string) => (v && v.trim() ? <span>{v}</span> : <span className="profile-dim">—</span>)
const yn = (b?: boolean) => (b ? 'Yes' : 'No')
const isSet = (m: Record<string, boolean>, k: string) => !!m[k]

function Select<T extends string>({ value, options, onChange }: {
  value: T; options: readonly T[]; onChange: (v: T) => void
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  )
}

// ---- Company & product -------------------------------------------------------
export function CompanySection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Company & product" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => (
        <>
          <ReadRow label="Product">{dim(a.company.productName)}</ReadRow>
          <ReadRow label="Organization">{dim(a.company.orgName)}</ReadRow>
          <ReadRow label="Type">{dim(a.company.productType)}</ReadRow>
          <ReadRow label="Description">{dim(a.company.description)}</ReadRow>
          <ReadRow label="URLs"><Chips items={a.company.urls} /></ReadRow>
        </>
      )}
      renderEdit={(d, set) => (
        <>
          <Field label="Organization name"><input value={d.company.orgName} onChange={(e) => set('company', { orgName: e.target.value })} /></Field>
          <Field label="Product name"><input value={d.company.productName} onChange={(e) => set('company', { productName: e.target.value })} /></Field>
          <Field label="Product type">
            <Select value={d.company.productType as string} options={['cms', 'webapp', 'api', 'ecommerce', 'other']}
              onChange={(v) => set('company', { productType: v })} />
          </Field>
          <Field label="Description"><textarea rows={4} value={d.company.description} onChange={(e) => set('company', { description: e.target.value })} /></Field>
          <Field label="Primary URLs (one per line)"><ListTextarea rows={2} value={d.company.urls} onChange={(urls) => set('company', { urls })} /></Field>
        </>
      )} />
  )
}

// ---- Environments ------------------------------------------------------------
export function EnvironmentsSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Environments" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => {
        const e = a.environments, au = e.testAuth || ({} as OnboardingAnswers['environments']['testAuth'])
        return (
          <>
            <ReadRow label="Test mode">{dim(e.mode)}</ReadRow>
            <ReadRow label="Staging URLs"><Chips items={e.staticUrls} /></ReadRow>
            {e.buildCmd && <ReadRow label="Build">{dim(e.buildCmd)}</ReadRow>}
            {e.deployCmd && <ReadRow label="Deploy">{dim(e.deployCmd)}</ReadRow>}
            <ReadRow label="Login required">{yn(au.required)}</ReadRow>
            {au.required && <>
              <ReadRow label="Login URL">{dim(au.loginUrl)}</ReadRow>
              <ReadRow label="Test user">{dim(au.username)}</ReadRow>
              <ReadRow label="Password">{isSet(secretsSet, 'environments.testAuth.password') ? '•••• set' : <span className="profile-dim">not set</span>}</ReadRow>
            </>}
          </>
        )
      }}
      renderEdit={(d, set) => {
        const au = d.environments.testAuth
        const setAuth = (patch: Partial<typeof au>) => set('environments', { testAuth: { ...au, ...patch } })
        return (
          <>
            <Field label="How should we test?">
              <Select value={d.environments.mode} options={['static', 'script', 'local', 'deployed']}
                onChange={(v) => set('environments', { mode: v })} />
            </Field>
            <Field label="Staging / QA URLs (one per line)"><ListTextarea rows={2} value={d.environments.staticUrls} onChange={(staticUrls) => set('environments', { staticUrls })} /></Field>
            <Field label="Build command"><input value={d.environments.buildCmd} onChange={(e) => set('environments', { buildCmd: e.target.value })} /></Field>
            <Field label="Deploy command"><input value={d.environments.deployCmd} onChange={(e) => set('environments', { deployCmd: e.target.value })} /></Field>
            <Field label="Readiness URL pattern"><input value={d.environments.readinessUrlPattern} onChange={(e) => set('environments', { readinessUrlPattern: e.target.value })} /></Field>
            <label className="profile-inline-check"><input type="checkbox" checked={!!au.required} onChange={(e) => setAuth({ required: e.target.checked })} /> Requires login</label>
            {au.required && <>
              <Field label="Login URL"><input value={au.loginUrl} onChange={(e) => setAuth({ loginUrl: e.target.value })} /></Field>
              <Field label="Test username"><input value={au.username} onChange={(e) => setAuth({ username: e.target.value })} /></Field>
              <Field label="Test password"><SecretInput isSet={isSet(secretsSet, 'environments.testAuth.password')} value={au.password} onChange={(v) => setAuth({ password: v })} /></Field>
              <Field label="Auth notes"><input value={au.notes} onChange={(e) => setAuth({ notes: e.target.value })} /></Field>
            </>}
          </>
        )
      }} />
  )
}

// ---- Issue tracker -----------------------------------------------------------
export function IssueTrackerSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Issue tracker" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => {
        const it = a.issueTracker, sm = it.statusMapping || { ready_for_qa: [], in_qa: [] }
        return (
          <>
            <ReadRow label="Tracker">{dim(it.type)}</ReadRow>
            <ReadRow label="Base URL">{dim(it.baseUrl)}</ReadRow>
            <ReadRow label="Ticket URL">{dim(it.ticketUrlTemplate)}</ReadRow>
            <ReadRow label="Projects"><Chips items={it.projects} /></ReadRow>
            <ReadRow label="Account">{dim(it.email)}</ReadRow>
            <ReadRow label="Token">{isSet(secretsSet, 'issueTracker.token') ? '•••• set' : <span className="profile-dim">not set</span>}</ReadRow>
            <ReadRow label="Access">{`read ${yn(it.access?.read)} · write ${yn(it.access?.write)}`}</ReadRow>
            <ReadRow label="“Ready for QA”"><Chips items={sm.ready_for_qa} /></ReadRow>
            <ReadRow label="“In QA”"><Chips items={sm.in_qa} /></ReadRow>
          </>
        )
      }}
      renderEdit={(d, set, sset) => {
        const it = d.issueTracker
        const sm = it.statusMapping || { ready_for_qa: [], in_qa: [] }
        const setSm = (patch: Partial<typeof sm>) => set('issueTracker', { statusMapping: { ...sm, ...patch } })
        return (
          <>
            <Field label="Issue tracker"><Select value={it.type} options={['jira', 'linear', 'azure', 'github']} onChange={(v) => set('issueTracker', { type: v })} /></Field>
            <Field label="Base URL"><input value={it.baseUrl} onChange={(e) => set('issueTracker', { baseUrl: e.target.value })} /></Field>
            <Field label="Ticket URL ({key} = ticket id)"><input value={it.ticketUrlTemplate || ''} onChange={(e) => set('issueTracker', { ticketUrlTemplate: e.target.value })} /></Field>
            <Field label="Project keys (one per line)"><ListTextarea rows={2} value={it.projects} onChange={(projects) => set('issueTracker', { projects })} /></Field>
            <Field label="Account email"><input value={it.email} onChange={(e) => set('issueTracker', { email: e.target.value })} /></Field>
            <Field label="API token"><SecretInput isSet={isSet(sset, 'issueTracker.token')} value={it.token} onChange={(token) => set('issueTracker', { token })} /></Field>
            <AccessChecks value={it.access} onChange={(access) => set('issueTracker', { access })} />
            <Field label="“Ready for QA” statuses (one per line)"><ListTextarea rows={2} value={sm.ready_for_qa} onChange={(ready_for_qa) => setSm({ ready_for_qa })} /></Field>
            <Field label="“In QA” statuses (one per line)"><ListTextarea rows={2} value={sm.in_qa} onChange={(in_qa) => setSm({ in_qa })} /></Field>
          </>
        )
      }} />
  )
}

// ---- Version control ---------------------------------------------------------
export function VcsSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Version control" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => {
        const v = a.vcs
        return (
          <>
            <ReadRow label="Provider">{dim(v.type)}</ReadRow>
            <ReadRow label="Org / workspace">{dim(v.org)}</ReadRow>
            <ReadRow label="Repos"><Chips items={v.repos} /></ReadRow>
            <ReadRow label="Token">{isSet(secretsSet, 'vcs.token') ? '•••• set' : <span className="profile-dim">not set</span>}</ReadRow>
            <ReadRow label="Access">{`read ${yn(v.access?.read)} · write ${yn(v.access?.write)}`}</ReadRow>
          </>
        )
      }}
      renderEdit={(d, set, sset) => {
        const v = d.vcs
        return (
          <>
            <Field label="Version control"><Select value={v.type} options={['github', 'bitbucket', 'azure']} onChange={(t) => set('vcs', { type: t })} /></Field>
            <Field label="Org / workspace"><input value={v.org} onChange={(e) => set('vcs', { org: e.target.value })} /></Field>
            <Field label="Repos (one per line)"><ListTextarea rows={3} value={v.repos} onChange={(repos) => set('vcs', { repos })} /></Field>
            <Field label="API token"><SecretInput isSet={isSet(sset, 'vcs.token')} value={v.token} onChange={(token) => set('vcs', { token })} /></Field>
            <AccessChecks value={v.access} onChange={(access) => set('vcs', { access })} />
          </>
        )
      }} />
  )
}

// ---- Publish targets ---------------------------------------------------------
export function PublishSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Publish targets" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => {
        const p = a.publish, c = p.confluence || ({} as OnboardingAnswers['publish']['confluence'])
        return (
          <>
            <ReadRow label="Comment on ticket">{yn(p.jiraComment)}</ReadRow>
            <ReadRow label="Comment on PR">{yn(p.prComment)}</ReadRow>
            <ReadRow label="Slack webhook">{isSet(secretsSet, 'publish.slackWebhook') ? '•••• set' : <span className="profile-dim">not set</span>}</ReadRow>
            <ReadRow label="Confluence space">{dim(c.spaceKey)}</ReadRow>
          </>
        )
      }}
      renderEdit={(d, set, sset) => {
        const p = d.publish
        const c = p.confluence || { baseUrl: '', spaceKey: '', parentPage: '', token: '' }
        const setConf = (patch: Partial<typeof c>) => set('publish', { confluence: { ...c, ...patch } })
        return (
          <>
            <label className="profile-inline-check"><input type="checkbox" checked={!!p.jiraComment} onChange={(e) => set('publish', { jiraComment: e.target.checked })} /> Post comment to ticket</label>
            <label className="profile-inline-check"><input type="checkbox" checked={!!p.prComment} onChange={(e) => set('publish', { prComment: e.target.checked })} /> Post comment to PR</label>
            <Field label="Slack webhook"><SecretInput isSet={isSet(sset, 'publish.slackWebhook')} value={p.slackWebhook} onChange={(slackWebhook) => set('publish', { slackWebhook })} /></Field>
            <Field label="Confluence base URL"><input value={c.baseUrl} onChange={(e) => setConf({ baseUrl: e.target.value })} /></Field>
            <Field label="Confluence space key"><input value={c.spaceKey} onChange={(e) => setConf({ spaceKey: e.target.value })} /></Field>
            <Field label="Confluence parent page"><input value={c.parentPage} onChange={(e) => setConf({ parentPage: e.target.value })} /></Field>
            <Field label="Confluence token"><SecretInput isSet={isSet(sset, 'publish.confluence.token')} value={c.token} onChange={(token) => setConf({ token })} /></Field>
          </>
        )
      }} />
  )
}

// ---- Product QA knowledge (the star — feeds the QA skill) ---------------------
export function ProductQaSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Product QA knowledge" answers={answers} secretsSet={secretsSet} onSave={onSave}
      subtitle="What VERDIKT knows about how your product should behave. Editing this changes the generated QA skill — rebuild to apply."
      renderView={(a) => {
        const q = a.productQA
        return (
          <>
            <ReadRow label="Critical flows"><Chips items={q.criticalFlows} /></ReadRow>
            <ReadRow label="“Save” means">{dim(q.saveSemantics)}</ReadRow>
            <ReadRow label="“Publish” means">{dim(q.publishSemantics)}</ReadRow>
            <ReadRow label="Key pages">
              {q.keyPages && q.keyPages.length
                ? <Chips items={q.keyPages.map((p) => `${p.name}: ${p.route}`)} />
                : <span className="profile-dim">—</span>}
            </ReadRow>
            <ReadRow label="Risk areas"><Chips items={q.riskAreas} /></ReadRow>
            <ReadRow label="Always check"><Chips items={q.alwaysCheck} /></ReadRow>
          </>
        )
      }}
      renderEdit={(d, set) => {
        const q = d.productQA
        return (
          <>
            <Field label="Critical user flows (one per line)"><ListTextarea rows={3} value={q.criticalFlows} onChange={(criticalFlows) => set('productQA', { criticalFlows })} /></Field>
            <Field label="What does 'Save' mean?"><textarea rows={2} value={q.saveSemantics} onChange={(e) => set('productQA', { saveSemantics: e.target.value })} /></Field>
            <Field label="What does 'Publish' mean?"><textarea rows={2} value={q.publishSemantics} onChange={(e) => set('productQA', { publishSemantics: e.target.value })} /></Field>
            <Field label="Key pages (Name | /route, one per line)"><KeyPagesTextarea value={q.keyPages} onChange={(keyPages) => set('productQA', { keyPages })} /></Field>
            <Field label="Known risk areas (one per line)"><ListTextarea rows={3} value={q.riskAreas} onChange={(riskAreas) => set('productQA', { riskAreas })} /></Field>
            <Field label="Always check (one per line)"><ListTextarea rows={2} value={q.alwaysCheck} onChange={(alwaysCheck) => set('productQA', { alwaysCheck })} /></Field>
          </>
        )
      }} />
  )
}

// ---- Knowledge source --------------------------------------------------------
export function KnowledgeSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Knowledge source" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => {
        const k = a.knowledge
        return (
          <>
            <ReadRow label="Provider">{dim(k.provider)}</ReadRow>
            <ReadRow label="Link">{dim(k.link)}</ReadRow>
            <ReadRow label="Token">{isSet(secretsSet, 'knowledge.token') ? '•••• set' : <span className="profile-dim">not set</span>}</ReadRow>
            <ReadRow label="Access">{`read ${yn(k.access?.read)} · write ${yn(k.access?.write)}`}</ReadRow>
          </>
        )
      }}
      renderEdit={(d, set, sset) => {
        const k = d.knowledge
        return (
          <>
            <Field label="Provider"><Select value={k.provider} options={['none', 'notion', 'confluence']} onChange={(provider) => set('knowledge', { provider })} /></Field>
            <Field label="Link to docs"><input value={k.link} onChange={(e) => set('knowledge', { link: e.target.value })} /></Field>
            <Field label="Read token"><SecretInput isSet={isSet(sset, 'knowledge.token')} value={k.token} onChange={(token) => set('knowledge', { token })} /></Field>
            <AccessChecks value={k.access} onChange={(access) => set('knowledge', { access })} />
          </>
        )
      }} />
  )
}

// ---- API / Postman -----------------------------------------------------------
export function ApiSection({ answers, secretsSet, onSave }: SectionProps) {
  const [postmanMsg, setPostmanMsg] = useState('')
  return (
    <SectionCard title="API / Postman" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={(a) => (
        <>
          <ReadRow label="Base URL">{dim(a.api?.baseUrl)}</ReadRow>
          <ReadRow label="Postman collection">{a.api?.postmanCollectionPath ? <span>{a.api.postmanCollectionPath}</span> : <span className="profile-dim">(none)</span>}</ReadRow>
        </>
      )}
      renderEdit={(d, set) => (
        <>
          <Field label="Base URL"><input value={d.api?.baseUrl || ''} onChange={(e) => set('api', { baseUrl: e.target.value, postmanCollectionPath: d.api?.postmanCollectionPath || '' })} /></Field>
          <Field label="Postman collection">
            <input type="file" accept="application/json,.json" onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return
              setPostmanMsg('Uploading…')
              const r = await uploadPostman(f)
              if (r.ok) { set('api', { postmanCollectionPath: r.path || d.api?.postmanCollectionPath || '' }); setPostmanMsg(`Stored — ${r.endpointCount} endpoints parsed`) }
              else setPostmanMsg(`Error: ${r.error}`)
            }} />
          </Field>
          <div className="profile-dim">Collection: {d.api?.postmanCollectionPath || '(none)'} {postmanMsg && `· ${postmanMsg}`}</div>
        </>
      )} />
  )
}

// ---- Advanced: qaTargets (raw JSON) ------------------------------------------
export function QaTargetsSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Advanced — QA targeting (qaTargets)" answers={answers} secretsSet={secretsSet} onSave={onSave}
      subtitle="Seed entities + classify rules that steer the QA runner. Takes effect on the next QA run — no skill rebuild needed. Empty = generic default ruleset."
      renderView={(a) => {
        const t = a.qaTargets
        if (!t || Object.keys(t).length === 0) return <span className="profile-dim">Using defaults (generic ruleset)</span>
        return (
          <>
            <ReadRow label="Seed entities"><Chips items={t.seedEntities} /></ReadRow>
            <ReadRow label="Entity-dependent types"><Chips items={t.entityDependentTypes ? Object.keys(t.entityDependentTypes) : []} /></ReadRow>
            <ReadRow label="Classify rules">{t.classifyRules?.length ? `${t.classifyRules.length} rule(s)` : <span className="profile-dim">—</span>}</ReadRow>
          </>
        )
      }}
      renderEdit={(d, _set, _sset, replace) => <QaTargetsEditor value={d.qaTargets} onChange={(v) => replace('qaTargets', v)} />} />
  )
}

function QaTargetsEditor({ value, onChange }: { value?: OnboardingAnswers['qaTargets']; onChange: (v: OnboardingAnswers['qaTargets']) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2))
  const [err, setErr] = useState('')
  return (
    <Field label="qaTargets (JSON)">
      <textarea rows={10} spellCheck={false} value={text} style={{ fontFamily: 'monospace', fontSize: 12 }}
        onChange={(e) => {
          const t = e.target.value
          setText(t)
          try {
            const parsed = t.trim() ? JSON.parse(t) : undefined
            setErr('')
            onChange(parsed)
          } catch (ex) {
            setErr((ex as Error).message)  // keep last valid value; block is visual only
          }
        }} />
      {err && <span className="profile-json-err">Invalid JSON: {err}</span>}
    </Field>
  )
}

// ---- Anthropic key -----------------------------------------------------------
export function AnthropicSection({ answers, secretsSet, onSave }: SectionProps) {
  return (
    <SectionCard title="Claude API key" answers={answers} secretsSet={secretsSet} onSave={onSave}
      renderView={() => (
        <ReadRow label="Anthropic key">{isSet(secretsSet, 'anthropicKey') ? '•••• set' : <span className="profile-dim">not set</span>}</ReadRow>
      )}
      renderEdit={(d, _set, sset, replace) => (
        <Field label="Anthropic / Claude API key"><SecretInput isSet={isSet(sset, 'anthropicKey')} value={d.anthropicKey} onChange={(v) => replace('anthropicKey', v)} /></Field>
      )} />
  )
}
