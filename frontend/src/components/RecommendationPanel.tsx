import type { AIRecommendation, RiskLevel } from '../types/domain';

const RISK_COLOURS: Record<RiskLevel, string> = {
  LOW: '#22c55e',
  MEDIUM: '#eab308',
  HIGH: '#f97316',
  CRITICAL: '#ef4444',
};

interface Props {
  recommendation: AIRecommendation;
}

export function RecommendationPanel({ recommendation: rec }: Props) {
  const badgeStyle = {
    background: RISK_COLOURS[rec.risk_level],
    color: '#fff',
    borderRadius: '4px',
    padding: '2px 8px',
    fontWeight: 700,
  };

  return (
    <section className="panel">
      <h2>AI Recommendation</h2>

      <p>
        <strong>Recommended plan:</strong> <code>{rec.recommended_plan_id}</code>
        {rec.alternative_plan_id && (
          <> &nbsp;| Alternative: <code>{rec.alternative_plan_id}</code></>
        )}
      </p>

      <p>
        <strong>Confidence:</strong> {(rec.confidence * 100).toFixed(0)}%
        &nbsp;
        <strong>Risk:</strong> {rec.risk_score.toFixed(3)} <span style={badgeStyle}>{rec.risk_level}</span>
      </p>

      <p><strong>Reasoning:</strong> {rec.reasoning}</p>

      {rec.evidence.length > 0 && (
        <>
          <h3>Evidence</h3>
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Field</th>
                <th>Value</th>
                <th>Interpretation</th>
              </tr>
            </thead>
            <tbody>
              {rec.evidence.map((ev, i) => (
                <tr key={i}>
                  <td>{ev.source}</td>
                  <td><code>{ev.field}</code></td>
                  <td>{String(ev.value)}</td>
                  <td>{ev.interpretation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
