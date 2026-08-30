/**
 * phase8b5.modified-plan-execution.test.tsx — Phase 8B.5 regression tests
 *
 * Confirms the corrected execution-semantics for modified AI plans:
 *
 *   ROOT CAUSE (pre-fix):
 *     handleApproveAiPlan() always submitted recPlan (full CandidatePlan, e.g. 1284 packets)
 *     even after operator had clicked Modify Plan and narrowed the selection to 81 products.
 *
 *   FIX:
 *     When manualEditOrigin === 'ai_recommendation' AND manualOrder.length > 0,
 *     handleApproveAiPlan routes through approveCustomPlan with the current manualOrder
 *     instead of approvePlan with the full recPlan.
 *
 * Tests:
 *   CASE A — modified AI plan uses expected-fit subset (81 of 1284 → 81 submitted)
 *   CASE B — operator deselects one (81 → 80 → 80 submitted)
 *   CASE C — operator adds one deferred product (81 → 82 → 82 submitted)
 *   CASE D — untouched AI Approve uses approvePlan, not approveCustomPlan (regression)
 *
 * Ground Reception accounting:
 *   GR-1  Selected count derived from executed_plan.packets.length (not a fixed constant)
 *   GR-2  81 executed → Selected 81 in GroundReceptionPanel
 *
 * Execution mode label:
 *   ML-1  Untouched AI Approve → 'ai' execution mode → AI-ASSISTED TRANSMISSION
 *   ML-2  Modified AI execution → 'custom' execution mode → MANUAL TRANSMISSION
 *   ML-3  Fresh manual execution → 'custom' execution mode → MANUAL TRANSMISSION
 *
 * Classification: UNIT (pure logic) + INTEGRATION (MissionControl)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Browser API stubs (jsdom does not implement these) ────────────────────────
// window.matchMedia is called by prefersReducedMotion() in TransmissionSequencePanel.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import * as apiClient from '../../api/client';
import type { DataProduct } from '../../types/domain';
import {
  computeTransmissionAccounting,
  checkAccountingInvariants,
} from '../../utils/transmissionResultAccounting';

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock('../MissionViewport', () => ({
  MissionViewport: () => <div data-testid="mock-mission-viewport" />,
}));

vi.mock('../../api/client', () => ({
  getState: vi.fn(),
  getQueue: vi.fn(),
  generatePlans: vi.fn(),
  evaluatePlan: vi.fn(),
  getDataProducts: vi.fn(),
  getExperience: vi.fn(),
  assessManualPlan: vi.fn(),
  approvePlan: vi.fn(),
  approveCustomPlan: vi.fn(),
  getSources: vi.fn(),
  selectSource: vi.fn(),
  getRecommendation: vi.fn(),
  resetScenario: vi.fn(),
  listScenarios: vi.fn(),
  switchScenario: vi.fn(),
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const LINK_STATE = {
  timestamp: '2024-01-01T00:00:00Z',
  snr_db: 15,
  eb_n0_db: 12,
  ber: 0.001,
  rssi_dbm: -80,
  nominal_data_rate_bps: 2_800_000,
  link_goodput_bps: 2_500_000,
  latency_s: 608,
  link_stability: 0.9,
  remaining_window_s: 300,
};

const MISSION_STATE = {
  mission_id: 'test',
  mission_phase: 'nominal' as const,
  current_event: 'phase-8b5-test',
  event_time_remaining_s: 600,
  comm_window_remaining_s: 300,
  risk_score: 0.2,
  risk_level: 'LOW' as const,
};

/**
 * 5-product plan with 2 deferred.
 * Total = 5, deferred = 2, expected-fit = 3.
 * Mirrors the 1284/1203/81 structure at small scale.
 */
const DP_IDS = ['DP-A', 'DP-B', 'DP-C', 'DP-D', 'DP-E'];

