/**
 * TransmissionNarrativePanel — Phase 2E-D6 post-transmission result narrative.
 *
 * Connects the AI-ranked products from CandidatePrioritization to the actual
 * transmission outcomes from SimulationResult, giving the operator a concise
 * answer to: "Did the AI's priorities reflect what actually got through?"
 *
 * Shows:
 *  - AI-ranked product IDs mapped to their transmission outcome.
 *  - Count of "top-N AI picks" that were actually delivered.
 *  - Products deferred despite being AI-prioritized.
 *
 * All outcome data is deterministic (simulation result).
 * AI data is advisory — labelled clearly.
 */

import type { AnomalyEvent, CandidatePrioritization, RankedProduct, SimulationResult } from '../types/domain';

// ─── helpers ──────────────────────────────────────────────────────────────────

type Outcome = 'delivered' | 'deferred' | 'failed' | 'not_in_plan';

const OUTCOME_COLOUR: Record<Outcome, string> = {
  delivered:    'var(--signal, #35e7b7)',
  deferred:     'var(--text-muted, #6f83a3)',
  failed:       'var(--critical, #ff4d5e)',
  not_in_plan:  'var(--text-dim, #3d4a63)',
};

const OUTCOME_ICON: Record<Outcome, string> = {
  delivered:    '✓',
  deferred:     '⊘',
  failed:       '✗',
  not_in_plan:  '–',
};

const OUTCOME_LABEL: Record<Outcome, string> = {
  delivered:    'DELIVERED',
  deferred:     'DEFERRED BY DETERMINISTIC SCHEDULER',
  failed:       'FAILED DURING TRANSMISSION',
  not_in_plan:  'AI-RANKED · NOT IN APPROVED PLAN',
};

function outcomeOf(productId: string, sim: SimulationResult): Outcome {
  if (sim.delivered_packets.includes(productId)) return 'delivered';
  if (sim.deferred_packets.includes(productId)) return 'deferred';
  if (sim.failed_packets.includes(productId)) return 'failed';
  return 'not_in_plan';
}

// ─── main component ───────────────────────────────────────────────────────────

// ─── anomaly context helpers ──────────────────────────────────────────────────

/** Find all RankedProducts that reference a given anomaly ID. */
function rankedProductsForAnomaly(
  anomalyId: string,
  ranked: RankedProduct[],
): RankedProduct[] {
  return ranked.filter((rp) => rp.anomaly_ids.includes(anomalyId));
}

/** Return only anomalies that have at least one matching AI-ranked product. */
function resolveAnomalyContext(
  anomalies: AnomalyEvent[],
  ranked: RankedProduct[],
): Array<{ anomaly: AnomalyEvent; products: RankedProduct[] }> {
  const result: Array<{ anomaly: AnomalyEvent; products: RankedProduct[] }> = [];
  for (const anomaly of anomalies) {
    const products = rankedProductsForAnomaly(anomaly.anomaly_id, ranked);
    if (products.length > 0) {
      result.push({ anomaly, products: products.slice().sort((a, b) => a.priority - b.priority) });
    }
  }
  return result;
}

// ─── main component ───────────────────────────────────────────────────────────

interface Props {
  prioritization: CandidatePrioritization | null;
  simulationResult: SimulationResult | null;
  /** Phase 2E-D6: anomaly events from GET /state for mission context narrative. */
  anomalies?: AnomalyEvent[];
  /** Phase 2E-D6: true when the simulated plan is the AI-recommended plan. */
  isAiRecommendedPlan?: boolean;
}

