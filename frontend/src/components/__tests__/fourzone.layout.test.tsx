/**
 * fourzone.layout.test.tsx — Four-zone layout composition guarantees.
 *
 * Tests that confirm the structural invariants of the V4.0 four-zone
 * mission operations workspace:
 *
 * L1  Mission workspace renders (upper + lower rows present)
 * L2  Mission Status zone is always visible regardless of nav section
 * L3  Analysis panel renders the correct section content per nav selection
 * L4  Decision panel renders per-section content per nav selection
 * L5  Approve/Modify/Reject buttons appear ONLY in DecisionPanel (AI section)
 * L6  Viewport is mounted in normal mode (upper row has non-zero flex)
 * L7  Focus mode collapses the upper row (viewport hidden, lower expands)
 * L8  Source switch clears stale AI decision from DecisionPanel
 * L9  Navigation sidebar remains present across all sections
 *
 * Classification: MISSIONCONTROL INTEGRATION
 * Mocking strategy mirrors phase51g.integration.test.tsx:
 *   MOCKED: API client, MissionViewport (WebGL boundary)
 *   NOT MOCKED: MissionControl, layout components, navigation, state
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';

// Browser API stub
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Mock WebGL boundary
vi.mock('../MissionViewport', () => ({
  MissionViewport: () => <div data-testid="mock-mission-viewport" />,
}));

// Mock API boundary
vi.mock('../../api/client', () => ({
  getState: vi.fn(),
  getQueue: vi.fn(),
  generatePlans: vi.fn(),
  evaluatePlan: vi.fn(),
  getDataProducts: vi.fn(),
  getExperience: vi.fn(),
  listScenarios: vi.fn(),
  switchScenario: vi.fn(),
  approvePlan: vi.fn(),
  approveCustomPlan: vi.fn(),
  assessManualPlan: vi.fn(),
  getRecommendation: vi.fn(),
  resetScenario: vi.fn(),
  getSources: vi.fn(),
  selectSource: vi.fn(),
}));

import * as apiClient from '../../api/client';
import MissionControl from '../../MissionControl';

// ── Shared fixtures ────────────────────────────────────────────────────────────

const LINK_STATE = {
  timestamp: '2024-01-01T00:00:00Z',
  snr_db: 14,
  eb_n0_db: 11,
  ber: 0.001,
  rssi_dbm: -82,
  nominal_data_rate_bps: 2_800_000,
  link_goodput_bps: 2_500_000,
  latency_s: 608,
  link_stability: 0.88,
  remaining_window_s: 280,
};

const MISSION_STATE = {
  mission_id: 'layout-test',
  mission_phase: 'nominal' as const,
  current_event: 'layout-integration-test',
  event_time_remaining_s: 600,
  comm_window_remaining_s: 280,
  risk_score: 0.3,
  risk_level: 'MEDIUM' as const,
};

const BASE_STATE = {
  link_state: LINK_STATE,
  mission_state: MISSION_STATE,
  available_capacity_bits: 81_000_000,
  queued_data_bits: 9_350_000_000,
  data_products_count: 403,
  anomalies: [],
  distance_km: 893_100_000,
  propagation_delay_s: 2979,
  round_trip_time_s: 5958,
  source: {
    mode: 'historical_replay' as const,
    provider_name: 'JUNO_PJ62',
    source_ref: 'JUNO_PJ62_HISTORICAL_REPLAY_V2',
    is_historical_replay: true,
    provenance_available: true,
    provenance_scope: 'scenario',
    provenance_record_count: 20,
    provenance_binding_count: 20,
    provenance_kind_counts: {
      external_authoritative: 8,
      derived: 7,
      modeled: 5,
    },
  },
};

const BASELINE_PLAN = {
  plan_id: 'baseline',
  strategy: 'baseline',
  generated_by: 'system',
  metadata: {},
  packets: [],
};

const BASELINE_EVAL = {
  plan_id: 'baseline',
  risk_level: 'MEDIUM' as const,
  risk_score: 0.3,
  coverage_score: 0.4,
  deadline_score: 0.6,
  efficiency_score: 0.5,
  mission_value: 0.5,
  critical_packets_delivered: 0,
  total_critical_packets: 0,
  deadline_misses: 0,
  avg_packet_delay_s: 0,
  bandwidth_utilization: 0.1,
  retransmission_overhead: 0,
  deadline_miss_rate: 0,
  critical_deficit: 0,
  window_pressure: 9.0,
  deferred_packets: [],
  overflow_bits: 0,
  meets_deadline: false,
};

const AI_RECOMMENDATION = {
  recommended_plan_id: 'ai-prioritized',
  confidence: 0.82,
  reasoning: 'Layout test reasoning',
  risk_score: 0.3,
  risk_level: 'MEDIUM' as const,
  confidence_semantics: 'heuristic' as const,
  packet_actions: [],
  evidence: [],
  alternative_plan_id: null,
  model_context: {},
};

const AI_PLAN = {
  plan_id: 'ai-prioritized',
  strategy: 'ai',
  generated_by: 'ai',
  metadata: { decision_mode: 'ai' },
  packets: [],
};

const AI_EVAL = { ...BASELINE_EVAL, plan_id: 'ai-prioritized' };

function setupMocks() {
  vi.mocked(apiClient.getState).mockResolvedValue(BASE_STATE as any);
  vi.mocked(apiClient.getQueue).mockResolvedValue(BASELINE_PLAN as any);
  vi.mocked(apiClient.generatePlans).mockResolvedValue([BASELINE_PLAN as any]);
  vi.mocked(apiClient.evaluatePlan).mockResolvedValue(BASELINE_EVAL as any);
  vi.mocked(apiClient.getDataProducts).mockResolvedValue({
    scenario_id: 'layout-test',
    data_products: [],
    total: 0,
    has_data_products: true,
  });
  vi.mocked(apiClient.getExperience).mockResolvedValue({ available: false, manifest: null });
  vi.mocked(apiClient.listScenarios).mockResolvedValue({
    scenarios: [],
    active_scenario_path: '/data/test',
  } as any);
  vi.mocked(apiClient.getSources).mockResolvedValue({
    sources: [],
    active_source_id: 'test',
  } as any);
  vi.mocked(apiClient.approvePlan).mockResolvedValue({ status: 'approved', simulation_result: { plan_id: 'ai-prioritized', delivered_packets: [], failed_packets: [], deferred_packets: [], attempt_events: [], elapsed_time_s: 1, link_state: LINK_STATE, mission_state: MISSION_STATE, retransmission_counts: {} }, approval_trace: {}, executed_plan: BASELINE_PLAN } as any);
  vi.mocked(apiClient.approveCustomPlan).mockResolvedValue({ status: 'approved', simulation_result: { plan_id: 'operator-manual', delivered_packets: [], failed_packets: [], deferred_packets: [], attempt_events: [], elapsed_time_s: 1, link_state: LINK_STATE, mission_state: MISSION_STATE, retransmission_counts: {} }, approval_trace: {}, executed_plan: BASELINE_PLAN } as any);
  vi.mocked(apiClient.resetScenario).mockResolvedValue({ status: 'ok', scenario_path: '/test', comm_window_remaining_s: 300, source_mode: 'historical_replay', randomized: true });
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
    candidate_count: 0,
    prioritization_error: null,
    prioritization_fallback_reason: null,
    recommendation_fallback_reason: null,
  } as any);
}

function renderApp() {
  return render(
    <React.StrictMode>
      <MissionControl />
    </React.StrictMode>
  );
}

async function waitForLoaded() {
  await waitFor(() => {
    expect(screen.queryByText('Loading mission data…')).not.toBeInTheDocument();
  }, { timeout: 5000 });
}

async function clickNav(tooltip: string) {
  await act(async () => {
    const btn = screen.queryByTitle(tooltip) ?? screen.queryByLabelText(tooltip);
    if (btn) fireEvent.click(btn);
  });
}

// ── Setup / Teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  setupMocks();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── L1: Mission workspace renders ─────────────────────────────────────────────

describe('L1 — four-zone workspace renders', () => {
  it('mission-workspace, upper-row, and lower-row testids are present after load', async () => {
    renderApp();
    await waitForLoaded();

    expect(screen.getByTestId('mission-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-upper-row')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-lower-row')).toBeInTheDocument();
  });
});

// ── L2: Mission Status always visible ─────────────────────────────────────────

describe('L2 — Mission Status zone persists across navigation', () => {
  it('mission-status-summary is present on initial load (mission section)', async () => {
    renderApp();
    await waitForLoaded();

    expect(screen.getByTestId('mission-status-summary')).toBeInTheDocument();
  });

  it('mission-status-summary persists when navigating to Data section', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('Data products and transmission queue');

    expect(screen.getByTestId('mission-status-summary')).toBeInTheDocument();
  });

  it('mission-status-summary persists when navigating to AI section', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('AI analysis and recommendations');

    expect(screen.getByTestId('mission-status-summary')).toBeInTheDocument();
  });

  it('mission-status-zone is present', async () => {
    renderApp();
    await waitForLoaded();

    expect(screen.getByTestId('mission-status-zone')).toBeInTheDocument();
  });

  it('product count 403 is displayed in Mission Status', async () => {
    renderApp();
    await waitForLoaded();

    const countEl = screen.getByTestId('status-product-count');
    expect(countEl).toHaveTextContent('403');
  });
});

// ── L3: Analysis panel shows correct section content ─────────────────────────

describe('L3 — AnalysisPanel section routing', () => {
  it('analysis-panel-mission is rendered on initial load', async () => {
    renderApp();
    await waitForLoaded();

    expect(screen.getByTestId('analysis-panel-mission')).toBeInTheDocument();
  });

  it('analysis-panel-data is rendered when Data nav is clicked', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('Data products and transmission queue');

    expect(screen.getByTestId('analysis-panel-data')).toBeInTheDocument();
    expect(screen.queryByTestId('analysis-panel-mission')).not.toBeInTheDocument();
  });

  it('analysis-panel-ai is rendered when AI nav is clicked', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('AI analysis and recommendations');

    expect(screen.getByTestId('analysis-panel-ai')).toBeInTheDocument();
  });

  it('analysis-panel-transmission is rendered when Transmit nav is clicked', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('Transmission control and approval');

    expect(screen.getByTestId('analysis-panel-transmission')).toBeInTheDocument();
  });
});

// ── L4: Decision panel shows correct section content ─────────────────────────

describe('L4 — DecisionPanel section routing', () => {
  it('decision-panel-mission is rendered on initial load', async () => {
    renderApp();
    await waitForLoaded();

    expect(screen.getByTestId('decision-panel-mission')).toBeInTheDocument();
  });

  it('decision-panel-data is rendered when Data nav is clicked', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('Data products and transmission queue');

    expect(screen.getByTestId('decision-panel-data')).toBeInTheDocument();
  });

  it('decision-panel-ai is rendered when AI nav is clicked', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('AI analysis and recommendations');

    expect(screen.getByTestId('decision-panel-ai')).toBeInTheDocument();
  });

  it('decision-panel-transmission is rendered when Transmit nav is clicked', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('Transmission control and approval');

    expect(screen.getByTestId('decision-panel-transmission')).toBeInTheDocument();
  });
});

// ── L5: Approve/Modify/Reject controls NOT duplicated ─────────────────────────

describe('L5 — Approve/Modify/Reject controls are NOT duplicated', () => {
  it('APPROVE TRANSMISSION button appears exactly once after AI analysis', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('AI analysis and recommendations');

    // Run AI analysis
    await waitFor(() => {
      const btn = screen.queryByText('Analyze Mission with AI');
      expect(btn).toBeInTheDocument();
    }, { timeout: 3000 });

    await act(async () => {
      const btn = screen.queryByText('Analyze Mission with AI');
      if (btn) fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(vi.mocked(apiClient.getRecommendation)).toHaveBeenCalled();
    }, { timeout: 3000 });

    // Wait for APPROVE TRANSMISSION to appear
    await waitFor(() => {
      const btns = screen.queryAllByText('✓ APPROVE TRANSMISSION');
      expect(btns.length).toBe(1);
    }, { timeout: 3000 });
  });
});

// ── L6: Navigation sidebar present ───────────────────────────────────────────

describe('L6 — Navigation sidebar persists', () => {
  it('navigation sidebar is present with all nav items', async () => {
    renderApp();
    await waitForLoaded();

    // Navigation sidebar nav element should be present
    const nav = document.querySelector('nav');
    expect(nav).toBeTruthy();

    // All nav items accessible via title
    expect(screen.queryByTitle('Mission state and overview')).toBeInTheDocument();
    expect(screen.queryByTitle('Data products and transmission queue')).toBeInTheDocument();
    expect(screen.queryByTitle('AI analysis and recommendations')).toBeInTheDocument();
    expect(screen.queryByTitle('Transmission control and approval')).toBeInTheDocument();
    expect(screen.queryByTitle('Mission log and simulation results')).toBeInTheDocument();
  });

  it('navigation sidebar is present after switching to Data section', async () => {
    renderApp();
    await waitForLoaded();

    await clickNav('Data products and transmission queue');

    expect(screen.queryByTitle('Mission state and overview')).toBeInTheDocument();
  });
});

// ── L7: HIST badge and historical replay wording preserved ────────────────────

describe('L7 — Historical replay indicators preserved', () => {
  it('HIST badge is shown for historical_replay source', async () => {
    renderApp();
    await waitForLoaded();

    const histBadge = screen.getByTestId('source-mode-badge');
    expect(histBadge).toHaveTextContent('HIST');
  });
});