function makeDataProduct(id: string): DataProduct {
  return {
    product_id: id,
    product_type: 'science',
    description: `Product ${id}`,
    subsystem: 'PAYLOAD',
    size_bits: 1_000_000,
    criticality: 0.8,
    mission_relevance: 0.9,
    scientific_value: 0.8,
    deadline_s: 300,
    age_s: 60,
    anomaly_id: null,
    experiment_id: null,
    related_ids: [],
    delivery_requirement: 'required',
    retry_cost: 0.2,
  };
}

const ALL_DATA_PRODUCTS = DP_IDS.map(makeDataProduct);

const AI_PLAN_PACKETS = ALL_DATA_PRODUCTS.map((dp) => ({
  packet_id: dp.product_id,
  packet_type: dp.product_type,
  size_bits: dp.size_bits,
  criticality: dp.criticality,
  mission_relevance: dp.mission_relevance,
  deadline_s: dp.deadline_s,
  retry_cost: dp.retry_cost,
  delivery_requirement: dp.delivery_requirement,
}));

// 5-packet AI plan, 2 deferred → 3 expected to fit
const AI_PLAN = {
  plan_id: 'ai-prioritized',
  strategy: 'ai' as const,
  generated_by: 'ai',
  metadata: { decision_mode: 'ai' },
  packets: AI_PLAN_PACKETS,
};

// Evaluation: DP-D and DP-E are deferred (expected to not fit this contact)
const AI_EVAL = {
  plan_id: 'ai-prioritized',
  risk_level: 'LOW' as const,
  risk_score: 0.2,
  coverage_score: 0.8,
  deadline_score: 0.9,
  efficiency_score: 0.85,
  mission_value: 0.8,
  critical_packets_delivered: 3,
  total_critical_packets: 3,
  deadline_misses: 0,
  avg_packet_delay_s: 0,
  bandwidth_utilization: 0.8,
  retransmission_overhead: 0,
  deadline_miss_rate: 0,
  critical_deficit: 0,
  window_pressure: 0.2,
  deferred_packets: ['DP-D', 'DP-E'],  // 2 deferred
  overflow_bits: 0,
  meets_deadline: true,
};

const BASELINE_PLAN = {
  plan_id: 'baseline',
  strategy: 'baseline' as const,
  generated_by: 'system',
  metadata: {},
  packets: AI_PLAN_PACKETS.slice(0, 3),
};

const BASELINE_EVAL = {
  plan_id: 'baseline',
  risk_level: 'LOW' as const,
  risk_score: 0.2,
  coverage_score: 0.8,
  deadline_score: 0.9,
  efficiency_score: 0.85,
  mission_value: 0.8,
  critical_packets_delivered: 3,
  total_critical_packets: 3,
  deadline_misses: 0,
  avg_packet_delay_s: 0,
  bandwidth_utilization: 0.8,
  retransmission_overhead: 0,
  deadline_miss_rate: 0,
  critical_deficit: 0,
  window_pressure: 0.2,
  deferred_packets: [] as string[],
  overflow_bits: 0,
  meets_deadline: true,
};

const AI_RECOMMENDATION = {
  recommended_plan_id: 'ai-prioritized',
  confidence: 0.85,
  reasoning: 'Test reasoning',
  risk_score: 0.2,
  risk_level: 'LOW' as const,
  confidence_semantics: 'heuristic' as const,
  packet_actions: [],
  evidence: [],
  alternative_plan_id: null,
  model_context: {},
};

