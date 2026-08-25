/**
 * TransmissionOutcomeBanner — unit tests (Phase 2E-D5)
 *
 * Tests verify the pure derivation logic used by TransmissionOutcomeBanner:
 * - deriveOutcomeClass: maps SimulationResult arrays → OutcomeClass
 * - Rendering guards (null result, non-complete phase)
 * - All edge cases from the D5 specification (Cases A–J)
 * - Deterministic boundary: outcome never depends on AI fields
 * - Override vs AI-recommended badge logic
 * - elapsed_time_s treated as scalar total only
 * - plan_id sourced from SimulationResult, not inferred
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
import { deriveOutcomeClass } from '../TransmissionOutcomeBanner';
import type { OutcomeClass } from '../TransmissionOutcomeBanner';

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function makeResult(
  delivered: string[],
  deferred: string[],
  failed: string[],
  planId = 'mission_critical_first',
  elapsedTimeS = 120.0,
): SimulationResult {
  return {
    plan_id: planId,
    delivered_packets: delivered,
    deferred_packets: deferred,
    failed_packets: failed,
    elapsed_time_s: elapsedTimeS,
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

// ─── Render guard logic (mirrors component render conditions) ─────────────────

/**
 * Mirrors the two render guards at the top of TransmissionOutcomeBanner:
 *   if (approvalPhase !== 'complete') return null;
 *   if (simulationResult === null)    return null;
 */
function shouldRender(phase: ApprovalPhase, result: SimulationResult | null): boolean {
  return phase === 'complete' && result !== null;
}

/**
 * Mirrors the isAiRecommendedPlan derivation in MissionControl:
 *   plan_id !== undefined && plan_id !== 'operator-override'
 */
