// Framework-free tests for src/redact.ts — run via `npm test` (esbuild → node).
import { redactCredentials } from '../src/redact'

let passed = 0
let failed = 0

function eq<T>(actual: T, expected: T, label: string) {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a === e) {
    passed++
  } else {
    failed++
    console.error(`  FAIL ${label}\n    expected ${e}\n    got      ${a}`)
  }
}

// --- the real case from the NOR board ---------------------------------------
{
  const real = 'Demo users (password `demo1234`): `manager@northstar.demo` (Manager, tenant-ca)'
  const out = redactCredentials(real)
  eq(out.includes('demo1234'), false, 'the actual password never survives redaction')
  eq(out, 'Demo users (password `••••••`): `manager@northstar.demo` (Manager, tenant-ca)',
    'backtick-quoted password is masked, the rest of the line is untouched')
}

// --- separator forms --------------------------------------------------------
{
  eq(redactCredentials('password: hunter2'), 'password: ••••••', 'colon form')
  eq(redactCredentials('Password = hunter2'), 'Password = ••••••', 'equals form, case-insensitive')
  eq(redactCredentials('pwd: hunter2'), 'pwd: ••••••', 'pwd abbreviation')
  eq(redactCredentials('passcode "hunter2"'), 'passcode "••••••"', 'double-quoted value')
  eq(redactCredentials("passphrase 'hunter2'"), "passphrase '••••••'", 'single-quoted value')
}

// --- false positives: prose about passwords must stay readable --------------
{
  // No separator and no quoting -> this is prose, not a credential. Redacting here
  // would mangle ordinary acceptance criteria.
  eq(redactCredentials('Password reset flow sends an email'),
    'Password reset flow sends an email', 'prose "password reset" is not redacted')
  eq(redactCredentials('The user must change their password after first login'),
    'The user must change their password after first login', 'trailing prose untouched')
}

// --- other credential shapes ------------------------------------------------
{
  eq(redactCredentials('token: lin_api_abc123def456').includes('lin_api_abc123def456'), false,
    'api token value is masked')
  eq(redactCredentials('api_key = sk-ant-abc123').includes('sk-ant-abc123'), false,
    'api key value is masked')
}

// --- safety -----------------------------------------------------------------
{
  eq(redactCredentials(''), '', 'empty string is safe')
  eq(redactCredentials('no secrets here'), 'no secrets here', 'clean text passes through')
}

// --- report -----------------------------------------------------------------
console.log(`\nredact: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
