// frontend/src/components/UsageBreakdown.tsx
import { TicketUsage } from '../types'

export function UsageBreakdown({ usage }: { usage: TicketUsage }) {
  if (!usage || usage.tasks.length === 0) {
    return <div className="usage-breakdown usage-breakdown--empty">No AI spend recorded yet.</div>
  }
  return (
    <table className="usage-breakdown" style={{ width: '100%', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
          <th>Task</th><th>Model</th><th style={{ textAlign: 'right' }}>In/Out tok</th><th style={{ textAlign: 'right' }}>Cost</th>
        </tr>
      </thead>
      <tbody>
        {usage.tasks.map((t, i) => (
          <tr key={`${t.task}-${t.model}-${i}`}>
            <td>{t.task}</td>
            <td>{t.model ?? '—'}</td>
            <td style={{ textAlign: 'right' }}>
              {t.input_tokens == null ? '—' : `${t.input_tokens}/${t.output_tokens}`}
            </td>
            <td style={{ textAlign: 'right' }}>${t.cost_usd.toFixed(4)}</td>
          </tr>
        ))}
        <tr style={{ fontWeight: 600, borderTop: '1px solid var(--border)' }}>
          <td colSpan={2}>Total</td>
          <td style={{ textAlign: 'right' }}>{usage.total_input_tokens}/{usage.total_output_tokens}</td>
          <td style={{ textAlign: 'right' }}>${usage.total_cost_usd.toFixed(4)}</td>
        </tr>
      </tbody>
    </table>
  )
}
