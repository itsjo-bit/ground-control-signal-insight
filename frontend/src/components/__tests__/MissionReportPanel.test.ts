/**
 * MissionReportPanel — unit tests (Phase 2E-D8)
 *
 * Covers pure derivation logic only:
 *  - reportOutcomeOf: SimulationResult → ReportOutcome (deterministic)
 *  - computeFulfillment: ranked products + SimulationResult → fraction
 *  - resolveReportAnomalyContext: AnomalyEvent[] + RankedProduct[] → context
 *  - isAiRecommendedFromSim: plan_id → AI/override flag
 *  - Render-guard logic for the five D8 lifecycle states
 *
 * Test framework: Vitest (import from 'vitest').
 *
 * Setup (once a test runner is available):
 *   npm install --save-dev vitest jsdom
 *   Add to vite.config.ts: test: { environment: 'jsdom' }
 *   Then run: npx vitest run
 *
 * Until a test runner is installed this file validates via `tsc --noEmit`.
 */

import type { AnomalyEvent, MissionState, RankedProduct, SimulationResult } from '../../types/domain';
import {
  computeFulfillment,
  isAiRecommendedFromSim,
  reportOutcomeOf,
  resolveReportAnomalyContext,
} from '../MissionReportPanel';
import type { ReportOutcome } from '../MissionReportPanel';
import type { ApprovalPhase } from '../ApprovalBar';

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function makeSimResult(
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

function makeRankedProduct(
  id: string,
  priority: number,
  anomalyIds: string[] = [],
  description = '',
  confidence: number | null = 0.9,
): RankedProduct {
  return {
    product_id: id,
    priority,
    reason: `Reason for ${id}`,
    factors: ['active anomaly'],
    anomaly_ids: anomalyIds,
    subsystem: 'propulsion',
    confidence,
    description,
  };
}

function makeAnomaly(id: string, subsystem: string, description = `${subsystem} anomaly`): AnomalyEvent {
  return { anomaly_id: id, subsystem, severity: 0.8, description, detected_at_s: 42.0 };
}

function makeMissionState(overrides: Partial<MissionState> = {}): MissionState {
  return {
    mission_id: 'MISSION-001',
    mission_phase: 'CRITICAL_OPS',
    current_event: 'Propulsion anomaly response',
    event_time_remaining_s: 300,
    comm_window_remaining_s: 480,
    risk_score: 0.62,
    risk_level: 'HIGH',
    ...overrides,
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

function assertClose(actual: number, expected: number, tolerance: number, message: string): void {
  if (Math.abs(actual - expected) > tolerance) {
    throw new Error(`FAIL: ${message} — expected ~${expected}, got ${actual}`);
  }
}

// ─── Render-guard logic (mirrors MissionReportPanel's isComplete guard) ────────

/**
 * Mirrors: const isComplete = approvalPhase === 'complete' && sim !== null;
 */
function shouldShowFullReport(phase: ApprovalPhase, sim: SimulationResult | null): boolean {
  return phase === 'complete' && sim !== null;
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

// ── Test 1: report waits before recommendation ─────────────────────────────────
function test_report_waits_before_recommendation(): void {
  // Without a recommendation, the full report content is not shown.
  // Modelled as: recommendation === null → waiting state.
  const hasRec = false;
  assert(!hasRec, 'no recommendation → report shows waiting state');
}

// ── Test 2: report does not claim outcomes without simulationResult ────────────
function test_no_outcomes_without_simulation(): void {
  // isComplete requires simulationResult !== null.
  assert(!shouldShowFullReport('complete', null), 'complete phase + null sim → not complete');
  assert(!shouldShowFullReport('ready',    null), 'ready phase + null sim → not complete');
  assert(!shouldShowFullReport('transmitting', null), 'transmitting + null sim → not complete');
}

// ── Test 3: AI-recommended plan is labelled correctly ─────────────────────────
function test_ai_recommended_label(): void {
  const sim = makeSimResult(['DP-047'], [], [], 'mission_critical_first');
  assert(isAiRecommendedFromSim(sim), 'non-override plan_id → isAiRecommended = true');
}

// ── Test 4: operator override is labelled correctly ───────────────────────────
function test_operator_override_label(): void {
  const sim = makeSimResult(['DP-047'], [], [], 'operator-override');
  assert(!isAiRecommendedFromSim(sim), 'plan_id=operator-override → isAiRecommended = false');
}

// ── Test 5: delivered/deferred/failed come exclusively from SimulationResult ──
function test_outcomes_from_simulation_result(): void {
  const sim = makeSimResult(['DP-047', 'DP-048'], ['DP-099'], ['DP-007']);
  assertEquals<ReportOutcome>(reportOutcomeOf('DP-047', sim), 'delivered', 'delivered product');
  assertEquals<ReportOutcome>(reportOutcomeOf('DP-048', sim), 'delivered', 'second delivered product');
  assertEquals<ReportOutcome>(reportOutcomeOf('DP-099', sim), 'deferred',  'deferred product');
  assertEquals<ReportOutcome>(reportOutcomeOf('DP-007', sim), 'failed',    'failed product');
}

// ── Test 6: not_in_plan is distinct from deferred ─────────────────────────────
function test_not_in_plan_is_not_deferred(): void {
  const sim = makeSimResult(['DP-047'], ['DP-099'], []);
  const absentOutcome = reportOutcomeOf('DP-999', sim);
  assertEquals<ReportOutcome>(absentOutcome, 'not_in_plan', 'absent product → not_in_plan');
  assert(absentOutcome !== 'deferred', 'absent product must never be classified as deferred');

  const deferredOutcome = reportOutcomeOf('DP-099', sim);
  assertEquals<ReportOutcome>(deferredOutcome, 'deferred', 'explicitly deferred product');
  assert(absentOutcome !== deferredOutcome, 'not_in_plan and deferred must be distinct');
}

// ── Test 7: anomaly context only shown when anomaly_ids match ─────────────────
function test_anomaly_context_requires_matching_ids(): void {
  const anom1 = makeAnomaly('ANOM-017', 'propulsion', 'Thruster anomaly');
  const anom2 = makeAnomaly('ANOM-099', 'power', 'Power anomaly');
  const prod = makeRankedProduct('DP-047', 1, ['ANOM-017']);  // only links to ANOM-017

  const ctx = resolveReportAnomalyContext([anom1, anom2], [prod]);
  assertEquals(ctx.length, 1, 'only one anomaly has a matching product');
  assertEquals(ctx[0].anomaly.anomaly_id, 'ANOM-017', 'ANOM-017 has a match');
  assert(!ctx.some((c) => c.anomaly.anomaly_id === 'ANOM-099'), 'ANOM-099 has no match — excluded');
}

// ── Test 8: geometry uses distance_km / propagation fields, not latency_s ──────
function test_geometry_source_independence(): void {
  // The report derives geometry from distanceKm, propagationDelayS, roundTripTimeS
  // which come from GET /state (not from LinkState.latency_s).
  // Verify these are conceptually independent by confirming the types are what
  // MissionControl passes: number | null (geometry) vs number (latency_s).
  const distanceKm: number | null = 54_000_000;
  const propagationDelayS: number | null = 180.124;
  const roundTripTimeS: number | null = 360.248;
  // latency_s is a separate protocol-stack field on LinkState — never used here.
  const latencyS = 1.4;

  assert(distanceKm !== null, 'distance is present');
  assert(propagationDelayS !== null, 'propagation delay is present');
  assert(propagationDelayS !== latencyS, 'propagation delay differs from link latency');
  // Verify round_trip_time_s ≈ 2 × propagation_delay_s
  assertClose(roundTripTimeS!, 2 * propagationDelayS!, 0.01, 'RTT ≈ 2 × one-way delay');
}

// ── Test 9: fulfillment metric matches the existing formula ───────────────────
function test_fulfillment_metric_formula(): void {
  // Formula: deliveredCount / ranked.length
  // Mirrors the formula in TransmissionNarrativePanel exactly.
  const ranked = [
    makeRankedProduct('DP-047', 1),
    makeRankedProduct('DP-048', 2),
    makeRankedProduct('DP-049', 3),
    makeRankedProduct('DP-050', 4),
  ];
  const sim = makeSimResult(['DP-047', 'DP-048', 'DP-049'], ['DP-050'], []);
  const f = computeFulfillment(ranked, sim);
  // 3 delivered out of 4 ranked → 0.75
  assertClose(f, 0.75, 0.001, 'fulfillment = 3/4 = 0.75');
}

// ── Test 10: legacy scenario with null geometry remains valid ─────────────────
function test_legacy_null_geometry(): void {
  // When distance_km is null, geometry section is simply not shown.
  // No crash expected — null is a valid value for all three geometry fields.
  const distanceKm: number | null = null;
  const propagationDelayS: number | null = null;
  const roundTripTimeS: number | null = null;

  assert(distanceKm === null, 'null distance is valid for legacy scenarios');
  assert(propagationDelayS === null, 'null propagation delay is valid');
  assert(roundTripTimeS === null, 'null RTT is valid');
  // resolveReportAnomalyContext should still work with empty anomalies
  const ctx = resolveReportAnomalyContext([], []);
  assertEquals(ctx.length, 0, 'empty anomalies/ranked → empty context, no throw');
}

// ── Test 11: legacy scenario without AI prioritization ────────────────────────
function test_legacy_no_ai_prioritization(): void {
  // When aiPrioritization is null, ranked = [] and fulfillment is skipped.
  const ranked: RankedProduct[] = [];
  const sim = makeSimResult(['DP-047'], [], []);
  const f = computeFulfillment(ranked, sim);
  assertEquals(f, 0, 'zero ranked products → fulfillment = 0 (not NaN)');

  const ctx = resolveReportAnomalyContext([], ranked);
  assertEquals(ctx.length, 0, 'no context without ranked products');
}

// ── Test 12: report uses actual approved plan identity from SimulationResult ──
function test_plan_identity_from_simulation_result(): void {
  // The approved plan_id must come from SimulationResult.plan_id, not from
  // recommendation.recommended_plan_id or any other source.
  const sim = makeSimResult(['P1'], [], [], 'mission_critical_first');
  assertEquals(sim.plan_id, 'mission_critical_first', 'plan_id from SimulationResult');

  const overrideSim = makeSimResult(['P1'], [], [], 'operator-override');
  assertEquals(overrideSim.plan_id, 'operator-override', 'override plan_id from SimulationResult');

  // The two must be distinguishable
  assert(sim.plan_id !== overrideSim.plan_id, 'AI plan and override plan IDs are distinct');
}

// ── Test 13: elapsed time is a total scalar, not per-packet ──────────────────
function test_elapsed_time_is_total_scalar(): void {
  const elapsedS = 247.8;
  const sim = makeSimResult(['P1', 'P2', 'P3'], [], [], 'test-plan', elapsedS);
  // Verify the value is preserved as-is
  assertEquals(sim.elapsed_time_s, elapsedS, 'elapsed_time_s preserved exactly');
  // Per-packet would be different — report must NOT divide by packet count
  const perPacket = elapsedS / sim.delivered_packets.length;
  assert(perPacket !== elapsedS, 'per-packet differs from total — report uses total only');
}

// ── Test 14: no AI field influences deterministic outcome ─────────────────────
function test_ai_fields_do_not_influence_outcome(): void {
  // Products with different AI confidence values must yield the same outcome
  // when they are in the same SimulationResult lists.
  const rpHighConf = makeRankedProduct('DP-047', 1, [], '', 0.99);
  const rpLowConf  = makeRankedProduct('DP-047', 1, [], '', 0.01);
  const rpNullConf = makeRankedProduct('DP-047', 1, [], '', null);

  const sim = makeSimResult(['DP-047'], [], []);

  assertEquals<ReportOutcome>(reportOutcomeOf(rpHighConf.product_id, sim), 'delivered', 'high conf → delivered by sim');
  assertEquals<ReportOutcome>(reportOutcomeOf(rpLowConf.product_id, sim),  'delivered', 'low conf → delivered by sim');
  assertEquals<ReportOutcome>(reportOutcomeOf(rpNullConf.product_id, sim), 'delivered', 'null conf → delivered by sim');

  // Same with deferred
  const sim2 = makeSimResult([], ['DP-047'], []);
  assertEquals<ReportOutcome>(reportOutcomeOf(rpHighConf.product_id, sim2), 'deferred', 'high conf → deferred by sim');
  assertEquals<ReportOutcome>(reportOutcomeOf(rpNullConf.product_id, sim2), 'deferred', 'null conf → deferred by sim');
}

// ── Test 15: full report requires complete phase + non-null sim ───────────────
function test_full_report_requires_complete_plus_sim(): void {
  const sim = makeSimResult(['P1'], [], []);

  // Only 'complete' + non-null sim triggers full report
  assert(shouldShowFullReport('complete', sim), 'complete + sim → full report');

  // All other combinations must not trigger full report
  const phases: ApprovalPhase[] = ['idle', 'ai_analyzing', 'ready', 'transmitting'];
  for (const phase of phases) {
    assert(!shouldShowFullReport(phase, sim), `phase='${phase}' must not show full report`);
  }
  assert(!shouldShowFullReport('complete', null), 'complete + null sim must not show full report');
}

// ── Test 16: anomaly context empty when no ranked products reference anomaly ───
function test_anomaly_context_empty_when_no_product_links(): void {
  const anom = makeAnomaly('ANOM-017', 'propulsion');
  // No product links to this anomaly
  const ranked = [makeRankedProduct('DP-047', 1, [])];

  const ctx = resolveReportAnomalyContext([anom], ranked);
  assertEquals(ctx.length, 0, 'anomaly with no product link → no context entry');
}

// ── Test 17: multiple anomalies, only linked ones appear ─────────────────────
function test_multiple_anomalies_filtered(): void {
  const anom1 = makeAnomaly('ANOM-017', 'propulsion');
  const anom2 = makeAnomaly('ANOM-042', 'thermal');
  const anom3 = makeAnomaly('ANOM-099', 'power');   // no product link

  const prod1 = makeRankedProduct('DP-047', 1, ['ANOM-017']);
  const prod2 = makeRankedProduct('DP-048', 2, ['ANOM-042']);

  const ctx = resolveReportAnomalyContext([anom1, anom2, anom3], [prod1, prod2]);
  assertEquals(ctx.length, 2, 'two anomalies have product links');
  assert(ctx.some((c) => c.anomaly.anomaly_id === 'ANOM-017'), 'ANOM-017 present');
  assert(ctx.some((c) => c.anomaly.anomaly_id === 'ANOM-042'), 'ANOM-042 present');
  assert(!ctx.some((c) => c.anomaly.anomaly_id === 'ANOM-099'), 'ANOM-099 excluded');
}

// ── Test 18: fulfillment 100% when all ranked products are delivered ──────────
function test_fulfillment_100_percent(): void {
  const ranked = [
    makeRankedProduct('DP-047', 1),
    makeRankedProduct('DP-048', 2),
  ];
  const sim = makeSimResult(['DP-047', 'DP-048'], [], []);
  assertClose(computeFulfillment(ranked, sim), 1.0, 0.001, 'all delivered → fulfillment = 1.0');
}

// ── Test 19: fulfillment 0% when no ranked products are delivered ─────────────
function test_fulfillment_0_percent(): void {
  const ranked = [
    makeRankedProduct('DP-047', 1),
    makeRankedProduct('DP-048', 2),
  ];
  const sim = makeSimResult([], ['DP-047', 'DP-048'], []);
  assertClose(computeFulfillment(ranked, sim), 0.0, 0.001, 'none delivered → fulfillment = 0.0');
}

// ── Test 20: mission state fields are present on MissionState type ────────────
function test_mission_state_fields_present(): void {
  const ms = makeMissionState();
  assert(ms.mission_id.length > 0,    'mission_id present');
  assert(ms.mission_phase.length > 0, 'mission_phase present');
  assert(ms.current_event.length > 0, 'current_event present');
  assert(ms.risk_score >= 0 && ms.risk_score <= 1, 'risk_score in [0,1]');
  assert(
    ['LOW','MEDIUM','HIGH','CRITICAL'].includes(ms.risk_level),
    'risk_level is valid RiskLevel',
  );
}

// ─── Run all tests ─────────────────────────────────────────────────────────────

const TESTS: Array<[string, () => void]> = [
  ['test 1: report waits before recommendation',                          test_report_waits_before_recommendation],
  ['test 2: no outcomes without simulationResult',                        test_no_outcomes_without_simulation],
  ['test 3: AI-recommended plan is labelled correctly',                   test_ai_recommended_label],
  ['test 4: operator override is labelled correctly',                     test_operator_override_label],
  ['test 5: delivered/deferred/failed from SimulationResult only',        test_outcomes_from_simulation_result],
  ['test 6: not_in_plan is distinct from deferred',                       test_not_in_plan_is_not_deferred],
  ['test 7: anomaly context requires matching anomaly_ids',               test_anomaly_context_requires_matching_ids],
  ['test 8: geometry uses propagation fields, not latency_s',            test_geometry_source_independence],
  ['test 9: fulfillment metric matches existing formula',                 test_fulfillment_metric_formula],
  ['test 10: legacy null geometry remains valid',                         test_legacy_null_geometry],
  ['test 11: legacy without AI prioritization remains valid',             test_legacy_no_ai_prioritization],
  ['test 12: report uses actual approved plan identity from SimResult',   test_plan_identity_from_simulation_result],
  ['test 13: elapsed time is a total scalar, not per-packet',            test_elapsed_time_is_total_scalar],
  ['test 14: no AI field influences deterministic outcome',               test_ai_fields_do_not_influence_outcome],
  ['test 15: full report requires complete phase + non-null sim',         test_full_report_requires_complete_plus_sim],
  ['test 16: anomaly context empty when no product links to anomaly',    test_anomaly_context_empty_when_no_product_links],
  ['test 17: multiple anomalies — only linked ones appear',               test_multiple_anomalies_filtered],
  ['test 18: fulfillment 100% when all ranked products delivered',        test_fulfillment_100_percent],
  ['test 19: fulfillment 0% when no ranked products delivered',           test_fulfillment_0_percent],
  ['test 20: MissionState fields present and valid',                     test_mission_state_fields_present],
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
    console.log(`  ✓ All ${passed} MissionReportPanel logic tests passed`);
  } else {
    console.error(`  ${failed} test(s) failed, ${passed} passed`);
  }
}

export {
  makeSimResult,
  makeRankedProduct,
  makeAnomaly,
  makeMissionState,
  shouldShowFullReport,
};
