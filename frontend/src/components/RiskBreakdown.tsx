/**
 * RiskBreakdown — inline popover showing the 3 risk score components.
 * Feature 2: shown when the risk badge in RecommendationPanel is clicked.
 */
import type { EvaluationResult } from '../types/domain';

interface RiskWeights {
  w_deadline_miss: number;
  w_critical_deficit: number;
  w_window_pressure: number;
}

interface Props {
  evaluation: EvaluationResult;
  weights: RiskWeights;
  onClose: () => void;
}

interface RowProps {
  label: string;
  value: number;
  weight: number;
  contribution: number;
}

function BreakdownRow({ label, value, weight, contribution }: RowProps) {
  return (
    <div className="risk-row">
      <div className="risk-row__header">
        <span className="risk-row__label">{label}</span>
        <span className="risk-row__nums">
          {(value * 100).toFixed(0)}%
          <span className="risk-row__weight"> × {weight.toFixed(2)}</span>
          <span className="risk-row__contrib"> = {contribution.toFixed(3)}</span>
        </span>
      </div>
      <div className="risk-bar-track">
        <div
          className="risk-bar-fill"
          style={{
            width: `${Math.min(value * 100, 100)}%`,
            background: barColour(value),
            transition: 'width 350ms ease-out',
          }}
        />
      </div>
    </div>
  );
}

function barColour(v: number): string {
  if (v < 0.33) return 'var(--signal)';
  if (v < 0.66) return 'var(--warn)';
  return 'var(--critical)';
}

export function RiskBreakdown({ evaluation: ev, weights: w, onClose }: Props) {
  const rows: RowProps[] = [
    {
      label: 'Deadline miss rate',
      value: ev.deadline_miss_rate,
      weight: w.w_deadline_miss,
      contribution: ev.deadline_miss_rate * w.w_deadline_miss,
    },
    {
      label: 'Critical deficit',
      value: ev.critical_deficit,
      weight: w.w_critical_deficit,
      contribution: ev.critical_deficit * w.w_critical_deficit,
    },
    {
      label: 'Window pressure',
      value: ev.window_pressure,
      weight: w.w_window_pressure,
      contribution: ev.window_pressure * w.w_window_pressure,
    },
  ];

  return (
    <div className="risk-breakdown">
      <div className="risk-breakdown__header">
        <span>Risk breakdown — <code>{ev.plan_id}</code></span>
        <button className="risk-breakdown__close" onClick={onClose}>✕</button>
      </div>
      {rows.map((r) => (
        <BreakdownRow key={r.label} {...r} />
      ))}
      <div className="risk-breakdown__total">
        Total risk score: <strong>{ev.risk_score.toFixed(3)}</strong>
        &nbsp;({ev.risk_level})
      </div>
    </div>
  );
}
