import type { AIRecommendation, RiskLevel } from '../types/domain';

const RISK_COLOURS: Record<RiskLevel, string> = {
  LOW: '#22c55e',
  MEDIUM: '#eab308',
  HIGH: '#f97316',
  CRITICAL: '#ef4444',
};

interface Props {
  recommendation: AIRecommendation | null;
  providerName: string | null;
}

export function RecommendationPanel({ recommendation: rec, providerName }: Props) {
  // ── Unavailable state ────────────────────────────────────────────────────
  if (rec === null) {
    return (
      <section className="panel panel-full">
        <h2>AI Reasoning</h2>
        <p style={{ color: '#57606a' }}>
          <strong style={{ color: '#8b949e' }}>AI reasoning unavailable.</strong>
          &nbsp;The AI provider has not returned a recommendation.
        </p>
        <p style={{ color: '#57606a', fontSize: 12, marginTop: 6 }}>
          No reasoning, evidence, confidence score, or risk assessment is available.
          Ensure the backend has a scenario loaded and refresh to enable AI analysis.
        </p>
      </section>
    );
  }

  // ── Populated state ──────────────────────────────────────────────────────
  const badgeStyle = {
    background: RISK_COLOURS[rec.risk_level],
    color: '#fff',
    borderRadius: '4px',
    padding: '2px 8px',
    fontWeight: 700,
  };

  const heading = providerName
    ? `AI Reasoning — ${providerName}`
    : 'AI Reasoning';

  return (
    <section className="panel panel-full">
      <h2>{heading}</h2>

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