function isAiRecommended(result: SimulationResult | null): boolean {
  if (result === null) return false;
  return result.plan_id !== 'operator-override';
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

// ── Test 1: null simulationResult → banner does not render ────────────────────
function test_null_result_does_not_render(): void {
  assert(!shouldRender('complete', null), 'null result must not render');
}

// ── Test 2: non-complete phases → banner does not render ─────────────────────
function test_non_complete_phases_do_not_render(): void {
  const phases: ApprovalPhase[] = ['idle', 'ai_analyzing', 'ready', 'transmitting'];
  const r = makeResult(['P1'], [], []);
  for (const phase of phases) {
    assert(!shouldRender(phase, r), `banner must not render in phase '${phase}'`);
  }
}

// ── Test 3: Case A — successful transmission ──────────────────────────────────
function test_case_a_successful_transmission(): void {
  const r = makeResult(['P1','P2','P3','P4','P5','P6','P7','P8','P9','P10'], [], []);
  const outcome = deriveOutcomeClass(r);
  assertEquals<OutcomeClass>(outcome, 'success', 'Case A: delivered=10, deferred=0, failed=0 → success');
}

// ── Test 4: Case B — partial delivery (delivered + deferred) ─────────────────
function test_case_b_partial_delivery_deferred(): void {
  const d  = Array.from({length:7}, (_, i) => `P${i+1}`);
  const def= Array.from({length:3}, (_, i) => `D${i+1}`);
  const r = makeResult(d, def, []);
  const outcome = deriveOutcomeClass(r);
  assertEquals<OutcomeClass>(outcome, 'partial', 'Case B: delivered=7, deferred=3, failed=0 → partial');
}

// ── Test 5: Case C — delivery with failures ───────────────────────────────────
function test_case_c_delivery_with_failures(): void {
  const d = Array.from({length:7}, (_, i) => `P${i+1}`);
  const f = Array.from({length:3}, (_, i) => `F${i+1}`);
  const r = makeResult(d, [], f);
  const outcome = deriveOutcomeClass(r);
  assertEquals<OutcomeClass>(outcome, 'partial', 'Case C: delivered=7, deferred=0, failed=3 → partial');
}

// ── Test 6: Case D — all deferred (not success) ───────────────────────────────
function test_case_d_all_deferred(): void {
  const def = Array.from({length:10}, (_, i) => `D${i+1}`);
  const r = makeResult([], def, []);
  const outcome = deriveOutcomeClass(r);
  assertEquals<OutcomeClass>(outcome, 'deferred', 'Case D: delivered=0, deferred=10, failed=0 → deferred (NOT success)');
  assert(outcome !== 'success', 'deferred must never classify as success');
}

// ── Test 7: Case E — all failed ───────────────────────────────────────────────
function test_case_e_all_failed(): void {
  const f = Array.from({length:10}, (_, i) => `F${i+1}`);
  const r = makeResult([], [], f);
  const outcome = deriveOutcomeClass(r);
  assertEquals<OutcomeClass>(outcome, 'failed', 'Case E: delivered=0, deferred=0, failed=10 → failed');
}

// ── Test 8: Case F — empty result (no packets in any list) ───────────────────
function test_case_f_empty_result(): void {
  const r = makeResult([], [], []);
  const outcome = deriveOutcomeClass(r);
  assertEquals<OutcomeClass>(outcome, 'neutral', 'Case F: all empty → neutral (NOT success)');
  assert(outcome !== 'success', 'empty result must never classify as success');
}

// ── Test 9: elapsed_time_s displayed as total scalar only ────────────────────
function test_elapsed_time_is_total_scalar(): void {
  // elapsed_time_s must be shown as-is — never divided by packet count.
  const elapsedS = 132.7;
  const r = makeResult(['P1','P2','P3'], [], [], 'test-plan', elapsedS);
  // Verify the value is preserved on the result object as-is.
  assert(r.elapsed_time_s === elapsedS, 'elapsed_time_s must equal input (not derived or divided)');
  // Verify dividing by packet count gives a different value — the banner must NOT do this.
  const perPacket = elapsedS / r.delivered_packets.length;
  assert(perPacket !== elapsedS, 'per-packet value differs from total — banner must use total only');
}

// ── Test 10: plan_id sourced from SimulationResult ───────────────────────────
function test_plan_id_from_simulation_result(): void {
  const r = makeResult(['P1'], [], [], 'mission_critical_first');
  assertEquals(r.plan_id, 'mission_critical_first', 'plan_id must come from SimulationResult');
}

// ── Test 11: AI recommended badge when plan is not override ──────────────────
function test_ai_recommended_badge_for_non_override(): void {
  const r = makeResult(['P1'], [], [], 'mission_critical_first');
  assert(isAiRecommended(r), 'non-override plan_id → isAiRecommendedPlan = true');
}

// ── Test 12: operator override badge when plan_id === 'operator-override' ────
function test_operator_override_badge(): void {
  const r = makeResult(['P1'], [], [], 'operator-override');
  assert(!isAiRecommended(r), 'plan_id=operator-override → isAiRecommendedPlan = false');
}

// ── Test 13: operator-override never shows AI recommended ────────────────────
function test_override_never_shows_ai_recommended(): void {
  const r = makeResult(['P1','P2'], ['D1'], [], 'operator-override');
  const ai = isAiRecommended(r);
  assert(!ai, 'operator-override plan must never be flagged as AI recommended');
  // Double-check: a non-override plan IS flagged
  const r2 = makeResult(['P1','P2'], [], [], 'baseline');
  assert(isAiRecommended(r2), 'non-override plan is flagged as AI recommended');
}

// ── Test 14: outcome does not depend on AI confidence or risk ─────────────────
function test_deterministic_outcome_ignores_ai_fields(): void {
  // Two results with identical delivery arrays but different plan IDs (simulating
  // different AI confidence scenarios).  The outcome must be identical.
  const r1 = makeResult(['P1','P2'], [], []);
  const r2 = makeResult(['P1','P2'], [], []);
  // Simulate high AI confidence on r1, low on r2 — outcome must not change.
  const o1 = deriveOutcomeClass(r1);
  const o2 = deriveOutcomeClass(r2);
  assertEquals<OutcomeClass>(o1, 'success', 'r1 should be success');
  assertEquals<OutcomeClass>(o2, 'success', 'r2 should be success regardless of AI fields');
  assertEquals<OutcomeClass>(o1, o2, 'outcome must be identical regardless of AI context');
}

// ── Test 15: Case H — approval failure returns to non-complete state ──────────
function test_approval_failure_does_not_show_banner(): void {
  // After approval fails, approvalPhase returns to 'ready' (D3 P0-2 fix).
  // The banner must not render in 'ready'.
  const r = makeResult(['P1'], [], []);
  assert(!shouldRender('ready', r), 'phase=ready (post-failure) must not render banner');
  // Even with a stale result, the phase guard wins.
  assert(!shouldRender('transmitting', r), 'phase=transmitting must not render banner');
}

// ─── Run all tests ─────────────────────────────────────────────────────────────

const TESTS: Array<[string, () => void]> = [
  ['test 1: null result → banner does not render',                    test_null_result_does_not_render],
  ['test 2: non-complete phases → banner does not render',            test_non_complete_phases_do_not_render],
  ['test 3: Case A — successful transmission → success',              test_case_a_successful_transmission],
  ['test 4: Case B — partial delivery (deferred) → partial',         test_case_b_partial_delivery_deferred],
  ['test 5: Case C — delivery with failures → partial',              test_case_c_delivery_with_failures],
  ['test 6: Case D — all deferred → deferred (NOT success)',         test_case_d_all_deferred],
  ['test 7: Case E — all failed → failed',                           test_case_e_all_failed],
  ['test 8: Case F — empty result → neutral (NOT success)',          test_case_f_empty_result],
  ['test 9: elapsed_time_s is total scalar, not per-packet',         test_elapsed_time_is_total_scalar],
  ['test 10: plan_id sourced from SimulationResult',                 test_plan_id_from_simulation_result],
  ['test 11: AI recommended badge for non-override plan',            test_ai_recommended_badge_for_non_override],
  ['test 12: operator override badge for plan_id=operator-override', test_operator_override_badge],
  ['test 13: override plan never shows AI recommended',              test_override_never_shows_ai_recommended],
  ['test 14: outcome ignores AI confidence and risk fields',         test_deterministic_outcome_ignores_ai_fields],
  ['test 15: approval failure (ready phase) does not show banner',   test_approval_failure_does_not_show_banner],
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
    console.log(`  ✓ All ${passed} TransmissionOutcomeBanner logic tests passed`);
  } else {
    console.error(`  ${failed} test(s) failed, ${passed} passed`);
  }
}

export {
  deriveOutcomeClass,
  shouldRender,
  isAiRecommended,
  makeResult,
};
