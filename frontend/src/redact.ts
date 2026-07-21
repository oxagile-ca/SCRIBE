/**
 * Mask credentials in ticket text before it is DISPLAYED.
 *
 * Ticket descriptions routinely carry demo logins — the NOR board ships
 * "Demo users (password `demo1234`)" straight into its acceptance criteria, and
 * the dashboard rendered it verbatim on the queue.
 *
 * DISPLAY ONLY. The same description is sent to the QA runner via qa_targets,
 * and the run genuinely needs the password to authenticate. This function must
 * never be applied to data on its way to the backend — only to what a human
 * reads on screen.
 */

const MASK = '••••••'

/** Labels whose value is a credential. */
const LABEL = String.raw`pass(?:word|code|phrase)?|pwd|secret|token|api[_-]?key|auth`

/**
 * A credential is a label followed EITHER by a quoted value or by a
 * separator (`:` / `=`) and a bare value.
 *
 * The quoting/separator requirement is what keeps ordinary prose intact:
 * "Password reset flow" has neither, so it is left alone. Without that guard
 * this would silently mangle acceptance criteria that merely discuss passwords.
 */
const QUOTED = new RegExp(String.raw`\b(${LABEL})(\s*[:=]?\s*)([\`"'])([^\`"']+)\3`, 'gi')
const BARE = new RegExp(String.raw`\b(${LABEL})(\s*[:=]\s*)(\S+)`, 'gi')

export function redactCredentials(text: string): string {
  if (!text) return text
  return text
    .replace(QUOTED, (_m, label, sep, quote) => `${label}${sep}${quote}${MASK}${quote}`)
    .replace(BARE, (_m, label, sep) => `${label}${sep}${MASK}`)
}
