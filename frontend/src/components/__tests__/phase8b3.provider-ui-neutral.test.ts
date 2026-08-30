/**
 * phase8b3.provider-ui-neutral.test.ts — Phase 8B.3 regression tests
 *
 * Verifies that:
 *   1. The primary operator badge does NOT expose vendor names (Gemini, Granite, Ollama).
 *   2. External AI operation produces a neutral "ACTIVE" badge status.
 *   3. Fallback remains clearly visible — NOT hidden as normal operation.
 *   4. Local/deterministic providers are never labelled as AI.
 *   5. Unknown providers are never labelled as AI.
 *   6. Provider classification (kind) is unchanged — only the badge label is neutralized.
 *
 * These are pure logic tests — no DOM or React renderer required.
 */

import { describe, it, expect } from 'vitest';
import {
  classifyProvider,
  buildProviderBadgeLabel,
} from '../../utils/providerClassification';

// ─── Task 10/11: Negative assertions — primary badge MUST NOT contain vendor names ──

describe('Phase 8B.3 — primary badge does not expose vendor names', () => {
  const vendorNames = ['GEMINI', 'Gemini', 'gemini', 'GRANITE', 'Granite', 'granite', 'OLLAMA', 'Ollama', 'ollama'];
  const lifecycles = ['ready', 'analyzing', 'error', 'stale'] as const;

  for (const provider of ['Gemini', 'Granite', 'ollama']) {
    for (const lifecycle of lifecycles) {
      it(`${provider} + ${lifecycle} — badge must not contain vendor name`, () => {
        const badge = buildProviderBadgeLabel(provider, lifecycle);
        for (const v of vendorNames) {
          expect(badge).not.toContain(v);
        }
      });
    }
  }
});

describe('Phase 8B.3 — external AI normal operation shows neutral ACTIVE status', () => {
  it('Gemini ready → ACTIVE', () => {
    expect(buildProviderBadgeLabel('Gemini', 'ready')).toBe('ACTIVE');
  });

  it('Granite ready → ACTIVE', () => {
    expect(buildProviderBadgeLabel('Granite', 'ready')).toBe('ACTIVE');
  });

  it('ollama ready → ACTIVE', () => {
    expect(buildProviderBadgeLabel('ollama', 'ready')).toBe('ACTIVE');
  });

  it('Gemini analyzing → ANALYZING', () => {
    expect(buildProviderBadgeLabel('Gemini', 'analyzing')).toBe('ANALYZING');
  });

  it('Granite analyzing → ANALYZING', () => {
    expect(buildProviderBadgeLabel('Granite', 'analyzing')).toBe('ANALYZING');
  });

  it('Gemini stale → STALE', () => {
    expect(buildProviderBadgeLabel('Gemini', 'stale')).toBe('STALE');
  });

  it('Gemini error → FAILED', () => {
    expect(buildProviderBadgeLabel('Gemini', 'error')).toBe('FAILED');
  });
});

describe('Phase 8B.3 — fallback remains visible, not hidden', () => {
  it('local provider ready shows TRIAGE · LOCAL (not AI · ACTIVE)', () => {
    const badge = buildProviderBadgeLabel('local', 'ready');
    expect(badge).toBe('TRIAGE · LOCAL');
    expect(badge).not.toBe('ACTIVE');
    expect(badge).not.toMatch(/^AI /);
  });

  it('local provider analyzing shows TRIAGE · LOCAL · ANALYZING', () => {
    const badge = buildProviderBadgeLabel('local', 'analyzing');
    expect(badge).toBe('TRIAGE · LOCAL · ANALYZING');
    expect(badge).not.toMatch(/^AI /);
  });

  it('local provider error shows TRIAGE · LOCAL · FAILED', () => {
    const badge = buildProviderBadgeLabel('local', 'error');
    expect(badge).toBe('TRIAGE · LOCAL · FAILED');
  });

  it('local provider stale shows TRIAGE · STALE', () => {
    const badge = buildProviderBadgeLabel('local', 'stale');
    expect(badge).toBe('TRIAGE · STALE');
  });

  it('LocalRuleBasedProvider still maps to local_deterministic', () => {
    const badge = buildProviderBadgeLabel('LocalRuleBasedProvider', 'ready');
    expect(badge).toBe('TRIAGE · LOCAL');
    expect(badge).not.toMatch(/^AI /);
  });
});

describe('Phase 8B.3 — provider classification kind is unchanged', () => {
  it('granite → external_ai', () => {
    expect(classifyProvider('granite').kind).toBe('external_ai');
  });

  it('Granite → external_ai', () => {
    expect(classifyProvider('Granite').kind).toBe('external_ai');
  });

  it('gemini → external_ai', () => {
    expect(classifyProvider('gemini').kind).toBe('external_ai');
  });

  it('ollama → external_ai', () => {
    expect(classifyProvider('ollama').kind).toBe('external_ai');
  });

  it('local → local_deterministic', () => {
    expect(classifyProvider('local').kind).toBe('local_deterministic');
  });

  it('LocalRuleBasedProvider → local_deterministic', () => {
    expect(classifyProvider('LocalRuleBasedProvider').kind).toBe('local_deterministic');
  });

  it('null → unknown', () => {
    expect(classifyProvider(null).kind).toBe('unknown');
  });

  // Diagnostic display names are preserved for internal/log use
  it('granite displayName retains GRANITE for diagnostics', () => {
    expect(classifyProvider('granite').displayName).toBe('GRANITE');
  });

  it('gemini displayName retains GEMINI for diagnostics', () => {
    expect(classifyProvider('gemini').displayName).toBe('GEMINI');
  });

  it('ollama displayName retains OLLAMA for diagnostics', () => {
    expect(classifyProvider('ollama').displayName).toBe('OLLAMA');
  });
});

describe('Phase 8B.3 — unknown provider still uses ADVISORY (fail-safe)', () => {
  it('null → ADVISORY · READY', () => {
    expect(buildProviderBadgeLabel(null, 'ready')).toBe('ADVISORY · READY');
  });

  it('empty string → ADVISORY · READY', () => {
    expect(buildProviderBadgeLabel('', 'ready')).toBe('ADVISORY · READY');
  });

  it('unknown-provider → ADVISORY · READY', () => {
    expect(buildProviderBadgeLabel('unknown-provider', 'ready')).toBe('ADVISORY · READY');
  });
});
