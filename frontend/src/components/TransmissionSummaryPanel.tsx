/**
 * TransmissionSummaryPanel — Phase 2E-D3 pre-approval transmission summary.
 *
 * Shows the operator a concise human-readable summary of what would be
 * transmitted if the current plan is approved:
 *  - Packet count and total payload size
 *  - Deferred packet count
 *  - Risk level and bandwidth utilisation from the deterministic evaluation
 *  - Window budget visual bar (queued vs available)
 *
 * All values come from deterministic evaluation — no AI metrics shown here.
 * Renders nothing if neither a plan nor an evaluation is available.
 */

import type { CandidatePlan, EvaluationResult, RiskLevel } from '../types/domain';
import { formatBitsAsDataVolume } from '../utils/formatters';

// ─── helpers ──────────────────────────────────────────────────────────────────

function fmtSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60) return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(0)} s`;
}

const RISK_COLOR: Record<RiskLevel, string> = {
  LOW:      'var(--signal, #35e7b7)',
  MEDIUM:   'var(--warn,   #ffb648)',
  HIGH:     '#ff8a3d',
  CRITICAL: 'var(--critical, #ff4d5e)',
};

const RISK_BG: Record<RiskLevel, string> = {
  LOW:      'rgba(53,231,183,0.08)',
  MEDIUM:   'rgba(255,182,72,0.10)',
  HIGH:     'rgba(255,138,61,0.10)',
  CRITICAL: 'rgba(255,77,94,0.10)',
};

const RISK_BORDER: Record<RiskLevel, string> = {
  LOW:      'rgba(53,231,183,0.35)',
  MEDIUM:   'rgba(255,182,72,0.40)',
  HIGH:     'rgba(255,138,61,0.45)',
  CRITICAL: 'rgba(255,77,94,0.50)',
};

// ─── mini stat cell ───────────────────────────────────────────────────────────

function Stat({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div style={{ minWidth: 80 }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 9,
        color: 'var(--text-dim, #3d4a63)',
        textTransform: 'uppercase',
        letterSpacing: '0.08em',
        marginBottom: 2,
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontWeight: 700,
        fontSize: 14,
        color: color ?? 'var(--text, #dce6f5)',
        lineHeight: 1.2,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 9,
          color: 'var(--text-muted, #6f83a3)',
          marginTop: 1,
        }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ─── main component ───────────────────────────────────────────────────────────

interface Props {
  plan: CandidatePlan | null;
  evaluation: EvaluationResult | null;
  availableCapacityBits: number;
}

export function TransmissionSummaryPanel({ plan, evaluation, availableCapacityBits }: Props) {
  if (!plan && !evaluation) return null;

  // Derive values from plan packets when available.
  const totalBits = plan
    ? plan.packets.reduce((sum, p) => sum + p.size_bits, 0)
    : 0;
  const packetCount = plan?.packets.length ?? 0;
  const deferredCount = evaluation?.deferred_packets.length ?? 0;

  // Budget bar: total bits in plan vs available capacity.
  const barFill =
    availableCapacityBits > 0
      ? Math.min(1, totalBits / availableCapacityBits)
      : 0;
  const isOverBudget = totalBits > availableCapacityBits && availableCapacityBits > 0;

  const riskLevel = evaluation?.risk_level ?? 'MEDIUM';
  const riskScore = evaluation?.risk_score;
  const bwUtil = evaluation?.bandwidth_utilization;

  return (
    <section className="panel" style={{ paddingTop: 12, paddingBottom: 12 }}>
      {/* Panel header */}
      <h2 style={{ marginBottom: 10 }}>
        Transmission Summary
        <span style={{
          marginLeft: 8,
          fontSize: 9,
          fontWeight: 700,
          background: 'rgba(53,231,183,0.07)',
          color: 'var(--signal, #35e7b7)',
          border: '1px solid rgba(53,231,183,0.25)',
          borderRadius: 2,
          padding: '1px 6px',
          fontFamily: 'var(--font-mono)',
          letterSpacing: '0.06em',
        }}>
          DETERMINISTIC
        </span>
        {plan && (
          <span style={{
            marginLeft: 6,
            fontSize: 9,
            color: 'var(--text-dim)',
            fontFamily: 'var(--font-mono)',
          }}>
            {plan.strategy} plan
          </span>
        )}
      </h2>

      {/* Risk badge */}
      {evaluation && (
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '4px 12px',
          borderRadius: 4,
          marginBottom: 12,
          background: RISK_BG[riskLevel],
          border: `1px solid ${RISK_BORDER[riskLevel]}`,
        }}>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontWeight: 700,
            fontSize: 11,
            color: RISK_COLOR[riskLevel],
            letterSpacing: '0.06em',
          }}>
            {riskLevel} RISK
          </span>
          {riskScore !== undefined && (
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: RISK_COLOR[riskLevel],
              opacity: 0.8,
            }}>
              {riskScore.toFixed(3)}
            </span>
          )}
        </div>
      )}

      {/* Stats row */}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 12 }}>
        {plan && (
          <>
            <Stat
              label="Packets"
              value={String(packetCount)}
              sub="to transmit"
            />
            <Stat
              label="Payload"
              value={formatBitsAsDataVolume(totalBits)}
              sub="total selected"
            />
          </>
        )}
        {evaluation && (
          <>
            <Stat
              label="Deferred"
              value={String(deferredCount)}
              sub="packets"
              color={deferredCount > 0 ? 'var(--warn, #ffb648)' : undefined}
            />
            {bwUtil !== undefined && (
              <Stat
                label="BW Utilization"
                value={`${(bwUtil * 100).toFixed(1)}%`}
                sub="of window"
                color={bwUtil > 0.9 ? 'var(--warn, #ffb648)' : undefined}
              />
            )}
            {evaluation.deadline_misses > 0 && (
              <Stat
                label="Deadline Misses"
                value={String(evaluation.deadline_misses)}
                color="var(--critical, #ff4d5e)"
              />
            )}
            {evaluation.avg_packet_delay_s > 0 && (
              <Stat
                label="Avg Delay"
                value={fmtSeconds(evaluation.avg_packet_delay_s)}
              />
            )}
          </>
        )}
      </div>

      {/* Window budget bar */}
      {plan && availableCapacityBits > 0 && (
        <div>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontFamily: 'var(--font-mono)',
            fontSize: 9,
            color: 'var(--text-dim)',
            textTransform: 'uppercase',
            letterSpacing: '0.07em',
            marginBottom: 4,
          }}>
            <span>Window Budget</span>
            <span style={{ color: isOverBudget ? 'var(--critical, #ff4d5e)' : 'var(--text-muted)' }}>
              {formatBitsAsDataVolume(totalBits)} / {formatBitsAsDataVolume(availableCapacityBits)}
              {isOverBudget && ' ⚠ EXCEEDS WINDOW'}
            </span>
          </div>
          <div style={{
            height: 5,
            background: 'var(--border, #1a2540)',
            borderRadius: 3,
            overflow: 'hidden',
          }}>
            <div style={{
              height: '100%',
              width: `${(barFill * 100).toFixed(1)}%`,
              background: isOverBudget
                ? 'var(--critical, #ff4d5e)'
                : barFill > 0.85
                  ? 'var(--warn, #ffb648)'
                  : 'var(--signal, #35e7b7)',
              borderRadius: 3,
              transition: 'width 0.4s ease',
            }} />
          </div>
        </div>
      )}
    </section>
  );
}
