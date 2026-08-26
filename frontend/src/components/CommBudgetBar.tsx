/**
 * CommBudgetBar — Phase 2E-C2
 *
 * Renders the communication transmission budget at a glance:
 *
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │  COMM BUDGET                             6.4× oversubscribed │
 *   │  ████████████████████░░░░░░░░░░░░░░░░░░░░░░░  43 / 276 Mb   │
 *   │  Capacity: 43.2 Mb (480 s window)  Queued: 275.7 Mb          │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * If queued ≤ capacity the bar shows a healthy green fill.
 * If queued > capacity the fill is clipped to 100% and coloured amber/red
 * depending on oversubscription ratio.
 *
 * This component is purely presentational — it receives pre-computed values
 * from GET /state (Phase 2E-C1) and renders them.
 */

import { formatBitsAsDataVolume } from '../utils/formatters';

const DETERM_COLOR = 'var(--signal, #35e7b7)';
const WARN_COLOR   = 'var(--warn,   #ffb648)';
const CRIT_COLOR   = 'var(--critical, #ff4d5e)';
const DIM          = 'var(--text-dim,  #57606a)';
const MUTED        = 'var(--text-muted, #8b949e)';

interface Props {
  availableCapacityBits: number;
  queuedDataBits: number;
  dataProductsCount: number;
  remainingWindowS: number;
}

export function CommBudgetBar({
  availableCapacityBits,
  queuedDataBits,
  dataProductsCount,
  remainingWindowS,
}: Props) {
  const ratio = availableCapacityBits > 0
    ? queuedDataBits / availableCapacityBits
    : 0;

  // Fill fraction: always ≤ 1 for visual (bar never overflows)
  const fillFraction = Math.min(ratio, 1);

  // Colour by oversubscription level
  let barColor: string;
  let ratioLabel: string;
  let ratioColor: string;
  if (ratio >= 3) {
    barColor    = CRIT_COLOR;
    ratioLabel  = `${ratio.toFixed(1)}× oversubscribed`;
    ratioColor  = CRIT_COLOR;
  } else if (ratio > 1) {
    barColor    = WARN_COLOR;
    ratioLabel  = `${ratio.toFixed(1)}× oversubscribed`;
    ratioColor  = WARN_COLOR;
  } else {
    barColor    = DETERM_COLOR;
    ratioLabel  = `${(fillFraction * 100).toFixed(0)}% utilised`;
    ratioColor  = DETERM_COLOR;
  }

  const capacityLabel = formatBitsAsDataVolume(availableCapacityBits);
  const queuedLabel   = formatBitsAsDataVolume(queuedDataBits);

  return (
    <div style={{ marginTop: 10 }}>
      {/* Section header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        marginBottom: 5,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9,
          color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em',
        }}>
          Comm Budget
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
          color: ratioColor,
        }}>
          {ratioLabel}
        </span>
      </div>

      {/* Fill bar */}
      <div style={{
        height: 8, borderRadius: 2, width: '100%',
        background: 'rgba(255,255,255,0.06)',
        border: '1px solid rgba(255,255,255,0.07)',
        overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${(fillFraction * 100).toFixed(1)}%`,
          background: barColor,
          borderRadius: 2,
          transition: 'width 0.4s ease',
        }} />
      </div>

      {/* Legend row */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', marginTop: 4,
        fontFamily: 'var(--font-mono)', fontSize: 10,
      }}>
        <span style={{ color: DIM }}>
          Capacity&nbsp;
          <span style={{ color: MUTED }}>{capacityLabel}</span>
          <span style={{ color: DIM }}> / {remainingWindowS.toFixed(0)} s window</span>
        </span>
        <span style={{ color: DIM }}>
          Queued&nbsp;
          <span style={{ color: ratioColor, fontWeight: 700 }}>{queuedLabel}</span>
          {dataProductsCount > 0 && (
            <span style={{ color: DIM }}> · {dataProductsCount} products</span>
          )}
        </span>
      </div>
    </div>
  );
}
