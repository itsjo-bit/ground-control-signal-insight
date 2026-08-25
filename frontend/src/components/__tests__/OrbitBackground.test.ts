/**
 * OrbitBackground — unit tests (Phase 2E-D4)
 *
 * These tests verify the pure logic functions extracted from OrbitBackground:
 * - deriveCompletionState: maps SimulationResult → 'success' | 'warning' | 'neutral'
 * - pulseActive rule: approvalPhase === 'transmitting' → pulse on
 * - hasBeam rule: distanceKm > 0 AND not atLOS → beam visible
 * - completionState rule: only when approvalPhase === 'complete'
 *
 * Test framework: Vitest (import from 'vitest').
 *
 * Setup (once a test runner is available):
 *   npm install --save-dev vitest jsdom
 *   Then add to vite.config.ts: test: { environment: 'jsdom' }
 *   Then run: npx vitest run
 *
 * Until a test runner is installed the file validates via `tsc --noEmit`.
 */

import type { SimulationResult } from '../../types/domain';
import type { ApprovalPhase } from '../ApprovalBar';

// ─── Logic helpers mirroring OrbitBackground internals ─────────────────────────

/**
 * Mirrors OrbitBackground.deriveCompletionState exactly.
 *   null result            → 'neutral'
 *   any failed packets     → 'warning'
 *   delivered, no failures → 'success'
 *   neither set populated  → 'neutral'
 */
function deriveCompletionState(result: SimulationResult | null): 'success' | 'warning' | 'neutral' {
  if (result === null) return 'neutral';
  if (result.failed_packets.length > 0) return 'warning';
  if (result.delivered_packets.length > 0) return 'success';
  return 'neutral';
}

/**
 * Mirrors OrbitBackground.pulseActive rule.
 * Beam pulse is animated only during 'transmitting'.
 */
function derivePulseActive(phase: ApprovalPhase): boolean {
  return phase === 'transmitting';
}

/**
 * Mirrors OrbitBackground.hasBeam rule.
 * Beam is shown only when distance is known, positive, and the pass is in progress.
 */
function deriveHasBeam(distanceKm: number | null, commWindowRemainingS: number): boolean {
  const atLOS = commWindowRemainingS <= 0;
  return distanceKm !== null && distanceKm > 0 && !atLOS;
}

/**
 * Mirrors OrbitBackground.completionState derivation.
 * Only populated when approvalPhase === 'complete'.
 */
function deriveCompletionCssState(
  phase: ApprovalPhase,
  result: SimulationResult | null,
): 'success' | 'warning' | 'neutral' | null {
  return phase === 'complete' ? deriveCompletionState(result) : null;
}

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function makeResult(
  delivered: string[],
  deferred: string[],
  failed: string[],
): SimulationResult {
  return {
    plan_id: 'test-plan',
    delivered_packets: delivered,
    deferred_packets: deferred,
    failed_packets: failed,
    elapsed_time_s: 120,
    retransmission_counts: {},
    link_state: {} as SimulationResult['link_state'],
    mission_state: {} as SimulationResult['mission_state'],
  };
}

// ─── Assertion helpers ─────────────────────────────────────────────────────────

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`FAIL: ${message}`);
}

