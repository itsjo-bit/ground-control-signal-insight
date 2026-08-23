import type { AIRecommendation, CandidatePlan, EvaluationResult } from '../types/domain';

interface Props {
  activePlan: CandidatePlan;
  recommendation: AIRecommendation;
  evaluation: EvaluationResult | null;
}

/**
 * Side-by-side comparison of the active plan's packet order vs. the
 * AI-recommended order.  Driven by the plan currently selected in PlanSwitcher.
 */
export function PlanComparisonPanel({ activePlan, recommendation, evaluation }: Props) {
  // Build a lookup from packet_id → active plan rank (1-based)
  const activeRank: Record<string, number> = {};
  activePlan.packets.forEach((pkt, i) => {
    activeRank[pkt.packet_id] = i + 1;
  });

  // AI-ordered list from packet_actions — already ranked by the backend
  const aiActions = [...recommendation.packet_actions].sort((a, b) => a.rank - b.rank);

  return (
    <section className="panel panel-full">
      <h2>
        Active Plan vs. AI Recommended Order
        <span style={{ marginLeft: 10, fontSize: 12, fontWeight: 400, color: 'var(--text-muted)', textTransform: 'none', letterSpacing: 0 }}>
          AI plan: <code>{recommendation.recommended_plan_id}</code>
          {recommendation.alternative_plan_id && (
            <> · alt: <code>{recommendation.alternative_plan_id}</code></>
          )}
        </span>
        {evaluation && (
          <span style={{ marginLeft: 14, fontSize: 12, fontWeight: 400, color: 'var(--text-muted)', textTransform: 'none', letterSpacing: 0 }}>
            Mission value: <strong style={{ color: 'var(--text)' }}>{evaluation.mission_value.toFixed(2)}</strong>
            &nbsp;·&nbsp;BW util: <strong style={{ color: 'var(--text)' }}>{(evaluation.bandwidth_utilization * 100).toFixed(0)}%</strong>
            &nbsp;·&nbsp;Deferred: <strong style={{ color: evaluation.deferred_packets.length > 0 ? 'var(--warn)' : 'var(--text)' }}>{evaluation.deferred_packets.length}</strong>
          </span>
        )}
      </h2>

      <table>
        <thead>
          <tr>
            <th>AI Rank</th>
            <th>Packet ID</th>
            <th>Type</th>
            <th>Criticality</th>
            <th>Active Rank</th>
            <th>Moved</th>
          </tr>
        </thead>
        <tbody>
          {aiActions.map((action) => {
            const aRank = activeRank[action.packet_id] ?? '—';
            const moved = typeof aRank === 'number' && aRank !== action.rank;
            const movedUp = typeof aRank === 'number' && action.rank < aRank;
            const pkt = activePlan.packets.find((p) => p.packet_id === action.packet_id);

            return (
              <tr key={action.packet_id} style={moved ? { background: movedUp ? 'rgba(53,231,183,0.06)' : 'rgba(255,182,72,0.06)' } : undefined}>
                <td style={{ fontWeight: 700, color: 'var(--text)' }}>{action.rank}</td>
                <td><code>{action.packet_id}</code></td>
                <td>{pkt?.packet_type ?? '—'}</td>
                <td>{pkt ? pkt.criticality.toFixed(2) : '—'}</td>
                <td style={{ color: moved ? 'var(--text-muted)' : undefined }}>{aRank}</td>
                <td>
                  {moved && (
                    <span style={{ fontWeight: 700, color: movedUp ? 'var(--signal)' : 'var(--warn)' }}>
                      {movedUp ? `▲ +${(aRank as number) - action.rank}` : `▼ ${action.rank - (aRank as number)}`}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {!aiActions.some((a) => {
        const aRank = activeRank[a.packet_id];
        return typeof aRank === 'number' && aRank !== a.rank;
      }) && (
        <p style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>
          AI ordering matches active plan — no reordering recommended.
        </p>
      )}
    </section>
  );
}
