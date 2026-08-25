/**
 * MissionReportPanel — Phase 2E-D8.
 *
 * Consolidated, operator-facing mission report that tells the complete story of
 * one transmission decision and its actual deterministic outcome.
 *
 * Data sources (all sourced from existing MissionControl state — nothing invented):
 *   - MissionState        → mission identity, phase, event, risk
 *   - AIRecommendation    → recommended plan, confidence, reasoning
 *   - CandidatePrioritization → ranked products with descriptions, factors, anomaly_ids
 *   - SimulationResult    → SOLE authority for delivered / deferred / failed / elapsed_time_s
 *   - AnomalyEvent[]      → anomaly context (D6 flow, based on RankedProduct.anomaly_ids)
 *   - geometry fields     → distance_km / propagation_delay_s / round_trip_time_s
 *   - aiProvider          → provider label
 *
 * Deterministic boundary:
 *   AI data explains prioritization reasoning only.
 *   SimulationResult is the sole authority for actual transmission outcomes.
 *   AI confidence, AI reasoning, and AI risk values never determine delivery status.
 *
 * Export:
 *   Uses window.print() scoped via a CSS @media print rule — zero new dependencies.
 *   The exported content mirrors the displayed content exactly (no second pipeline).
 */

import type {
  AIRecommendation,
  AnomalyEvent,
  CandidatePrioritization,
  MissionState,
  RankedProduct,
  SimulationResult,
} from '../types/domain';
import type { ApprovalPhase } from './ApprovalBar';

// ─── Pure derivation helpers (exported for tests) ─────────────────────────────

export type ReportOutcome = 'delivered' | 'deferred' | 'failed' | 'not_in_plan';

/**
 * Map a product ID to its deterministic transmission outcome.
 * Reads only SimulationResult arrays — never AI fields.
 */
export function reportOutcomeOf(productId: string, sim: SimulationResult): ReportOutcome {
  if (sim.delivered_packets.includes(productId)) return 'delivered';
  if (sim.deferred_packets.includes(productId))  return 'deferred';
  if (sim.failed_packets.includes(productId))    return 'failed';
  return 'not_in_plan';
}

/**
 * Fulfillment metric: fraction of AI-ranked products that were actually delivered.
 * Exact formula reused from TransmissionNarrativePanel — not a new definition.
 */
export function computeFulfillment(
  rankedProducts: RankedProduct[],
  sim: SimulationResult,
): number {
  if (rankedProducts.length === 0) return 0;
  const deliveredCount = rankedProducts.filter(
    (rp) => sim.delivered_packets.includes(rp.product_id),
  ).length;
  return deliveredCount / rankedProducts.length;
}

/**
 * Resolve anomaly context: only anomalies that have at least one AI-ranked
 * product referencing them via anomaly_ids.  Mirrors D6 resolveAnomalyContext.
 * Never fabricates a relationship.
 */
export function resolveReportAnomalyContext(
  anomalies: AnomalyEvent[],
  ranked: RankedProduct[],
): Array<{ anomaly: AnomalyEvent; products: RankedProduct[] }> {
  const result: Array<{ anomaly: AnomalyEvent; products: RankedProduct[] }> = [];
  for (const anomaly of anomalies) {
    const products = ranked
      .filter((rp) => rp.anomaly_ids.includes(anomaly.anomaly_id))
      .sort((a, b) => a.priority - b.priority);
    if (products.length > 0) {
      result.push({ anomaly, products });
    }
  }
  return result;
}

/**
 * Whether the simulated plan was the AI-recommended plan.
 * Mirrors the derivation in MissionControl — plan_id !== 'operator-override'.
 */
export function isAiRecommendedFromSim(sim: SimulationResult): boolean {
  return sim.plan_id !== 'operator-override';
}

// ─── Format helpers ────────────────────────────────────────────────────────────

function fmtSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60)   return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(1)} s`;
}

function fmtKm(km: number): string {
  if (km >= 1_000_000) return `${(km / 1_000_000).toFixed(2)} M km`;
  if (km >= 1_000)     return `${(km / 1_000).toFixed(0)} k km`;
  return `${km.toFixed(0)} km`;
}

// ─── Design tokens (consistent with existing components) ──────────────────────

const OUTCOME_LABEL: Record<ReportOutcome, string> = {
  delivered:   'DELIVERED',
  deferred:    'DEFERRED BY DETERMINISTIC SCHEDULER',
  failed:      'FAILED DURING TRANSMISSION',
  not_in_plan: 'AI-RANKED · NOT IN APPROVED PLAN',
};

const OUTCOME_ICON: Record<ReportOutcome, string> = {
  delivered:   '✓',
  deferred:    '⊘',
  failed:      '✗',
  not_in_plan: '–',
};

const OUTCOME_COLOR: Record<ReportOutcome, string> = {
  delivered:   'var(--signal, #35e7b7)',
  deferred:    'var(--text-muted, #6f83a3)',
  failed:      'var(--critical, #ff4d5e)',
  not_in_plan: 'var(--text-dim, #3d4a63)',
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
      color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.10em',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      paddingBottom: 5, marginTop: 18, marginBottom: 10,
    }}>
      {children}
    </div>
  );
}

function StatCell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ minWidth: 80 }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 14, color: color ?? 'var(--text)' }}>
        {value}
      </div>
    </div>
  );
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  approvalPhase: ApprovalPhase;
  missionState: MissionState | null;
  recommendation: AIRecommendation | null;
  aiPrioritization: CandidatePrioritization | null;
  aiProvider: string | null;
  simulationResult: SimulationResult | null;
  anomalies: AnomalyEvent[];
  distanceKm: number | null;
  propagationDelayS: number | null;
  roundTripTimeS: number | null;
}

// ─── Main component ───────────────────────────────────────────────────────────

export function MissionReportPanel({
  approvalPhase,
  missionState,
  recommendation,
  aiPrioritization,
  aiProvider,
  simulationResult,
  anomalies,
  distanceKm,
  propagationDelayS,
  roundTripTimeS,
}: Props) {
  const sim = simulationResult;
  const isComplete = approvalPhase === 'complete' && sim !== null;

  // Derive fulfillment + anomaly context only when we have simulation data
  const ranked = aiPrioritization?.ranked_products.slice().sort((a, b) => a.priority - b.priority) ?? [];
  const fulfillment = isComplete ? computeFulfillment(ranked, sim!) : null;
  const anomalyContext = isComplete && ranked.length > 0
    ? resolveReportAnomalyContext(anomalies, ranked)
    : [];

  const aiRecommended = isComplete ? isAiRecommendedFromSim(sim!) : null;

  function handleExport() {
    window.print();
  }

  return (
    <section
      className="panel"
      id="gcsi-mission-report"
      style={{ paddingTop: 12, paddingBottom: 14 }}
    >
      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0 }}>
          <span style={{ color: 'var(--signal, #35e7b7)' }}>◉</span>&nbsp;Mission Report
          <span style={{
            marginLeft: 8, fontSize: 9, fontWeight: 700,
            background: isComplete ? 'rgba(53,231,183,0.06)' : 'rgba(255,255,255,0.04)',
            color: isComplete ? 'var(--signal)' : 'var(--text-dim)',
            border: `1px solid ${isComplete ? 'rgba(53,231,183,0.22)' : 'rgba(255,255,255,0.08)'}`,
            borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
          }}>
            {isComplete ? 'POST-TRANSMISSION' : 'AWAITING TRANSMISSION'}
          </span>
        </h2>
        {isComplete && (
          <button
            onClick={handleExport}
            title="Print / save this report"
            style={{
              marginLeft: 'auto',
              fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
              padding: '3px 10px', borderRadius: 3, cursor: 'pointer',
              background: 'rgba(53,231,183,0.08)',
              color: 'var(--signal)',
              border: '1px solid rgba(53,231,183,0.30)',
            }}
          >
            ⬇ Export / Print
          </button>
        )}
      </div>

      {/* ── Pre-transmission waiting states ── */}
      {!recommendation && (
        <p style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          Waiting for AI recommendation…
        </p>
      )}

      {recommendation && !isComplete && (
        <p style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          AI recommendation received. Approve a plan to generate the post-transmission report.
        </p>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 1 — Mission Identity
      ──────────────────────────────────────────────────────────────────── */}
      {missionState && (
        <>
          <SectionHeading>1 · Mission Identity</SectionHeading>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 8 }}>
            <StatCell label="Mission ID"    value={missionState.mission_id} />
            <StatCell label="Phase"         value={missionState.mission_phase} />
            <StatCell label="Current Event" value={missionState.current_event} />
            <StatCell
              label="Mission Risk"
              value={`${missionState.risk_level} (${(missionState.risk_score * 100).toFixed(0)}%)`}
              color={
                missionState.risk_level === 'CRITICAL' ? 'var(--critical)' :
                missionState.risk_level === 'HIGH'     ? 'var(--warn)' :
                missionState.risk_level === 'MEDIUM'   ? 'var(--warn)' :
                'var(--signal)'
              }
            />
          </div>
        </>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 2 — Decision Summary (shown as soon as recommendation exists)
      ──────────────────────────────────────────────────────────────────── */}
      {recommendation && (
        <>
          <SectionHeading>2 · Decision Summary</SectionHeading>

          {/* AI / Override badge */}
          {aiRecommended !== null && (
            <div style={{
              marginBottom: 10, fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
              letterSpacing: '0.08em', textTransform: 'uppercase',
              color: aiRecommended ? 'var(--ai, #7c9eff)' : 'var(--warn, #ffb648)',
            }}>
              {aiRecommended ? '◈ AI RECOMMENDED' : '⊛ OPERATOR OVERRIDE'}
            </div>
          )}

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 10 }}>
            <StatCell label="AI Provider"         value={aiProvider ?? '—'} color="var(--ai)" />
            <StatCell label="AI Confidence"       value={`${(recommendation.confidence * 100).toFixed(0)}%`} color="var(--ai)" />
            <StatCell label="AI Recommended Plan" value={recommendation.recommended_plan_id} />
            {sim && (
              <StatCell
                label="Approved Plan"
                value={sim.plan_id}
                color={aiRecommended ? 'var(--signal)' : 'var(--warn)'}
              />
            )}
          </div>

          {/* Boundary banner */}
          <div style={{
            display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10,
          }}>
            <span style={{
              background: 'rgba(124,158,255,0.07)', color: 'var(--ai, #7c9eff)',
              border: '1px solid rgba(124,158,255,0.22)', borderRadius: 2,
              padding: '2px 7px', fontFamily: 'var(--font-mono)', fontSize: 9,
            }}>
              ◈ AI — semantic ranking · anomaly reasoning · mission context
            </span>
            <span style={{
              background: 'rgba(53,231,183,0.05)', color: 'var(--signal, #35e7b7)',
              border: '1px solid rgba(53,231,183,0.18)', borderRadius: 2,
              padding: '2px 7px', fontFamily: 'var(--font-mono)', fontSize: 9,
            }}>
              ● DETERMINISTIC — capacity · feasibility · risk · transmission outcome
            </span>
          </div>

          {/* AI overall reasoning */}
          {recommendation.reasoning && (
            <div style={{
              fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5,
              background: 'rgba(0,0,0,0.12)', borderRadius: 3, padding: '7px 10px',
              fontStyle: 'italic', marginBottom: 4,
              borderLeft: '2px solid rgba(124,158,255,0.25)',
            }}>
              "{recommendation.reasoning}"
            </div>
          )}
        </>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 3 — AI Prioritization (shown when prioritization available)
      ──────────────────────────────────────────────────────────────────── */}
      {aiPrioritization && ranked.length > 0 && (
        <>
          <SectionHeading>3 · AI Prioritization ({ranked.length} products ranked)</SectionHeading>
          <div style={{ maxHeight: 240, overflowY: 'auto' }}>
            {ranked.map((rp) => {
              const outcome = isComplete ? reportOutcomeOf(rp.product_id, sim!) : null;
              const label = rp.description ? rp.description : rp.product_id;
              return (
                <div
                  key={rp.product_id}
                  style={{
                    display: 'flex', alignItems: 'flex-start', gap: 7,
                    padding: '5px 4px',
                    borderBottom: '1px solid rgba(255,255,255,0.035)',
                  }}
                >
                  {/* Priority number */}
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9,
                    color: 'var(--ai)', minWidth: 22, textAlign: 'right', flexShrink: 0, paddingTop: 1,
                  }}>
                    #{rp.priority}
                  </span>

                  {/* Outcome icon (post-transmission only) */}
                  {outcome !== null && (
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 11,
                      color: OUTCOME_COLOR[outcome], minWidth: 12, flexShrink: 0, paddingTop: 1,
                    }}>
                      {OUTCOME_ICON[outcome]}
                    </span>
                  )}

                  {/* Description / ID */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span style={{
                        fontSize: 11, color: 'var(--text)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {label}
                      </span>
                      {rp.description && (
                        <code style={{ fontSize: 9, color: 'var(--ai)' }}>{rp.product_id}</code>
                      )}
                      {rp.subsystem && (
                        <span style={{ fontSize: 9, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
                          {rp.subsystem}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic', marginTop: 1 }}>
                      {rp.reason}
                    </div>
                    {rp.anomaly_ids.length > 0 && (
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 2 }}>
                        {rp.anomaly_ids.map((a) => (
                          <code key={a} style={{
                            fontSize: 9, fontFamily: 'var(--font-mono)', fontWeight: 700,
                            color: 'var(--warn, #ffb648)',
                            background: 'rgba(255,182,72,0.08)',
                            border: '1px solid rgba(255,182,72,0.2)',
                            borderRadius: 2, padding: '0 4px',
                          }}>{a}</code>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Outcome badge (post-transmission only) */}
                  {outcome !== null && (
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                      color: OUTCOME_COLOR[outcome],
                      background: outcome === 'delivered' ? 'rgba(53,231,183,0.07)'
                        : outcome === 'failed' ? 'rgba(255,77,94,0.07)'
                        : outcome === 'deferred' ? 'rgba(255,255,255,0.04)'
                        : 'transparent',
                      border: `1px solid ${
                        outcome === 'delivered' ? 'rgba(53,231,183,0.25)'
                        : outcome === 'failed' ? 'rgba(255,77,94,0.25)'
                        : outcome === 'deferred' ? 'rgba(255,255,255,0.06)'
                        : 'transparent'
                      }`,
                      borderRadius: 2, padding: '1px 5px', flexShrink: 0, textAlign: 'right',
                    }}>
                      {OUTCOME_LABEL[outcome]}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 4 — Transmission Outcome (post-transmission only)
      ──────────────────────────────────────────────────────────────────── */}
      {isComplete && (
        <>
          <SectionHeading>4 · Deterministic Transmission Outcome</SectionHeading>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 8 }}>
            <StatCell label="Delivered" value={String(sim!.delivered_packets.length)} color="var(--signal)" />
            <StatCell
              label="Deferred"
              value={String(sim!.deferred_packets.length)}
              color={sim!.deferred_packets.length > 0 ? 'var(--text-muted)' : 'var(--text-dim)'}
            />
            <StatCell
              label="Failed"
              value={String(sim!.failed_packets.length)}
              color={sim!.failed_packets.length > 0 ? 'var(--critical)' : 'var(--text-dim)'}
            />
            <StatCell label="Elapsed Time" value={fmtSeconds(sim!.elapsed_time_s)} />
            <StatCell label="Plan ID"      value={sim!.plan_id} />
          </div>

          {/* Retransmissions */}
          {Object.values(sim!.retransmission_counts).some((v) => v > 0) && (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--warn)',
              marginBottom: 6,
            }}>
              ⚠ Retransmissions:{' '}
              {Object.entries(sim!.retransmission_counts)
                .filter(([, v]) => v > 0)
                .map(([id, n]) => `${id} ×${n}`)
                .join('  ')}
            </div>
          )}

          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)',
            marginBottom: 4,
          }}>
            ● DETERMINISTIC TRANSMISSION RESULT — outcomes determined by link quality, window budget, and packet feasibility.
            Not AI-generated.
          </div>
        </>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 5 — Anomaly Context (post-transmission, when context exists)
      ──────────────────────────────────────────────────────────────────── */}
      {isComplete && anomalyContext.length > 0 && (
        <>
          <SectionHeading>5 · Anomaly Context</SectionHeading>
          {anomalyContext.map(({ anomaly, products }) => (
            <div key={anomaly.anomaly_id} style={{ marginBottom: 12 }}>
              {/* Anomaly header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                <code style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                  color: 'var(--warn, #ffb648)',
                  background: 'rgba(255,182,72,0.08)',
                  border: '1px solid rgba(255,182,72,0.2)',
                  borderRadius: 2, padding: '1px 5px',
                }}>
                  {anomaly.anomaly_id}
                </code>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                  {anomaly.subsystem}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
                  severity {(anomaly.severity * 100).toFixed(0)}%
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text)', marginBottom: 5, paddingLeft: 4 }}>
                {anomaly.description}
              </div>
              {/* Products linked via anomaly_ids */}
              {products.map((rp, idx) => {
                const outcome = reportOutcomeOf(rp.product_id, sim!);
                const isLast = idx === products.length - 1;
                const label = rp.description ? rp.description : rp.product_id;
                return (
                  <div key={rp.product_id} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 6,
                    paddingLeft: 8, marginBottom: isLast ? 0 : 4,
                  }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', flexShrink: 0, marginTop: 1 }}>
                      {isLast ? '└─' : '├─'}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ai)' }}>#{rp.priority}</span>
                        <span style={{ fontSize: 11, color: 'var(--text)' }}>{label}</span>
                        {rp.description && <code style={{ fontSize: 9, color: 'var(--ai)' }}>{rp.product_id}</code>}
                      </div>
                      <div style={{
                        fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                        color: OUTCOME_COLOR[outcome], marginTop: 2,
                      }}>
                        {OUTCOME_ICON[outcome]}&nbsp;{OUTCOME_LABEL[outcome]}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 6 — Communication Geometry
      ──────────────────────────────────────────────────────────────────── */}
      {distanceKm !== null && (
        <>
          <SectionHeading>6 · Communication Geometry</SectionHeading>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 4 }}>
            <StatCell label="Distance"           value={fmtKm(distanceKm)} />
            {propagationDelayS !== null && (
              <StatCell label="One-Way Delay"    value={fmtSeconds(propagationDelayS)} />
            )}
            {roundTripTimeS !== null && (
              <StatCell label="Round-Trip Time"  value={fmtSeconds(roundTripTimeS)} />
            )}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
            Signal propagation delay only — independent from link-layer latency and transmission window.
          </div>
        </>
      )}

      {/* ────────────────────────────────────────────────────────────────────
          Section 7 — Fulfillment Metric (post-transmission, AI prioritization required)
      ──────────────────────────────────────────────────────────────────── */}
      {isComplete && ranked.length > 0 && fulfillment !== null && (
        <>
          <SectionHeading>7 · AI Priority Fulfillment</SectionHeading>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
            {/* Bar */}
            <div style={{ flex: 1, minWidth: 120 }}>
              <div style={{ height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${(fulfillment * 100).toFixed(0)}%`,
                  background: fulfillment >= 0.7 ? 'var(--signal)' : fulfillment >= 0.4 ? 'var(--warn)' : 'var(--critical)',
                  borderRadius: 3,
                  transition: 'width 0.4s ease',
                }} />
              </div>
            </div>
            <span style={{
              fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 15,
              color: fulfillment >= 0.7 ? 'var(--signal)' : fulfillment >= 0.4 ? 'var(--warn)' : 'var(--critical)',
            }}>
              {(fulfillment * 100).toFixed(0)}%
            </span>
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
            Fraction of AI-ranked products actually delivered.
            AI outcome metric only — not a mission performance indicator.
          </div>
        </>
      )}

      {/* ── Footer disclaimer ── */}
      <div style={{
        marginTop: 14, fontFamily: 'var(--font-mono)', fontSize: 9,
        color: 'var(--text-dim)', paddingTop: 8,
        borderTop: '1px solid rgba(255,255,255,0.04)',
      }}>
        AI rankings are advisory. All transmission outcomes are determined by deterministic simulation —
        link quality, window budget, and packet feasibility.
      </div>
    </section>
  );
}
