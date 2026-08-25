/**
 * TransmissionOutcomeBanner — Phase 2E-D5.
 *
 * Compact, persistent post-transmission outcome summary rendered directly
 * inside the approval panel immediately after ApprovalBar.
 *
 * Renders null until approvalPhase === 'complete' AND simulationResult is
 * non-null.  Never claims a successful outcome merely because the workflow
 * reached 'complete' — outcome is derived exclusively from SimulationResult.
 *
 * Deterministic boundary:
 *   All outcome classification uses ONLY:
 *     simulationResult.delivered_packets (Bernoulli trial successes)
 *     simulationResult.deferred_packets  (window exhaustion)
 *     simulationResult.failed_packets    (MAX_ATTEMPTS exhausted)
 *     simulationResult.elapsed_time_s    (total scalar — NOT divided per-packet)
 *     simulationResult.plan_id           (identity of actually simulated plan)
 *
 *   AI confidence, AI ranking, AI reasoning, risk score, risk level, and
 *   anomaly data are NOT used to derive or display the transmission outcome.
 *
 * Override detection:
 *   isAiRecommendedPlan drives the AI RECOMMENDED / OPERATOR OVERRIDE badge.
 *   It is the caller's responsibility to set this correctly:
 *     true  when the approved plan was the AI-recommended plan
 *     false when the operator submitted a reordered override
 */

import type { ApprovalPhase } from './ApprovalBar';
import type { SimulationResult } from '../types/domain';

// ─── Public types ─────────────────────────────────────────────────────────────

export interface TransmissionOutcomeBannerProps {
  approvalPhase: ApprovalPhase;
  simulationResult: SimulationResult | null;
  /** true when the approved plan was the AI recommendation; false for operator override. */
  isAiRecommendedPlan: boolean;
}

// ─── Outcome classification ───────────────────────────────────────────────────

export type OutcomeClass =
  | 'success'       // delivered > 0, failed === 0
  | 'partial'       // delivered > 0, (deferred > 0 OR failed > 0)
  | 'failed'        // delivered === 0, failed > 0
  | 'deferred'      // delivered === 0, failed === 0, deferred > 0
  | 'neutral';      // all three lists empty

/**
 * Derive outcome class from SimulationResult arrays.
 * This is a pure function of the deterministic simulation output.
 * Never uses AI fields.
 */
export function deriveOutcomeClass(result: SimulationResult): OutcomeClass {
  const d = result.delivered_packets.length;
  const def = result.deferred_packets.length;
  const f = result.failed_packets.length;
  if (d > 0 && f === 0 && def === 0) return 'success';
  if (d > 0 && (def > 0 || f > 0))  return 'partial';
  if (d === 0 && f > 0)              return 'failed';
  if (d === 0 && def > 0)            return 'deferred';
  return 'neutral';
}

const OUTCOME_LABEL: Record<OutcomeClass, string> = {
  success:  'TRANSMISSION COMPLETE',
  partial:  'PARTIAL DELIVERY',
  failed:   'TRANSMISSION FAILED',
  deferred: 'TRANSMISSION DEFERRED',
  neutral:  'NO PACKETS REPORTED',
};

// accent color for the left border + heading
const OUTCOME_ACCENT: Record<OutcomeClass, string> = {
  success:  'var(--signal, #35e7b7)',
  partial:  'var(--warn, #ffb648)',
  failed:   'var(--critical, #ff4d5e)',
  deferred: 'var(--text-muted, #6f83a3)',
  neutral:  'var(--text-dim, #3d4a63)',
};

const OUTCOME_BG: Record<OutcomeClass, string> = {
  success:  'rgba(53,231,183,0.06)',
  partial:  'rgba(255,182,72,0.06)',
  failed:   'rgba(255,77,94,0.06)',
  deferred: 'rgba(111,131,163,0.06)',
  neutral:  'rgba(61,74,99,0.06)',
};

// ─── Format helpers (mirrors existing project helpers, kept local) ────────────

function fmtSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60)   return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(1)} s`;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function TransmissionOutcomeBanner({
  approvalPhase,
  simulationResult,
  isAiRecommendedPlan,
}: TransmissionOutcomeBannerProps) {
  // Only render when transmission is complete AND we have an actual result.
  if (approvalPhase !== 'complete') return null;
  if (simulationResult === null)    return null;

  const sim = simulationResult;
  const outcome = deriveOutcomeClass(sim);
  const accent  = OUTCOME_ACCENT[outcome];

  const deliveredCount = sim.delivered_packets.length;
  const deferredCount  = sim.deferred_packets.length;
  const failedCount    = sim.failed_packets.length;

  return (
    <div style={{
      borderLeft: `3px solid ${accent}`,
      background: OUTCOME_BG[outcome],
      borderRadius: '0 4px 4px 0',
      padding: '12px 14px',
      marginTop: 10,
      marginBottom: 2,
    }}>
      {/* ── Row 1: outcome label + plan origin badge ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontWeight: 700,
          fontSize: 12,
          color: accent,
          letterSpacing: '0.06em',
        }}>
          {OUTCOME_LABEL[outcome]}
        </span>

        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 9,
          fontWeight: 700,
          padding: '1px 7px',
          borderRadius: 2,
          letterSpacing: '0.06em',
          ...(isAiRecommendedPlan
            ? { background: 'rgba(124,158,255,0.10)', color: 'var(--ai, #7c9eff)', border: '1px solid rgba(124,158,255,0.35)' }
            : { background: 'rgba(255,182,72,0.10)',  color: 'var(--warn, #ffb648)', border: '1px solid rgba(255,182,72,0.40)' }),
        }}>
          {isAiRecommendedPlan ? 'AI RECOMMENDED' : 'OPERATOR OVERRIDE'}
        </span>
      </div>

      {/* ── Row 2: delivered / deferred / failed counts ── */}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>Delivered</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 16, color: 'var(--signal, #35e7b7)' }}>{deliveredCount}</div>
        </div>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>Deferred</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 16, color: deferredCount > 0 ? 'var(--warn, #ffb648)' : 'var(--text-muted)' }}>{deferredCount}</div>
        </div>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>Failed</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 16, color: failedCount > 0 ? 'var(--critical, #ff4d5e)' : 'var(--text-muted)' }}>{failedCount}</div>
        </div>
      </div>

      {/* ── Row 3: plan ID + elapsed time ── */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          Plan:&nbsp;
          <code style={{ fontSize: 11 }}>{sim.plan_id}</code>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          {/* elapsed_time_s is the total transmission duration — not per-packet. */}
          Elapsed:&nbsp;
          <span style={{ color: 'var(--text)', fontWeight: 600 }}>{fmtSeconds(sim.elapsed_time_s)}</span>
        </div>
      </div>

      {/* ── Row 4: deterministic boundary disclaimer ── */}
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9,
        color: 'var(--text-dim)', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 6,
      }}>
        ● DETERMINISTIC TRANSMISSION RESULT — outcomes determined by link quality, window budget, and packet feasibility. Not AI-generated.
      </div>
    </div>
  );
}
