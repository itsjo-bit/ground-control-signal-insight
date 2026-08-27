import { useState } from 'react';
import type { AIRecommendation, EvaluationResult, RiskLevel } from '../types/domain';
import { RiskBreakdown } from './RiskBreakdown';

// Badge tokens reference theme.css CSS variables — no hardcoded hex.
const RISK_BADGE_BG: Record<RiskLevel, string> = {
  LOW:      'var(--risk-low-bg)',
  MEDIUM:   'var(--risk-medium-bg)',
  HIGH:     'var(--risk-high-bg)',
  CRITICAL: 'var(--risk-critical-bg)',
};
const RISK_BADGE_COLOR: Record<RiskLevel, string> = {
  LOW:      'var(--risk-low-color)',
  MEDIUM:   'var(--risk-medium-color)',
  HIGH:     'var(--risk-high-color)',
  CRITICAL: 'var(--risk-critical-color)',
};
const RISK_BADGE_BORDER: Record<RiskLevel, string> = {
  LOW:      'var(--risk-low-border)',
  MEDIUM:   'var(--risk-medium-border)',
  HIGH:     'var(--risk-high-border)',
  CRITICAL: 'var(--risk-critical-border)',
};
const RISK_BADGE_GLOW: Record<RiskLevel, string> = {
  LOW:      'var(--risk-low-glow)',
  MEDIUM:   'var(--risk-medium-glow)',
  HIGH:     'var(--risk-high-glow)',
  CRITICAL: 'var(--risk-critical-glow)',
};

interface RiskWeights {
  w_deadline_miss: number;
  w_critical_deficit: number;
  w_window_pressure: number;
}

interface Props {
  recommendation: AIRecommendation | null;
  /** The actual provider that produced the recommendation (use for badge). */
  providerName: string | null;
  /** The provider originally requested by configuration (shown when fallback occurred). */
  requestedProviderName?: string | null;
  /** Fallback reason for Stage 2; set when recommendation fell back to Local. */
  recommendationFallbackReason?: string | null;
  /** EvaluationResult for the recommended plan — used for risk breakdown (Feature 2). */
  evaluation: EvaluationResult | null;
  /** Risk weights from the backend config — used for risk breakdown (Feature 2). */
  riskWeights: RiskWeights | null;
  /** Phase 4.1: actual Stage-1 prioritization provider (null for legacy scenarios). */
  prioritizationProvider?: string | null;
  /** Phase 4.1: actual Stage-2 recommendation provider (equals providerName). */
  recommendationProvider?: string | null;
}

