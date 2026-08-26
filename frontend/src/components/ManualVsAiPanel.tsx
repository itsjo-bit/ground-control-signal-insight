/**
 * ManualVsAiPanel — Phase 4.2F5
 *
 * Shows a side-by-side comparison of Manual plan metrics vs AI-assisted
 * plan metrics using the same deterministic evaluators.
 *
 * IMPORTANT:
 * - Only shown when both assessments exist in the current session.
 * - Numbers come from actual evaluation results — never hard-coded.
 * - Does not claim AI always wins; Manual may produce superior metrics.
 */

import type { EvaluationResult } from '../types/domain';
import type { ManualAssessmentResult } from '../experience/missionExperienceReducer';
import { formatBitsAsDataVolume } from '../utils/formatters';

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
};

const SANS: React.CSSProperties = {
  fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
};

function riskColor(risk: string): string {
  if (risk === 'LOW') return '#34d399';
  if (risk === 'MEDIUM') return '#f59e0b';
  if (risk === 'HIGH') return '#fb923c';
  return '#f87171';
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface ManualVsAiPanelProps {
  manualAssessment: ManualAssessmentResult;
  aiEval: EvaluationResult;
  aiPlanPayloadBits: number;
  aiPlanPacketCount: number;
  availableCapacityBits: number;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ManualVsAiPanel({
  manualAssessment,
  aiEval,
  aiPlanPayloadBits,
  aiPlanPacketCount,
  availableCapacityBits: _availableCapacityBits,
}: ManualVsAiPanelProps) {
  const manualEval = manualAssessment.evaluation;
  const manualSummary = manualAssessment.capacity_summary;

  // Compute metrics from evaluation results
  const manualReqDelivery = manualEval.total_critical_packets > 0
    ? (manualEval.critical_packets_delivered / manualEval.total_critical_packets)
    : 1.0;
  const aiReqDelivery = aiEval.total_critical_packets > 0
    ? (aiEval.critical_packets_delivered / aiEval.total_critical_packets)
    : 1.0;

  const rows: Array<{
    label: string;
    manual: string;
    ai: string;
    manualWins?: boolean;
    aiWins?: boolean;
  }> = [
    {
      label: 'Products',
      manual: `${manualSummary.selected_count}`,
      ai: `${aiPlanPacketCount}`,
    },
    {
      label: 'Payload',
      manual: formatBitsAsDataVolume(manualSummary.selected_bits),
      ai: formatBitsAsDataVolume(aiPlanPayloadBits),
    },
    {
      label: 'Risk',
      manual: manualEval.risk_level,
      ai: aiEval.risk_level,
      manualWins: ['LOW', 'MEDIUM'].indexOf(manualEval.risk_level) < ['LOW', 'MEDIUM'].indexOf(aiEval.risk_level),
      aiWins: ['LOW', 'MEDIUM'].indexOf(aiEval.risk_level) < ['LOW', 'MEDIUM'].indexOf(manualEval.risk_level),
    },
    {
      label: 'Req. Delivery',
      manual: `${(manualReqDelivery * 100).toFixed(0)}%`,
      ai: `${(aiReqDelivery * 100).toFixed(0)}%`,
      manualWins: manualReqDelivery > aiReqDelivery,
      aiWins: aiReqDelivery > manualReqDelivery,
    },
    {
      label: 'Deferred',
      manual: `${manualEval.deferred_packets.length}`,
      ai: `${aiEval.deferred_packets.length}`,
      manualWins: manualEval.deferred_packets.length < aiEval.deferred_packets.length,
      aiWins: aiEval.deferred_packets.length < manualEval.deferred_packets.length,
    },
    {
      label: 'Deadline Misses',
      manual: `${manualEval.deadline_misses}`,
      ai: `${aiEval.deadline_misses}`,
      manualWins: manualEval.deadline_misses < aiEval.deadline_misses,
      aiWins: aiEval.deadline_misses < manualEval.deadline_misses,
    },
  ];

  return (
    <div style={{
      background: 'rgba(8,12,22,0.95)',
      border: '1px solid rgba(46,58,79,0.7)',
      borderRadius: 8,
      overflow: 'hidden',
      marginBottom: 10,
    }}>
      {/* Header */}
      <div style={{
        padding: '8px 12px',
        borderBottom: '1px solid rgba(46,58,79,0.7)',
        background: 'rgba(0,0,0,0.2)',
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.1em' }}>
          MANUAL vs AI-ASSISTED — DETERMINISTIC COMPARISON
        </span>
        <span style={{ ...SANS, fontSize: 9, color: 'rgba(147,160,180,0.35)', marginLeft: 'auto' }}>
          Same scenario · Same evaluators
        </span>
      </div>

      {/* Column headers */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1px solid rgba(46,58,79,0.5)' }}>
        <div style={{ padding: '5px 8px', background: 'rgba(52,211,153,0.04)' }}>
          <div style={{ ...MONO, fontSize: 9, color: '#34d399', fontWeight: 700 }}>MANUAL</div>
        </div>
        <div style={{ padding: '5px 8px', background: 'rgba(46,58,79,0.1)', textAlign: 'center' }}>
          <div style={{ ...MONO, fontSize: 8, color: 'rgba(147,160,180,0.3)' }}>METRIC</div>
        </div>
        <div style={{ padding: '5px 8px', background: 'rgba(76,141,255,0.04)', textAlign: 'right' }}>
          <div style={{ ...MONO, fontSize: 9, color: '#6EA8FF', fontWeight: 700 }}>AI ASSISTED</div>
        </div>
      </div>

      {/* Rows */}
      {rows.map((row) => (
        <div key={row.label} style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr',
          borderBottom: '1px solid rgba(46,58,79,0.25)',
          alignItems: 'center',
        }}>
          {/* Manual value */}
          <div style={{
            padding: '5px 8px',
            background: row.manualWins ? 'rgba(52,211,153,0.05)' : 'transparent',
          }}>
            <span style={{
              ...MONO, fontSize: 12, fontWeight: 700,
              color: row.label === 'Risk'
                ? riskColor(row.manual)
                : row.manualWins ? '#34d399' : '#e2e8f4',
            }}>
              {row.manual}
            </span>
            {row.manualWins && (
              <span style={{ ...MONO, fontSize: 9, color: '#34d399', marginLeft: 4 }}>✓</span>
            )}
          </div>
          {/* Metric label */}
          <div style={{ padding: '5px 0', textAlign: 'center' }}>
            <span style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.5)' }}>{row.label}</span>
          </div>
          {/* AI value */}
          <div style={{
            padding: '5px 8px',
            textAlign: 'right',
            background: row.aiWins ? 'rgba(76,141,255,0.05)' : 'transparent',
          }}>
            <span style={{
              ...MONO, fontSize: 12, fontWeight: 700,
              color: row.label === 'Risk'
                ? riskColor(row.ai)
                : row.aiWins ? '#6EA8FF' : '#e2e8f4',
            }}>
              {row.ai}
            </span>
            {row.aiWins && (
              <span style={{ ...MONO, fontSize: 9, color: '#6EA8FF', marginLeft: 4 }}>✓</span>
            )}
          </div>
        </div>
      ))}

      {/* Footer disclaimer */}
      <div style={{ padding: '6px 10px', background: 'rgba(0,0,0,0.1)' }}>
        <span style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.35)' }}>
          Comparison uses deterministic PlanEvaluator on both plans. No cherry-picking — metrics shown as-is.
        </span>
      </div>
    </div>
  );
}
