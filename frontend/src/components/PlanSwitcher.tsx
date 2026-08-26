/**
 * PlanSwitcher — tab bar for switching between the candidate plans.
 * Feature 1: local state switch, no re-fetch, AI-pick badge.
 * Supports 4 deterministic plans + optional ai-prioritized 5th plan.
 */
import type { CandidatePlan, EvaluationResult } from '../types/domain';

const STRATEGY_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  deadline_first: 'Deadline First',
  mission_critical_first: 'Mission Critical',
  value_per_cost: 'Value/Cost',
  // AI-prioritized plan
  ai_prioritized: 'AI Prioritized',
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
              {/* Provenance badge for the AI-prioritized plan */}
              {plan.strategy === 'ai_prioritized' && (
                <span className="plan-tab__ai-badge" style={{ background: 'rgba(76,141,255,0.15)', color: '#6EA8FF', border: '1px solid rgba(76,141,255,0.30)' }}>
                  AI
                </span>
              )}
              {isAi && plan.strategy !== 'ai_prioritized' && (
                <span className="plan-tab__ai-badge">AI pick</span>
              )}
              {isAi && plan.strategy === 'ai_prioritized' && (
                <span className="plan-tab__ai-badge" style={{ background: 'rgba(52,211,153,0.12)', color: '#34d399', border: '1px solid rgba(52,211,153,0.25)' }}>
                  ★ rec
                </span>
              )}
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