export function RecommendationPanel({
  recommendation: rec,
  providerName,
  requestedProviderName,
  recommendationFallbackReason,
  evaluation,
  riskWeights,
  prioritizationProvider,
  recommendationProvider,
}: Props) {
  const [showBreakdown, setShowBreakdown] = useState(false);

  // ── Unavailable state ────────────────────────────────────────────────────
  if (rec === null) {
    return (
      <section className="panel">
<h2>AI Reasoning</h2>
<p style={{ color: '#57606a' }}>
<strong style={{ color: '#8b949e' }}>AI reasoning unavailable.</strong>
          &nbsp;The AI provider has not returned a recommendation.
        </p>
<p style={{ color: '#57606a', fontSize: 12, marginTop: 6 }}>
          No reasoning, evidence, confidence score, or risk assessment is available.
          Ensure the backend has a scenario loaded and refresh to enable AI analysis.
        </p>
</section>
    );
  }

  // ── Populated state ──────────────────────────────────────────────────────
  const badgeStyle = {
    background:  RISK_BADGE_BG[rec.risk_level],
    color:       RISK_BADGE_COLOR[rec.risk_level],
    border:      `1px solid ${RISK_BADGE_BORDER[rec.risk_level]}`,
    boxShadow:   RISK_BADGE_GLOW[rec.risk_level],
    borderRadius: '4px',
    padding: '2px 9px',
    fontWeight: 700 as const,
    cursor: evaluation && riskWeights ? 'pointer' : 'default',
    outline: 'none',
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
  };

  const hasFallback = !!recommendationFallbackReason;

  // Determine provider kind for truthful labeling
  const lower = (providerName ?? '').toLowerCase();
  const isLocal = lower === 'local' || lower === 'localrulebasedprovider' || lower === 'local_rule_based';
  const isExternal = lower === 'granite' || lower === 'gemini' || lower === 'ollama';

  const heading = isLocal
    ? `Deterministic Recommendation — ${providerName}`
    : providerName
    ? `AI Reasoning — ${providerName}`
    : 'AI Reasoning';

  return (
    <section className="panel ai-hero">
      <h2>
        {heading}
        {hasFallback && (
          <span style={{
            marginLeft: 8, fontSize: 9, fontWeight: 700,
            background: 'rgba(255,182,72,0.10)',
            color: 'var(--warn, #f59e0b)',
            border: '1px solid rgba(255,182,72,0.35)',
            borderRadius: 2, padding: '1px 6px',
            fontFamily: 'var(--font-mono)',
          }}>
            ⚠ FALLBACK
          </span>
        )}
      </h2>
      {hasFallback && (
        <div style={{
          background: 'rgba(255,182,72,0.08)', border: '1px solid rgba(255,182,72,0.35)',
          borderRadius: 4, padding: '7px 10px', marginBottom: 10, fontSize: 12,
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--warn, #f59e0b)', fontSize: 10, marginBottom: 3 }}>
            ⚠ PLAN RECOMMENDATION FALLBACK
          </div>
          <div style={{ color: 'var(--text-muted, #8b949e)', marginBottom: 4 }}>
            {recommendationFallbackReason}
          </div>
          {requestedProviderName && (
            <div style={{ color: 'var(--text-dim, #57606a)', fontSize: 11 }}>
              Requested: {requestedProviderName} · Actual: {providerName ?? 'Local'}.
              Risk assessment uses the deterministic evaluator regardless of provider.
            </div>
          )}
        </div>
      )}
<p>
<strong>Recommended plan:</strong>
<code>{rec.recommended_plan_id}</code>
        {rec.alternative_plan_id && (
          <> &nbsp;| Alternative: <code>{rec.alternative_plan_id}</code>
</>
        )}
      </p>
<p>
        <strong>
          {rec.confidence_semantics === 'heuristic'
            ? 'Heuristic Score:'
            : rec.confidence_semantics === 'uncalibrated_llm' || isExternal
            ? 'AI Confidence Score:'
            : 'Advisory Score:'}
        </strong>{' '}
        {(rec.confidence * 100).toFixed(0)} / 100{' '}
        <span style={{ fontSize: 10, color: 'var(--text-dim, #57606a)', fontStyle: 'italic' }}>
          ({rec.confidence_semantics === 'uncalibrated_llm'
            ? 'uncalibrated model judgment — not a probability of mission success'
            : rec.confidence_semantics === 'heuristic'
            ? 'deterministic heuristic — not a probability of mission success'
            : 'advisory — not a probability of mission success'})
        </span>
        &nbsp;
        <strong>Plan risk:</strong>{' '}
        {rec.risk_score.toFixed(3)}{' '}
        <button
          style={badgeStyle}
          title={evaluation && riskWeights ? 'Click to see risk breakdown' : undefined}
          onClick={() => evaluation && riskWeights && setShowBreakdown((s) => !s)}
        >
          {rec.risk_level}
          {evaluation && riskWeights && (
            <span style={{ marginLeft: 5, opacity: 0.7, fontSize: 10 }}>
              {showBreakdown ? '▲' : '▼'}
            </span>
          )}
        </button>
</p>

      {/* Feature 2: risk breakdown popover */}
      {showBreakdown && evaluation && riskWeights && (
        <RiskBreakdown
          evaluation={evaluation}
          weights={riskWeights}
          onClose={() => setShowBreakdown(false)}
        />
      )}

      {/* Phase 4.1: Stage-specific provider identity — show when providers differ */}
      {prioritizationProvider && recommendationProvider && prioritizationProvider !== recommendationProvider && (
        <p style={{ fontSize: 11, color: 'var(--text-muted, #8b949e)', marginTop: 4 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>
            Prioritization: {prioritizationProvider} · Recommendation: {recommendationProvider}
          </span>
        </p>
      )}

<p>
<strong>Reasoning:</strong> {rec.reasoning}</p>

      {rec.evidence.length > 0 && (
        <>
<h3>Evidence</h3>
<table>
<thead>
<tr>
<th>Source</th>
<th>Field</th>
<th>Value</th>
<th>Interpretation</th>
</tr>
</thead>
<tbody>
              {rec.evidence.map((ev, i) => (
                <tr key={i}>
<td>{ev.source}</td>
<td>
<code>{ev.field}</code>
</td>
<td>{String(ev.value)}</td>
<td>{ev.interpretation}</td>
</tr>
              ))}
            </tbody>
</table>
</>
      )}
    </section>
  );
}
