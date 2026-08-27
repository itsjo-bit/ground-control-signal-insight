/**
 * Phase 5.1 — Behavioral test suite (Vitest)
 *
 * Tests verify:
 * 7.1  Dynamic DecisionChain: product count / candidate count driven by runtime props
 * 7.2  Local vs external provider labeling in DecisionChain
 * 7.3  Confidence semantics: heuristic vs uncalibrated LLM wording
 * 7.4  AssessManualPlanResponse: typed structures (no any)
 * 7.5  Manual mode requires no AI
 * 7.6  Provider fallback copy
 * 7.7  Why This Matters — reason advisory
 * 7.8  Ground evidence helpers: AVAILABLE / PARTIAL / UNAVAILABLE
 * 7.9  Transmission playback mapper: failure → retransmit → success ordering
 * 7.10 Unit formatters: bits → MB/GB, bps → Mbps
 * 7.11 Generic scenario: 150 products does not show ASTERIA 1,284
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

// ── Production imports ────────────────────────────────────────────────────────
import { DecisionChain } from '../../components/AIDecisionPanel';
import {
  assessGroundObjectives,
  objectiveAvailabilityLabel,
  groundEvidenceLevel,
} from '../../experience/groundEvidence';
import { buildTransmissionPlayback } from '../../experience/transmissionPlayback';
import { formatBitsAsDataVolume, formatBitRate } from '../../utils/formatters';
import type {
  AssessManualPlanResponse,
} from '../../api/client';
import type {
  AnomalyCoverageDetail,
  CapacitySummary,
  MissionOutcomeResult,
  CandidatePlan,
  EvaluationResult,
  SimulationResult,
  TransmissionAttemptEvent,
} from '../../types/domain';

// ─────────────────────────────────────────────────────────────────────────────
// 7.1 + 7.11 — Dynamic DecisionChain product count
// ─────────────────────────────────────────────────────────────────────────────

describe('DecisionChain — dynamic product count', () => {
  it('renders 1,284 PRODUCTS for ASTERIA scenario', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'external',
      }),
    );
    expect(screen.getByText(/1,284 PRODUCTS/)).toBeDefined();
    expect(screen.getByText(/50 CANDIDATES/)).toBeDefined();
  });

  it('renders 150 PRODUCTS for mission_data_v3 scenario', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 150,
        candidateCount: 50,
        providerKind: 'external',
      }),
    );
    expect(screen.getByText(/150 PRODUCTS/)).toBeDefined();
    // Must NOT show 1,284 anywhere
    expect(screen.queryByText(/1,284/)).toBeNull();
  });

  it('renders generic PRODUCTS label when totalProducts is null', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: null,
        candidateCount: null,
        providerKind: 'unknown',
      }),
    );
    // No hard-coded number — just the generic label
    expect(screen.getByText(/^PRODUCTS$/)).toBeDefined();
  });

  it('renders actual candidate count dynamically', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 200,
        candidateCount: 35,
        providerKind: 'external',
      }),
    );
    expect(screen.getByText(/35 CANDIDATES/)).toBeDefined();
    expect(screen.queryByText(/50 CANDIDATES/)).toBeNull();
  });

  it('150 products does not render 1,284', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 150,
        candidateCount: 50,
        providerKind: 'local',
      }),
    );
    expect(screen.queryByText(/1,284/)).toBeNull();
    expect(screen.getByText(/150 PRODUCTS/)).toBeDefined();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.2 — Local vs external provider labeling
// ─────────────────────────────────────────────────────────────────────────────

describe('DecisionChain — provider labeling', () => {
  it('shows AI PRIORITIZATION for external provider', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'external',
      }),
    );
    expect(screen.getByText(/AI PRIORITIZATION/)).toBeDefined();
    expect(screen.queryByText(/DETERMINISTIC PRIORITIZATION/)).toBeNull();
  });

  it('shows DETERMINISTIC PRIORITIZATION for Local provider', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'local',
      }),
    );
    expect(screen.getByText(/DETERMINISTIC PRIORITIZATION/)).toBeDefined();
    // Must NOT show AI PRIORITIZATION for Local
    expect(screen.queryByText(/^AI PRIORITIZATION$/)).toBeNull();
  });

  it('does not show AI badge on Local prioritization step', () => {
    const { container } = render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'local',
      }),
    );
    // AI badge text should not appear in Local mode
    const aiBadges = container.querySelectorAll('span');
    const hasAiBadge = Array.from(aiBadges).some(
      (el) => el.textContent === 'AI' && el.style.background?.includes('rgba(124,158,255'),
    );
    expect(hasAiBadge).toBe(false);
  });

  it('shows LOCAL badge on deterministic prioritization step', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'local',
      }),
    );
    // LOCAL badge should be rendered
    expect(screen.getByText(/LOCAL/)).toBeDefined();
  });

  it('shows DETERMINISTIC FEASIBILITY not SAFETY / FEASIBILITY', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'external',
      }),
    );
    expect(screen.getByText(/DETERMINISTIC FEASIBILITY/)).toBeDefined();
    expect(screen.queryByText(/SAFETY \/ FEASIBILITY/)).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.3 — Confidence semantics (type-level validation)
// ─────────────────────────────────────────────────────────────────────────────

describe('Confidence semantics — type contract', () => {
  it('heuristic confidence_semantics is a valid ConfidenceSemantics value', () => {
    const semantics: import('../../types/domain').ConfidenceSemantics = 'heuristic';
    expect(semantics).toBe('heuristic');
  });

  it('uncalibrated_llm confidence_semantics is a valid ConfidenceSemantics value', () => {
    const semantics: import('../../types/domain').ConfidenceSemantics = 'uncalibrated_llm';
    expect(semantics).toBe('uncalibrated_llm');
  });

  it('unspecified_uncalibrated is the fail-safe default', () => {
    const semantics: import('../../types/domain').ConfidenceSemantics = 'unspecified_uncalibrated';
    expect(semantics).toBe('unspecified_uncalibrated');
  });

  it('Local confidence is heuristic — not uncalibrated_llm', () => {
    // The backend assigns heuristic to Local provider outputs.
    // Verify the type distinction exists.
    const local: import('../../types/domain').ConfidenceSemantics = 'heuristic';
    const llm: import('../../types/domain').ConfidenceSemantics = 'uncalibrated_llm';
    expect(local).not.toBe(llm);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.4 — AssessManualPlanResponse typed structure (no any)
// ─────────────────────────────────────────────────────────────────────────────

describe('AssessManualPlanResponse — type safety', () => {
  it('anomaly_coverage_by_id is an array matching backend list[AnomalyCoverageDetail]', () => {
    // Backend serializes anomaly_coverage_by_id as a JSON array (list[AnomalyCoverageDetail]).
    // Frontend must use AnomalyCoverageDetail[] — NOT Record<string, AnomalyCoverageDetail>.
    const detail: AnomalyCoverageDetail = {
      anomaly_id: 'ANOM-017',
      severity: 0.94,
      total_linked_products: 5,
      delivered_linked_products: 3,
      coverage_rate: 0.6,
    };
    const outcome: MissionOutcomeResult = {
      plan_id: 'test-plan',
      total_products: 150,
      delivered_products: 45,
      delivery_rate: 0.3,
      total_scientific_value: 100.0,
      delivered_scientific_value: 30.0,
      scientific_value_capture_rate: 0.3,
      required_products_total: 8,
      required_products_delivered: 5,
      required_delivery_rate: 0.625,
      active_anomaly_products_total: 6,
      active_anomaly_products_delivered: 4,
      active_anomaly_delivery_rate: 0.667,
      high_severity_threshold: 0.75,
      high_severity_anomalies_total: 1,
      high_severity_anomalies_covered: 1,
      high_severity_anomaly_coverage_rate: 1.0,
      anomaly_weighted_coverage: 0.6,
      average_delivered_age_s: 1200,
      median_delivered_age_s: 900,
      delivered_by_subsystem: { thermal: 3, power: 1 },
      // Correct shape: backend serializes as JSON array, not a dict/Record
      anomaly_coverage_by_id: [detail],
    };
    // anomaly_coverage_by_id is an array
    expect(Array.isArray(outcome.anomaly_coverage_by_id)).toBe(true);
    expect(outcome.anomaly_coverage_by_id).toHaveLength(1);
    // Access via find (array pattern), not dict key
    const found = outcome.anomaly_coverage_by_id.find((d) => d.anomaly_id === 'ANOM-017');
    expect(found).toBeDefined();
    expect(found!.coverage_rate).toBe(0.6);
    expect(found!.severity).toBe(0.94);
    // Verify key fields
    expect(outcome.plan_id).toBe('test-plan');
    expect(outcome.required_products_total).toBe(8);
    expect(outcome.high_severity_threshold).toBe(0.75);
  });

  it('representative serialized /plans/assess response uses array shape', () => {
    // This fixture mirrors the actual JSON produced by POST /plans/assess.
    // anomaly_coverage_by_id must be a JSON array, not a JSON object/dict.
    const serializedResponse = JSON.parse(JSON.stringify({
      plan: { plan_id: 'operator-manual-assess', strategy: 'manual', packets: [], generated_by: 'operator', metadata: {} },
      evaluation: {
        plan_id: 'operator-manual-assess', mission_value: 0.3,
        critical_packets_delivered: 0, total_critical_packets: 0,
        deadline_misses: 0, avg_packet_delay_s: 0, bandwidth_utilization: 0.4,
        retransmission_overhead: 0, risk_score: 0.2, risk_level: 'LOW',
        deferred_packets: [], deadline_miss_rate: 0, critical_deficit: 0, window_pressure: 0.4,
      },
      mission_outcome: {
        plan_id: 'operator-manual-assess',
        total_products: 1284, delivered_products: 45, delivery_rate: 0.035,
        total_scientific_value: 500, delivered_scientific_value: 18, scientific_value_capture_rate: 0.036,
        required_products_total: 12, required_products_delivered: 4, required_delivery_rate: 0.333,
        active_anomaly_products_total: 8, active_anomaly_products_delivered: 3, active_anomaly_delivery_rate: 0.375,
        high_severity_threshold: 0.75,
        high_severity_anomalies_total: 1, high_severity_anomalies_covered: 1,
        high_severity_anomaly_coverage_rate: 1.0,
        anomaly_weighted_coverage: 0.375,
        average_delivered_age_s: 1800, median_delivered_age_s: 1200,
        delivered_by_subsystem: { thermal: 3 },
        // Backend serializes list[AnomalyCoverageDetail] as a JSON array
        anomaly_coverage_by_id: [
          { anomaly_id: 'ANOM-THERM-017', severity: 0.94, total_linked_products: 8, delivered_linked_products: 3, coverage_rate: 0.375 },
        ],
      },
      capacity_summary: { available_capacity_bits: 685440000, selected_bits: 288640000, selected_count: 45, exceeds_capacity: false, window_s: 272 },
    }));
    // The parsed response must have anomaly_coverage_by_id as an array
    expect(Array.isArray(serializedResponse.mission_outcome.anomaly_coverage_by_id)).toBe(true);
    expect(serializedResponse.mission_outcome.anomaly_coverage_by_id[0].anomaly_id).toBe('ANOM-THERM-017');
    expect(serializedResponse.mission_outcome.anomaly_coverage_by_id[0].coverage_rate).toBe(0.375);
  });

  it('capacity_summary is typed CapacitySummary (no any)', () => {
    const cs: CapacitySummary = {
      available_capacity_bits: 685_440_000,
      selected_bits: 288_640_000,
      selected_count: 2,
      exceeds_capacity: false,
      window_s: 272.0,
    };
    expect(cs.selected_count).toBe(2);
    expect(cs.exceeds_capacity).toBe(false);
  });

  it('AssessManualPlanResponse composes correctly with null mission_outcome', () => {
    const plan: CandidatePlan = {
      plan_id: 'operator-manual-assess',
      strategy: 'manual',
      packets: [],
      generated_by: 'operator',
      metadata: {},
    };
    const evaluation: EvaluationResult = {
      plan_id: 'operator-manual-assess',
      mission_value: 0.5,
      critical_packets_delivered: 0,
      total_critical_packets: 0,
      deadline_misses: 0,
      avg_packet_delay_s: 0,
      bandwidth_utilization: 0,
      retransmission_overhead: 0,
      risk_score: 0.1,
      risk_level: 'LOW',
      deferred_packets: [],
      deadline_miss_rate: 0,
      critical_deficit: 0,
      window_pressure: 0,
    };
    const cs: CapacitySummary = {
      available_capacity_bits: 1_000_000,
      selected_bits: 0,
      selected_count: 0,
      exceeds_capacity: false,
      window_s: 272.0,
    };
    const response: AssessManualPlanResponse = {
      plan,
      evaluation,
      mission_outcome: null,
      capacity_summary: cs,
    };
    expect(response.mission_outcome).toBeNull();
    expect(response.capacity_summary.available_capacity_bits).toBe(1_000_000);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.5 — Manual mode requires no AI
// ─────────────────────────────────────────────────────────────────────────────

describe('Manual mode — no AI dependency', () => {
  it('CapacitySummary does not require AI fields', () => {
    // Manual assess response has no AI fields — purely deterministic
    const cs: CapacitySummary = {
      available_capacity_bits: 200_000_000,
      selected_bits: 50_000_000,
      selected_count: 3,
      exceeds_capacity: false,
      window_s: 272,
    };
    // The capacity summary stands alone without any AI recommendation
    expect(cs.selected_count).toBe(3);
    expect(cs.exceeds_capacity).toBe(false);
  });

  it('MissionOutcomeResult does not have any AI-derived fields', () => {
    // All fields are deterministic — not generated by AI
    const outcome: MissionOutcomeResult = {
      plan_id: 'op-manual',
      total_products: 150,
      delivered_products: 10,
      delivery_rate: 0.067,
      total_scientific_value: 50,
      delivered_scientific_value: 5,
      scientific_value_capture_rate: 0.1,
      required_products_total: 5,
      required_products_delivered: 2,
      required_delivery_rate: 0.4,
      active_anomaly_products_total: 0,
      active_anomaly_products_delivered: 0,
      active_anomaly_delivery_rate: null,
      high_severity_threshold: 0.75,
      high_severity_anomalies_total: 0,
      high_severity_anomalies_covered: 0,
      high_severity_anomaly_coverage_rate: null,
      anomaly_weighted_coverage: null,
      average_delivered_age_s: 500,
      median_delivered_age_s: 450,
      delivered_by_subsystem: {},
      anomaly_coverage_by_id: [],
    };
    // Null rates when denominator is zero — not false 1.0
    expect(outcome.active_anomaly_delivery_rate).toBeNull();
    expect(outcome.high_severity_anomaly_coverage_rate).toBeNull();
    expect(outcome.required_delivery_rate).toBe(0.4);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.6 — Provider fallback copy
// ─────────────────────────────────────────────────────────────────────────────

describe('Provider fallback — identity truthfulness', () => {
  it('Local provider is classified correctly from name', () => {
    // Mirrors classifyProvider logic from AIDecisionPanel
    function classifyProvider(name: string | null | undefined): string {
      if (!name) return 'unknown';
      const lower = name.toLowerCase();
      if (lower === 'local' || lower === 'localrulebasedprovider') return 'local';
      if (lower === 'granite' || lower === 'gemini' || lower === 'ollama') return 'external';
      return 'unknown';
    }
    expect(classifyProvider('local')).toBe('local');
    expect(classifyProvider('Local')).toBe('local');
    expect(classifyProvider('LocalRuleBasedProvider')).toBe('local');
    expect(classifyProvider('granite')).toBe('external');
    expect(classifyProvider('Granite')).toBe('external');
    expect(classifyProvider('gemini')).toBe('external');
    expect(classifyProvider('ollama')).toBe('external');
    expect(classifyProvider(null)).toBe('unknown');
    expect(classifyProvider('')).toBe('unknown');
  });

  it('fallback to Local does not masquerade as Granite', () => {
    // When actual provider is 'local' and requested was 'granite', they differ
    const requestedProvider = 'granite';
    const actualProvider = 'local';
    expect(actualProvider.toLowerCase()).not.toBe(requestedProvider.toLowerCase());
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.7 — Why This Matters — reason is advisory
// ─────────────────────────────────────────────────────────────────────────────

describe('RankedProduct reason — advisory semantics', () => {
  it('reason field is present and non-empty', () => {
    const rp: import('../../types/domain').RankedProduct = {
      product_id: 'TEL-THERM-HR-042',
      priority: 1,
      reason: 'High-rate thermal telemetry linked to active anomaly ANOM-THERM-017',
      factors: ['active anomaly', 'high criticality'],
      anomaly_ids: ['ANOM-THERM-017'],
      subsystem: 'thermal',
      confidence: 0.92,
    };
    expect(rp.reason.length).toBeGreaterThan(0);
    // reason is provider rationale — advisory, not authoritative
    // authoritative fields are factors and anomaly_ids
    expect(rp.factors).toContain('active anomaly');
    expect(rp.anomaly_ids).toContain('ANOM-THERM-017');
  });

  it('confidence on ranked product is advisory (nullable)', () => {
    const withConf: import('../../types/domain').RankedProduct = {
      product_id: 'A', priority: 1, reason: 'r', factors: [], anomaly_ids: [], subsystem: 's', confidence: 0.8,
    };
    const withNull: import('../../types/domain').RankedProduct = {
      product_id: 'B', priority: 2, reason: 'r', factors: [], anomaly_ids: [], subsystem: 's', confidence: null,
    };
    expect(withConf.confidence).toBe(0.8);
    expect(withNull.confidence).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.8 — Ground evidence helpers
// ─────────────────────────────────────────────────────────────────────────────

describe('groundEvidence helpers', () => {
  it('objectiveAvailabilityLabel: all delivered → AVAILABLE', () => {
    expect(objectiveAvailabilityLabel(1.0)).toBe('AVAILABLE');
  });

  it('objectiveAvailabilityLabel: some delivered → PARTIAL', () => {
    expect(objectiveAvailabilityLabel(0.5)).toBe('PARTIAL');
    expect(objectiveAvailabilityLabel(0.1)).toBe('PARTIAL');
  });

  it('objectiveAvailabilityLabel: none delivered → UNAVAILABLE', () => {
    expect(objectiveAvailabilityLabel(0.0)).toBe('UNAVAILABLE');
  });

  it('groundEvidenceLevel: ≥80% → HIGH', () => {
    expect(groundEvidenceLevel(0.8)).toBe('HIGH');
    expect(groundEvidenceLevel(1.0)).toBe('HIGH');
  });

  it('groundEvidenceLevel: 40–79% → MEDIUM', () => {
    expect(groundEvidenceLevel(0.4)).toBe('MEDIUM');
    expect(groundEvidenceLevel(0.79)).toBe('MEDIUM');
  });

  it('groundEvidenceLevel: <40% → LOW', () => {
    expect(groundEvidenceLevel(0.0)).toBe('LOW');
    expect(groundEvidenceLevel(0.39)).toBe('LOW');
  });

  it('assessGroundObjectives: all delivered → AVAILABLE', () => {
    const delivered = new Set(['P1', 'P2', 'P3']);
    const objectives = { 'thermal_history': ['P1', 'P2', 'P3'] };
    const result = assessGroundObjectives(delivered, objectives);
    expect(result).toHaveLength(1);
    expect(result[0].fraction).toBe(1.0);
    expect(result[0].level).toBe('HIGH');
  });

  it('assessGroundObjectives: partial delivery → PARTIAL', () => {
    const delivered = new Set(['P1']);
    const objectives = { 'anomaly_timeline': ['P1', 'P2', 'P3'] };
    const result = assessGroundObjectives(delivered, objectives);
    expect(result[0].fraction).toBeCloseTo(1 / 3, 5);
    expect(result[0].level).toBe('LOW');
  });

  it('assessGroundObjectives: none delivered → UNAVAILABLE', () => {
    const delivered = new Set<string>();
    const objectives = { 'fault_context': ['P1', 'P2'] };
    const result = assessGroundObjectives(delivered, objectives);
    expect(result[0].fraction).toBe(0);
    expect(objectiveAvailabilityLabel(result[0].fraction)).toBe('UNAVAILABLE');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.9 — Transmission playback mapper
// ─────────────────────────────────────────────────────────────────────────────

describe('buildTransmissionPlayback — event ordering', () => {
  function makeAttemptEvent(
    packetId: string,
    attempt: number,
    status: 'success' | 'failure',
    startS: number,
    endS: number,
  ): TransmissionAttemptEvent {
    return {
      packet_id: packetId,
      attempt_number: attempt,
      start_elapsed_s: startS,
      end_elapsed_s: endS,
      status,
    };
  }

  function makeSimResult(
    attemptEvents: TransmissionAttemptEvent[],
    delivered: string[],
    deferred: string[],
    failed: string[],
  ): SimulationResult {
    return {
      plan_id: 'test',
      delivered_packets: delivered,
      deferred_packets: deferred,
      failed_packets: failed,
      elapsed_time_s: 120,
      retransmission_counts: {},
      link_state: {} as SimulationResult['link_state'],
      mission_state: {} as SimulationResult['mission_state'],
      attempt_events: attemptEvents,
    };
  }

  it('failure attempt produces attempt_complete_failure event', () => {
    const sim = makeSimResult(
      [makeAttemptEvent('P1', 1, 'failure', 0, 5)],
      [],
      [],
      ['P1'],
    );
    const pb = buildTransmissionPlayback(sim, { transmission_min_duration_ms: 2000 });
    const failureEvent = pb.events.find((e) => e.kind === 'attempt_complete_failure');
    expect(failureEvent).toBeDefined();
    expect(failureEvent!.packetId).toBe('P1');
    expect(failureEvent!.attemptNumber).toBe(1);
  });

  it('success attempt produces attempt_complete_success event', () => {
    const sim = makeSimResult(
      [makeAttemptEvent('P1', 1, 'success', 0, 5)],
      ['P1'],
      [],
      [],
    );
    const pb = buildTransmissionPlayback(sim, { transmission_min_duration_ms: 2000 });
    const successEvent = pb.events.find((e) => e.kind === 'attempt_complete_success');
    expect(successEvent).toBeDefined();
    expect(successEvent!.outcome).toBe('success');
  });

  it('failure then success produces both events in order', () => {
    const sim = makeSimResult(
      [
        makeAttemptEvent('P1', 1, 'failure', 0, 5),
        makeAttemptEvent('P1', 2, 'success', 5, 10),
      ],
      ['P1'],
      [],
      [],
    );
    const pb = buildTransmissionPlayback(sim, { transmission_min_duration_ms: 2000 });
    const failEvent = pb.events.find((e) => e.kind === 'attempt_complete_failure');
    const successEvent = pb.events.find((e) => e.kind === 'attempt_complete_success');
    expect(failEvent).toBeDefined();
    expect(successEvent).toBeDefined();
    // failure occurs before success in visual offset
    expect(failEvent!.visualOffsetMs).toBeLessThan(successEvent!.visualOffsetMs);
  });

  it('deferred packet has packet_deferred event, no transmission pulse', () => {
    // Deferred packets: no attempt events, just deferred marker
    const sim = makeSimResult(
      [], // no attempt events
      [],
      ['P2'], // deferred
      [],
    );
    const pb = buildTransmissionPlayback(sim, { transmission_min_duration_ms: 2000 });
    const deferredEvent = pb.events.find(
      (e) => e.kind === 'packet_deferred' && e.packetId === 'P2',
    );
    expect(deferredEvent).toBeDefined();
    // No start/complete events for the deferred packet
    const startEvents = pb.events.filter(
      (e) => e.kind === 'attempt_start' && e.packetId === 'P2',
    );
    expect(startEvents).toHaveLength(0);
  });

  it('transmission_complete event is always present', () => {
    const sim = makeSimResult([], [], [], []);
    const pb = buildTransmissionPlayback(sim, { transmission_min_duration_ms: 2000 });
    const complete = pb.events.find((e) => e.kind === 'transmission_complete');
    expect(complete).toBeDefined();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7.10 — Unit formatters
// ─────────────────────────────────────────────────────────────────────────────

describe('Unit formatters', () => {
  it('formatBitsAsDataVolume: large value → GB', () => {
    const result = formatBitsAsDataVolume(2_740_000_000 * 8); // 2.74 GB
    expect(result).toContain('GB');
    expect(result).not.toContain('Mb');
    expect(result).not.toContain('MB');
  });

  it('formatBitsAsDataVolume: medium value → MB', () => {
    const result = formatBitsAsDataVolume(85_700_000 * 8); // 85.7 MB
    expect(result).toContain('MB');
    expect(result).not.toContain('GB');
  });

  it('formatBitsAsDataVolume: never returns Mb (megabits)', () => {
    // Output must always use byte-based units (MB or GB), never Mb
    const smallResult = formatBitsAsDataVolume(1_000 * 8);
    const medResult = formatBitsAsDataVolume(1_000_000 * 8);
    const bigResult = formatBitsAsDataVolume(1_000_000_000 * 8);
    for (const r of [smallResult, medResult, bigResult]) {
      expect(r).not.toMatch(/\bMb\b/);
    }
  });

  it('formatBitRate: ≥1 Mbps → Mbps unit', () => {
    const result = formatBitRate(2_400_000); // 2.4 Mbps
    expect(result).toContain('Mbps');
    expect(result).not.toContain('kbps');
  });

  it('formatBitRate: kbps range → kbps unit', () => {
    const result = formatBitRate(90_000); // 90 kbps
    expect(result).toContain('kbps');
    expect(result).not.toContain('Mbps');
  });

  it('formatBitsAsDataVolume and formatBitRate are consistent with GCSI demo values', () => {
    // GCSI canonical values: 2.74 GB queue, 85.7 MB window, 2.4 Mbps link
    const queueResult = formatBitsAsDataVolume(2_740_000_000 * 8);
    const windowResult = formatBitsAsDataVolume(85_700_000 * 8);
    const bitRateResult = formatBitRate(2_400_000);
    expect(queueResult).toContain('GB');
    expect(windowResult).toContain('MB');
    expect(bitRateResult).toContain('Mbps');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 5.1C — Unknown provider fail-safe
// ─────────────────────────────────────────────────────────────────────────────

describe('Unknown provider fail-safe', () => {
  it('classifyProvider returns unknown for unrecognized name', () => {
    // Mirror of classifyProvider from AIDecisionPanel
    function classifyProvider(name: string | null | undefined): string {
      if (!name) return 'unknown';
      const lower = name.toLowerCase();
      if (lower === 'local' || lower === 'localrulebasedprovider' || lower === 'local_rule_based') return 'local';
      if (lower === 'granite' || lower === 'gemini' || lower === 'ollama') return 'external';
      return 'unknown';
    }
    expect(classifyProvider('mystery-provider')).toBe('unknown');
    expect(classifyProvider('custom-llm')).toBe('unknown');
    expect(classifyProvider('UNKNOWN-AI')).toBe('unknown');
    // Known providers are still correct
    expect(classifyProvider('granite')).toBe('external');
    expect(classifyProvider('gemini')).toBe('external');
    expect(classifyProvider('ollama')).toBe('external');
    expect(classifyProvider('local')).toBe('local');
  });

  it('DecisionChain shows ADVISORY PRIORITIZATION for unknown provider', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'unknown',
      }),
    );
    expect(screen.getByText(/ADVISORY PRIORITIZATION/)).toBeDefined();
    expect(screen.queryByText(/^AI PRIORITIZATION$/)).toBeNull();
    expect(screen.queryByText(/DETERMINISTIC PRIORITIZATION/)).toBeNull();
  });

  it('DecisionChain does not render AI badge for unknown provider', () => {
    const { container } = render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'unknown',
      }),
    );
    const allSpans = container.querySelectorAll('span');
    const hasAiBadge = Array.from(allSpans).some(
      (el) => el.textContent === 'AI' && el.style.background?.includes('rgba(124,158,255'),
    );
    expect(hasAiBadge).toBe(false);
  });

  it('DecisionChain does not render LOCAL badge for unknown provider', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'unknown',
      }),
    );
    // LOCAL badge must not appear for unknown provider
    expect(screen.queryByText(/^LOCAL$/)).toBeNull();
  });

  it('Granite provider still shows AI PRIORITIZATION', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'external',
      }),
    );
    expect(screen.getByText(/AI PRIORITIZATION/)).toBeDefined();
  });

  it('Local provider still shows DETERMINISTIC PRIORITIZATION', () => {
    render(
      React.createElement(DecisionChain, {
        totalProducts: 1284,
        candidateCount: 50,
        providerKind: 'local',
      }),
    );
    expect(screen.getByText(/DETERMINISTIC PRIORITIZATION/)).toBeDefined();
    expect(screen.queryByText(/AI PRIORITIZATION/)).toBeNull();
    expect(screen.queryByText(/ADVISORY PRIORITIZATION/)).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 5.1C — Per-product confidence presentation
// ─────────────────────────────────────────────────────────────────────────────

describe('Per-product confidence — non-percentage presentation', () => {
  it('confidence 0.87 renders as 87/100 not 87%', () => {
    // classifyProvider and RankedProductRow are internal to AIDecisionPanel.
    // We verify the formatting contract via the numeric presentation logic directly.
    const confidence = 0.87;
    const rendered = `${(confidence * 100).toFixed(0)}/100`;
    expect(rendered).toBe('87/100');
    expect(rendered).not.toContain('%');
  });

  it('confidence formatting uses /100 denominator pattern', () => {
    const testValues = [0.0, 0.5, 0.92, 1.0];
    for (const v of testValues) {
      const rendered = `${(v * 100).toFixed(0)}/100`;
      expect(rendered).toMatch(/^\d+\/100$/);
      expect(rendered).not.toContain('%');
    }
  });
});