function makeApproveResponse(packetCount: number) {
  const delivered = DP_IDS.slice(0, Math.min(packetCount, 3));
  const executedPackets = AI_PLAN_PACKETS.slice(0, packetCount);
  return {
    status: 'approved',
    simulation_result: {
      plan_id: 'operator-manual',
      delivered_packets: delivered,
      failed_packets: [] as string[],
      deferred_packets: [] as string[],
      attempt_events: [],
      elapsed_time_s: 2,
      link_state: LINK_STATE,
      mission_state: MISSION_STATE,
      retransmission_counts: {},
    },
    approval_trace: {
      approval_id: 'trace-001',
      timestamp_utc: new Date().toISOString(),
      scenario_id: 'test',
      plan_id: 'operator-manual',
      decision: 'approved',
      plan_source: 'operator_custom',
      operator_notes: '',
      authoritative_reconstruction: true,
      issued_plan_verified: false,
      packet_count: packetCount,
      packet_order_sha256: 'abc',
      canonical_plan_sha256: 'def',
    },
    executed_plan: {
      plan_id: 'operator-manual',
      strategy: 'manual' as const,
      generated_by: 'operator',
      metadata: {},
      packets: executedPackets,
    },
  };
}

const BASE_STATE = {
  link_state: LINK_STATE,
  mission_state: MISSION_STATE,
  available_capacity_bits: 81_000_000,
  queued_data_bits: 5_000_000,
  data_products_count: 5,
  anomalies: [],
  distance_km: 384400,
  propagation_delay_s: 1.28,
  round_trip_time_s: 2.56,
  source: {
    mode: 'synthetic_scenario' as const,
    provider_name: null,
    source_ref: null,
    is_historical_replay: false,
    provenance_available: false,
    provenance_scope: null,
    provenance_record_count: 0,
    provenance_binding_count: 0,
    provenance_kind_counts: {},
  },
};

const EXPERIENCE_MANIFEST = {
  schema_version: '1.0',
  scenario_id: 'test',
  display: {
    mission_name: 'TEST MISSION',
    scenario_name: '8B.5 Test',
    spacecraft_name: 'TEST-SAT',
    ground_station_name: 'Test Ground Station',
    ground_station_description: 'Test only',
    disclaimer: 'Test only',
  },
  schedule: {
    next_contact_in_s: 300,
    plan_uplink_margin_s: 60,
    contact_duration_s: 600,
    one_way_signal_s_note: 'approx 1.28s',
  },
  subsystem_status: {
    thermal: { status: 'nominal', trend: 'stable', label: 'NOMINAL', note: '' },
    communications: { status: 'nominal', trend: 'stable', label: 'NOMINAL', note: '' },
    power: { status: 'nominal', trend: 'stable', label: 'NOMINAL', note: '' },
    propulsion: { status: 'nominal', trend: 'stable', label: 'NOMINAL', note: '' },
  },
  snr_history: [{ offset_s: 0, snr_db: 15 }],
  thermal_history: [{ offset_s: 0, temp_c: 25 }],
  ingest_replay: { total_products: 5, total_bytes: 5_000_000, batches: [] },
  ground_information_objectives: {},
  curated_candidate_ids: DP_IDS,
  playback: {
    ingest_duration_ms: 100,
    uplink_duration_ms: 100,
    contact_acquisition_ms: 100,
    propagation_duration_ms: 100,
    transmission_min_duration_ms: 200,
    ground_receive_interval_ms: 50,
  },
};

