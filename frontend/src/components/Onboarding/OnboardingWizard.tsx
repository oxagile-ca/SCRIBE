import { useState, type ReactNode } from 'react'
import {
  OnboardingAnswers,
  emptyAnswers,
  Access,
  IssueType,
  VcsType,
  EnvMode,
  KnowledgeProvider,
} from '../../onboardingSchema'
import { submitOnboarding } from '../../api'

const STEPS = [
  'Company & product',
  'Environments',
  'Issue tracker',
  'Version control',
  'Publish targets',
  'Product QA knowledge',
  'Knowledge source',
  'Anthropic key',
  'Review & generate',
]

// ---- small helpers -----------------------------------------------------------
const linesToArr = (s: string) => s.split('\n').map((l) => l.trim()).filter(Boolean)
const arrToLines = (a: string[]) => a.join('\n')

function AccessChecks({ value, onChange }: { value: Access; onChange: (a: Access) => void }) {
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="ob-field">
      <span>{label}</span>
      {children}
    </label>
  )
}

export default function OnboardingWizard({ onComplete }: { onComplete: () => void }) {
  const [answers, setAnswers] = useState<OnboardingAnswers>(emptyAnswers)
  const [step, setStep] = useState(0)
  const [errors, setErrors] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState<Record<string, unknown> | null>(null)

  // section-level merge helper (one level of nesting)
  function set<K extends keyof OnboardingAnswers>(section: K, patch: Partial<OnboardingAnswers[K]>) {
    setAnswers((a) => ({ ...a, [section]: { ...(a[section] as object), ...patch } as OnboardingAnswers[K] }))
  }

  async function generate() {
    setSubmitting(true)
    setErrors([])
    const res = await submitOnboarding(answers)
    setSubmitting(false)
    if (res.ok) {
      setDone(res.summary ?? {})
    } else {
      setErrors(res.errors ?? [res.error ?? 'Onboarding failed'])
    }
  }

  const c = answers.company
  const env = answers.environments
  const it = answers.issueTracker
  const vcs = answers.vcs
  const pub = answers.publish
  const qa = answers.productQA
  const kn = answers.knowledge

  function renderStep() {
    switch (step) {
      case 0:
        return (
          <>
            <Field label="Organization name">
              <input value={c.orgName} onChange={(e) => set('company', { orgName: e.target.value })} />
            </Field>
            <Field label="Product name *">
              <input value={c.productName} onChange={(e) => set('company', { productName: e.target.value })} />
            </Field>
            <Field label="Product description (what it does, who uses it)">
              <textarea rows={3} value={c.description} onChange={(e) => set('company', { description: e.target.value })} />
            </Field>
            <Field label="Product type">
              <select value={c.productType} onChange={(e) => set('company', { productType: e.target.value })}>
                <option value="cms">CMS</option>
                <option value="webapp">Web app</option>
                <option value="api">API</option>
                <option value="ecommerce">E-commerce</option>
                <option value="other">Other</option>
              </select>
            </Field>
            <Field label="Primary URLs (one per line)">
              <textarea rows={2} value={arrToLines(c.urls)} onChange={(e) => set('company', { urls: linesToArr(e.target.value) })} />
            </Field>
          </>
        )
      case 1:
        return (
          <>
            <Field label="How should we test?">
              <select value={env.mode} onChange={(e) => set('environments', { mode: e.target.value as EnvMode })}>
                <option value="static">Static staging URL (no deploy)</option>
                <option value="script">Build &amp; deploy via scripts</option>
                <option value="local">Local dev server</option>
                <option value="deployed">Already-deployed env</option>
              </select>
            </Field>
            {(env.mode === 'static' || env.mode === 'deployed') && (
              <Field label="Staging / QA URLs (one per line) *">
                <textarea rows={2} value={arrToLines(env.staticUrls)} onChange={(e) => set('environments', { staticUrls: linesToArr(e.target.value) })} />
              </Field>
            )}
            {env.mode === 'local' && (
              <Field label="Local dev server URL (e.g. http://localhost:3000)">
                <textarea rows={1} value={arrToLines(env.staticUrls)} onChange={(e) => set('environments', { staticUrls: linesToArr(e.target.value) })} />
              </Field>
            )}
            {env.mode === 'script' && (
              <>
                <Field label="Build command *">
                  <input value={env.buildCmd} onChange={(e) => set('environments', { buildCmd: e.target.value })} placeholder="docker build -t {snapshot} ." />
                </Field>
                <Field label="Deploy command *">
                  <input value={env.deployCmd} onChange={(e) => set('environments', { deployCmd: e.target.value })} placeholder="./deploy.sh {env} {snapshot}" />
                </Field>
                <Field label="Readiness URL pattern">
                  <input value={env.readinessUrlPattern} onChange={(e) => set('environments', { readinessUrlPattern: e.target.value })} placeholder="https://{env}.example.com" />
                </Field>
              </>
            )}
            <label className="ob-inline">
              <input type="checkbox" checked={env.testAuth.required} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, required: e.target.checked } })} />
              The test environment requires login
            </label>
            {env.testAuth.required && (
              <>
                <Field label="Login URL">
                  <input value={env.testAuth.loginUrl} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, loginUrl: e.target.value } })} />
                </Field>
                <Field label="Test username">
                  <input value={env.testAuth.username} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, username: e.target.value } })} />
                </Field>
                <Field label="Test password (stored encrypted, never committed)">
                  <input type="password" value={env.testAuth.password} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, password: e.target.value } })} />
                </Field>
                <Field label="Auth notes (SSO, MFA, etc.)">
                  <input value={env.testAuth.notes} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, notes: e.target.value } })} />
                </Field>
              </>
            )}
          </>
        )
      case 2:
        return (
          <>
            <Field label="Issue tracker *">
              <select value={it.type} onChange={(e) => set('issueTracker', { type: e.target.value as IssueType })}>
                <option value="jira">Jira</option>
                <option value="linear">Linear</option>
                <option value="azure">Azure DevOps Boards</option>
                <option value="github">GitHub Issues</option>
              </select>
            </Field>
            <Field label="Base URL / workspace">
              <input value={it.baseUrl} onChange={(e) => set('issueTracker', { baseUrl: e.target.value })} placeholder="https://acme.atlassian.net" />
            </Field>
            <Field label="Project keys (one per line)">
              <textarea rows={2} value={arrToLines(it.projects)} onChange={(e) => set('issueTracker', { projects: linesToArr(e.target.value) })} />
            </Field>
            <Field label="Account email">
              <input value={it.email} onChange={(e) => set('issueTracker', { email: e.target.value })} />
            </Field>
            <Field label="API token">
              <input type="password" value={it.token} onChange={(e) => set('issueTracker', { token: e.target.value })} />
            </Field>
            <AccessChecks value={it.access} onChange={(access) => set('issueTracker', { access })} />
          </>
        )
      case 3:
        return (
          <>
            <Field label="Version control *">
              <select value={vcs.type} onChange={(e) => set('vcs', { type: e.target.value as VcsType })}>
                <option value="github">GitHub</option>
                <option value="bitbucket">Bitbucket</option>
                <option value="azure">Azure DevOps Repos</option>
              </select>
            </Field>
            <Field label="Org / workspace">
              <input value={vcs.org} onChange={(e) => set('vcs', { org: e.target.value })} />
            </Field>
            <Field label="Repos (one per line)">
              <textarea rows={3} value={arrToLines(vcs.repos)} onChange={(e) => set('vcs', { repos: linesToArr(e.target.value) })} />
            </Field>
            <Field label="API token">
              <input type="password" value={vcs.token} onChange={(e) => set('vcs', { token: e.target.value })} />
            </Field>
            <AccessChecks value={vcs.access} onChange={(access) => set('vcs', { access })} />
          </>
        )
      case 4:
        return (
          <>
            <label className="ob-inline">
              <input type="checkbox" checked={pub.jiraComment} onChange={(e) => set('publish', { jiraComment: e.target.checked })} /> Post a comment to the ticket
            </label>
            <label className="ob-inline">
              <input type="checkbox" checked={pub.prComment} onChange={(e) => set('publish', { prComment: e.target.checked })} /> Post a comment to the PR
            </label>
            <Field label="Slack webhook URL (optional)">
              <input value={pub.slackWebhook} onChange={(e) => set('publish', { slackWebhook: e.target.value })} />
            </Field>
            <fieldset className="ob-fieldset">
              <legend>Confluence (optional)</legend>
              <Field label="Base URL">
                <input value={pub.confluence.baseUrl} onChange={(e) => set('publish', { confluence: { ...pub.confluence, baseUrl: e.target.value } })} />
              </Field>
              <Field label="Space key">
                <input value={pub.confluence.spaceKey} onChange={(e) => set('publish', { confluence: { ...pub.confluence, spaceKey: e.target.value } })} />
              </Field>
              <Field label="Parent page">
                <input value={pub.confluence.parentPage} onChange={(e) => set('publish', { confluence: { ...pub.confluence, parentPage: e.target.value } })} />
              </Field>
              <Field label="Token">
                <input type="password" value={pub.confluence.token} onChange={(e) => set('publish', { confluence: { ...pub.confluence, token: e.target.value } })} />
              </Field>
            </fieldset>
          </>
        )
      case 5:
        return (
          <>
            <p className="ob-hint">This is what personalizes your QA skill — what to test, and what counts as save/publish.</p>
            <Field label="Critical user flows (one per line)">
              <textarea rows={3} value={arrToLines(qa.criticalFlows)} onChange={(e) => set('productQA', { criticalFlows: linesToArr(e.target.value) })} placeholder="Create and publish an article" />
            </Field>
            <Field label="What does 'Save' mean in your product?">
              <textarea rows={2} value={qa.saveSemantics} onChange={(e) => set('productQA', { saveSemantics: e.target.value })} />
            </Field>
            <Field label="What does 'Publish' mean in your product?">
              <textarea rows={2} value={qa.publishSemantics} onChange={(e) => set('productQA', { publishSemantics: e.target.value })} />
            </Field>
            <Field label="Key pages (one per line: Name | /route)">
              <textarea
                rows={3}
                value={qa.keyPages.map((p) => `${p.name} | ${p.route}`).join('\n')}
                onChange={(e) =>
                  set('productQA', {
                    keyPages: linesToArr(e.target.value).map((line) => {
                      const [name, route] = line.split('|')
                      return { name: (name || '').trim(), route: (route || '').trim() }
                    }),
                  })
                }
              />
            </Field>
            <Field label="Known risk areas / past bugs (one per line)">
              <textarea rows={3} value={arrToLines(qa.riskAreas)} onChange={(e) => set('productQA', { riskAreas: linesToArr(e.target.value) })} />
            </Field>
            <Field label="Always check (one per line)">
              <textarea rows={2} value={arrToLines(qa.alwaysCheck)} onChange={(e) => set('productQA', { alwaysCheck: linesToArr(e.target.value) })} placeholder="No console errors" />
            </Field>
          </>
        )
      case 6:
        return (
          <>
            <p className="ob-hint">Give read access to a Notion or Confluence space so we can pull product context and knowledge.</p>
            <Field label="Provider">
              <select value={kn.provider} onChange={(e) => set('knowledge', { provider: e.target.value as KnowledgeProvider })}>
                <option value="none">None</option>
                <option value="notion">Notion</option>
                <option value="confluence">Confluence</option>
              </select>
            </Field>
            {kn.provider !== 'none' && (
              <>
                <Field label="Link to the docs space / page">
                  <input value={kn.link} onChange={(e) => set('knowledge', { link: e.target.value })} placeholder="https://www.notion.so/acme/Product-Docs" />
                </Field>
                <Field label="Read token / integration secret">
                  <input type="password" value={kn.token} onChange={(e) => set('knowledge', { token: e.target.value })} />
                </Field>
                <AccessChecks value={kn.access} onChange={(access) => set('knowledge', { access })} />
              </>
            )}
          </>
        )
      case 7:
        return (
          <Field label="Anthropic API key (the runner uses this; stored encrypted)">
            <input type="password" value={answers.anthropicKey} onChange={(e) => setAnswers((a) => ({ ...a, anthropicKey: e.target.value }))} placeholder="sk-ant-..." />
          </Field>
        )
      case 8:
        return (
          <div className="ob-review">
            <ul>
              <li><b>Product:</b> {c.productName || '—'} ({c.productType})</li>
              <li><b>Test mode:</b> {env.mode}{env.staticUrls[0] ? ` → ${env.staticUrls[0]}` : ''}</li>
              <li><b>Issue tracker:</b> {it.type} (read:{String(it.access.read)} write:{String(it.access.write)})</li>
              <li><b>VCS:</b> {vcs.type} (read:{String(vcs.access.read)} write:{String(vcs.access.write)})</li>
              <li><b>Knowledge:</b> {kn.provider}{kn.provider !== 'none' && kn.link ? ` → ${kn.link}` : ''}</li>
              <li><b>Critical flows:</b> {qa.criticalFlows.length}</li>
              <li><b>Risk-area patterns:</b> {qa.riskAreas.length}</li>
              <li><b>Anthropic key:</b> {answers.anthropicKey ? 'set' : 'not set'}</li>
            </ul>
            {errors.length > 0 && (
              <div className="ob-errors">
                <b>Please fix:</b>
                <ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
              </div>
            )}
          </div>
        )
      default:
        return null
    }
  }

  if (done) {
    return (
      <div className="ob-overlay">
        <div className="ob-card ob-done">
          <h2>✅ {String(done.productName ?? 'Your instance')} is set up</h2>
          <p>
            Backend config, secrets, a product-customized <code>/qa-evidence</code> skill, and{' '}
            {String(done.patternRules ?? 0)} risk-area pattern(s) were generated.
          </p>
          <button className="ob-primary" onClick={onComplete}>Enter dashboard →</button>
        </div>
      </div>
    )
  }

  return (
    <div className="ob-overlay">
      <div className="ob-card">
        <div className="ob-head">
          <h2>Set up QA Pilot</h2>
          <div className="ob-progress">Step {step + 1} of {STEPS.length} — {STEPS[step]}</div>
          <div className="ob-bar"><div style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div>
        </div>

        <div className="ob-body">{renderStep()}</div>

        <div className="ob-foot">
          <button disabled={step === 0 || submitting} onClick={() => setStep((s) => s - 1)}>Back</button>
          {step < STEPS.length - 1 ? (
            <button className="ob-primary" onClick={() => setStep((s) => s + 1)}>Next</button>
          ) : (
            <button className="ob-primary" disabled={submitting} onClick={generate}>
              {submitting ? 'Generating…' : 'Generate setup'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
