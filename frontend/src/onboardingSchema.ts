// Shape of the onboarding wizard answers. Mirrors backend/onboarding.py expectations.

export type Access = { read: boolean; write: boolean }

export type IssueType = 'jira' | 'linear' | 'azure' | 'github'
export type VcsType = 'github' | 'bitbucket' | 'azure'
export type EnvMode = 'static' | 'script' | 'local' | 'deployed'
export type KnowledgeProvider = 'none' | 'notion' | 'confluence'

export interface OnboardingAnswers {
  company: {
    orgName: string
    productName: string
    description: string
    productType: string
    urls: string[]
  }
  environments: {
    mode: EnvMode
    staticUrls: string[]
    buildCmd: string
    deployCmd: string
    readinessUrlPattern: string
    testAuth: {
      required: boolean
      loginUrl: string
      username: string
      password: string
      notes: string
    }
  }
  issueTracker: {
    type: IssueType
    baseUrl: string
    projects: string[]
    email: string
    token: string
    access: Access
    statusMapping: { ready_for_qa: string[]; in_qa: string[] }
  }
  vcs: {
    type: VcsType
    org: string
    repos: string[]
    token: string
    access: Access
  }
  publish: {
    jiraComment: boolean
    prComment: boolean
    slackWebhook: string
    confluence: { baseUrl: string; spaceKey: string; parentPage: string; token: string }
  }
  productQA: {
    criticalFlows: string[]
    saveSemantics: string
    publishSemantics: string
    keyPages: { name: string; route: string }[]
    riskAreas: string[]
    alwaysCheck: string[]
  }
  knowledge: {
    provider: KnowledgeProvider
    link: string
    token: string
    access: Access
  }
  api?: {
    baseUrl: string
    postmanCollectionPath: string
  }
  anthropicKey: string
}

export function emptyAnswers(): OnboardingAnswers {
  return {
    company: { orgName: '', productName: '', description: '', productType: 'webapp', urls: [] },
    environments: {
      mode: 'static',
      staticUrls: [],
      buildCmd: '',
      deployCmd: '',
      readinessUrlPattern: '',
      testAuth: { required: false, loginUrl: '', username: '', password: '', notes: '' },
    },
    issueTracker: {
      type: 'jira',
      baseUrl: '',
      projects: [],
      email: '',
      token: '',
      access: { read: true, write: true },
      statusMapping: { ready_for_qa: ['Ready for QA'], in_qa: ['In QA'] },
    },
    vcs: { type: 'github', org: '', repos: [], token: '', access: { read: true, write: true } },
    publish: {
      jiraComment: true,
      prComment: true,
      slackWebhook: '',
      confluence: { baseUrl: '', spaceKey: '', parentPage: '', token: '' },
    },
    productQA: {
      criticalFlows: [],
      saveSemantics: '',
      publishSemantics: '',
      keyPages: [],
      riskAreas: [],
      alwaysCheck: [],
    },
    knowledge: { provider: 'none', link: '', token: '', access: { read: true, write: false } },
    anthropicKey: '',
  }
}
