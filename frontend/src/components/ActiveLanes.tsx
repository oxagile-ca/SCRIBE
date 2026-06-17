import { Lane } from '../types'
import { EvidenceHistoryItem } from '../api'
import LaneCard from './LaneCard'

const MAX_LANES = 3

interface Props {
  lanes: Lane[]
  onCancel: (laneId: string) => void
  onStartNext: () => void
  onCheckEvidence: (laneId: string) => void
  onCheckDeploy: (laneId: string) => void
  onRunCommand: (laneId: string, command: string) => void
  onGenerateReport: (laneId: string) => void
  onResume: (laneId: string) => void
  onOverrideCouncil: (laneId: string, reason: string) => Promise<void>
  onStartFromQuartermaster: (lane: Lane) => void
  evidenceHistory?: EvidenceHistoryItem[]
  needsBuildDeploy?: boolean
}

export default function ActiveLanes({ lanes, onCancel, onStartNext, onCheckEvidence, onCheckDeploy, onRunCommand, onGenerateReport, onResume, onOverrideCouncil, onStartFromQuartermaster, evidenceHistory = [], needsBuildDeploy = true }: Props) {
  const emptySlots = MAX_LANES - lanes.length

  return (
    <div className="lanes">
      {lanes.map(lane => {
        const freshEvidence = evidenceHistory.find(e => e.key === lane.ticket.key)
        const laneWithFreshReport = freshEvidence?.reportUrl
          ? { ...lane, reportUrl: freshEvidence.reportUrl }
          : lane
        return (
          <LaneCard key={lane.id} lane={laneWithFreshReport} onCancel={onCancel} onCheckEvidence={onCheckEvidence} onCheckDeploy={onCheckDeploy} onRunCommand={onRunCommand} onGenerateReport={onGenerateReport} onResume={onResume} onOverrideCouncil={onOverrideCouncil} onStartFromQuartermaster={onStartFromQuartermaster} needsBuildDeploy={needsBuildDeploy} />
        )
      })}
      {emptySlots > 0 && (
        <div className="lane-card lane-card--empty" onClick={onStartNext}>
          + Start Next Ticket
        </div>
      )}
    </div>
  )
}
