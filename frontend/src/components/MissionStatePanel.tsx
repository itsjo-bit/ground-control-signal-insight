import type { MissionState, RiskLevel } from '../types/domain';

interface BadgeTokens {
  background: string;
  color: string;
  border: string;
  boxShadow: string;
}

const RISK_BADGE: Record<RiskLevel, BadgeTokens> = {
  LOW: {
    background: 'var(--risk-low-bg)',
    color:      'var(--risk-low-color)',
    border:     `1px solid var(--risk-low-border)`,
    boxShadow:  'var(--risk-low-glow)',
  },
  MEDIUM: {
    background: 'var(--risk-medium-bg)',
    color:      'var(--risk-medium-color)',
    border:     `1px solid var(--risk-medium-border)`,
    boxShadow:  'var(--risk-medium-glow)',
  },
  HIGH: {
    background: 'var(--risk-high-bg)',
    color:      'var(--risk-high-color)',
    border:     `1px solid var(--risk-high-border)`,
    boxShadow:  'var(--risk-high-glow)',
  },
  CRITICAL: {
    background: 'var(--risk-critical-bg)',
    color:      'var(--risk-critical-color)',
    border:     `1px solid var(--risk-critical-border)`,
    boxShadow:  'var(--risk-critical-glow)',
  },
};

interface Props {
  missionState: MissionState;
}

export function MissionStatePanel({ missionState: ms }: Props) {
  const tokens = RISK_BADGE[ms.risk_level];
  const badgeStyle = {
    ...tokens,
    borderRadius: '4px',
    padding: '2px 9px',
    fontWeight: 700 as const,
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
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
            <td>Mission risk</td>
            <td>{ms.risk_score.toFixed(3)} <span style={badgeStyle}>{ms.risk_level}</span></td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}