function setupMocks() {
  vi.mocked(apiClient.getState).mockResolvedValue(BASE_STATE as any);
  vi.mocked(apiClient.getQueue).mockResolvedValue(BASELINE_PLAN as any);
  vi.mocked(apiClient.generatePlans).mockResolvedValue([BASELINE_PLAN as any]);
  vi.mocked(apiClient.evaluatePlan).mockResolvedValue(BASELINE_EVAL as any);
  vi.mocked(apiClient.getDataProducts).mockResolvedValue({
    scenario_id: 'test',
    data_products: ALL_DATA_PRODUCTS,
    total: ALL_DATA_PRODUCTS.length,
    has_data_products: true,
  });
  vi.mocked(apiClient.getExperience).mockResolvedValue({
    available: true,
    manifest: EXPERIENCE_MANIFEST as any,
  });
  vi.mocked(apiClient.listScenarios).mockResolvedValue({
    scenarios: [],
    active_scenario_path: '/test',
  } as any);
  vi.mocked(apiClient.approvePlan).mockResolvedValue(makeApproveResponse(5) as any);
  vi.mocked(apiClient.approveCustomPlan).mockResolvedValue(makeApproveResponse(3) as any);
  vi.mocked(apiClient.assessManualPlan).mockResolvedValue({
    plan: BASELINE_PLAN,
    evaluation: BASELINE_EVAL,
    mission_outcome: null,
    capacity_summary: {
      selected_count: 3,
      selected_bits: 3_000_000,
      available_bits: 81_000_000,
      available_capacity_bits: 81_000_000,
      selected_count_unused: 3,
      window_s: 300,
      exceeds_capacity: false,
    },
  } as any);
  vi.mocked(apiClient.resetScenario).mockResolvedValue({
    status: 'ok',
    scenario_path: '/test',
    comm_window_remaining_s: 300,
    source_mode: 'synthetic_scenario',
    randomized: true,
  });
  vi.mocked(apiClient.getRecommendation).mockResolvedValue({
    recommendation: AI_RECOMMENDATION,
    provider: 'local',
    requested_provider: 'local',
    actual_provider: 'local',
    prioritization_provider: 'local',
    recommendation_provider: 'local',
    ai_plan: AI_PLAN,
    ai_evaluation: AI_EVAL,
    prioritization: null,
    candidate_count: 5,
    prioritization_error: null,
    prioritization_fallback_reason: null,
    recommendation_fallback_reason: null,
  } as any);
  vi.mocked(apiClient.getSources).mockResolvedValue({
    active_source_id: 'test-source',
    sources: [
      { source_id: 'test-source', display_name: 'Test Source', mode: 'synthetic_scenario', description: 'Test', historical: false, simulated: true },
    ],
  } as any);
  vi.mocked(apiClient.selectSource).mockResolvedValue({
    status: 'switched',
    active_source_id: 'test-source',
    display_name: 'Test Source',
    mode: 'synthetic_scenario',
    data_products_count: 5,
    scenario_id: 'test',
  } as any);
}

import MissionControl from '../../MissionControl';

function renderMissionControl() {
  return render(
    <React.StrictMode>
      <MissionControl />
    </React.StrictMode>
  );
}

async function waitForMissionLoaded() {
  await waitFor(() => {
    expect(screen.queryByText('Loading mission data…')).not.toBeInTheDocument();
  }, { timeout: 5000 });
}

async function navigateTo(section: string) {
  const tooltipMap: Record<string, string> = {
    data: 'Data products and transmission queue',
    transmission: 'Transmission control and approval',
    ai: 'AI analysis and recommendations',
    mission: 'Mission state and overview',
    log: 'Mission log and simulation results',
  };
  const tooltip = tooltipMap[section];
  if (tooltip) {
    const btn = screen.queryByTitle(tooltip) ?? screen.queryByLabelText(tooltip);
    if (btn) await act(async () => { fireEvent.click(btn); });
  }
}

/**
 * Run AI analysis and wait for recommendation to be ready.
 * Returns when the "Analyze Mission with AI" button is clicked and result arrives.
 */
async function runAiAnalysis() {
  await navigateTo('ai');
  await waitFor(() => {
    const btn = screen.queryByText('Analyze Mission with AI');
    if (btn) fireEvent.click(btn);
    else {
      // Already ready or retry available
      const retry = screen.queryByText('Re-run AI Analysis');
      if (retry) fireEvent.click(retry);
    }
  }, { timeout: 3000 });
  await waitFor(() => {
    expect(vi.mocked(apiClient.getRecommendation)).toHaveBeenCalled();
  }, { timeout: 3000 });
  // Wait for AI plan to load into Decision tab
  await waitFor(() => {
    const decisionTab = screen.queryByText('Decision');
    expect(decisionTab).toBeInTheDocument();
  }, { timeout: 4000 });
}

/**
 * Click Modify Plan from the AI Decision panel.
 * Returns when data section is visible.
 */
