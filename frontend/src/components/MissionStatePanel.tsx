import type { MissionState, RiskLevel } from '../types/domain';

const RISK_COLOURS: Record<RiskLevel, string> = {
  LOW: '#22c55e',
  MEDIUM: '#eab308',
  HIGH: '#f97316',
  CRITICAL: '#ef4444',
};

interface Props {
  missionState: MissionState;
}

export function MissionStatePanel({ missionState: ms }: Props) {
  const badgeStyle = {
    background: RISK_COLOURS[ms.risk_level],
    color: '#fff',
    borderRadius: '4px',
    padding: '2px 8px',
    fontWeight: 700,
  };

  return (
    <section className="panel">
      <h2>Mission State</h2>
      <table>
        <tbody>
          <tr><td>ID</td><td>{ms.mission_id}</td></tr>
          <tr><td>Phase</td><td>{ms.mission_phase}</td></tr>
          <tr><td>Event</td><td>{ms.current_event}</td></tr>
          <tr><td>Event remaining</td><td>{ms.event_time_remaining_s.toFixed(1)} s</td></tr>
          <tr><td>Comm window</td><td>{ms.comm_window_remaining_s.toFixed(1)} s</td></tr>
          <tr>
            <td>Risk score</td>
            <td>{ms.risk_score.toFixed(3)} <span style={badgeStyle}>{ms.risk_level}</span></td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}
