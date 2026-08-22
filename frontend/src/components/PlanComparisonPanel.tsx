import type { AIRecommendation, CandidatePlan } from '../types/domain';

interface Props {
  baseline: CandidatePlan;
  recommendation: AIRecommendation;
}

/**
 * Side-by-side comparison of the baseline packet order vs. the AI-recommended
 * order.  All ordering data comes directly from the API — no frontend logic
 * recomputes or changes packet priority.
 *
 * Baseline order: baseline.packets (from GET /queue — BaselineScheduler)
 * AI order:       recommendation.packet_actions (from POST /agent/recommend)
 */
export function PlanComparisonPanel({ baseline, recommendation }: Props) {
  // Build a lookup from packet_id → baseline rank (1-based)
  const baselineRank: Record<string, number> = {};
  baseline.packets.forEach((pkt, i) => {
    baselineRank[pkt.packet_id] = i + 1;
  });

  // AI-ordered list from packet_actions — already ranked by the backend
  const aiActions = [...recommendation.packet_actions].sort((a, b) => a.rank - b.rank);

  return (
    <section className="panel panel-full">
      <h2>
        Baseline vs. AI Recommended Order
        <span style={{ marginLeft: 10, fontSize: 12, fontWeight: 400, color: '#8b949e', textTransform: 'none', letterSpacing: 0 }}>
          AI plan: <code style={{ background: '#21262d', borderRadius: 3, padding: '1px 5px' }}>{recommendation.recommended_plan_id}</code>
          {recommendation.alternative_plan_id && (
            <> · alt: <code style={{ background: '#21262d', borderRadius: 3, padding: '1px 5px' }}>{recommendation.alternative_plan_id}</code></>
          )}
        </span>
      </h2>

      <table>
        <thead>
          <tr>
            <th>AI Rank</th>
            <th>Packet ID</th>
            <th>Type</th>
            <th>Criticality</th>
            <th>Baseline Rank</th>
            <th>Moved</th>
          </tr>
        </thead>
        <tbody>
          {aiActions.map((action) => {
            const bRank = baselineRank[action.packet_id] ?? '—';
            const moved = typeof bRank === 'number' && bRank !== action.rank;
            const movedUp = typeof bRank === 'number' && action.rank < bRank;
            const pkt = baseline.packets.find((p) => p.packet_id === action.packet_id);

            return (
              <tr key={action.packet_id} style={moved ? { background: movedUp ? '#0f2d0f' : '#2d1c0a' } : undefined}>
                <td style={{ fontWeight: 700, color: '#e6edf3' }}>{action.rank}</td>
                <td><code>{action.packet_id}</code></td>
                <td>{pkt?.packet_type ?? '—'}</td>
                <td>{pkt ? pkt.criticality.toFixed(2) : '—'}</td>
                <td style={{ color: moved ? '#8b949e' : undefined }}>{bRank}</td>
                <td>
                  {moved && (
                    <span style={{ fontWeight: 700, color: movedUp ? '#22c55e' : '#f97316' }}>
                      {movedUp ? `▲ +${(bRank as number) - action.rank}` : `▼ ${action.rank - (bRank as number)}`}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {!aiActions.some((a) => {
        const bRank = baselineRank[a.packet_id];
        return typeof bRank === 'number' && bRank !== a.rank;
      }) && (
        <p style={{ marginTop: 8, color: '#8b949e', fontSize: 12 }}>
          AI ordering matches baseline — no reordering recommended.
        </p>
      )}
    </section>
  );
}
