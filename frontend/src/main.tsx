import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import OnboardingGate from './components/Onboarding/OnboardingGate'
import './styles/theme.css'
import './styles/layout.css'
import './styles/chat.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <OnboardingGate />
  </StrictMode>,
)