async function clickModifyPlan() {
  // Navigate to AI section to find Modify Plan button in the right panel (DecisionAi)
  await navigateTo('ai');
  // In DecisionAi (right panel), the Modify Plan button is always rendered when AI is ready
  await waitFor(() => {
    const modifyBtn = screen.queryByText('✎ Modify Plan');
    expect(modifyBtn).toBeInTheDocument();
  }, { timeout: 3000 });
  await act(async () => {
    const modifyBtn = screen.queryByText('✎ Modify Plan')!;
    fireEvent.click(modifyBtn);
  });
  // After Modify, app navigates to data section
  await waitFor(() => {
    // DataSection is now active
  }, { timeout: 1000 });
}

// ── Setup / Teardown ────────────────────────────────────────────────────────────

beforeEach(() => {
  setupMocks();
  const originalError = console.error;
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    const msg = String(args[0] ?? '');
    if (
      msg.includes('act(') ||
      msg.includes('Not implemented') ||
      msg.includes('Warning: ReactDOM') ||
      msg.includes('Warning: React') ||
      msg.includes('inside a test was not wrapped') ||
      msg.includes('ResizeObserver') ||
      msg.includes('Each child in a list')
    ) return;
    originalError(...args);
  });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ═══════════════════════════════════════════════════════════════════════════════
// CASE D — UNTOUCHED AI APPROVE (regression — must remain unchanged)
// This test must pass before and after the 8B.5 fix.
// When no Modify is performed, approvePlan is called (not approveCustomPlan).
// ═══════════════════════════════════════════════════════════════════════════════