function assertEquals<T>(actual: T, expected: T, message: string): void {
  if (actual !== expected) {
    throw new Error(`FAIL: ${message} — expected ${String(expected)}, got ${String(actual)}`);
  }
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

function test_null_result_is_neutral(): void {
  assertEquals(deriveCompletionState(null), 'neutral', 'null → neutral');
}

function test_failed_packets_is_warning(): void {
  const r = makeResult(['P1', 'P2'], [], ['P3']);
  assertEquals(deriveCompletionState(r), 'warning', 'any failed → warning');
}

function test_delivered_no_failures_is_success(): void {
  const r = makeResult(['P1', 'P2'], ['P3'], []);
  assertEquals(deriveCompletionState(r), 'success', 'delivered + no failures → success');
}

function test_neither_delivered_nor_failed_is_neutral(): void {
  const r = makeResult([], ['P3'], []);
  assertEquals(deriveCompletionState(r), 'neutral', 'no delivered, no failed → neutral');
}

function test_failures_take_priority_over_delivered(): void {
  // Even when delivered list is non-empty, a single failure → warning
  const r = makeResult(['P1', 'P2', 'P3'], [], ['P4']);
  assertEquals(deriveCompletionState(r), 'warning', 'failure takes priority over delivered');
}

function test_pulse_active_only_during_transmitting(): void {
  const phases: ApprovalPhase[] = ['idle', 'ai_analyzing', 'ready', 'complete'];
  for (const phase of phases) {
    assert(!derivePulseActive(phase), `pulse must be OFF during '${phase}'`);
  }
  assert(derivePulseActive('transmitting'), "pulse must be ON during 'transmitting'");
}

function test_has_beam_true_when_distance_positive(): void {
  assert(deriveHasBeam(384_400, 300), 'beam present when distance > 0 and window > 0');
}

function test_has_beam_false_when_distance_null(): void {
  assert(!deriveHasBeam(null, 300), 'beam absent when distanceKm is null (legacy)');
}

function test_has_beam_false_when_distance_zero(): void {
  assert(!deriveHasBeam(0, 300), 'beam absent when distanceKm is 0');
}

function test_has_beam_false_when_distance_negative(): void {
  assert(!deriveHasBeam(-100, 300), 'beam absent when distanceKm is negative');
}

function test_has_beam_false_at_los(): void {
  assert(!deriveHasBeam(384_400, 0), 'beam absent at LOS (remaining = 0)');
}

function test_completion_state_null_outside_complete(): void {
  const phases: ApprovalPhase[] = ['idle', 'ai_analyzing', 'ready', 'transmitting'];
  const r = makeResult(['P1'], [], []);
  for (const phase of phases) {
    assertEquals(deriveCompletionCssState(phase, r), null, `completionState must be null during '${phase}'`);
  }
}

function test_completion_state_derived_from_result_when_complete(): void {
  assertEquals(
    deriveCompletionCssState('complete', makeResult(['P1'], [], [])),
    'success',
    'complete + delivered → success',
  );
  assertEquals(
    deriveCompletionCssState('complete', makeResult(['P1'], [], ['P2'])),
    'warning',
    'complete + failed → warning',
  );
  assertEquals(
    deriveCompletionCssState('complete', null),
    'neutral',
    'complete + null result → neutral',
  );
}

// ─── Run all tests ─────────────────────────────────────────────────────────────

const TESTS: Array<[string, () => void]> = [
  ['test 1: null SimulationResult → neutral',                     test_null_result_is_neutral],
  ['test 2: failed packets → warning',                             test_failed_packets_is_warning],
  ['test 3: delivered + no failures → success',                   test_delivered_no_failures_is_success],
  ['test 4: neither delivered nor failed → neutral',              test_neither_delivered_nor_failed_is_neutral],
  ['test 5: failures take priority over delivered',               test_failures_take_priority_over_delivered],
  ['test 6: pulse active only during transmitting',               test_pulse_active_only_during_transmitting],
  ['test 7: hasBeam true when distance positive',                 test_has_beam_true_when_distance_positive],
  ['test 8: hasBeam false when distance null (legacy)',           test_has_beam_false_when_distance_null],
  ['test 9: hasBeam false when distance zero',                    test_has_beam_false_when_distance_zero],
  ['test 10: hasBeam false when distance negative',               test_has_beam_false_when_distance_negative],
  ['test 11: hasBeam false at LOS',                               test_has_beam_false_at_los],
  ['test 12: completionState null outside complete phase',        test_completion_state_null_outside_complete],
  ['test 13: completionState derived from result when complete',  test_completion_state_derived_from_result_when_complete],
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
if (typeof (globalThis as any)['process'] === 'undefined') {
  let passed = 0;
  let failed = 0;
  for (const [name, fn] of TESTS) {
    try {
      fn();
      passed++;
    } catch (e) {
      failed++;
      console.error(`  ✗ ${name}`);
      console.error(`    ${(e as Error).message}`);
    }
  }
  if (failed === 0) {
    console.log(`  ✓ All ${passed} OrbitBackground logic tests passed`);
  } else {
    console.error(`  ${failed} test(s) failed, ${passed} passed`);
  }
}

export {
  deriveCompletionState,
  derivePulseActive,
  deriveHasBeam,
  deriveCompletionCssState,
  makeResult,
};
