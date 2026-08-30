/**
 * MissionDecisionPanel — Phase 2E-D2
 *
 * Establishes the traceable chain between AI prioritization and the actual
 * transmission plan so the operator always knows WHAT is being approved and WHY.
 *
 * Data flow this panel makes visible:
 *
 *   CandidatePrioritization (AI ranking)
 *       ↓
 *   AIRecommendation.recommended_plan_id
 *       ↓
 *   CandidatePlan (packets selected by the deterministic scheduler, ordered by AI)
 *       ↓
 *   EvaluationResult (deterministic feasibility, risk, deferred list)
 *       ↓
 *   Operator approves via the existing ApprovalBar
 *
 * Responsibilities:
 * - Show which products the AI ranked and WHY (from CandidatePrioritization).
 * - Show which products the deterministic scheduler SELECTED vs DEFERRED
 *   (from the recommended CandidatePlan + EvaluationResult.deferred_packets).
 * - Display volume, estimated transmission time, risk, and confidence.
 * - Keep the AI/deterministic boundary explicit and visible.
 * - Gracefully handle legacy scenarios (no CandidatePrioritization).
 *
 * IMPORTANT SEMANTIC RULES (enforced by labels):
 * - AI "prioritizes" — it does not "select" or "transmit".
 * - The deterministic scheduler "selects for the current window".
 * - Estimated transmission time is a display estimate (payload / goodput).
 *   It is NOT a guaranteed delivery time. It is NOT equal to propagation delay.
 * - AI confidence is AI judgment — not a physical mission confidence.
 */

import { useState } from 'react';
import type {
  AIRecommendation,
  CandidatePlan,
  CandidatePrioritization,
  EvaluationResult,
  LinkState,
  Packet,
  RankedProduct,
  RiskLevel,
} from '../types/domain';

// ─── Design tokens (mirrors existing components) ───────────────────────────────

const AI_COLOR     = 'var(--ai, #7c9eff)';
const DETERM_COLOR = 'var(--signal, #35e7b7)';
const WARN_COLOR   = 'var(--warn, #ffb648)';
const CRIT_COLOR   = 'var(--critical, #ff4d5e)';
const MUTED        = 'var(--text-muted, #8b949e)';
const DIM          = 'var(--text-dim, #57606a)';
const TEXT         = 'var(--text, #dce6f5)';

// ─── Risk colour helpers ───────────────────────────────────────────────────────

const RISK_COLOR: Record<RiskLevel, string> = {
  LOW:      DETERM_COLOR,
  MEDIUM:   WARN_COLOR,
  HIGH:     '#ff8a3d',
  CRITICAL: CRIT_COLOR,
};

const RISK_BG: Record<RiskLevel, string> = {
  LOW:      'rgba(53,231,183,0.07)',
  MEDIUM:   'rgba(255,182,72,0.09)',
  HIGH:     'rgba(255,138,61,0.09)',
  CRITICAL: 'rgba(255,77,94,0.09)',
};

const RISK_BORDER: Record<RiskLevel, string> = {
  LOW:      'rgba(53,231,183,0.3)',
  MEDIUM:   'rgba(255,182,72,0.38)',
  HIGH:     'rgba(255,138,61,0.42)',
  CRITICAL: 'rgba(255,77,94,0.45)',
};

// ─── Formatting helpers ────────────────────────────────────────────────────────

import { formatBitsAsDataVolume } from '../utils/formatters';

function fmtSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60) return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(0)} s`;
}

// ─── Factor badge (mirrors AIDecisionPanel) ────────────────────────────────────

const FACTOR_SHORT: Record<string, string> = {
  'active anomaly':          'ANOMALY',
  'high severity anomaly':   'HIGH ANOMALY',
  'high criticality':        'CRITICAL',
  'medium criticality':      'MEDIUM',
  'deadline urgency':        'DEADLINE',
  'mission relevance':       'MISSION',
  'scientific value':        'SCIENCE',
  'data freshness':          'FRESH',
  'related products':        'RELATED',
  'subsystem dependency':    'DEPENDENCY',
  'operational necessity':   'NECESSITY',
  'routine housekeeping':    'ROUTINE',
  'low mission urgency':     'LOW URGENCY',
  'long deadline':           'LONG DEADLINE',
  'composite urgency score': 'COMPOSITE',
};

const FACTOR_IS_HIGH = new Set([
  'active anomaly', 'high severity anomaly', 'high criticality',
  'deadline urgency', 'operational necessity',
]);

function FactorBadge({ factor }: { factor: string }) {
  const label = FACTOR_SHORT[factor] ?? factor.toUpperCase();
  const isHigh = FACTOR_IS_HIGH.has(factor);
  return (
    <span style={{
      display: 'inline-block',
      background: isHigh ? 'rgba(255,77,94,0.10)' : 'rgba(124,158,255,0.08)',
      color: isHigh ? CRIT_COLOR : AI_COLOR,
      border: `1px solid ${isHigh ? 'rgba(255,77,94,0.30)' : 'rgba(124,158,255,0.22)'}`,
      borderRadius: 2,
      padding: '1px 5px',
      fontSize: 9,
      fontWeight: 700,
      fontFamily: 'var(--font-mono)',
      letterSpacing: '0.06em',
      marginRight: 3,
      marginBottom: 2,
    }}>
      {label}
    </span>
  );
}

// ─── Section divider label ─────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)',
      fontSize: 9,
      color: DIM,
      textTransform: 'uppercase' as const,
      letterSpacing: '0.10em',
      fontWeight: 600,
      marginBottom: 6,
      marginTop: 14,
      paddingBottom: 4,
      borderBottom: '1px solid rgba(255,255,255,0.05)',
    }}>
      {children}
    </div>
  );
}

// ─── Stat cell ─────────────────────────────────────────────────────────────────

function Stat({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div style={{ minWidth: 80 }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
        textTransform: 'uppercase' as const, letterSpacing: '0.07em', marginBottom: 2,
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 15,
        color: color ?? TEXT, lineHeight: 1.2,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: MUTED, marginTop: 1 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ─── AI-vs-Deterministic boundary banner ──────────────────────────────────────

function BoundaryBanner() {
  return (
    <div style={{
      display: 'flex', gap: 8, flexWrap: 'wrap' as const,
      margin: '10px 0',
    }}>
      <span style={{
        background: 'rgba(124,158,255,0.07)', color: AI_COLOR,
        border: '1px solid rgba(124,158,255,0.22)', borderRadius: 2,
        padding: '2px 7px', fontFamily: 'var(--font-mono)', fontSize: 9,
      }}>
        ◈ AI — semantic ranking · anomaly reasoning · mission context
      </span>
      <span style={{
        background: 'rgba(53,231,183,0.05)', color: DETERM_COLOR,
        border: '1px solid rgba(53,231,183,0.18)', borderRadius: 2,
        padding: '2px 7px', fontFamily: 'var(--font-mono)', fontSize: 9,
      }}>
        ● DETERMINISTIC — capacity · feasibility · risk · window budget
      </span>
    </div>
  );
}

// ─── Individual ranked product row (expandable) ────────────────────────────────

/**
 * Outcome of a single AI-ranked product against the deterministic plan.
 *
 * 'selected'   — the deterministic scheduler included this packet in the plan.
 * 'deferred'   — EvaluationResult.deferred_packets includes this packet_id.
 * 'not_in_plan'— the packet is not present in the recommended plan at all
 *               (e.g. not bridged, or the plan's candidate set didn't reach it).
 */
type ProductOutcome = 'selected' | 'deferred' | 'not_in_plan';

const OUTCOME_COLOR: Record<ProductOutcome, string> = {
  selected:    DETERM_COLOR,
  deferred:    WARN_COLOR,
  not_in_plan: DIM,
};

const OUTCOME_LABEL: Record<ProductOutcome, string> = {
  selected:    'SELECTED',
  deferred:    'DEFERRED',
  not_in_plan: 'NOT IN PLAN',
};

const OUTCOME_ICON: Record<ProductOutcome, string> = {
  selected:    '✓',
  deferred:    '⊘',
  not_in_plan: '–',
};

interface RankedRow {
  rp: RankedProduct;
  outcome: ProductOutcome;
  packet: Packet | null; // the matching packet from the recommended plan, if any
}

function ProductRow({ row }: { row: RankedRow }) {
  const [expanded, setExpanded] = useState(false);
  const { rp, outcome, packet } = row;

  return (
    <div style={{ borderBottom: '1px solid rgba(255,255,255,0.035)' }}>
      {/* Collapsed row */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 4px', cursor: 'pointer', userSelect: 'none' as const,
        }}
        onClick={() => setExpanded((e) => !e)}
        title={expanded ? 'Collapse' : 'Expand detail'}
      >
        {/* AI rank */}
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM,
          minWidth: 22, textAlign: 'right' as const, flexShrink: 0,
        }}>
          {String(rp.priority).padStart(2, '0')}
        </span>

        {/* Outcome icon */}
        <span style={{
          fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 11,
          color: OUTCOME_COLOR[outcome], minWidth: 12, flexShrink: 0,
        }}>
          {OUTCOME_ICON[outcome]}
        </span>

        {/* Product ID */}
        <code style={{ color: AI_COLOR, fontSize: 11, minWidth: 120, flexShrink: 0 }}>
          {rp.product_id}
        </code>

        {/* Subsystem */}
        {rp.subsystem && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: MUTED, flexShrink: 0 }}>
            {rp.subsystem}
          </span>
        )}

        {/* Factor badges (top 2 collapsed) */}
        <span style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 2, flex: 1 }}>
          {rp.factors.slice(0, 2).map((f) => <FactorBadge key={f} factor={f} />)}
          {rp.factors.length > 2 && (
            <span style={{ fontSize: 9, color: DIM, fontFamily: 'var(--font-mono)', padding: '1px 3px' }}>
              +{rp.factors.length - 2}
            </span>
          )}
        </span>

        {/* Size (when we have the packet) */}
        {packet && (
          <span style={{ fontSize: 10, color: DIM, fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
            {formatBitsAsDataVolume(packet.size_bits)}
          </span>
        )}

        {/* Outcome badge */}
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
          color: OUTCOME_COLOR[outcome],
          background: outcome === 'selected'
            ? 'rgba(53,231,183,0.07)'
            : outcome === 'deferred'
              ? 'rgba(255,182,72,0.07)'
              : 'transparent',
          border: `1px solid ${
            outcome === 'selected' ? 'rgba(53,231,183,0.22)'
              : outcome === 'deferred' ? 'rgba(255,182,72,0.3)'
              : 'transparent'
          }`,
          borderRadius: 2, padding: '1px 5px', flexShrink: 0, minWidth: 72, textAlign: 'center' as const,
        }}>
          {OUTCOME_LABEL[outcome]}
        </span>

        {/* Expand toggle */}
        <span style={{ fontSize: 9, color: DIM, flexShrink: 0 }}>{expanded ? '▲' : '▼'}</span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          padding: '7px 10px 10px 44px',
          background: 'rgba(124,158,255,0.025)',
          borderTop: '1px solid rgba(255,255,255,0.035)',
        }}>
          {/* WHY AI label */}
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
            textTransform: 'uppercase' as const, letterSpacing: '0.09em',
            marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5,
          }}>
            <span style={{ color: AI_COLOR }}>◈</span> AI PRIORITIZATION REASONING
          </div>

          {rp.anomaly_ids.length > 0 && (
            <div style={{ marginBottom: 4, fontSize: 12 }}>
              <span style={{ color: DIM, fontFamily: 'var(--font-mono)', fontSize: 10 }}>Anomaly  </span>
              {rp.anomaly_ids.map((a) => (
                <code key={a} style={{ color: CRIT_COLOR, fontSize: 11, marginRight: 5 }}>{a}</code>
              ))}
            </div>
          )}

          {rp.factors.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM, marginBottom: 3 }}>
                Decision factors
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 2 }}>
                {rp.factors.map((f) => <FactorBadge key={f} factor={f} />)}
              </div>
            </div>
          )}

          <div style={{ marginBottom: 4, fontSize: 12 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM, marginBottom: 3 }}>
              AI reasoning
            </div>
            <div style={{
              fontSize: 12, color: MUTED, lineHeight: 1.5,
              background: 'rgba(0,0,0,0.12)', borderRadius: 3, padding: '5px 8px',
              fontStyle: 'italic' as const,
            }}>
              "{rp.reason}"
            </div>
          </div>

          {rp.confidence !== null && rp.confidence !== undefined && (
            <div style={{ fontSize: 11, color: DIM, fontFamily: 'var(--font-mono)', marginTop: 4 }}>
              AI confidence for this product:{' '}
              <span style={{ color: AI_COLOR, fontWeight: 700 }}>
                {(rp.confidence * 100).toFixed(0)}%
              </span>
              <span style={{ fontSize: 10, color: DIM, marginLeft: 6 }}>
                (AI judgment — not physical mission confidence)
              </span>
            </div>
          )}

          {/* Deterministic outcome explanation */}
          {outcome === 'deferred' && (
            <div style={{
              marginTop: 7, padding: '5px 8px',
              background: 'rgba(255,182,72,0.06)',
              border: '1px solid rgba(255,182,72,0.25)', borderRadius: 3,
              fontSize: 11, fontFamily: 'var(--font-mono)', color: WARN_COLOR,
            }}>
              ● DETERMINISTIC: This product was prioritized by the AI but could not fit
              in the current communication window. Deferred to next transmission opportunity.
            </div>
          )}
          {outcome === 'not_in_plan' && (
            <div style={{
              marginTop: 7, padding: '5px 8px',
              background: 'rgba(87,96,106,0.06)',
              border: '1px solid rgba(87,96,106,0.22)', borderRadius: 3,
              fontSize: 11, fontFamily: 'var(--font-mono)', color: DIM,
            }}>
              ● DETERMINISTIC: This product was AI-ranked but is not present in the
              evaluated transmission plan for this window.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface MissionDecisionPanelProps {
  /** AI prioritization from POST /agent/recommend. null for legacy scenarios. */
  prioritization: CandidatePrioritization | null;
  /** AI recommendation (plan_id, packet_actions, reasoning). null when unavailable. */
  recommendation: AIRecommendation | null;
  /** All generated candidate plans. Used to find the recommended plan's packets. */
  allPlans: CandidatePlan[];
  /** Evaluation result for the AI-recommended plan. null when unavailable. */
  recEval: EvaluationResult | null;
  /** Current link state — used only to derive estimated transmission time. */
  linkState: LinkState | null;
  /** Provider name, e.g. "granite", "local". null when unavailable. */
  providerName: string | null;
  /** Error surfaced when AI prioritization fell back to deterministic. */
  prioritizationError: string | null;
  /** Total number of candidates AI analyzed (≤ 50). */
  candidateCount: number | null;
}

// ─── Main component ────────────────────────────────────────────────────────────

export function MissionDecisionPanel({
  prioritization,
  recommendation,
  allPlans,
  recEval,
  linkState,
  providerName,
  prioritizationError,
  candidateCount,
}: MissionDecisionPanelProps) {

  // ── Find the recommended plan (from allPlans by plan_id) ─────────────────────
  //
  // This is the exact CandidatePlan generated after AI reordering. Its .packets
  // list is the set that the deterministic scheduler selected for this window,
  // in AI priority order. This is NOT the baseline queue.
  const recPlan: CandidatePlan | null =
    recommendation
      ? (allPlans.find((p) => p.plan_id === recommendation.recommended_plan_id) ?? null)
      : null;

  // Set of packet_ids in the recommended plan (for O(1) lookup)
  const recPlanPacketIds = new Set<string>(recPlan?.packets.map((p) => p.packet_id) ?? []);

  // Set of packet_ids that the deterministic evaluator deferred
  const deferredByEval = new Set<string>(recEval?.deferred_packets ?? []);

  // ── Derive summary numbers ────────────────────────────────────────────────────

  // Total bits in the recommended plan (sum of packets actually selected)
  const selectedBits = recPlan
    ? recPlan.packets.reduce((sum, p) => sum + p.size_bits, 0)
    : 0;

  const selectedCount  = recPlan?.packets.length ?? 0;
  const deferredCount  = recEval?.deferred_packets.length ?? 0;

  // Estimated transmission time: payload / goodput (display estimate only).
  // This is NOT the transmission simulator result. NOT the propagation delay.
  // It is a rough pre-approval estimate derived from deterministic values.
  let estTransmissionS: number | null = null;
  if (selectedBits > 0 && linkState && linkState.link_goodput_bps > 0) {
    estTransmissionS = selectedBits / linkState.link_goodput_bps;
  }

  // ── Build ranked product rows with deterministic outcome ─────────────────────
  //
  // For each AI-ranked product:
  //   - 'selected'    → packet is in recPlan and NOT in deferred list
  //   - 'deferred'    → packet is either in deferredByEval or present in
  //                     recPlanPacketIds but the evaluator says it was deferred
  //   - 'not_in_plan' → packet_id is absent from recPlan.packets entirely
  const rankedRows: RankedRow[] = (prioritization?.ranked_products ?? [])
    .slice()
    .sort((a, b) => a.priority - b.priority)
    .map((rp): RankedRow => {
      const inPlan = recPlanPacketIds.has(rp.product_id);
      const isDeferred = deferredByEval.has(rp.product_id);
      const packet = recPlan?.packets.find((p) => p.packet_id === rp.product_id) ?? null;

      let outcome: ProductOutcome;
      if (!inPlan) {
        outcome = 'not_in_plan';
      } else if (isDeferred) {
        outcome = 'deferred';
      } else {
        outcome = 'selected';
      }

      return { rp, outcome, packet };
    });

  const aiSelectedRows = rankedRows.filter((r) => r.outcome === 'selected');
  const aiDeferredRows = rankedRows.filter((r) => r.outcome === 'deferred');
  const aiNotInPlan    = rankedRows.filter((r) => r.outcome === 'not_in_plan');

  const isDeterministicFallback = !!prioritizationError;

  // ── No recommendation at all ──────────────────────────────────────────────────
  if (!recommendation) {
    return (
      <section className="panel ai-hero">
        <h2>
          <span style={{ color: AI_COLOR }}>◈</span>&nbsp;Mission Decision
        </h2>
        <p style={{ color: MUTED, fontSize: 12 }}>
          Waiting for AI recommendation…
        </p>
        <p style={{ color: DIM, fontSize: 11, marginTop: 4 }}>
          No transmission plan can be reviewed until the AI provider returns a recommendation.
        </p>
      </section>
    );
  }

  // ── Legacy scenario — recommendation exists but no CandidatePrioritization ───
  if (!prioritization) {
    return (
      <section className="panel ai-hero">
        <h2>
          <span style={{ color: AI_COLOR }}>◈</span>&nbsp;Mission Decision
          {providerName && (
            <span style={{
              marginLeft: 8, fontSize: 9, fontWeight: 700,
              background: 'rgba(124,158,255,0.10)', color: AI_COLOR,
              border: '1px solid rgba(124,158,255,0.28)',
              borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
            }}>
              {providerName}
            </span>
          )}
          <span style={{
            marginLeft: 6, fontSize: 9, fontWeight: 700,
            color: DIM, background: 'rgba(87,96,106,0.07)',
            border: '1px solid rgba(87,96,106,0.22)',
            borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
          }}>
            LEGACY MODE
          </span>
        </h2>
        <p style={{ color: MUTED, fontSize: 12, marginBottom: 8 }}>
          AI product prioritization is not available for this scenario (legacy packet mode).
          The recommended plan is <code>{recommendation.recommended_plan_id}</code>.
        </p>
        {recPlan && (
          <div style={{ fontSize: 12, color: MUTED }}>
            <span style={{ color: DIM, fontFamily: 'var(--font-mono)', fontSize: 10 }}>PACKETS IN PLAN  </span>
            <strong style={{ color: TEXT }}>{recPlan.packets.length}</strong>
            <span style={{ color: DIM, marginLeft: 12, fontFamily: 'var(--font-mono)', fontSize: 10 }}>PAYLOAD  </span>
            <strong style={{ color: TEXT }}>{formatBitsAsDataVolume(selectedBits)}</strong>
          </div>
        )}
        {recEval && (
          <div style={{ marginTop: 8 }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 11,
              color: RISK_COLOR[recEval.risk_level],
              background: RISK_BG[recEval.risk_level],
              border: `1px solid ${RISK_BORDER[recEval.risk_level]}`,
              borderRadius: 3, padding: '2px 8px',
            }}>
              {recEval.risk_level} RISK
            </span>
            <span style={{ fontSize: 10, color: DIM, fontFamily: 'var(--font-mono)', marginLeft: 8 }}>
              score {recEval.risk_score.toFixed(3)}
            </span>
          </div>
        )}
        <p style={{ color: DIM, fontSize: 11, marginTop: 8 }}>
          Use a v2/v3 scenario with <code>data_products</code> to enable AI decision transparency.
        </p>
      </section>
    );
  }

  // ── Full v2/v3 decision panel ─────────────────────────────────────────────────

  return (
    <section className="panel ai-hero">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <h2>
        <span style={{ color: AI_COLOR }}>◈</span>&nbsp;Mission Decision
        {providerName && (
          <span style={{
            marginLeft: 8, fontSize: 9, fontWeight: 700,
            background: isDeterministicFallback ? 'rgba(255,182,72,0.09)' : 'rgba(124,158,255,0.10)',
            color: isDeterministicFallback ? WARN_COLOR : AI_COLOR,
            border: `1px solid ${isDeterministicFallback ? 'rgba(255,182,72,0.32)' : 'rgba(124,158,255,0.28)'}`,
            borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
          }}>
            {providerName}
          </span>
        )}
        <span style={{
          marginLeft: 6, fontSize: 9, fontWeight: 700,
          color: isDeterministicFallback ? WARN_COLOR : DETERM_COLOR,
          background: isDeterministicFallback ? 'rgba(255,182,72,0.06)' : 'rgba(53,231,183,0.06)',
          border: `1px solid ${isDeterministicFallback ? 'rgba(255,182,72,0.28)' : 'rgba(53,231,183,0.22)'}`,
          borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
        }}>
          {isDeterministicFallback ? '⚠ FALLBACK' : '● ACTIVE'}
        </span>
      </h2>

      {/* ── AI fallback banner ──────────────────────────────────────────── */}
      {prioritizationError && (
        <div style={{
          background: 'rgba(255,182,72,0.07)', border: '1px solid rgba(255,182,72,0.32)',
          borderRadius: 4, padding: '7px 10px', marginBottom: 10, fontSize: 12,
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: WARN_COLOR, fontSize: 10, marginBottom: 2 }}>
            ⚠ AI PRIORITIZATION UNAVAILABLE — DETERMINISTIC FALLBACK ACTIVE
          </div>
          <div style={{ color: MUTED }}>{prioritizationError}</div>
          <div style={{ color: DIM, fontSize: 11, marginTop: 3 }}>
            Deterministic scheduler has selected the transmission plan. Operator approval is still required.
          </div>
        </div>
      )}

      {/* ── AI vs Deterministic boundary ───────────────────────────────── */}
      <BoundaryBanner />

      {/* ── AI overall reasoning ────────────────────────────────────────── */}
      <SectionLabel>AI Overall Reasoning</SectionLabel>
      <div style={{
        fontSize: 12, color: MUTED, lineHeight: 1.55,
        background: 'rgba(124,158,255,0.03)', borderRadius: 3,
        padding: '8px 10px', marginBottom: 2, fontStyle: 'italic' as const,
      }}>
        "{prioritization.overall_reasoning}"
      </div>

      {/* ── Decision summary stats ──────────────────────────────────────── */}
      <SectionLabel>Transmission Decision Summary</SectionLabel>
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' as const, marginBottom: 10 }}>
        <Stat
          label="Candidates analyzed"
          value={String(candidateCount ?? prioritization.candidate_count ?? prioritization.ranked_products.length)}
          sub="by AI"
          color={AI_COLOR}
        />
        <Stat
          label="Prioritized queue"
          value={String(selectedCount)}
          sub="products in plan"
          color={DETERM_COLOR}
        />
        <Stat
          label="Projected this contact"
          value={String(selectedCount - deferredCount)}
          sub="non-deferred"
          color={DETERM_COLOR}
        />
        <Stat
          label="Projected deferred"
          value={deferredCount > 0 ? String(deferredCount) : '0'}
          sub="capacity limited"
          color={deferredCount > 0 ? WARN_COLOR : undefined}
        />
        <Stat
          label="Priority queue payload"
          value={selectedBits > 0 ? formatBitsAsDataVolume(selectedBits) : '—'}
          sub="full plan (incl. deferred)"
        />
        {estTransmissionS !== null && (
          <Stat
            label="Full queue tx time"
            value={fmtSeconds(estTransmissionS)}
            sub="est. at current goodput"
            color={MUTED}
          />
        )}
        <Stat
          label="AI confidence"
          value={`${(prioritization.confidence * 100).toFixed(0)}%`}
          sub="AI judgment only"
          color={AI_COLOR}
        />
      </div>

      {/* ── Deterministic risk assessment ───────────────────────────────── */}
      {recEval && (
        <>
          <SectionLabel>Deterministic Risk Assessment</SectionLabel>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' as const, marginBottom: 10, alignItems: 'center' }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12,
              color: RISK_COLOR[recEval.risk_level],
              background: RISK_BG[recEval.risk_level],
              border: `1px solid ${RISK_BORDER[recEval.risk_level]}`,
              borderRadius: 3, padding: '3px 10px',
            }}>
              {recEval.risk_level} RISK
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: MUTED }}>
              score {recEval.risk_score.toFixed(3)}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: MUTED }}>
              BW util {(recEval.bandwidth_utilization * 100).toFixed(0)}%
            </span>
            {recEval.deadline_misses > 0 && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: CRIT_COLOR, fontWeight: 700 }}>
                {recEval.deadline_misses} deadline miss{recEval.deadline_misses !== 1 ? 'es' : ''}
              </span>
            )}
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM }}>
              (authoritative — deterministic evaluation)
            </span>
          </div>
        </>
      )}

      {/* ── Recommended plan identification ─────────────────────────────── */}
      <SectionLabel>Recommended Transmission Plan</SectionLabel>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' as const, marginBottom: 8, alignItems: 'center' }}>
        <code style={{ color: AI_COLOR, fontSize: 12 }}>{recommendation.recommended_plan_id}</code>
        {recPlan && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM }}>
            strategy: {recPlan.strategy}
          </span>
        )}
        {!recPlan && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: WARN_COLOR }}>
            ⚠ Plan not found in current candidate set
          </span>
        )}
        {recommendation.alternative_plan_id && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM }}>
            alt: <code>{recommendation.alternative_plan_id}</code>
          </span>
        )}
      </div>

      {/* ── AI primary decision factors ──────────────────────────────────── */}
      {prioritization.decision_factors.length > 0 && (
        <>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
            textTransform: 'uppercase' as const, letterSpacing: '0.09em',
            marginBottom: 4,
          }}>
            AI primary decision factors
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 2, marginBottom: 10 }}>
            {prioritization.decision_factors.map((f) => <FactorBadge key={f} factor={f} />)}
          </div>
        </>
      )}

      {/* ── Ranked products with outcomes ───────────────────────────────── */}
      <SectionLabel>
        AI Priority → Deterministic Outcome
        <span style={{ marginLeft: 10, fontWeight: 400, letterSpacing: 0, textTransform: 'none' as const, color: DIM }}>
          {aiSelectedRows.length} selected · {aiDeferredRows.length} deferred · {aiNotInPlan.length} not in plan
        </span>
      </SectionLabel>

      {rankedRows.length === 0 ? (
        <div style={{ color: DIM, fontSize: 12, fontFamily: 'var(--font-mono)' }}>
          No ranked products available.
        </div>
      ) : (
        <div style={{ maxHeight: 400, overflowY: 'auto' as const }}>
          {/* Selected products first */}
          {aiSelectedRows.length > 0 && (
            <>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                color: DETERM_COLOR, textTransform: 'uppercase' as const,
                letterSpacing: '0.07em', padding: '4px 4px 2px',
                marginTop: 4,
              }}>
                ✓ Selected for transmission window
              </div>
              {aiSelectedRows.map((row) => <ProductRow key={row.rp.product_id} row={row} />)}
            </>
          )}

          {/* Deferred products */}
          {aiDeferredRows.length > 0 && (
            <>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                color: WARN_COLOR, textTransform: 'uppercase' as const,
                letterSpacing: '0.07em', padding: '4px 4px 2px',
                marginTop: 8,
              }}>
                ⊘ AI-prioritized but deferred by deterministic scheduler
              </div>
              {aiDeferredRows.map((row) => <ProductRow key={row.rp.product_id} row={row} />)}
            </>
          )}

          {/* Not in plan */}
          {aiNotInPlan.length > 0 && (
            <>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                color: DIM, textTransform: 'uppercase' as const,
                letterSpacing: '0.07em', padding: '4px 4px 2px',
                marginTop: 8,
              }}>
                – AI-ranked but not in evaluated plan
              </div>
              {aiNotInPlan.map((row) => <ProductRow key={row.rp.product_id} row={row} />)}
            </>
          )}
        </div>
      )}

      {/* ── Disclaimer ──────────────────────────────────────────────────── */}
      <div style={{
        marginTop: 12, fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
        paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.04)', lineHeight: 1.5,
      }}>
        AI rankings are advisory. Transmission selection and feasibility are determined by the
        deterministic scheduler and evaluated against actual link capacity.
        Prioritized queue = full plan including products that may be deferred.
        Projected this contact = products not deferred by the capacity evaluator.
        Full queue tx time = priority queue payload ÷ current goodput (display estimate — not a guarantee).
        Approve the recommended plan below.
      </div>

    </section>
  );
}
