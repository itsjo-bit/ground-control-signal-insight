/**
 * AIDecisionPanel — Phase 2D AI Decision Transparency component.
 *
 * Shows the operator what the AI actually did:
 * - Which provider performed the prioritization
 * - How many candidates were analysed
 * - Overall AI confidence
 * - Ranked list of data products with reasons, factors, anomaly links
 * - Expandable per-product detail
 * - Decision chain diagram (AI vs Deterministic)
 * - Graceful AI-unavailable state
 *
 * IMPORTANT VISUAL CONTRACT:
 * - AI-derived information is labelled with a purple "AI" badge
 * - Deterministic information (risk score, window, etc.) is labelled separately
 * - AI confidence is NEVER equated with physical mission confidence
 */

import { useState } from 'react';
import type { CandidatePrioritization, RankedProduct } from '../types/domain';

// ─── Constants ────────────────────────────────────────────────────────────────

const AI_COLOR = 'var(--ai, #7c9eff)';
const DETERM_COLOR = 'var(--signal, #35e7b7)';
const WARN_COLOR = 'var(--warn, #ffb648)';
const CRIT_COLOR = 'var(--critical, #ff4d5e)';
const MUTED = 'var(--text-muted, #8b949e)';
const DIM = 'var(--text-dim, #57606a)';

/** Factor → display label; factors not in this map are shown as-is. */
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

const FACTOR_IS_HIGH: Set<string> = new Set([
  'active anomaly', 'high severity anomaly', 'high criticality',
  'deadline urgency', 'operational necessity',
]);

function factorBadge(factor: string) {
  const label = FACTOR_SHORT[factor] ?? factor.toUpperCase();
  const isHigh = FACTOR_IS_HIGH.has(factor);
  return (
    <span
      key={factor}
      style={{
        display: 'inline-block',
        background: isHigh ? 'rgba(255,77,94,0.12)' : 'rgba(124,158,255,0.10)',
        color: isHigh ? CRIT_COLOR : AI_COLOR,
        border: `1px solid ${isHigh ? 'rgba(255,77,94,0.35)' : 'rgba(124,158,255,0.25)'}`,
        borderRadius: 2,
        padding: '1px 5px',
        fontSize: 9,
        fontWeight: 700,
        fontFamily: 'var(--font-mono)',
        letterSpacing: '0.06em',
        marginRight: 3,
        marginBottom: 2,
      }}
    >
      {label}
    </span>
  );
}

// ─── Expandable ranked product row ────────────────────────────────────────────

