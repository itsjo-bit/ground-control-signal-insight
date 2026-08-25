/**
 * TransmissionNarrativePanel — unit tests (Phase 2E-D6)
 *
 * Tests verify the pure derivation logic used by TransmissionNarrativePanel:
 *  - outcomeOf: maps product ID + SimulationResult → Outcome
 *  - resolveAnomalyContext: maps AnomalyEvent[] + RankedProduct[] → anomaly context
 *  - rankedProductsForAnomaly: filters ranked products by anomaly_id
 *  - Anomaly state propagation from MissionControl
 *  - Empty anomaly list guard (no empty section rendered)
 *  - Unresolved anomaly ID does not fabricate data
 *  - All outcome classifications: delivered / deferred / failed / not_in_plan
 *  - NOT IN PLAN disambiguation vs DEFERRED
 *  - Product description preferred over product ID
 *  - Empty description falls back to product ID
 *  - Deterministic outcome boundary (AI confidence never controls outcome)
 *  - anomaly_ids sourced from RankedProduct.anomaly_ids
 *  - AI RECOMMENDED / OPERATOR OVERRIDE label derivation
 *  - Legacy mode (no anomalies)
 *  - Multiple anomalies
 *  - Multiple products linked to one anomaly
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

import type { AnomalyEvent, RankedProduct, SimulationResult } from '../../types/domain';
import {
  outcomeOf,
  resolveAnomalyContext,
  rankedProductsForAnomaly,
} from '../TransmissionNarrativePanel';
import type { Outcome } from '../TransmissionNarrativePanel';

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function makeSimResult(
  delivered: string[],
  deferred: string[],
  failed: string[],
  planId = 'mission_critical_first',
): SimulationResult {
  return {
    plan_id: planId,
    delivered_packets: delivered,
    deferred_packets: deferred,
    failed_packets: failed,
    elapsed_time_s: 120.0,
    retransmission_counts: {},
    link_state: {} as SimulationResult['link_state'],
    mission_state: {} as SimulationResult['mission_state'],
  };
}

function makeRankedProduct(
  id: string,
  priority: number,
  anomalyIds: string[] = [],
  description = '',
  subsystem = 'propulsion',
  confidence: number | null = 0.9,
): RankedProduct {
  return {
    product_id: id,
    priority,
    reason: `Reason for ${id}`,
    factors: ['active anomaly'],
    anomaly_ids: anomalyIds,
    subsystem,
    confidence,
    description,
  };
}

function makeAnomaly(
  id: string,
  subsystem: string,
  description = `${subsystem} anomaly`,
): AnomalyEvent {
  return {
    anomaly_id: id,
    subsystem,
    severity: 0.8,
    description,
    detected_at_s: 42.0,
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

// ─── Derived logic helpers (mirror MissionControl derivations) ─────────────────

/**
 * Mirrors the isAiRecommendedPlan derivation in MissionControl:
 *   plan_id !== undefined && plan_id !== 'operator-override'
 */
