// Masked secret input shared by the Settings modal and the Application Profile page.
// Blank submit keeps the existing secret (backend treats blank = keep); typing replaces it.
export default function SecretInput({
  isSet, value, onChange,
}: {
  isSet: boolean
  value: string
  onChange: (v: string) => void
}) {
  return (
    <input
      type="password"
      value={value}
      placeholder={isSet ? '•••• set — leave blank to keep' : 'not set'}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
