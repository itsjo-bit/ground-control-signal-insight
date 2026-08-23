/**
 * PlanSwitcher — tab bar for switching between the 4 candidate plans.
 * Feature 1: local state switch, no re-fetch, AI-pick badge.
 */
import type { CandidatePlan, EvaluationResult } from '../types/domain';

const STRATEGY_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  deadline_first: 'Deadline First',
  mission_critical_first: 'Mission Critical',
  value_per_cost: 'Value/Cost',
};

interface Props {
  plans: CandidatePlan[];
  evaluations: EvaluationResult[];
  activePlanId: string;
  aiRecommendedPlanId: string | null;
  onSelect: (planId: string) => void;
}

export function PlanSwitcher({
  plans,
  evaluations,
  activePlanId,
  aiRecommendedPlanId,
  onSelect,
}: Props) {
  return (
    <div className="plan-switcher">
      {plans.map((plan) => {
        const ev = evaluations.find((e) => e.plan_id === plan.plan_id);
        const isActive = plan.plan_id === activePlanId;
        const isAi = plan.plan_id === aiRecommendedPlanId;
        return (
          <button
            key={plan.plan_id}
            className={'plan-tab' + (isActive ? ' plan-tab--active' : '')}
            onClick={() => onSelect(plan.plan_id)}
          >
            <span className="plan-tab__label">
              {STRATEGY_LABELS[plan.strategy] ?? plan.strategy}
            </span>
            {isAi && <span className="plan-tab__ai-badge">AI pick</span>}
            {ev && (
              <span
                className="plan-tab__risk"
                style={{ color: riskColour(ev.risk_level) }}
              >
                {ev.risk_score.toFixed(3)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function riskColour(level: string): string {
  switch (level) {
    case 'LOW': return 'var(--signal)';
    case 'MEDIUM': return 'var(--warn)';
    case 'HIGH': return '#ff8a3d';
    case 'CRITICAL': return 'var(--critical)';
    default: return 'var(--text-muted)';
  }
}