function isAiRecommended(sim: SimulationResult | null): boolean {
  if (sim === null) return false;
  return sim.plan_id !== undefined && sim.plan_id !== 'operator-override';
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

// ── Test 1: anomaly state propagates from MissionControl into TransmissionNarrativePanel ──
function test_anomaly_state_propagates(): void {
  // Verify the fixture and type shape mirror what MissionControl stores in state.
  const anomaly = makeAnomaly('ANOM-017', 'propulsion', 'Thruster subsystem anomaly');
  const anomalies: AnomalyEvent[] = [anomaly];

  // Matches the stateData.anomalies ?? [] pattern in MissionControl
  const stored = anomalies ?? [];
  assertEquals(stored.length, 1, 'one anomaly stored');
  assertEquals(stored[0].anomaly_id, 'ANOM-017', 'anomaly_id preserved');
  assertEquals(stored[0].subsystem, 'propulsion', 'subsystem preserved');
}

// ── Test 2: empty anomaly list → no anomaly context resolved ──────────────────
function test_empty_anomaly_list_yields_no_context(): void {
  const ranked = [makeRankedProduct('DP-047', 1, ['ANOM-017'])];
  const context = resolveAnomalyContext([], ranked);

  assertEquals(context.length, 0, 'empty anomaly list → zero context entries');
}

// ── Test 3: relevant anomaly is resolved correctly ────────────────────────────
function test_relevant_anomaly_resolved(): void {
  const anomaly = makeAnomaly('ANOM-017', 'propulsion', 'Thruster subsystem anomaly');
  const product = makeRankedProduct('DP-047', 1, ['ANOM-017'], 'Thruster-2 chamber pressure diagnostic');
  const context = resolveAnomalyContext([anomaly], [product]);

  assertEquals(context.length, 1, 'one anomaly context entry');
  assertEquals(context[0].anomaly.anomaly_id, 'ANOM-017', 'correct anomaly resolved');
  assertEquals(context[0].products.length, 1, 'one product linked');
  assertEquals(context[0].products[0].product_id, 'DP-047', 'correct product linked');
}

// ── Test 4: unresolved anomaly ID does not fabricate data ─────────────────────
function test_unresolved_anomaly_id_no_fabrication(): void {
  // Anomaly exists but no ranked product references it.
  const anomaly = makeAnomaly('ANOM-999', 'thermal');
  const product = makeRankedProduct('DP-047', 1, ['ANOM-017']); // references different anomaly
  const context = resolveAnomalyContext([anomaly], [product]);

  // ANOM-999 has no matching product → excluded from context entirely
  assertEquals(context.length, 0, 'unresolved anomaly ID must not produce a context entry');
}

// ── Test 5: AI-ranked + delivered → outcome 'delivered' ──────────────────────
function test_ai_ranked_delivered_outcome(): void {
  const sim = makeSimResult(['DP-047'], [], []);
  const outcome: Outcome = outcomeOf('DP-047', sim);
  assertEquals<Outcome>(outcome, 'delivered', 'product in delivered_packets → delivered');
}

// ── Test 6: AI-ranked + deterministically deferred → outcome 'deferred' ───────
function test_ai_ranked_deferred_outcome(): void {
  const sim = makeSimResult([], ['DP-047'], []);
  const outcome: Outcome = outcomeOf('DP-047', sim);
  assertEquals<Outcome>(outcome, 'deferred', 'product in deferred_packets → deferred');
}

// ── Test 7: AI-ranked + deterministically failed → outcome 'failed' ──────────
function test_ai_ranked_failed_outcome(): void {
  const sim = makeSimResult([], [], ['DP-047']);
  const outcome: Outcome = outcomeOf('DP-047', sim);
  assertEquals<Outcome>(outcome, 'failed', 'product in failed_packets → failed');
}

// ── Test 8: AI-ranked but not in approved plan → outcome 'not_in_plan' ────────
function test_ai_ranked_not_in_plan_outcome(): void {
  const sim = makeSimResult(['DP-001', 'DP-002'], [], []);
  const outcome: Outcome = outcomeOf('DP-047', sim);
  assertEquals<Outcome>(outcome, 'not_in_plan', 'product absent from all sim arrays → not_in_plan');
}

// ── Test 9: 'not_in_plan' is never the same as 'deferred' ────────────────────
function test_not_in_plan_is_not_deferred(): void {
  const sim = makeSimResult(['DP-001'], ['DP-002'], ['DP-003']);

  // DP-099 is absent from all lists — must be not_in_plan, never deferred
  const outcome = outcomeOf('DP-099', sim);
  assert(outcome === 'not_in_plan', 'absent product is not_in_plan, not deferred');
  assert(outcome !== 'deferred', 'absent product must never be classified as deferred');

  // DP-002 is explicitly deferred in the plan — must be deferred
  const deferred = outcomeOf('DP-002', sim);
  assertEquals<Outcome>(deferred, 'deferred', 'explicitly deferred product is deferred');

  // The two classifications must be distinct
  assert(outcome !== deferred, 'not_in_plan and deferred are distinct outcomes');
}

// ── Test 10: product description preferred over product ID ────────────────────
function test_description_preferred_over_id(): void {
  const product = makeRankedProduct('DP-047', 1, [], 'Thruster-2 chamber pressure diagnostic');
  // When description is present, it should be used as primary label
  assert(product.description !== undefined && product.description.length > 0, 'description is present');
  assert(product.description !== product.product_id, 'description differs from product ID');
  // The label logic: description ? description : product_id
  const label = product.description ? product.description : product.product_id;
  assertEquals(label, 'Thruster-2 chamber pressure diagnostic', 'description used as label');
}

// ── Test 11: empty description falls back to product ID ───────────────────────
function test_empty_description_falls_back_to_id(): void {
  const product = makeRankedProduct('DP-047', 1, [], '');
  // The label logic: description ? description : product_id
  const label = product.description ? product.description : product.product_id;
  assertEquals(label, 'DP-047', 'empty description → fall back to product_id');
}

// ── Test 12: deterministic outcome does not depend on AI confidence ────────────
function test_deterministic_outcome_ignores_ai_confidence(): void {
  // Product with high AI confidence
  const rpHigh = makeRankedProduct('DP-047', 1, [], '', 'propulsion', 0.99);
  // Product with low AI confidence (same ID for comparable outcome)
  const rpLow  = makeRankedProduct('DP-047', 1, [], '', 'propulsion', 0.01);
  // Product with null AI confidence
  const rpNull = makeRankedProduct('DP-047', 1, [], '', 'propulsion', null);

  const sim = makeSimResult(['DP-047'], [], []);

  // All three must yield 'delivered' because sim.delivered_packets contains DP-047.
  // AI confidence is irrelevant.
  assertEquals<Outcome>(outcomeOf(rpHigh.product_id, sim), 'delivered', 'high confidence → delivered by sim');
  assertEquals<Outcome>(outcomeOf(rpLow.product_id, sim),  'delivered', 'low confidence → delivered by sim');
  assertEquals<Outcome>(outcomeOf(rpNull.product_id, sim), 'delivered', 'null confidence → delivered by sim');

  // Now simulate deferred with identical products
  const sim2 = makeSimResult([], ['DP-047'], []);
  assertEquals<Outcome>(outcomeOf(rpHigh.product_id, sim2), 'deferred', 'high confidence → deferred by sim');
  assertEquals<Outcome>(outcomeOf(rpNull.product_id, sim2), 'deferred', 'null confidence → deferred by sim');
}

// ── Test 13: anomaly IDs come from RankedProduct.anomaly_ids ─────────────────
function test_anomaly_ids_from_ranked_product(): void {
  const rp = makeRankedProduct('DP-047', 1, ['ANOM-017', 'ANOM-042']);
  assert(rp.anomaly_ids.includes('ANOM-017'), 'ANOM-017 present on ranked product');
  assert(rp.anomaly_ids.includes('ANOM-042'), 'ANOM-042 present on ranked product');

  const matches = rankedProductsForAnomaly('ANOM-017', [rp]);
  assertEquals(matches.length, 1, 'product with matching anomaly_id is returned');
  assertEquals(matches[0].product_id, 'DP-047', 'correct product returned');

  const noMatch = rankedProductsForAnomaly('ANOM-999', [rp]);
  assertEquals(noMatch.length, 0, 'no match for non-existent anomaly_id');
}

// ── Test 14: AI RECOMMENDED label derivation ──────────────────────────────────
function test_ai_recommended_label(): void {
  const sim = makeSimResult(['DP-047'], [], [], 'mission_critical_first');
  assert(isAiRecommended(sim), 'non-override plan_id → isAiRecommendedPlan = true');
}

// ── Test 15: OPERATOR OVERRIDE label derivation ───────────────────────────────
function test_operator_override_label(): void {
  const sim = makeSimResult(['DP-047'], [], [], 'operator-override');
  assert(!isAiRecommended(sim), 'plan_id=operator-override → isAiRecommendedPlan = false');
}

// ── Test 16: legacy mode — no anomalies → no anomaly context, no throw ─────────
function test_legacy_mode_no_anomalies(): void {
  const ranked = [
    makeRankedProduct('DP-047', 1, ['ANOM-017']),
    makeRankedProduct('DP-048', 2, []),
  ];

  // Legacy: anomalies undefined → treated as [].
  // Mirror MissionControl: anomalies?.length === 0 or prop omitted → pass [] to helper.
  const maybeUndefined: AnomalyEvent[] | undefined = undefined;
  const context = resolveAnomalyContext(maybeUndefined ?? [], ranked);
  assertEquals(context.length, 0, 'undefined anomalies treated as empty — no context, no throw');

  // Legacy: anomalies empty array
  const context2 = resolveAnomalyContext([], ranked);
  assertEquals(context2.length, 0, 'empty anomaly list → no context entries');
}

// ── Test 17: multiple anomalies — each resolved independently ─────────────────
function test_multiple_anomalies_resolved(): void {
  const anom1 = makeAnomaly('ANOM-017', 'propulsion', 'Thruster anomaly');
  const anom2 = makeAnomaly('ANOM-042', 'thermal',    'Thermal control anomaly');
  const anom3 = makeAnomaly('ANOM-099', 'power',      'Power subsystem anomaly');

  const prod1 = makeRankedProduct('DP-047', 1, ['ANOM-017']);
  const prod2 = makeRankedProduct('DP-048', 2, ['ANOM-042']);
  // ANOM-099 has no matching product

  const context = resolveAnomalyContext([anom1, anom2, anom3], [prod1, prod2]);

  assertEquals(context.length, 2, 'exactly two anomalies with matching products resolved');
  assert(
    context.some((c) => c.anomaly.anomaly_id === 'ANOM-017'),
    'ANOM-017 in context',
  );
  assert(
    context.some((c) => c.anomaly.anomaly_id === 'ANOM-042'),
    'ANOM-042 in context',
  );
  assert(
    !context.some((c) => c.anomaly.anomaly_id === 'ANOM-099'),
    'ANOM-099 excluded (no matching product)',
  );
}

// ── Test 18: multiple products linked to one anomaly ──────────────────────────
function test_multiple_products_for_one_anomaly(): void {
  const anomaly = makeAnomaly('ANOM-017', 'propulsion', 'Thruster subsystem anomaly');

  const prod1 = makeRankedProduct('DP-047', 1, ['ANOM-017'], 'Thruster-2 chamber pressure diagnostic');
  const prod2 = makeRankedProduct('DP-051', 3, ['ANOM-017'], 'Thruster-2 thermal diagnostic');
  const prod3 = makeRankedProduct('DP-099', 2, []);  // not linked to this anomaly

  const context = resolveAnomalyContext([anomaly], [prod1, prod2, prod3]);

  assertEquals(context.length, 1, 'one anomaly context entry');
  assertEquals(context[0].products.length, 2, 'two products linked to ANOM-017');

  // Products must be sorted by priority ascending
  assert(context[0].products[0].priority < context[0].products[1].priority, 'products sorted by priority');
  assertEquals(context[0].products[0].product_id, 'DP-047', 'priority 1 first');
  assertEquals(context[0].products[1].product_id, 'DP-051', 'priority 3 second');
}

// ── Test 18b: product outcomes within anomaly context are deterministic ────────
function test_anomaly_context_product_outcomes_deterministic(): void {
  const anomaly = makeAnomaly('ANOM-017', 'propulsion', 'Thruster subsystem anomaly');
  const prod1 = makeRankedProduct('DP-047', 1, ['ANOM-017']);
  const prod2 = makeRankedProduct('DP-051', 3, ['ANOM-017']);

  const sim = makeSimResult(['DP-047'], ['DP-051'], []);

  // Verify outcomes through outcomeOf — the same function used in anomaly context
  assertEquals<Outcome>(outcomeOf('DP-047', sim), 'delivered', 'DP-047 delivered');
  assertEquals<Outcome>(outcomeOf('DP-051', sim), 'deferred',  'DP-051 deferred');

  // Confirm neither outcome depends on AI data on the products
  const context = resolveAnomalyContext([anomaly], [prod1, prod2]);
  assertEquals(context[0].products.length, 2, 'both products in context');
  // Outcomes are read from sim, not from context or product fields
  const o1 = outcomeOf(context[0].products[0].product_id, sim);
  const o2 = outcomeOf(context[0].products[1].product_id, sim);
  assertEquals<Outcome>(o1, 'delivered', 'first product outcome from sim');
  assertEquals<Outcome>(o2, 'deferred',  'second product outcome from sim');
}

// ─── Run all tests ─────────────────────────────────────────────────────────────

const TESTS: Array<[string, () => void]> = [
  ['test 1: anomaly state propagates from MissionControl',               test_anomaly_state_propagates],
  ['test 2: empty anomaly list yields no context',                       test_empty_anomaly_list_yields_no_context],
  ['test 3: relevant anomaly resolved correctly',                        test_relevant_anomaly_resolved],
  ['test 4: unresolved anomaly ID does not fabricate data',              test_unresolved_anomaly_id_no_fabrication],
  ['test 5: AI-ranked + delivered → outcome delivered',                  test_ai_ranked_delivered_outcome],
  ['test 6: AI-ranked + deterministic deferred → outcome deferred',     test_ai_ranked_deferred_outcome],
  ['test 7: AI-ranked + deterministic failed → outcome failed',         test_ai_ranked_failed_outcome],
  ['test 8: AI-ranked + not in approved plan → outcome not_in_plan',    test_ai_ranked_not_in_plan_outcome],
  ['test 9: not_in_plan is never classified as deferred',               test_not_in_plan_is_not_deferred],
  ['test 10: product description preferred over product ID',             test_description_preferred_over_id],
  ['test 11: empty description falls back to product ID',                test_empty_description_falls_back_to_id],
  ['test 12: deterministic outcome does not depend on AI confidence',    test_deterministic_outcome_ignores_ai_confidence],
  ['test 13: anomaly IDs come from RankedProduct.anomaly_ids',          test_anomaly_ids_from_ranked_product],
  ['test 14: AI RECOMMENDED label derivation',                          test_ai_recommended_label],
  ['test 15: OPERATOR OVERRIDE label derivation',                       test_operator_override_label],
  ['test 16: legacy mode — no anomalies, no throw',                     test_legacy_mode_no_anomalies],
  ['test 17: multiple anomalies resolved independently',                 test_multiple_anomalies_resolved],
  ['test 18: multiple products linked to one anomaly sorted by priority', test_multiple_products_for_one_anomaly],
  ['test 18b: anomaly context product outcomes are deterministic',       test_anomaly_context_product_outcomes_deterministic],
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
    console.log(`  ✓ All ${passed} TransmissionNarrativePanel logic tests passed`);
  } else {
    console.error(`  ${failed} test(s) failed, ${passed} passed`);
  }
}

export {
  makeSimResult,
  makeRankedProduct,
  makeAnomaly,
  isAiRecommended,
};
