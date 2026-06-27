import { Ticket } from './types'
import { isTicketQAed } from './components/QueueRow'

export interface TicketGroup { key: string; title: string; tickets: Ticket[] }
export interface FeatureSummary { key: string; title: string; total: number; qaed: number }

const UNGROUPED = '__ungrouped__'

/** Group tickets by epic (parent) or by first label. Tickets missing the key collect
 *  into a trailing 'Ungrouped' group. Real groups are ordered by size (desc). */
export function groupTickets(tickets: Ticket[], by: 'epic' | 'label'): TicketGroup[] {
  const groups = new Map<string, TicketGroup>()
  const ungrouped: Ticket[] = []
  for (const t of tickets) {
    let key: string | null = null
    let title = ''
    if (by === 'epic') {
      if (t.parent) { key = t.parent.key; title = t.parent.title || t.parent.key }
    } else {
      const first = (t.labels && t.labels[0]) || ''
      if (first) { key = first; title = first }
    }
    if (!key) { ungrouped.push(t); continue }
    let g = groups.get(key)
    if (!g) { g = { key, title, tickets: [] }; groups.set(key, g) }
    g.tickets.push(t)
  }
  const ordered = Array.from(groups.values()).sort((a, b) => b.tickets.length - a.tickets.length)
  if (ungrouped.length) ordered.push({ key: UNGROUPED, title: 'Ungrouped', tickets: ungrouped })
  return ordered
}

/** Top-N "features" by ticket count for the hero card. A feature is an epic (parent);
 *  epic-less tickets roll up under their first label so the card is never empty. */
export function topFeatures(tickets: Ticket[], n: number): FeatureSummary[] {
  const map = new Map<string, FeatureSummary>()
  for (const t of tickets) {
    let key: string
    let title: string
    if (t.parent) { key = `epic:${t.parent.key}`; title = t.parent.title || t.parent.key }
    else { const lbl = (t.labels && t.labels[0]) || 'Other'; key = `label:${lbl}`; title = lbl }
    let f = map.get(key)
    if (!f) { f = { key, title, total: 0, qaed: 0 }; map.set(key, f) }
    f.total += 1
    if (isTicketQAed(t)) f.qaed += 1
  }
  return Array.from(map.values()).sort((a, b) => b.total - a.total).slice(0, n)
}
