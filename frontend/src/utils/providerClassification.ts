/**
 * providerClassification.ts — Phase 5.1E
 *
 * Single authoritative provider classification for all UI components.
 *
 * Rules (INVARIANT from Phase 5.1C):
 *   Known external LLM  → { kind: 'external_ai', badge: 'AI · <NAME>' }
 *   Known local/deterministic → { kind: 'local_deterministic', badge: 'TRIAGE · LOCAL' }
 *   Unknown/null/empty  → { kind: 'unknown', badge: 'ADVISORY' }
 *
 * NEVER: unknown → AI
 * NEVER: local → AI
 * Provider identity is the ONLY authority for the AI label.
 */

export type ProviderKind = 'external_ai' | 'local_deterministic' | 'unknown';

export interface ProviderClassification {
  kind: ProviderKind;
  /** Display name in UPPER CASE (e.g. "GRANITE", "LOCAL", "ADVISORY"). */
  displayName: string;
}

/**
 * Classify a provider name string into a typed classification.
 *
 * @param providerName - The raw provider name from the backend (actual_provider).
 */
export function classifyProvider(providerName: string | null | undefined): ProviderClassification {
  if (!providerName || providerName.trim() === '') {
    return { kind: 'unknown', displayName: 'ADVISORY' };
  }

  const n = providerName.toLowerCase().trim();

  // Known local / deterministic / rule-based providers
  if (
    n.includes('local') ||
    n.includes('deterministic') ||
    n.includes('rule') ||
    n === 'fallback'
  ) {
    return { kind: 'local_deterministic', displayName: 'LOCAL' };
  }

  // Known external LLM providers
  if (n.includes('granite')) return { kind: 'external_ai', displayName: 'GRANITE' };
  if (n.includes('gemini')) return { kind: 'external_ai', displayName: 'GEMINI' };
  if (n.includes('ollama')) return { kind: 'external_ai', displayName: 'OLLAMA' };

  // Any other non-empty string that does not match known local patterns:
  // if it looks like an AI model, label it AI — but if unknown, fall safe to ADVISORY.
  // Conservative approach: only recognize explicitly known external providers as AI.
  // Anything else is ADVISORY (fail-safe).
  return { kind: 'unknown', displayName: 'ADVISORY' };
}

/**
 * Build the top-bar badge label for the AI lifecycle badge.
 * Returns the full badge text (e.g. "AI · GRANITE · ANALYZING", "TRIAGE · LOCAL", "ADVISORY · READY").
 *
 * Phase 5.1F (WORKSTREAM J):
 * Provider identity determines whether the word "AI" is allowed.
 * Unknown or local providers NEVER receive an AI badge, even during analyzing/error states.
 *
 * Rules:
 *   Known external LLM + analyzing  → "AI · <NAME> · ANALYZING"
 *   Known external LLM + error      → "AI · <NAME> · FAILED"
 *   Local/deterministic + analyzing → "TRIAGE · LOCAL · ANALYZING"
 *   Local/deterministic + error     → "TRIAGE · LOCAL · FAILED"
 *   Unknown + analyzing             → "ADVISORY · ANALYZING"
 *   Unknown + error                 → "ADVISORY · FAILED"
 */
export function buildProviderBadgeLabel(
  providerName: string | null | undefined,
  lifecycle: 'analyzing' | 'ready' | 'error' | 'stale',
): string {
  const classification = classifyProvider(providerName);

  // Phase 5.1F: classify first, then build label per provider kind
  if (lifecycle === 'analyzing') {
    if (classification.kind === 'external_ai') return `AI · ${classification.displayName} · ANALYZING`;
    if (classification.kind === 'local_deterministic') return 'TRIAGE · LOCAL · ANALYZING';
    return 'ADVISORY · ANALYZING';
  }

  if (lifecycle === 'error') {
    if (classification.kind === 'external_ai') return `AI · ${classification.displayName} · FAILED`;
    if (classification.kind === 'local_deterministic') return 'TRIAGE · LOCAL · FAILED';
    return 'ADVISORY · FAILED';
  }

  if (lifecycle === 'stale') {
    if (classification.kind === 'local_deterministic') return 'TRIAGE · STALE';
    if (classification.kind === 'unknown') return 'ADVISORY · STALE';
    return `AI · ${classification.displayName} · STALE`;
  }

  // lifecycle === 'ready'
  if (classification.kind === 'local_deterministic') return 'TRIAGE · LOCAL';
  if (classification.kind === 'unknown') return 'ADVISORY · READY';
  return `AI · ${classification.displayName}`;
}