export function TransmissionNarrativePanel({ prioritization, simulationResult, anomalies, isAiRecommendedPlan }: Props) {
  // Only shown when both AI prioritization and simulation result are available.
  if (!prioritization || !simulationResult) return null;
  if (prioritization.ranked_products.length === 0) return null;

  const sim = simulationResult;
  const ranked = prioritization.ranked_products.slice().sort((a, b) => a.priority - b.priority);

  // Map each AI-ranked product to its actual transmission outcome.
  const rows = ranked.map((rp) => ({
    productId: rp.product_id,
    priority: rp.priority,
    subsystem: rp.subsystem,
    // Phase 2E-D3 (D3-C): include description for human-readable product names.
    description: rp.description ?? '',
    outcome: outcomeOf(rp.product_id, sim),
  }));

  // Phase 2E-D6: resolve anomaly context — only anomalies with AI-ranked products.
  const anomalyContext = resolveAnomalyContext(anomalies ?? [], ranked);

  const deliveredCount = rows.filter((r) => r.outcome === 'delivered').length;
  const deferredCount  = rows.filter((r) => r.outcome === 'deferred').length;
  const failedCount    = rows.filter((r) => r.outcome === 'failed').length;
  const notInPlan      = rows.filter((r) => r.outcome === 'not_in_plan').length;

  // "Accuracy": fraction of top-N AI picks that actually got delivered
  const accuracy = ranked.length > 0 ? deliveredCount / ranked.length : 0;
  const accuracyPct = (accuracy * 100).toFixed(0);

  return (
    <section className="panel" style={{ paddingTop: 12, paddingBottom: 14 }}>
      {/* Header */}
      <h2 style={{ marginBottom: 6 }}>
        <span style={{ color: 'var(--ai, #7c9eff)' }}>◈</span>&nbsp;AI vs Actual Outcomes
        <span style={{
          marginLeft: 8, fontSize: 9, fontWeight: 700,
          background: 'rgba(53,231,183,0.06)', color: 'var(--signal)',
          border: '1px solid rgba(53,231,183,0.22)',
          borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
        }}>
          POST-TRANSMISSION
        </span>
      </h2>

      {/* Phase 2E-D6: AI recommended / operator override context label */}
      {isAiRecommendedPlan !== undefined && (
        <div style={{
          marginBottom: 10, fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
          letterSpacing: '0.08em', textTransform: 'uppercase',
          color: isAiRecommendedPlan ? 'var(--ai, #7c9eff)' : 'var(--warn, #ffb648)',
        }}>
          {isAiRecommendedPlan ? '◈ AI RECOMMENDED' : '⊛ OPERATOR OVERRIDE'}
        </div>
      )}

      {/* Phase 2E-D6: Mission context — anomalies with AI-ranked products */}
      {anomalyContext.length > 0 && (
        <div style={{
          marginBottom: 12,
          borderTop: '1px solid rgba(255,255,255,0.06)',
          paddingTop: 10,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
            color: 'var(--text-dim)', textTransform: 'uppercase',
            letterSpacing: '0.1em', marginBottom: 8,
          }}>
            Mission Context
          </div>
          {anomalyContext.map(({ anomaly, products }) => (
            <div key={anomaly.anomaly_id} style={{ marginBottom: 10 }}>
              {/* Anomaly identity */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <code style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                  color: 'var(--warn, #ffb648)',
                  background: 'rgba(255,182,72,0.08)',
                  border: '1px solid rgba(255,182,72,0.2)',
                  borderRadius: 2, padding: '1px 5px',
                }}>
                  {anomaly.anomaly_id}
                </code>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                  color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em',
                }}>
                  {anomaly.subsystem}
                </span>
              </div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 10,
                color: 'var(--text)', marginBottom: 6, paddingLeft: 4,
              }}>
                {anomaly.description}
              </div>
              {/* Products linked to this anomaly */}
              {products.map((rp, idx) => {
                const outcome = outcomeOf(rp.product_id, sim);
                const isLast = idx === products.length - 1;
                const label = rp.description ? rp.description : rp.product_id;
                return (
                  <div key={rp.product_id} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 6,
                    paddingLeft: 8, marginBottom: isLast ? 0 : 4,
                  }}>
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 10,
                      color: 'var(--text-dim)', flexShrink: 0, marginTop: 1,
                    }}>
                      {isLast ? '└─' : '├─'}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <span style={{
                          fontFamily: 'var(--font-mono)', fontSize: 9,
                          color: 'var(--ai)', minWidth: 22,
                        }}>
                          #{rp.priority}
                        </span>
                        <span style={{
                          fontFamily: 'var(--font-mono)', fontSize: 10,
                          color: 'var(--text)', flex: 1,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {label}
                        </span>
                        {rp.description && (
                          <code style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ai)' }}>
                            {rp.product_id}
                          </code>
                        )}
                      </div>
                      <div style={{
                        fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                        color: OUTCOME_COLOUR[outcome], marginTop: 2,
                      }}>
                        {OUTCOME_ICON[outcome]}&nbsp;{OUTCOME_LABEL[outcome]}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      {/* AI Priority Fulfillment header before stats when anomaly context is shown */}
      {anomalyContext.length > 0 && (
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
          color: 'var(--text-dim)', textTransform: 'uppercase',
          letterSpacing: '0.1em', marginBottom: 8,
          borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 10,
        }}>
          AI Priority Fulfillment
        </div>
      )}

      {/* Summary stats */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
            AI-Ranked
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--ai)' }}>
            {ranked.length}
          </div>
        </div>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
            Delivered
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--signal)' }}>
            {deliveredCount}
          </div>
        </div>
        {deferredCount > 0 && (
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
              Deferred
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-muted)' }}>
              {deferredCount}
            </div>
          </div>
        )}
        {failedCount > 0 && (
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
              Failed
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--critical)' }}>
              {failedCount}
            </div>
          </div>
        )}
        {notInPlan > 0 && (
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
              Not in plan
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-dim)' }}>
              {notInPlan}
            </div>
          </div>
        )}
        {/* Accuracy bar */}
        <div style={{ flex: 1, minWidth: 100 }}>
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            fontFamily: 'var(--font-mono)', fontSize: 9,
            color: 'var(--text-dim)', textTransform: 'uppercase',
            letterSpacing: '0.08em', marginBottom: 4,
          }}>
            <span>AI priorities fulfilled</span>
            <span style={{ color: accuracy >= 0.7 ? 'var(--signal)' : accuracy >= 0.4 ? 'var(--warn)' : 'var(--critical)' }}>
              {accuracyPct}%
            </span>
          </div>
          <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%',
              width: `${accuracyPct}%`,
              background: accuracy >= 0.7 ? 'var(--signal)' : accuracy >= 0.4 ? 'var(--warn)' : 'var(--critical)',
              borderRadius: 2,
              transition: 'width 0.4s ease',
            }} />
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 3 }}>
            AI outcome data — not a mission performance metric
          </div>
        </div>
      </div>

      {/* Per-product outcome list */}
      <div style={{ maxHeight: 220, overflowY: 'auto' }}>
        {rows.map((row) => (
          <div
            key={row.productId}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '5px 4px',
              borderBottom: '1px solid rgba(255,255,255,0.035)',
            }}
          >
            {/* AI priority rank */}
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10,
              color: 'var(--text-dim)', minWidth: 22, textAlign: 'right',
            }}>
              {String(row.priority).padStart(2, '0')}
            </span>

            {/* Outcome icon */}
            <span style={{
              fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12,
              color: OUTCOME_COLOUR[row.outcome], minWidth: 14, textAlign: 'center',
            }}>
              {OUTCOME_ICON[row.outcome]}
            </span>

            {/* Phase 2E-D3 (D3-C): description + product ID.
                When description is present show it as the primary label with ID below.
                When absent fall back to showing just the product ID as before. */}
            <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
              {row.description ? (
                <>
                  <div style={{ color: 'var(--text)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {row.description}
                  </div>
                  <code style={{ color: 'var(--ai)', fontSize: 9, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {row.productId}
                  </code>
                </>
              ) : (
                <code style={{ color: 'var(--ai)', fontSize: 11 }}>
                  {row.productId}
                </code>
              )}
            </div>

            {/* Subsystem */}
            {row.subsystem && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', minWidth: 80, textAlign: 'right' }}>
                {row.subsystem}
              </span>
            )}

            {/* Outcome label */}
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
              color: OUTCOME_COLOUR[row.outcome],
              background: row.outcome === 'delivered'
                ? 'rgba(53,231,183,0.07)'
                : row.outcome === 'failed'
                  ? 'rgba(255,77,94,0.07)'
                  : row.outcome === 'deferred'
                    ? 'rgba(255,255,255,0.04)'
                    : 'transparent',
              border: `1px solid ${
                row.outcome === 'delivered' ? 'rgba(53,231,183,0.25)'
                  : row.outcome === 'failed' ? 'rgba(255,77,94,0.25)'
                  : row.outcome === 'deferred' ? 'rgba(255,255,255,0.06)'
                  : 'transparent'
              }`,
              borderRadius: 2, padding: '1px 5px', textAlign: 'center',
              flexShrink: 0,
            }}>
              {OUTCOME_LABEL[row.outcome]}
            </span>
          </div>
        ))}
      </div>

      {/* Disclaimer */}
      <div style={{
        marginTop: 10, fontFamily: 'var(--font-mono)', fontSize: 9,
        color: 'var(--text-dim)', paddingTop: 8,
        borderTop: '1px solid rgba(255,255,255,0.04)',
      }}>
        AI rankings are advisory. Actual outcomes are determined by deterministic simulation — link quality, window budget, and packet feasibility.
      </div>
    </section>
  );
}

// ─── exports for tests ────────────────────────────────────────────────────────
export { outcomeOf, resolveAnomalyContext, rankedProductsForAnomaly };
export type { Outcome };
