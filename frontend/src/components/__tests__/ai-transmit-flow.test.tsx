/**
 * ai-transmit-flow.test.tsx — AI transmission lifecycle gate + density removal tests
 *
 * Tests verify:
 *   T1  AI/STANDBY  — "AI Analysis Required" shown, "Analyze Mission with AI" present,
 *                     "Authorization Required" absent, clicking calls onRunAiAnalysis
 *   T2  AI/ANALYZING — analyzing state visible, button disabled, "Authorization Required" absent
 *   T3  AI/ERROR    — failure guidance shown, retry invokes onRunAiAnalysis
 *   T4  AI/STALE    — stale warning shown, re-run CTA available, no authorization
 *   T5  AI/READY    — "Authorization Required" shown, "Open AI Copilot" CTA, no dead "Go to Decision"
 *   T6  MANUAL      — manual mode unaffected (Approval bar present, no AI gate)
 *   T7  CONFIG DENSITY — density controls absent, Interface section absent,
 *                        Main Control Layout still renders, 3D toggles still render
 *
 * Classification: UNIT (component rendering)
 * Strategy: render TransmissionSection and ConfigPanel directly with controlled props.
 *           No MissionControl integration needed — the lifecycle gate lives in TransmissionSection.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

// ── Minimal component imports ─────────────────────────────────────────────────
// We exercise the real components (not mocked) — only the heavy boundaries are stubbed.

// Stub ResizableSection so it simply renders its children and title as data-testid
vi.mock('../ResizableSection', () => ({
  ResizableSection: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`section-${title.replace(/\s+/g, '-').toLowerCase()}`}>
      <span>{title}</span>
      {children}
    </div>
  ),
}));

// Stub TransmissionSummaryPanel — not under test here
vi.mock('../TransmissionSummaryPanel', () => ({
  TransmissionSummaryPanel: () => <div data-testid="transmission-summary-panel" />,
}));

// Stub ApprovalBar — not under test here
vi.mock('../ApprovalBar', () => ({
  ApprovalBar: () => <div data-testid="approval-bar" />,
}));

// Stub TransmissionOutcomeBanner
vi.mock('../TransmissionOutcomeBanner', () => ({
  TransmissionOutcomeBanner: () => <div data-testid="transmission-outcome-banner" />,
}));

// Import components AFTER vi.mock declarations
// We import the private TransmissionSection indirectly by rendering through AnalysisPanel
// but the cleanest approach is to test via AnalysisPanel with section='transmission'.
import { AnalysisPanel } from '../RightPanel';
import { ConfigPanel } from '../ConfigPanel';
import type { AiLifecycle, DecisionMode } from '../../types/domain';
import type { ViewSettings } from '../../hooks/useViewSettings';

// ── Shared fixtures ────────────────────────────────────────────────────────────

const NOOP = () => {};

const BASELINE_PLAN = {
  plan_id: 'baseline',
  strategy: 'baseline' as const,
  generated_by: 'system',
  metadata: {},
  packets: [],
};

const BASELINE_EVAL = {
  plan_id: 'baseline',
  risk_level: 'MEDIUM' as const,
  risk_score: 0.3,
  coverage_score: 0.5,
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

const MOCK_RECOMMENDATION = {
  recommended_plan_id: 'ai-plan',
  confidence: 0.8,
  reasoning: 'test',
  risk_score: 0.2,
  risk_level: 'LOW' as const,
  confidence_semantics: 'heuristic' as const,
  packet_actions: [],
  evidence: [],
  alternative_plan_id: null,
  model_context: {},
};

const VIEW_SETTINGS: ViewSettings = {
  showStarfield: true,
  showCommLink: true,
  showLabels: true,
  smoothCamera: true,
  density: 'comfortable',
};

function makeProps(overrides: {
  decisionMode?: DecisionMode;
  aiLifecycle?: AiLifecycle;
  aiError?: string | null;
  recommendation?: typeof MOCK_RECOMMENDATION | null;
  onRunAiAnalysis?: () => void;
  onNavigateSection?: (section: string) => void;
}) {
  const {
    decisionMode = 'ai',
    aiLifecycle = 'standby',
    aiError = null,
    recommendation = null,
    onRunAiAnalysis = NOOP,
    onNavigateSection = NOOP,
  } = overrides;

  return {
    section: 'transmission' as const,
    viewSettings: VIEW_SETTINGS,
    onUpdateSetting: NOOP as any,
    onResetSettings: NOOP,
    onResetPanelWidth: NOOP,
    panelWidth: 480,
    panelDefaultWidth: 480,
    workspaceMode: 'normal' as const,
    onSetWorkspaceMode: NOOP,
    linkState: null,
    missionState: null,
    distanceKm: null,
    propagationDelayS: null,
    roundTripTimeS: null,
    availableCapacityBits: 81_000_000,
    queuedDataBits: 0,
    dataProductsCount: 0,
    anomalies: [],
    queue: BASELINE_PLAN,
    recommendation,
    aiProvider: null,
    aiRequestedProvider: null,
    aiActualProvider: null,
    aiPrioritization: null,
    aiCandidateCount: null,
    aiPrioritizationError: null,
    aiPrioritizationFallbackReason: null,
    aiRecommendationFallbackReason: null,
    allPlans: [BASELINE_PLAN],
    allEvaluations: [BASELINE_EVAL],
    activePlanId: 'baseline',
    approvalPhase: 'ready' as const,
    approveResult: null,
    whatIfEvals: null,
    whatIfSnr: null,
    recPlan: null,
    recEval: null,
    activeEval: BASELINE_EVAL,
    activePlan: BASELINE_PLAN,
    riskWeights: { w_deadline_miss: 0.4, w_critical_deficit: 0.4, w_window_pressure: 0.2 },
    onApproved: NOOP,
    onTransmitting: NOOP,
    onApprovalError: NOOP,
    onWhatIfResult: NOOP as any,
    onSelectPlan: NOOP,
    decisionMode,
    onSelectDecisionMode: NOOP,
    aiLifecycle,
    aiError,
    onRunAiAnalysis,
    onNavigateSection: onNavigateSection as any,
    rawDataProducts: [],
    hasDataProducts: false,
    manualSelectedIds: new Set<string>(),
    manualOrder: [],
    manualPlan: null,
    manualEditOrigin: 'manual' as const,
    aiBaselineDeferredIds: new Set<string>(),
    onToggleManualSelect: NOOP,
    onClearManualSelection: NOOP,
    onManualReorder: NOOP,
    experienceManifest: null,
    experienceAvailable: false,
    manualAssessment: null,
    manualAssessmentLoading: false,
    manualAssessmentError: null,
    manualAssessmentStale: false,
    onManualEvaluate: NOOP,
    onManualTransmit: NOOP,
    onApproveAiPlan: NOOP,
    onModifyAiPlan: NOOP,
    onRejectAiPlan: NOOP,
    aiRecommendationRejected: false,
    sessionEvents: [],
    choreographyActive: false,
    pendingExecutionPlan: null,
    executionId: null,
    authorizedAtMs: null,
    playbackStartedAtMs: null,
    onSetPlaybackStarted: NOOP,
    onExecuteApproval: NOOP as any,
    onChoreographyComplete: NOOP,
    onChoreographyError: NOOP,
    onAttemptPulse: NOOP,
    onChoreographyPhaseChange: NOOP,
    presentationPhase: 'plan_uplink' as const,
    executionResult: null,
  };
}

// ── T1: AI/STANDBY ────────────────────────────────────────────────────────────

describe('T1 — AI / standby', () => {
  it('shows "AI Analysis Required" heading', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'standby' })));
    expect(screen.getByText('AI Analysis Required')).toBeInTheDocument();
  });

  it('shows "Analyze Mission with AI" button', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'standby' })));
    expect(screen.getByText('Analyze Mission with AI')).toBeInTheDocument();
  });

  it('does NOT show "Authorization Required"', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'standby' })));
    expect(screen.queryByText('Authorization Required')).not.toBeInTheDocument();
  });

  it('clicking "Analyze Mission with AI" calls onRunAiAnalysis exactly once', () => {
    const spy = vi.fn();
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'standby', onRunAiAnalysis: spy })));
    fireEvent.click(screen.getByText('Analyze Mission with AI'));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('no approval/transmission is triggered when clicking Analyze', () => {
    const approveSpy = vi.fn();
    const transmitSpy = vi.fn();
    const props = makeProps({ decisionMode: 'ai', aiLifecycle: 'standby' });
    render(React.createElement(AnalysisPanel, { ...props, onApproveAiPlan: approveSpy, onManualTransmit: transmitSpy }));
    fireEvent.click(screen.getByText('Analyze Mission with AI'));
    expect(approveSpy).not.toHaveBeenCalled();
    expect(transmitSpy).not.toHaveBeenCalled();
  });
});

// ── T2: AI/ANALYZING ─────────────────────────────────────────────────────────

describe('T2 — AI / analyzing', () => {
  it('shows analyzing state heading', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'analyzing' })));
    expect(screen.getByText('AI Analysis In Progress')).toBeInTheDocument();
  });

  it('the analyzing button is disabled', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'analyzing' })));
    const btn = screen.getAllByText('Analyzing…').find(el => el.tagName === 'BUTTON') as HTMLButtonElement;
    expect(btn).toBeDefined();
    expect(btn.disabled).toBe(true);
  });

  it('does NOT show "Authorization Required"', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'analyzing' })));
    expect(screen.queryByText('Authorization Required')).not.toBeInTheDocument();
  });
});

// ── T3: AI/ERROR ─────────────────────────────────────────────────────────────

describe('T3 — AI / error', () => {
  it('shows "AI Analysis Failed" heading', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'error' })));
    expect(screen.getByText('AI Analysis Failed')).toBeInTheDocument();
  });

  it('shows "Retry AI Analysis" button', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'error' })));
    expect(screen.getByText('Retry AI Analysis')).toBeInTheDocument();
  });

  it('surfaces aiError message when provided', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'error', aiError: 'Connection timeout' })));
    expect(screen.getByText('Connection timeout')).toBeInTheDocument();
  });

  it('clicking Retry calls onRunAiAnalysis', () => {
    const spy = vi.fn();
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'error', onRunAiAnalysis: spy })));
    fireEvent.click(screen.getByText('Retry AI Analysis'));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('does NOT show "Authorization Required"', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'error' })));
    expect(screen.queryByText('Authorization Required')).not.toBeInTheDocument();
  });
});

// ── T4: AI/STALE ─────────────────────────────────────────────────────────────

describe('T4 — AI / stale', () => {
  it('shows stale analysis heading', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'stale' })));
    expect(screen.getByText('AI Analysis Stale')).toBeInTheDocument();
  });

  it('shows "Re-run AI Analysis" CTA', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'stale' })));
    expect(screen.getByText('Re-run AI Analysis')).toBeInTheDocument();
  });

  it('clicking Re-run calls onRunAiAnalysis', () => {
    const spy = vi.fn();
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'stale', onRunAiAnalysis: spy })));
    fireEvent.click(screen.getByText('Re-run AI Analysis'));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('does NOT show "Authorization Required"', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'stale' })));
    expect(screen.queryByText('Authorization Required')).not.toBeInTheDocument();
  });
});

// ── T5: AI/READY ─────────────────────────────────────────────────────────────

describe('T5 — AI / ready', () => {
  it('shows "Authorization Required" heading', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'ready', recommendation: MOCK_RECOMMENDATION })));
    expect(screen.getByText('Authorization Required')).toBeInTheDocument();
  });

  it('shows "Open AI Copilot" CTA', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'ready', recommendation: MOCK_RECOMMENDATION })));
    expect(screen.getByText('Open AI Copilot')).toBeInTheDocument();
  });

  it('does NOT show dead "Go to Decision" control', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'ready', recommendation: MOCK_RECOMMENDATION })));
    expect(screen.queryByText('Go to Decision')).not.toBeInTheDocument();
  });

  it('clicking "Open AI Copilot" calls onNavigateSection with "ai"', () => {
    const spy = vi.fn();
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'ai', aiLifecycle: 'ready', recommendation: MOCK_RECOMMENDATION, onNavigateSection: spy })));
    fireEvent.click(screen.getByText('Open AI Copilot'));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith('ai');
  });
});

// ── T6: MANUAL mode regression ───────────────────────────────────────────────

describe('T6 — manual mode regression', () => {
  it('does NOT show AI gate sections in manual mode', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'manual', aiLifecycle: 'standby' })));
    expect(screen.queryByText('AI Analysis Required')).not.toBeInTheDocument();
    expect(screen.queryByText('Authorization Required')).not.toBeInTheDocument();
  });

  it('Approval section renders in manual mode', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'manual', aiLifecycle: 'standby' })));
    expect(screen.getByText('Approval')).toBeInTheDocument();
  });

  it('ApprovalBar is present in manual mode', () => {
    render(React.createElement(AnalysisPanel, makeProps({ decisionMode: 'manual', aiLifecycle: 'standby' })));
    expect(screen.getByTestId('approval-bar')).toBeInTheDocument();
  });
});

// ── T7: Config Density removed ───────────────────────────────────────────────

describe('T7 — ConfigPanel density controls removed', () => {
  function renderConfig() {
    return render(
      React.createElement(ConfigPanel, {
        settings: VIEW_SETTINGS,
        onUpdate: NOOP as any,
        onResetSettings: NOOP,
        onResetPanelWidth: NOOP,
        panelWidth: 480,
        panelDefaultWidth: 480,
      })
    );
  }

  it('does NOT render "Density" label', () => {
    renderConfig();
    expect(screen.queryByText('Density')).not.toBeInTheDocument();
  });

  it('does NOT render "Compact" button', () => {
    renderConfig();
    expect(screen.queryByText('Compact')).not.toBeInTheDocument();
  });

  it('does NOT render "Comfortable" button', () => {
    renderConfig();
    expect(screen.queryByText('Comfortable')).not.toBeInTheDocument();
  });

  it('does NOT render empty "Interface" section heading', () => {
    renderConfig();
    expect(screen.queryByText('Interface')).not.toBeInTheDocument();
  });

  it('still renders "Main Control Layout" section', () => {
    renderConfig();
    expect(screen.getByText('Main Control Layout')).toBeInTheDocument();
  });

  it('still renders 3D View section', () => {
    renderConfig();
    expect(screen.getByText('3D View')).toBeInTheDocument();
  });

  it('Starfield toggle still renders', () => {
    renderConfig();
    expect(screen.getByText('Starfield')).toBeInTheDocument();
  });

  it('Communication link toggle still renders', () => {
    renderConfig();
    expect(screen.getByText('Communication link')).toBeInTheDocument();
  });
});