function RankedProductRow({ rp, rank }: { rp: RankedProduct; rank: number }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      {/* Collapsed row */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '7px 4px',
          cursor: 'pointer', userSelect: 'none',
        }}
        onClick={() => setExpanded((e) => !e)}
        title={expanded ? 'Collapse' : 'Expand AI reasoning'}
      >
        {/* Rank number */}
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, color: DIM,
          minWidth: 24, textAlign: 'right', flexShrink: 0,
        }}>
          {String(rank).padStart(2, '0')}
        </span>

        {/* Product ID */}
        <code style={{ color: AI_COLOR, fontSize: 11, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {rp.product_id}
        </code>

        {/* Factor badges (top 2 only in collapsed view) */}
        <span style={{ display: 'flex', flexWrap: 'wrap', gap: 2, flex: 1 }}>
          {rp.factors.slice(0, 2).map((f) => factorBadge(f))}
          {rp.factors.length > 2 && (
            <span style={{ fontSize: 9, color: DIM, fontFamily: 'var(--font-mono)', padding: '1px 3px' }}>
              +{rp.factors.length - 2}
            </span>
          )}
        </span>

        {/* Anomaly badge */}
        {rp.anomaly_ids.length > 0 && (
          <span style={{
            fontSize: 9, fontFamily: 'var(--font-mono)', fontWeight: 700,
            color: CRIT_COLOR, flexShrink: 0,
          }}>
            {rp.anomaly_ids[0]}
          </span>
        )}

        {/* Per-product confidence (if present) */}
        {rp.confidence !== null && rp.confidence !== undefined && (
          <span style={{ fontSize: 9, color: MUTED, fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
            {(rp.confidence * 100).toFixed(0)}%
          </span>
        )}

        {/* Expand toggle */}
        <span style={{ fontSize: 10, color: DIM, flexShrink: 0 }}>
          {expanded ? '▲' : '▼'}
        </span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          padding: '6px 8px 10px 32px',
          background: 'rgba(124,158,255,0.03)',
          borderTop: '1px solid rgba(255,255,255,0.04)',
        }}>
          {/* WHY AI PRIORITIZED THIS header */}
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
            textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ color: AI_COLOR }}>◈</span> WHY AI PRIORITIZED THIS
          </div>

          {rp.subsystem && (
            <div style={{ marginBottom: 4, fontSize: 12 }}>
              <span style={{ color: DIM, fontFamily: 'var(--font-mono)', fontSize: 10 }}>Subsystem  </span>
              <span style={{ color: MUTED }}>{rp.subsystem}</span>
            </div>
          )}

          {rp.anomaly_ids.length > 0 && (
            <div style={{ marginBottom: 4, fontSize: 12 }}>
              <span style={{ color: DIM, fontFamily: 'var(--font-mono)', fontSize: 10 }}>Anomaly  </span>
              {rp.anomaly_ids.map((a) => (
                <code key={a} style={{ color: CRIT_COLOR, fontSize: 11, marginRight: 4 }}>{a}</code>
              ))}
            </div>
          )}

          {rp.factors.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM, marginBottom: 3 }}>
                Decision factors
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                {rp.factors.map((f) => factorBadge(f))}
              </div>
            </div>
          )}

          {rp.confidence !== null && rp.confidence !== undefined && (
            <div style={{ marginBottom: 4, fontSize: 12 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM }}>
                AI confidence{' '}
              </span>
              <span style={{ color: AI_COLOR, fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
                {(rp.confidence * 100).toFixed(0)}%
              </span>
              <span style={{ fontSize: 10, color: DIM, marginLeft: 6 }}>
                (AI judgment only — not physical mission confidence)
              </span>
            </div>
          )}

          <div style={{ marginTop: 6 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: DIM, marginBottom: 3 }}>
              AI reasoning
            </div>
            <div style={{
              fontSize: 12, color: MUTED, lineHeight: 1.5,
              background: 'rgba(0,0,0,0.15)', borderRadius: 3, padding: '5px 8px',
              fontStyle: 'italic',
            }}>
              "{rp.reason}"
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Decision chain diagram ────────────────────────────────────────────────────

function DecisionChain() {
  const steps: Array<{ label: string; sub: string; ai?: boolean }> = [
    { label: 'SPACECRAFT DATA', sub: 'Raw telemetry & products' },
    { label: '~150 PRODUCTS', sub: 'Full queued data product set' },
    { label: 'CANDIDATE FILTER', sub: 'Deterministic pre-filter — token-safe' },
    { label: '≤50 CANDIDATES', sub: 'Bounded for AI context window' },
    { label: 'AI PRIORITIZATION', sub: 'Semantic mission reasoning', ai: true },
    { label: 'RANKED DATA', sub: 'AI advisory ordering' },
    { label: 'SAFETY / FEASIBILITY', sub: 'Deterministic — authoritative' },
    { label: 'TRANSMISSION PLAN', sub: 'Final approved plan' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0, fontSize: 10 }}>
      {steps.map((s, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <div style={{
            width: '100%', padding: '4px 10px', borderRadius: 3, textAlign: 'center',
            background: s.ai ? 'rgba(124,158,255,0.12)' : 'rgba(255,255,255,0.03)',
            border: `1px solid ${s.ai ? 'rgba(124,158,255,0.35)' : 'rgba(255,255,255,0.06)'}`,
            fontFamily: 'var(--font-mono)',
          }}>
            <div style={{ fontWeight: 700, fontSize: 9, color: s.ai ? AI_COLOR : MUTED, letterSpacing: '0.07em' }}>
              {s.label}
              {s.ai && (
                <span style={{
                  marginLeft: 5, background: 'rgba(124,158,255,0.2)', color: AI_COLOR,
                  borderRadius: 2, padding: '0 4px', fontSize: 8,
                }}>AI</span>
              )}
            </div>
            <div style={{ fontSize: 8, color: DIM, marginTop: 1 }}>{s.sub}</div>
          </div>
          {i < steps.length - 1 && (
            <div style={{ color: DIM, fontSize: 10, lineHeight: '12px' }}>↓</div>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Main panel ───────────────────────────────────────────────────────────────

interface Props {
  prioritization: CandidatePrioritization | null;
  providerName: string | null;
  candidateCount: number | null;
  prioritizationError: string | null;
}

export function AIDecisionPanel({
  prioritization,
  providerName,
  candidateCount,
  prioritizationError,
}: Props) {
  const [showChain, setShowChain] = useState(false);
  const isDeterministicFallback = !!prioritizationError;

  return (
    <section className="panel ai-hero">
      {/* Header */}
      <h2>
        <span style={{ color: AI_COLOR }}>◈</span>&nbsp;AI Prioritization
        {providerName && (
          <span style={{
            marginLeft: 8, fontSize: 9, fontWeight: 700,
            background: isDeterministicFallback ? 'rgba(255,182,72,0.10)' : 'rgba(124,158,255,0.12)',
            color: isDeterministicFallback ? WARN_COLOR : AI_COLOR,
            border: `1px solid ${isDeterministicFallback ? 'rgba(255,182,72,0.35)' : 'rgba(124,158,255,0.3)'}`,
            borderRadius: 2, padding: '1px 6px',
          }}>
            {providerName}
          </span>
        )}
        <span style={{
          marginLeft: 6, fontSize: 9, fontWeight: 700,
          color: isDeterministicFallback ? WARN_COLOR : DETERM_COLOR,
          background: isDeterministicFallback ? 'rgba(255,182,72,0.07)' : 'rgba(53,231,183,0.07)',
          border: `1px solid ${isDeterministicFallback ? 'rgba(255,182,72,0.3)' : 'rgba(53,231,183,0.25)'}`,
          borderRadius: 2, padding: '1px 6px', fontFamily: 'var(--font-mono)',
        }}>
          {isDeterministicFallback ? '⚠ FALLBACK' : '● ACTIVE'}
        </span>
        <button
          style={{
            marginLeft: 'auto', background: 'none', border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 3, color: DIM, fontSize: 9, padding: '2px 7px',
            fontFamily: 'var(--font-mono)', cursor: 'pointer',
          }}
          onClick={() => setShowChain((v) => !v)}
          title="Show/hide decision chain diagram"
        >
          {showChain ? 'Hide chain' : 'Decision chain'}
        </button>
      </h2>

      {/* AI failure banner */}
      {prioritizationError && (
        <div style={{
          background: 'rgba(255,182,72,0.08)', border: '1px solid rgba(255,182,72,0.35)',
          borderRadius: 4, padding: '7px 10px', marginBottom: 10, fontSize: 12,
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: WARN_COLOR, fontSize: 10, marginBottom: 3 }}>
            ⚠ AI PRIORITIZATION UNAVAILABLE
          </div>
          <div style={{ color: MUTED }}>{prioritizationError}</div>
          <div style={{ color: DIM, fontSize: 11, marginTop: 4 }}>
            Deterministic candidate ordering is active. Mission safety is unaffected.
          </div>
        </div>
      )}

      {/* No prioritization at all */}
      {!prioritization && !prioritizationError && (
        <div style={{ color: DIM, fontSize: 12 }}>
          AI prioritization is not available for this scenario (legacy packet mode).
          Use a v2 scenario with data_products to enable AI decision transparency.
        </div>
      )}

      {prioritization && (
        <>
          {/* Summary stats row */}
          <div style={{ display: 'flex', gap: 16, marginBottom: 10, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Analyzed
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: MUTED }}>
                {candidateCount ?? prioritization.candidate_count ?? prioritization.ranked_products.length}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Ranked
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: MUTED }}>
                {prioritization.ranked_products.length}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                AI Confidence
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: AI_COLOR }}>
                {(prioritization.confidence * 100).toFixed(0)}%
              </div>
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
                Primary factors
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                {prioritization.decision_factors.slice(0, 4).map((f) => factorBadge(f))}
              </div>
            </div>
          </div>

          {/* AI vs Deterministic label row */}
          <div style={{
            display: 'flex', gap: 10, marginBottom: 10, fontSize: 10,
            fontFamily: 'var(--font-mono)', flexWrap: 'wrap',
          }}>
            <span style={{
              background: 'rgba(124,158,255,0.08)', color: AI_COLOR,
              border: '1px solid rgba(124,158,255,0.25)', borderRadius: 2, padding: '2px 7px',
            }}>
              AI CONFIDENCE: {(prioritization.confidence * 100).toFixed(0)}% — semantic judgment
            </span>
            <span style={{
              background: 'rgba(53,231,183,0.06)', color: DETERM_COLOR,
              border: '1px solid rgba(53,231,183,0.2)', borderRadius: 2, padding: '2px 7px',
            }}>
              RISK / FEASIBILITY: deterministic — authoritative
            </span>
          </div>

          {/* Overall reasoning */}
          <h3>Overall Reasoning</h3>
          <p style={{ fontSize: 12, color: MUTED, lineHeight: 1.55, marginBottom: 10 }}>
            {prioritization.overall_reasoning}
          </p>

          {/* Top priorities */}
          {prioritization.ranked_products.length > 0 && (
            <>
              <h3>Top Priorities</h3>
              <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                {prioritization.ranked_products
                  .slice()
                  .sort((a, b) => a.priority - b.priority)
                  .map((rp) => (
                    <RankedProductRow key={rp.product_id} rp={rp} rank={rp.priority} />
                  ))}
              </div>
            </>
          )}
        </>
      )}

      {/* Decision chain diagram */}
      {showChain && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <h3>Decision Chain</h3>
          <DecisionChain />
        </div>
      )}
    </section>
  );
}
