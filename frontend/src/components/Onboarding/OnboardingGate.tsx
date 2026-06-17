import { useEffect, useState } from 'react'
import { getOnboardingStatus } from '../../api'
import OnboardingWizard from './OnboardingWizard'
import App from '../../App'
import '../../styles/onboarding.css'

type GateState = 'loading' | 'onboarding' | 'ready'

export default function OnboardingGate() {
  const [state, setState] = useState<GateState>('loading')

  async function check() {
    try {
      const status = await getOnboardingStatus()
      setState(status.configured ? 'ready' : 'onboarding')
    } catch {
      // Backend unreachable or old: don't trap the user in onboarding — show the dashboard.
      setState('ready')
    }
  }

  useEffect(() => {
    check()
  }, [])

  if (state === 'loading') {
    return <div className="ob-loading">Loading…</div>
  }
  if (state === 'onboarding') {
    return <OnboardingWizard onComplete={() => setState('ready')} />
  }
  return <App />
}