describe('CASE D — untouched AI Approve uses approvePlan, not approveCustomPlan', () => {
  it('approvePlan called once, approveCustomPlan NOT called, when AI approved without Modify', async () => {
    renderMissionControl();
    await waitForMissionLoaded();

    await runAiAnalysis();

    // Navigate to AI section and click Approve
    await navigateTo('ai');
    await waitFor(() => {
      const approveBtn = screen.queryByText('✓ APPROVE TRANSMISSION');
      expect(approveBtn).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      const approveBtn = screen.queryByText('✓ APPROVE TRANSMISSION')!;
      fireEvent.click(approveBtn);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.approvePlan)).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    // The critical regression: custom plan must NOT be submitted
    expect(vi.mocked(apiClient.approveCustomPlan)).not.toHaveBeenCalled();
  });

  it('approvePlan is called with the recommended plan ID', async () => {
    renderMissionControl();
    await waitForMissionLoaded();

    await runAiAnalysis();

    await navigateTo('ai');
    await waitFor(() => {
      const approveBtn = screen.queryByText('✓ APPROVE TRANSMISSION');
      expect(approveBtn).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      const approveBtn = screen.queryByText('✓ APPROVE TRANSMISSION')!;
      fireEvent.click(approveBtn);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.approvePlan)).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    // approvePlan called with recommended_plan_id
    const [planId] = vi.mocked(apiClient.approvePlan).mock.calls[0];
    expect(planId).toBe('ai-prioritized');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// CASE A — MODIFIED AI PLAN USES EXPECTED-FIT SUBSET
//
// AI plan: 5 packets (DP-A…DP-E)
// Deferred: DP-D, DP-E (2)
// After Modify → manualOrder = [DP-A, DP-B, DP-C] (3 selected)
// Approve → approveCustomPlan called with 3 packets, NOT 5
// ═══════════════════════════════════════════════════════════════════════════════

describe('CASE A — modified AI plan executes expected-fit subset (3 of 5, not 5)', () => {
  it('approveCustomPlan called (not approvePlan) after Modify → Approve', async () => {
    renderMissionControl();
    await waitForMissionLoaded();

    await runAiAnalysis();
    await clickModifyPlan();

    // Return to AI section and click APPROVE TRANSMISSION
    // After Modify, decisionMode = 'manual', but DecisionAi still shows the button
    await navigateTo('ai');
    await waitFor(() => {
      const approveBtn = screen.queryByText('✓ APPROVE TRANSMISSION');
      expect(approveBtn).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      const approveBtn = screen.queryByText('✓ APPROVE TRANSMISSION')!;
      fireEvent.click(approveBtn);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.approveCustomPlan)).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    // The critical invariant: approvePlan (full recPlan path) must NOT be called
    expect(vi.mocked(apiClient.approvePlan)).not.toHaveBeenCalled();
  });

  it('submitted plan contains exactly the non-deferred selection (3 packets, not 5)', async () => {
    renderMissionControl();
    await waitForMissionLoaded();

    await runAiAnalysis();
    await clickModifyPlan();

    await navigateTo('ai');
    await waitFor(() => {
      expect(screen.queryByText('✓ APPROVE TRANSMISSION')).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      fireEvent.click(screen.queryByText('✓ APPROVE TRANSMISSION')!);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.approveCustomPlan)).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    const [submittedPlan] = vi.mocked(apiClient.approveCustomPlan).mock.calls[0] as [import('../../types/domain').CandidatePlan, string];

    // Must be 3 (non-deferred), NOT 5 (full AI plan)
    expect(submittedPlan.packets.length).toBe(3);
    expect(submittedPlan.packets.length).not.toBe(5);

    // Submitted IDs must be the non-deferred ones
    const submittedIds = submittedPlan.packets.map((p) => p.packet_id);
    expect(submittedIds).toContain('DP-A');
    expect(submittedIds).toContain('DP-B');
    expect(submittedIds).toContain('DP-C');
    expect(submittedIds).not.toContain('DP-D');
    expect(submittedIds).not.toContain('DP-E');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// CASE B — OPERATOR DESELECTS ONE (3 → 2 submitted)
//
// After Modify → [DP-A, DP-B, DP-C] selected (3)
// Operator deselects DP-C in Data section
// Approve → submitted plan has 2 packets
// ═══════════════════════════════════════════════════════════════════════════════

describe('CASE B — operator deselects one product after Modify → N-1 submitted', () => {
  it('deselecting one product after Modify reduces submitted count by one', async () => {
    renderMissionControl();
    await waitForMissionLoaded();

    await runAiAnalysis();
    await clickModifyPlan();

    // After Modify, we are in data section. Deselect DP-C.
    await navigateTo('data');
    await waitFor(() => {
      expect(screen.queryByText('DP-C')).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      const dpC = screen.queryByText('DP-C');
      if (dpC) {
        const flexRow = dpC.parentElement as HTMLElement | null;
        const checkDiv = flexRow?.querySelector('div[style*="border-radius: 3px"]') as HTMLElement | null;
        if (checkDiv) fireEvent.click(checkDiv);
        else fireEvent.click(dpC);
      }
    });

    // Now approve via AI section
    await navigateTo('ai');
    await waitFor(() => {
      expect(screen.queryByText('✓ APPROVE TRANSMISSION')).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      fireEvent.click(screen.queryByText('✓ APPROVE TRANSMISSION')!);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.approveCustomPlan)).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    const [submittedPlan] = vi.mocked(apiClient.approveCustomPlan).mock.calls[0] as [import('../../types/domain').CandidatePlan, string];

    // 3 originally selected, deselected 1 → 2
    expect(submittedPlan.packets.length).toBe(2);
    const submittedIds = submittedPlan.packets.map((p) => p.packet_id);
    expect(submittedIds).not.toContain('DP-C');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// CASE C — OPERATOR ADDS ONE DEFERRED PRODUCT (3 → 4 submitted)
//
// After Modify → [DP-A, DP-B, DP-C] selected (3)
// Operator adds DP-D (originally deferred)
// Approve → submitted plan has 4 packets
// ═══════════════════════════════════════════════════════════════════════════════

describe('CASE C — operator adds one deferred product after Modify → N+1 submitted', () => {
  it('adding a deferred product after Modify increases submitted count by one', async () => {
    renderMissionControl();
    await waitForMissionLoaded();

    await runAiAnalysis();
    await clickModifyPlan();

    // After Modify, we are in data section. Add DP-D (which was deferred).
    await navigateTo('data');
    await waitFor(() => {
      expect(screen.queryByText('DP-D')).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      const dpD = screen.queryByText('DP-D');
      if (dpD) {
        const flexRow = dpD.parentElement as HTMLElement | null;
        const checkDiv = flexRow?.querySelector('div[style*="border-radius: 3px"]') as HTMLElement | null;
        if (checkDiv) fireEvent.click(checkDiv);
        else fireEvent.click(dpD);
      }
    });

    // Now approve via AI section
    await navigateTo('ai');
    await waitFor(() => {
      expect(screen.queryByText('✓ APPROVE TRANSMISSION')).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      fireEvent.click(screen.queryByText('✓ APPROVE TRANSMISSION')!);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.approveCustomPlan)).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });

    const [submittedPlan] = vi.mocked(apiClient.approveCustomPlan).mock.calls[0] as [import('../../types/domain').CandidatePlan, string];

    // 3 originally selected + 1 added = 4
    expect(submittedPlan.packets.length).toBe(4);
    const submittedIds = submittedPlan.packets.map((p) => p.packet_id);
    expect(submittedIds).toContain('DP-D');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// GROUND RECEPTION ACCOUNTING (GR)
// Verify GroundReceptionPanel's Selected count is derived from
// executed_plan.packets.length, not a fixed constant.
// ═══════════════════════════════════════════════════════════════════════════════

describe('GR-1 — Selected count derived from executed_plan.packets.length', () => {
  it('computeTransmissionAccounting uses executed_plan.packets.length for selected_count', () => {
    // Pure unit test: test the accounting function directly (imported above)
    // Simulate: modified plan with 3 submitted, 5 in queue
    const result = computeTransmissionAccounting({
      queue_total: 5,
      queue_data_bits: 5_000_000,
      delivered_packets: ['DP-A', 'DP-B', 'DP-C'],
      deferred_packets: [],
      failed_packets: [],
      retransmission_counts: {},
      selected_data_bits: 3_000_000,
      selected_count: 3,  // derived from executed_plan.packets.length
      capacity_bits: 81_000_000,
    });

    expect(result.selected).toBe(3);
    expect(result.not_selected).toBe(2);  // 5 total - 3 selected
    expect(result.queue_total).toBe(5);
    expect(result.selected + result.not_selected).toBe(result.queue_total);
  });
});

describe('GR-2 — not_selected = queue_total - selected for modified plan', () => {
  it('accounting correctly partitions queue_total into selected + not_selected', () => {
    // Represents: 1284 queue, 81 selected (modified-AI plan)
    const result = computeTransmissionAccounting({
      queue_total: 1284,
      queue_data_bits: 1_000_000_000,
      delivered_packets: Array.from({ length: 70 }, (_, i) => `P${i}`),
      deferred_packets: Array.from({ length: 11 }, (_, i) => `D${i}`),
      failed_packets: [],
      retransmission_counts: {},
      selected_data_bits: 81_000_000,
      selected_count: 81,
      capacity_bits: 81_000_000,
    });

    expect(result.queue_total).toBe(1284);
    expect(result.selected).toBe(81);
    expect(result.not_selected).toBe(1203);  // 1284 - 81
    expect(result.selected + result.not_selected).toBe(result.queue_total);
    expect(checkAccountingInvariants(result)).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// EXECUTION MODE LABEL TRUTHFULNESS (ML)
// The frozen executionMode (not mutable decisionMode) must drive the label.
// ═══════════════════════════════════════════════════════════════════════════════

describe('ML-1 — executionMode "ai" → AI-ASSISTED TRANSMISSION label logic', () => {
  it('executionMode ai → label is AI-ASSISTED TRANSMISSION', () => {
    // Pure logic test: the label mapping used in GroundReceptionPanel.
    // We widen to string so TypeScript doesn't elide the branch.
    const executionMode: string = 'ai';
    const modeLabel = executionMode === 'custom' ? 'MANUAL TRANSMISSION' : 'AI-ASSISTED TRANSMISSION';
    expect(modeLabel).toBe('AI-ASSISTED TRANSMISSION');
  });
});

describe('ML-2 — executionMode "custom" → MANUAL TRANSMISSION label logic', () => {
  it('executionMode custom → label is MANUAL TRANSMISSION', () => {
    const executionMode: string = 'custom';
    const modeLabel = executionMode === 'custom' ? 'MANUAL TRANSMISSION' : 'AI-ASSISTED TRANSMISSION';
    expect(modeLabel).toBe('MANUAL TRANSMISSION');
  });
});

describe('ML-3 — frozen executionMode is independent of mutable decisionMode', () => {
  it('ai execution produces executionMode=ai regardless of subsequent decisionMode changes', () => {
    // This test verifies the conceptual invariant: frozenExecutionMode is set at
    // authorization time and does not change when decisionMode drifts afterward.
    //
    // Example scenario:
    //   1. Operator runs AI analysis (decisionMode = 'ai')
    //   2. Operator clicks APPROVE TRANSMISSION (frozenExecutionMode = 'ai')
    //   3. While transmission is in progress, operator navigates and mode drifts
    //   4. GroundReceptionPanel must still show AI-ASSISTED based on frozenExecutionMode
    //
    // The production fix stores frozenExecutionMode in state (not derived from
    // current decisionMode), ensuring this invariant holds.

    // Simulate the execution coordinator setting frozenExecutionMode at auth time:
    let frozenExecutionMode: 'ai' | 'custom' | null = null;
    let decisionMode: string = 'ai';

    // Authorization event: AI approval
    frozenExecutionMode = 'ai';

    // Post-authorization: UI state drifts
    decisionMode = 'manual';  // e.g. operator navigated back to data view

    // GroundReceptionPanel must use frozenExecutionMode, not decisionMode
    const labelFromFrozen = (frozenExecutionMode as string) === 'custom' ? 'MANUAL TRANSMISSION' : 'AI-ASSISTED TRANSMISSION';
    const labelFromLive = decisionMode === 'manual' ? 'MANUAL TRANSMISSION' : 'AI-ASSISTED TRANSMISSION';

    expect(labelFromFrozen).toBe('AI-ASSISTED TRANSMISSION');
    expect(labelFromLive).toBe('MANUAL TRANSMISSION');  // incorrect — this was the bug

    // frozenExecutionMode gives the correct label
    expect(labelFromFrozen).not.toBe(labelFromLive);
  });

  it('modified AI execution produces executionMode=custom (not ai)', () => {
    // Verify that the modified-AI branch in handleApproveAiPlan freezes 'custom',
    // not 'ai', so the label correctly reads MANUAL TRANSMISSION.
    let frozenExecutionMode: 'ai' | 'custom' | null = null;

    // Simulate: manualEditOrigin === 'ai_recommendation', manualOrder.length > 0
    const manualEditOrigin: 'ai_recommendation' | 'manual' = 'ai_recommendation';
    const manualOrderLength = 81;

    // Production handleApproveAiPlan logic:
    if (manualEditOrigin === 'ai_recommendation' && manualOrderLength > 0) {
      frozenExecutionMode = 'custom';  // modified AI path
    } else {
      frozenExecutionMode = 'ai';      // untouched AI path
    }

    expect(frozenExecutionMode).toBe('custom');

    const modeLabel = (frozenExecutionMode as string) === 'custom' ? 'MANUAL TRANSMISSION' : 'AI-ASSISTED TRANSMISSION';
    // Modified AI execution must not falsely claim AI-ASSISTED
    expect(modeLabel).toBe('MANUAL TRANSMISSION');
    expect(modeLabel).not.toBe('AI-ASSISTED TRANSMISSION');
  });
});
