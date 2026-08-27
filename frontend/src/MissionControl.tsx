/**
 * MissionControl — GCSI V3.5 primary layout.
 *
 * V3.5 changes:
 * - Adaptive workspace system: normal | expanded | focus
 * - Focus mode hides 3D viewport; full panel workspace
 * - Expanded mode: ~58vw panel, 3D still visible
 * - Keyboard shortcuts: Ctrl+Shift+F (toggle focus), Esc (exit focus)
 * - Workspace mode persists across navigation; reset does NOT change mode
 * - workspaceMode stored in localStorage
 */
import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import {
  getState,
  getQueue,
  getRecommendation,
  generatePlans,
  evaluatePlan,
  resetScenario,
  getDataProducts,
  listScenarios,
  switchScenario,
  getExperience,
  assessManualPlan,
  approvePlan,
  approveCustomPlan,
} from './api/client';
import type {
  AIRecommendation,
  AiLifecycle,
  AnomalyEvent,
  ApproveResponse,
  CandidatePlan,
  CandidatePrioritization,
  DataProduct,
  DecisionMode,
  EvaluationResult,
  LinkState,
  MissionState,
  ScenarioInfo,
  WhatIfEvalResponse,
} from './types/domain';
import type { ExperienceManifest } from './types/experience';
import type { ApprovalPhase } from './components/ApprovalBar';
import { NavigationSidebar, type NavSection } from './components/NavigationSidebar';
import { MissionViewport } from './components/MissionViewport';
import { RightPanel } from './components/RightPanel';
import { useResizablePanel } from './hooks/useResizablePanel';
import { useViewSettings } from './hooks/useViewSettings';
import type { ManualAssessmentResult, SessionEvent } from './experience/missionExperienceReducer';

// ── Workspace mode ─────────────────────────────────────────────────────────────

export type WorkspaceMode = 'normal' | 'expanded' | 'focus';

const WORKSPACE_MODE_KEY = 'GCSI_WORKSPACE_MODE_v1';

function loadWorkspaceMode(): WorkspaceMode {
  try {
    const raw = localStorage.getItem(WORKSPACE_MODE_KEY) as WorkspaceMode | null;
    // Always start normal after a fresh session (don't trap in focus on reload)
    if (raw === 'expanded') return 'expanded';
    return 'normal';
  } catch {
    return 'normal';
  }
}

function saveWorkspaceMode(mode: WorkspaceMode) {
  try { localStorage.setItem(WORKSPACE_MODE_KEY, mode); } catch { /* ignore */ }
}

// ── Global styles ─────────────────────────────────────────────────────────────

const styles = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body, #root {
    height: 100%;
    overflow: hidden;
  }
  body {
    font-family: 'IBM Plex Sans', system-ui, sans-serif;
    background: #080B11;
    color: #E6EBF2;
    font-size: 13px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  #root {
    display: flex;
    flex-direction: column;
  }
  /* Subtle scrollbars */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(76,141,255,0.18); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(76,141,255,0.32); }

  /* ── V3.3 Gray + Blue design tokens ── */
  :root {
    --panel:        #121822;
    --panel-alt:    #161D28;
    --panel-bg:     rgba(18,24,34,0.7);
    --panel-border: rgba(46,58,79,0.8);
    --panel-radius: 10px;
    --border:       rgba(46,58,79,0.8);
    --border-strong: rgba(53,67,90,0.9);
    --text:       #E6EBF2;
    --text-muted: #93A0B4;
    --text-dim:   #4A5770;
    --signal:   #34d399;
    --warn:     #f59e0b;
    --critical: #f87171;
    --ai:       #6EA8FF;
    --font-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', monospace;
    --font-sans: 'IBM Plex Sans', system-ui, sans-serif;
    --risk-low-bg:       rgba(52,211,153,0.10);
    --risk-low-color:    #34d399;
    --risk-low-border:   rgba(52,211,153,0.28);
    --risk-low-glow:     none;
    --risk-medium-bg:    rgba(245,158,11,0.10);
    --risk-medium-color: #f59e0b;
    --risk-medium-border: rgba(245,158,11,0.30);
    --risk-medium-glow:  none;
    --risk-high-bg:      rgba(251,146,60,0.10);
    --risk-high-color:   #fb923c;
    --risk-high-border:  rgba(251,146,60,0.32);
    --risk-high-glow:    none;
    --risk-critical-bg:      rgba(248,113,113,0.10);
    --risk-critical-color:   #f87171;
    --risk-critical-border:  rgba(248,113,113,0.35);
    --risk-critical-glow:    none;
    --btn-primary-bg:    #4C8DFF;
    --btn-primary-color: #ffffff;
    --btn-primary-glow:  none;
    --tab-active-glow:   none;
    --ai-panel-border:   rgba(76,141,255,0.22);
    --ai-panel-glow:     none;
    --bg: #080B11;
  }

  /* ── Panel base ── */
  .panel {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: var(--panel-radius);
    padding: 14px 16px;
    margin-bottom: 10px;
    min-width: 0;
    box-sizing: border-box;
    overflow-x: hidden;
  }
  .panel h2 {
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: none;
    letter-spacing: 0.01em;
    margin-bottom: 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 8px;
  }
  .panel h3 {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--text-muted);
    font-weight: 500;
    letter-spacing: 0.01em;
    margin: 12px 0 6px;
  }
  .panel p {
    margin-bottom: 8px;
    line-height: 1.6;
    font-size: 12.5px;
    color: var(--text-muted);
  }
  .waveform-wrap {
    background: rgba(0,0,0,0.2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    margin-bottom: 12px;
  }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td {
    padding: 6px 8px;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }
  th {
    color: var(--text-dim);
    font-weight: 500;
    font-size: 10px;
    letter-spacing: 0.03em;
    font-family: var(--font-sans);
    text-transform: none;
  }
  td { font-family: var(--font-mono); font-size: 12px; }
  td:first-child { font-family: var(--font-sans); color: var(--text-muted); font-size: 12px; }
  code {
    background: rgba(129,140,248,0.10);
    color: var(--ai);
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
    font-family: var(--font-mono);
  }
  .ai-hero {
    border-color: var(--ai-panel-border);
    background: rgba(8,12,20,0.96);
  }
  .ai-hero h2 { color: var(--ai); border-bottom-color: rgba(129,140,248,0.15); }
  .approval-bar {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: var(--panel-radius);
    padding: 14px 16px;
  }

  /* ── V3.5: workspace transitions ── */
  .workspace-right-panel {
    transition: width 0.25s cubic-bezier(0.4,0,0.2,1);
  }
  .workspace-viewport {
    transition: flex 0.25s cubic-bezier(0.4,0,0.2,1), opacity 0.2s ease;
  }
  .workspace-divider {
    transition: opacity 0.2s ease;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
`;

export default function MissionControl() {
  // ── Resize + settings hooks ────────────────────────────────────────────────
  const { width: panelWidth, handleMouseDown: handleDividerMouseDown, resetWidth: resetPanelWidth, DEFAULT_WIDTH } = useResizablePanel();
  const { settings: viewSettings, update: updateViewSetting, resetSettings } = useViewSettings();

  // ── V3.5: Workspace mode ───────────────────────────────────────────────────
  const [workspaceMode, setWorkspaceModeRaw] = useState<WorkspaceMode>(loadWorkspaceMode);

  const setWorkspaceMode = useCallback((mode: WorkspaceMode) => {
    setWorkspaceModeRaw(mode);
    saveWorkspaceMode(mode);
  }, []);

  const toggleFocus = useCallback(() => {
    setWorkspaceMode(workspaceMode === 'focus' ? 'normal' : 'focus');
  }, [workspaceMode, setWorkspaceMode]);

  // ── V3.5: Keyboard shortcuts ───────────────────────────────────────────────
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Esc: exit focus mode (if not in a modal/input context)
      if (e.key === 'Escape') {
        const active = document.activeElement;
        const isInput = active instanceof HTMLInputElement ||
                        active instanceof HTMLTextAreaElement ||
                        active instanceof HTMLSelectElement;
        if (!isInput && workspaceMode === 'focus') {
          setWorkspaceMode('normal');
        }
        return;
      }
      // Ctrl+Shift+F: toggle focus mode (avoid when typing)
      if (e.ctrlKey && e.shiftKey && e.key === 'F') {
        const active = document.activeElement;
        const isInput = active instanceof HTMLInputElement ||
                        active instanceof HTMLTextAreaElement ||
                        active instanceof HTMLSelectElement;
        if (!isInput) {
          e.preventDefault();
          toggleFocus();
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [workspaceMode, setWorkspaceMode, toggleFocus]);

  // ── Mission state ──────────────────────────────────────────────────────────
  const [linkState, setLinkState] = useState<LinkState | null>(null);
  const [missionState, setMissionState] = useState<MissionState | null>(null);
  const [availableCapacityBits, setAvailableCapacityBits] = useState<number>(0);
  const [queuedDataBits, setQueuedDataBits] = useState<number>(0);
  const [dataProductsCount, setDataProductsCount] = useState<number>(0);
  const [anomalies, setAnomalies] = useState<AnomalyEvent[]>([]);
  const [distanceKm, setDistanceKm] = useState<number | null>(null);
  const [propagationDelayS, setPropagationDelayS] = useState<number | null>(null);
  const [roundTripTimeS, setRoundTripTimeS] = useState<number | null>(null);
  const [queue, setQueue] = useState<CandidatePlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);
  const [approvalPhase, setApprovalPhase] = useState<ApprovalPhase>('idle');
  const [allPlans, setAllPlans] = useState<CandidatePlan[]>([]);
  const [allEvaluations, setAllEvaluations] = useState<EvaluationResult[]>([]);
  const [activePlanId, setActivePlanId] = useState<string>('baseline');
  const [whatIfEvals, setWhatIfEvals] = useState<EvaluationResult[] | null>(null);
  const [whatIfSnr, setWhatIfSnr] = useState<number | null>(null);
  const totalWindowRef = useRef<number | null>(null);

  // ── V3.4: Raw data products ────────────────────────────────────────────────
  const [rawDataProducts, setRawDataProducts] = useState<DataProduct[]>([]);
  const [hasDataProducts, setHasDataProducts] = useState<boolean>(false);

  // ── V3.4: Scenario management ──────────────────────────────────────────────
  const [availableScenarios, setAvailableScenarios] = useState<ScenarioInfo[]>([]);
  const [activeScenarioPath, setActiveScenarioPath] = useState<string | null>(null);
  const [scenarioSwitching, setScenarioSwitching] = useState(false);

  // ── V3.4: Decision mode ────────────────────────────────────────────────────
  const [decisionMode, setDecisionMode] = useState<DecisionMode>('unselected');

  // ── V3.4: AI lifecycle ─────────────────────────────────────────────────────
  const [aiLifecycle, setAiLifecycle] = useState<AiLifecycle>('standby');
  const [aiError, setAiError] = useState<string | null>(null);
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
  const [aiProvider, setAiProvider] = useState<string | null>(null);
  const [aiRequestedProvider, setAiRequestedProvider] = useState<string | null>(null);
  const [aiActualProvider, setAiActualProvider] = useState<string | null>(null);
  const [aiPrioritization, setAiPrioritization] = useState<CandidatePrioritization | null>(null);
  const [aiCandidateCount, setAiCandidateCount] = useState<number | null>(null);
  const [aiPrioritizationError, setAiPrioritizationError] = useState<string | null>(null);
  const [aiPrioritizationFallbackReason, setAiPrioritizationFallbackReason] = useState<string | null>(null);
  const [aiRecommendationFallbackReason, setAiRecommendationFallbackReason] = useState<string | null>(null);
  const aiRequestInFlight = useRef(false);

  // ── V3.4: Manual mode state ────────────────────────────────────────────────
  // manualOrder is the SINGLE SOURCE OF TRUTH for manual selection.
  // manualSelectedIds is a pure derivation — never mutated independently.
  // Invariant: new Set(manualOrder).size === manualOrder.length (always unique).
  const [manualOrder, setManualOrder] = useState<string[]>([]);
  const manualSelectedIds = useMemo(() => new Set(manualOrder), [manualOrder]);

  // ── Phase 5.1D: Application-level execution coordinator ──────────────────
  // This state lives at MissionControl level so it survives navigation.
  // Each executionId maps to AT MOST ONE backend approval request (ever).
  //
  // executionPromiseRef is a Map<id, Promise>. Once an entry exists for an id,
  // NO second dispatch is ever made for that id.
  // Navigation / remount / StrictMode double-effects cannot call /approve again.

  /** Monotonic counter to generate unique execution IDs. */
  const executionCounter = useRef(0);

  /** Current execution ID. Null when idle. */
  const [executionId, setExecutionId] = useState<string | null>(null);

  /** The Promise for the in-flight or completed approval request. Keyed by executionId. */
  const executionPromiseRef = useRef<Map<string, Promise<ApproveResponse>>>(new Map());

  /** Wall-clock ms at which the current execution's visual playback began. */
  const [playbackStartedAtMs, setPlaybackStartedAtMs] = useState<number | null>(null);

  // ── Phase 4.2F4: Transmission choreography ────────────────────────────────
  /** When true, TransmissionSequencePanel is active in TransmissionSection. */
  const [choreographyActive, setChoreographyActive] = useState<boolean>(false);
  /** Plan to execute when backend approval is called during choreography. */
  const [pendingExecutionPlan, setPendingExecutionPlan] = useState<CandidatePlan | null>(null);
  /** Whether to use /approve (ai) or /approve/custom (manual/modified). */
  const [pendingExecutionMode, setPendingExecutionMode] = useState<'ai' | 'custom'>('custom');

  // ── Phase 4.2F: Experience manifest ───────────────────────────────────────
  const [experienceManifest, setExperienceManifest] = useState<ExperienceManifest | null>(null);
  const [experienceAvailable, setExperienceAvailable] = useState<boolean>(false);
  const [_experienceLoading, setExperienceLoading] = useState<boolean>(false);

  // ── Phase 4.2F: Manual assessment ─────────────────────────────────────────
  const [manualAssessment, setManualAssessment] = useState<ManualAssessmentResult | null>(null);
  const [manualAssessmentLoading, setManualAssessmentLoading] = useState<boolean>(false);
  const [manualAssessmentError, setManualAssessmentError] = useState<string | null>(null);
  const [manualAssessmentStale, setManualAssessmentStale] = useState<boolean>(false);
  const [_manualAssessmentOrderFingerprint, setManualAssessmentOrderFingerprint] = useState<string | null>(null);

  // ── Phase 4.2F5: Session event log ────────────────────────────────────────
  const [sessionEvents, setSessionEvents] = useState<SessionEvent[]>([]);
  let _sessionEventCounter = useRef(0);

  const addSessionEvent = useCallback((type: SessionEvent['type'], detail?: string) => {
    setSessionEvents((prev) => [
      ...prev,
      {
        id: `ev-${++_sessionEventCounter.current}`,
        timestamp: Date.now(),
        type,
        detail,
      },
    ]);
  }, []);

  // ── Navigation ─────────────────────────────────────────────────────────────
  const [activeSection, setActiveSection] = useState<NavSection>('mission');

  // ── Phase 4.2F: Load experience manifest ──────────────────────────────────
  const loadExperience = useCallback(async () => {
    setExperienceLoading(true);
    try {
      const resp = await getExperience();
      setExperienceAvailable(resp.available);
      setExperienceManifest(resp.manifest);
    } catch {
      setExperienceAvailable(false);
      setExperienceManifest(null);
    } finally {
      setExperienceLoading(false);
    }
  }, []);

  // ── Phase 4.2F: Manual plan assessment ────────────────────────────────────
  const handleManualEvaluate = useCallback(async () => {
    if (manualOrder.length === 0) return;
    // Pre-flight invariant check — must be unique before sending to backend
    const seen = new Set<string>();
    const dupes = manualOrder.filter((id) => seen.has(id) || !seen.add(id));
    if (dupes.length > 0) {
      setManualAssessmentError(`MANUAL PLAN STATE INVALID: Duplicate product ID(s): ${[...new Set(dupes)].join(', ')}`);
      return;
    }
    const fingerprint = manualOrder.join(',');
    setManualAssessmentLoading(true);
    setManualAssessmentError(null);
    setManualAssessmentOrderFingerprint(fingerprint);
    try {
      const resp = await assessManualPlan(manualOrder);
      const result: ManualAssessmentResult = {
        plan: resp.plan,
        evaluation: resp.evaluation,
        mission_outcome: resp.mission_outcome,
        capacity_summary: resp.capacity_summary,
        orderFingerprint: fingerprint,
      };
      setManualAssessment(result);
      setManualAssessmentStale(false);
      addSessionEvent('manual_plan_assessed', `${manualOrder.length} products`);
    } catch (err) {
      setManualAssessmentError(String(err));
    } finally {
      setManualAssessmentLoading(false);
    }
  }, [manualOrder, addSessionEvent]);

  // ── V3.4: Load mission data — NO AI ───────────────────────────────────────
  const loadMissionData = useCallback(async (markStale = false) => {
    setLoading(true);
    setError(null);
    setApproveResult(null);
    setWhatIfEvals(null);
    setWhatIfSnr(null);
    setApprovalPhase('idle');
    try {
      const [stateData, queueData] = await Promise.all([getState(), getQueue()]);
      setLinkState(stateData.link_state);
      setMissionState(stateData.mission_state);
      setAvailableCapacityBits(stateData.available_capacity_bits ?? 0);
      setQueuedDataBits(stateData.queued_data_bits ?? 0);
      setDataProductsCount(stateData.data_products_count ?? 0);
      setAnomalies(stateData.anomalies ?? []);
      setDistanceKm(stateData.distance_km ?? null);
      setPropagationDelayS(stateData.propagation_delay_s ?? null);
      setRoundTripTimeS(stateData.round_trip_time_s ?? null);
      setQueue(queueData);
      if (totalWindowRef.current === null) {
        totalWindowRef.current = stateData.mission_state.comm_window_remaining_s;
      }
      try {
        const plans = await generatePlans();
        // Plans are just the 4 deterministic baselines — AI plan is excluded.
        // If the AI was previously run, its plan entry is removed from the list
        // here; it will be re-added after the operator runs AI analysis again.
        setAllPlans(plans);
        const evals = await Promise.all(plans.map((p) => evaluatePlan(p)));
        setAllEvaluations(evals);
        setActivePlanId(plans[0]?.plan_id ?? 'baseline');
      } catch {
        setAllPlans([]);
        setAllEvaluations([]);
      }
      try {
        const dpResp = await getDataProducts();
        setRawDataProducts(dpResp.data_products);
        setHasDataProducts(dpResp.has_data_products);
      } catch {
        setRawDataProducts([]);
        setHasDataProducts(stateData.data_products_count > 0);
      }
      if (markStale) {
        // Mark AI as stale and clear the previous AI plan so stale plan data
        // does not remain in the plan list while awaiting re-analysis.
        setAiLifecycle((lc) => lc === 'ready' ? 'stale' : lc);
        // Remove ai-prioritized entries from the lists — they will be re-added
        // after the operator explicitly runs AI analysis again.
        setAllPlans((prev) => prev.filter((p) => p.plan_id !== 'ai-prioritized'));
        setAllEvaluations((prev) => prev.filter((e) => e.plan_id !== 'ai-prioritized'));
        setRecommendation(null);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // ── V3.4: Refresh — mission data only, never AI ────────────────────────────
  const refresh = useCallback(async () => {
    await loadMissionData(true);
    await loadExperience();  // re-fetch experience on refresh (no intro replay restart)
  }, [loadMissionData, loadExperience]);

  // ── Phase 4.2F: Clear manual-specific state ───────────────────────────────
  const clearManualAssessmentState = useCallback(() => {
    setManualAssessment(null);
    setManualAssessmentLoading(false);
    setManualAssessmentError(null);
    setManualAssessmentStale(false);
    setManualAssessmentOrderFingerprint(null);
  }, []);

  // ── V3.4: Reset scenario ──────────────────────────────────────────────────
  const handleReset = useCallback(async () => {
    setResetting(true);
    setError(null);
    setDecisionMode('unselected');
    setAiLifecycle('standby');
    setAiError(null);
    setRecommendation(null);
    setAiProvider(null);
    setAiRequestedProvider(null);
    setAiActualProvider(null);
    setAiPrioritization(null);
    setAiCandidateCount(null);
    setAiPrioritizationError(null);
    setAiPrioritizationFallbackReason(null);
    setAiRecommendationFallbackReason(null);
    // Remove ai-prioritized plan from the list on reset
    setAllPlans((prev) => prev.filter((p) => p.plan_id !== 'ai-prioritized'));
    setAllEvaluations((prev) => prev.filter((e) => e.plan_id !== 'ai-prioritized'));
    setManualOrder([]);
    clearManualAssessmentState();
    setAiRecommendationRejected(false);
    setChoreographyActive(false);
    setPendingExecutionPlan(null);
    // Reset execution coordinator
    setExecutionId(null);
    setPlaybackStartedAtMs(null);
    executionPromiseRef.current.clear();
    setSessionEvents([]);
    aiRequestInFlight.current = false;
    // V3.5: workspace mode is NOT reset on mission reset
    try {
      await resetScenario();
      totalWindowRef.current = null;
    } catch { /* ignore */ }
    finally {
      setResetting(false);
    }
    await loadMissionData(false);
    await loadExperience();  // re-fetch experience manifest after reset
  }, [loadMissionData, loadExperience, clearManualAssessmentState]);

  // ── V3.4: Initial load — NO AI ────────────────────────────────────────────
  useEffect(() => {
    const init = async () => {
      setLoading(true);
      try {
        const scenList = await listScenarios();
        setAvailableScenarios(scenList.scenarios);
        setActiveScenarioPath(scenList.active_scenario_path);
      } catch { /* informational */ }
      await loadMissionData(false);
      await loadExperience();  // load experience on initial page load
    };
    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── V3.4: Scenario switch ─────────────────────────────────────────────────
  const handleSwitchScenario = useCallback(async (filename: string) => {
    setScenarioSwitching(true);
    setDecisionMode('unselected');
    setAiLifecycle('standby');
    setAiError(null);
    setRecommendation(null);
    setAiProvider(null);
    setAiRequestedProvider(null);
    setAiActualProvider(null);
    setAiPrioritization(null);
    setAiCandidateCount(null);
    setAiPrioritizationError(null);
    setAiPrioritizationFallbackReason(null);
    setAiRecommendationFallbackReason(null);
    // Remove ai-prioritized plan from the list on scenario switch
    setAllPlans((prev) => prev.filter((p) => p.plan_id !== 'ai-prioritized'));
    setAllEvaluations((prev) => prev.filter((e) => e.plan_id !== 'ai-prioritized'));
    setManualOrder([]);
    clearManualAssessmentState();
    setAiRecommendationRejected(false);
    setChoreographyActive(false);
    setPendingExecutionPlan(null);
    // Reset execution coordinator
    setExecutionId(null);
    setPlaybackStartedAtMs(null);
    executionPromiseRef.current.clear();
    // Clear scenario-specific experience state on switch
    setExperienceManifest(null);
    setExperienceAvailable(false);
    aiRequestInFlight.current = false;
    totalWindowRef.current = null;
    try {
      await switchScenario(filename);
      const scenList = await listScenarios();
      setAvailableScenarios(scenList.scenarios);
      setActiveScenarioPath(scenList.active_scenario_path);
      await loadMissionData(false);
      await loadExperience();  // load new scenario's experience (may be unavailable)
    } catch (err) {
      setError(`Failed to switch scenario: ${err}`);
    } finally {
      setScenarioSwitching(false);
    }
  }, [loadMissionData, loadExperience, clearManualAssessmentState]);

  // ── V3.4: Explicit AI analysis — ONLY called by operator action ───────────
  const runAiAnalysis = useCallback(async () => {
    if (aiRequestInFlight.current) return;
    aiRequestInFlight.current = true;
    setAiLifecycle('analyzing');
    setAiError(null);
    setRecommendation(null);
    setAiPrioritization(null);
    setAiCandidateCount(null);
    setAiPrioritizationError(null);
    setAiPrioritizationFallbackReason(null);
    setAiRecommendationFallbackReason(null);
    addSessionEvent('ai_analysis_requested');
    // Clear stale AI plan before re-analysis so the old plan is never kept
    // when a new analysis produces a different ranking.
    setAllPlans((prev) => prev.filter((p) => p.plan_id !== 'ai-prioritized'));
    setAllEvaluations((prev) => prev.filter((e) => e.plan_id !== 'ai-prioritized'));
    if (allPlans.filter((p) => p.plan_id !== 'ai-prioritized').length === 0) {
      try {
        const plans = await generatePlans();
        setAllPlans(plans);
        const evals = await Promise.all(plans.map((p) => evaluatePlan(p)));
        setAllEvaluations(evals);
      } catch { /* use existing */ }
    }
    try {
      const resp = await getRecommendation();
      setRecommendation(resp.recommendation);
      // Prefer actual_provider for display; fall back to provider for backwards compat.
      setAiProvider(resp.actual_provider ?? resp.provider);
      setAiRequestedProvider(resp.requested_provider ?? resp.provider);
      setAiActualProvider(resp.actual_provider ?? resp.provider);
      setAiPrioritization(resp.prioritization ?? null);
      setAiCandidateCount(resp.candidate_count ?? null);
      setAiPrioritizationError(resp.prioritization_error ?? null);
      setAiPrioritizationFallbackReason(resp.prioritization_fallback_reason ?? null);
      setAiRecommendationFallbackReason(resp.recommendation_fallback_reason ?? null);
      // Merge ai-prioritized plan/evaluation into state (v2/v3 path).
      // Deduplication: the stale entry was already removed above; just append.
      if (resp.ai_plan) {
        setAllPlans((prev) => {
          const withoutAi = prev.filter((p) => p.plan_id !== resp.ai_plan!.plan_id);
          return [...withoutAi, resp.ai_plan!];
        });
      }
      if (resp.ai_evaluation) {
        setAllEvaluations((prev) => {
          const withoutAi = prev.filter((e) => e.plan_id !== resp.ai_evaluation!.plan_id);
          return [...withoutAi, resp.ai_evaluation!];
        });
      }
      setAiLifecycle('ready');
      setApprovalPhase('ready');
      addSessionEvent('ai_analysis_completed', resp.actual_provider ?? resp.provider ?? undefined);
    } catch (err) {
      setAiLifecycle('error');
      setAiError(String(err));
    } finally {
      aiRequestInFlight.current = false;
    }
  }, [allPlans, addSessionEvent]);

  // ── Approval handlers ──────────────────────────────────────────────────────

  function handleApproved(result: ApproveResponse) {
    setApproveResult(result);
    setLinkState(result.simulation_result.link_state);
    setMissionState(result.simulation_result.mission_state);
    setApprovalPhase('complete');
    addSessionEvent('ground_reception_completed', `delivered=${result.simulation_result.delivered_packets.length}`);
    setActiveSection('log');
  }

  function handleApprovalError() {
    setApprovalPhase('ready');
  }

  function handleWhatIfResult(result: WhatIfEvalResponse, snrDb: number) {
    if (result.evaluations.length === 0) {
      setWhatIfEvals(null);
      setWhatIfSnr(null);
    } else {
      setWhatIfEvals(result.evaluations);
      setWhatIfSnr(snrDb);
    }
  }

  // ── V3.4: Manual selection helpers ───────────────────────────────────────

  /**
   * Assert the uniqueness invariant. Throws in dev if violated.
   * Returns false in production so callers can show a user-facing error.
   */
  function assertUniqueProductOrder(order: string[], context: string): boolean {
    const unique = new Set(order);
    if (unique.size !== order.length) {
      const seen = new Set<string>();
      const dupes = order.filter((id) => seen.has(id) || !seen.add(id));
      const msg = `[GCSI] Manual plan invariant violation in ${context}: duplicate IDs [${[...new Set(dupes)].join(', ')}]`;
      console.error(msg);
      return false;
    }
    return true;
  }

  /**
   * Single, StrictMode-safe toggle handler.
   * manualOrder is mutated atomically — no nested setState calls.
   */
  function handleToggleManualSelect(productId: string) {
    setManualOrder((prev) => {
      let next: string[];
      if (prev.includes(productId)) {
        // Deselect: remove ALL occurrences (guard against prior corruption)
        next = prev.filter((id) => id !== productId);
      } else {
        // Select: append — but only if not already present (idempotent)
        if (prev.includes(productId)) return prev;
        next = [...prev, productId];
      }
      // Invalidate assessment when selection changes (side-effect via timeout
      // to avoid setState-inside-setState; MissionControl is mounted at all times)
      if (manualAssessment !== null) {
        setTimeout(() => {
          setManualAssessmentStale(true);
          setManualAssessmentOrderFingerprint(next.join(','));
        }, 0);
      }
      return next;
    });
  }

  function handleClearManualSelection() {
    setManualOrder([]);
    clearManualAssessmentState();
  }

  function handleManualReorder(newOrder: string[]) {
    // Validate before accepting: every ID must be unique and belong to the current selection
    const currentSet = new Set(manualOrder);
    const newSet = new Set(newOrder);
    // Must preserve all selected IDs — no additions, no removals, no dupes
    if (newOrder.length !== currentSet.size) return; // size mismatch
    if (!assertUniqueProductOrder(newOrder, 'handleManualReorder')) return;
    for (const id of newSet) {
      if (!currentSet.has(id)) return; // unknown ID rejected
    }
    setManualOrder(newOrder);
    if (manualAssessment !== null) setManualAssessmentStale(true);
    setManualAssessmentOrderFingerprint(newOrder.join(','));
  }

  // ── Phase 4.2F4: Manual transmit — enters choreography ───────────────────
  const handleManualTransmit = useCallback(() => {
    if (manualOrder.length === 0) return;
    // Pre-flight invariant check — must be unique before executing
    const seenTx = new Set<string>();
    const dupesTx = manualOrder.filter((id) => seenTx.has(id) || !seenTx.add(id));
    if (dupesTx.length > 0) {
      setError(`MANUAL PLAN STATE INVALID: Duplicate product ID(s): ${[...new Set(dupesTx)].join(', ')}`);
      return;
    }
    // Build execution plan — prefer assessed plan (authoritative facts) if fresh
    const localPlan: CandidatePlan = {
      plan_id: 'operator-manual',
      strategy: 'manual',
      generated_by: 'operator',
      metadata: { decision_mode: 'manual', selected_count: manualOrder.length },
      packets: manualOrder.map((id) => {
        const dp = rawDataProducts.find((p) => p.product_id === id);
        return dp ? {
          packet_id: dp.product_id,
          packet_type: dp.product_type,
          size_bits: dp.size_bits,
          criticality: dp.criticality,
          mission_relevance: dp.mission_relevance,
          deadline_s: dp.deadline_s,
          retry_cost: dp.retry_cost,
          delivery_requirement: dp.delivery_requirement,
        } : null;
      }).filter(Boolean) as import('./types/domain').Packet[],
    };
    const planToExecute = (manualAssessment && !manualAssessmentStale)
      ? manualAssessment.plan
      : localPlan;

    const newId = `exec-${++executionCounter.current}`;
    setExecutionId(newId);
    setPlaybackStartedAtMs(null);
    setPendingExecutionPlan(planToExecute);
    setPendingExecutionMode('custom');
    setChoreographyActive(true);
    setApprovalPhase('transmitting');
    addSessionEvent('plan_uplink_started', `manual:${manualOrder.length} products`);
    setActiveSection('transmission');
  }, [manualAssessment, manualAssessmentStale, manualOrder, rawDataProducts, addSessionEvent]);

  // ── Derived values ─────────────────────────────────────────────────────────

  const displayEvals = whatIfEvals ?? allEvaluations;
  const activePlan = allPlans.find((p) => p.plan_id === activePlanId) ?? (queue as CandidatePlan);
  const activeEval = displayEvals.find((e) => e.plan_id === activePlanId) ?? null;
  const recEval = recommendation
    ? (displayEvals.find((e) => e.plan_id === recommendation.recommended_plan_id) ?? null)
    : null;
  const riskWeights = { w_deadline_miss: 0.40, w_critical_deficit: 0.40, w_window_pressure: 0.20 };
  const recPlan = recommendation
    ? (allPlans.find((p) => p.plan_id === recommendation.recommended_plan_id) ?? null)
    : null;

  // ── Phase 4.2F3: AI plan human decision handlers ─────────────────────────
  // These are placed after recPlan to avoid forward-reference.
  const [aiRecommendationRejected, setAiRecommendationRejected] = useState<boolean>(false);

  /** Approve: start choreography with AI plan. Creates a new executionId and enters choreography. */
  const handleApproveAiPlan = useCallback(() => {
    if (!recPlan) return;
    const newId = `exec-${++executionCounter.current}`;
    setExecutionId(newId);
    setPlaybackStartedAtMs(null);
    setAiRecommendationRejected(false);
    setPendingExecutionPlan(recPlan);
    setPendingExecutionMode('ai');
    setChoreographyActive(true);
    setApprovalPhase('transmitting');
    addSessionEvent('recommendation_approved', `plan=${recPlan.plan_id}`);
    setActiveSection('transmission');
  }, [recPlan, addSessionEvent]);

  /** Modify: seed manual mode with AI plan packet IDs, switch to manual planning. */
  const handleModifyAiPlan = useCallback(() => {
    if (!recPlan) return;
    const orderedIds = recPlan.packets.map((p) => p.packet_id);
    setManualOrder(orderedIds);
    clearManualAssessmentState();
    setDecisionMode('manual');
    setAiRecommendationRejected(false);
    setActiveSection('data');
  }, [recPlan, clearManualAssessmentState]);

  /** Reject: no backend mutation, no transmission, no state change except flag. */
  const handleRejectAiPlan = useCallback(() => {
    setAiRecommendationRejected(true);
    setApprovalPhase('idle');
    addSessionEvent('recommendation_rejected');
  }, [addSessionEvent]);

  /**
   * Execute the actual backend approval during choreography.
   * Called by TransmissionSequencePanel when contact is acquired.
   *
   * SINGLE-SHOT GUARANTEE:
   * For a given executionId, this function dispatches AT MOST ONE backend request.
   * If a Promise already exists in executionPromiseRef for this executionId,
   * the existing Promise is returned — the API is NOT called again.
   * This means navigation, remount, StrictMode double-effects, and rapid clicks
   * all resolve to the same single backend call.
   */
  const handleExecuteApproval = useCallback(async (activeExecutionId: string): Promise<ApproveResponse> => {
    const map = executionPromiseRef.current;
    // Return existing promise if already dispatched for this id
    if (map.has(activeExecutionId)) {
      return map.get(activeExecutionId)!;
    }
    if (!pendingExecutionPlan) throw new Error('No pending execution plan');
    // Create and store the promise BEFORE awaiting — this is the guard
    const promise = (pendingExecutionMode === 'ai' && recommendation)
      ? approvePlan(recommendation.recommended_plan_id, pendingExecutionPlan)
      : approveCustomPlan(pendingExecutionPlan, 'operator transmission');
    map.set(activeExecutionId, promise);
    return promise;
  }, [pendingExecutionPlan, pendingExecutionMode, recommendation]);

  /** Called when TransmissionSequencePanel completes the full sequence. */
  const handleChoreographyComplete = useCallback((result: ApproveResponse) => {
    setChoreographyActive(false);
    addSessionEvent('transmission_completed', `delivered=${result.simulation_result.delivered_packets.length}`);
    handleApproved(result);
  }, [addSessionEvent]); // handleApproved is not in deps because it's defined below as a function

  const manualPlan: CandidatePlan | null = manualOrder.length > 0 ? {
    plan_id: 'operator-manual',
    strategy: 'manual',
    generated_by: 'operator',
    metadata: { decision_mode: 'manual', selected_count: manualOrder.length },
    packets: manualOrder.map((id) => {
      const dp = rawDataProducts.find((p) => p.product_id === id);
      return dp ? {
        packet_id: dp.product_id,
        packet_type: dp.product_type,
        size_bits: dp.size_bits,
        criticality: dp.criticality,
        mission_relevance: dp.mission_relevance,
        deadline_s: dp.deadline_s,
        retry_cost: dp.retry_cost,
        delivery_requirement: dp.delivery_requirement,
      } : null;
    }).filter(Boolean) as import('./types/domain').Packet[],
  } : null;

  // ── V3.5: Compute panel width based on workspace mode ─────────────────────
  // In focus mode: panel fills everything except sidebar (64px)
  // In expanded mode: clamp(650px, 58vw, 1100px)
  // In normal mode: use manual panelWidth

  // ── Render ────────────────────────────────────────────────────────────────

  const isFocus = workspaceMode === 'focus';
  const isExpanded = workspaceMode === 'expanded';

  return (
    <>
      <style>{styles}</style>

      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      <div style={{
        height: 42,
        background: 'rgba(6,9,18,0.98)',
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        display: 'flex',
        alignItems: 'center',
        paddingLeft: 16,
        paddingRight: 12,
        gap: 10,
        flexShrink: 0,
        zIndex: 100,
        position: 'relative',
      }}>
        {/* Live pulse dot */}
        <span style={{
          display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
          background: '#34d399', animation: 'pulse 2.5s infinite', flexShrink: 0,
        }} title="Live" />

        {/* Title */}
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em',
          color: '#e2e8f4',
          flexShrink: 0,
        }}>
          GCSI
        </span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, color: 'rgba(122,143,168,0.7)',
          fontWeight: 400,
          flexShrink: 0,
        }}>
          Ground Control Signal Insight
        </span>

        {/* SIM badge */}
        <span style={{
          padding: '2px 7px',
          background: 'rgba(245,158,11,0.08)',
          color: '#f59e0b',
          border: '1px solid rgba(245,158,11,0.22)',
          borderRadius: 4,
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontSize: 9, fontWeight: 600, letterSpacing: '0.04em',
          flexShrink: 0,
        }}>
          SIM
        </span>

        {/* What-if indicator */}
        {whatIfEvals !== null && (
          <span style={{
            padding: '2px 8px',
            background: 'rgba(245,158,11,0.08)',
            color: '#f59e0b',
            border: '1px solid rgba(245,158,11,0.28)',
            borderRadius: 4,
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9, fontWeight: 600,
            flexShrink: 0,
          }}>
            What-if · {whatIfSnr?.toFixed(1)} dB
          </span>
        )}

        {/* V3.4: Decision mode badge */}
        {decisionMode !== 'unselected' && (
          <span style={{
            padding: '2px 8px',
            background: decisionMode === 'manual' ? 'rgba(52,211,153,0.07)' : 'rgba(76,141,255,0.07)',
            color: decisionMode === 'manual' ? '#34d399' : '#6EA8FF',
            border: `1px solid ${decisionMode === 'manual' ? 'rgba(52,211,153,0.22)' : 'rgba(76,141,255,0.22)'}`,
            borderRadius: 4,
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9, fontWeight: 600,
            flexShrink: 0,
          }}>
            {decisionMode === 'manual' ? 'MANUAL' : 'AI ASSISTED'}
          </span>
        )}

        {/* V3.4: AI lifecycle badge — provider-aware labeling */}
        {aiLifecycle !== 'standby' && ((): React.ReactNode => {
          // Determine if the actual provider is local/deterministic
          const ap = (aiActualProvider ?? aiProvider ?? '').toLowerCase();
          const isLocalProvider = ap.includes('local') || ap.includes('deterministic') || ap.includes('rule');
          const providerLabel = aiLifecycle === 'analyzing' ? 'ANALYZING'
            : aiLifecycle === 'ready' ? (aiProvider?.toUpperCase() ?? 'READY')
            : aiLifecycle === 'error' ? 'FAILED'
            : 'STALE';
          // Local fallback: show TRIAGE · LOCAL instead of AI · Local
          const badgePrefix = isLocalProvider && (aiLifecycle === 'ready' || aiLifecycle === 'stale') ? 'TRIAGE' : 'AI';
          const badgeLabel = `${badgePrefix} · ${providerLabel}`;
          const isReady = aiLifecycle === 'ready';
          const bgColor = aiLifecycle === 'analyzing' ? 'rgba(76,141,255,0.07)' :
                          isReady ? (isLocalProvider ? 'rgba(245,158,11,0.07)' : 'rgba(52,211,153,0.07)') :
                          aiLifecycle === 'error' ? 'rgba(248,113,113,0.07)' : 'rgba(245,158,11,0.07)';
          const fgColor = aiLifecycle === 'analyzing' ? '#6EA8FF' :
                          isReady ? (isLocalProvider ? '#f59e0b' : '#34d399') :
                          aiLifecycle === 'error' ? '#f87171' : '#f59e0b';
          const borderColor = aiLifecycle === 'analyzing' ? 'rgba(76,141,255,0.22)' :
                              isReady ? (isLocalProvider ? 'rgba(245,158,11,0.22)' : 'rgba(52,211,153,0.22)') :
                              aiLifecycle === 'error' ? 'rgba(248,113,113,0.22)' : 'rgba(245,158,11,0.22)';
          return (
            <span style={{
              padding: '2px 8px', background: bgColor, color: fgColor,
              border: `1px solid ${borderColor}`, borderRadius: 4,
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
              fontSize: 9, fontWeight: 600, flexShrink: 0,
            }} title={isLocalProvider ? 'Deterministic local fallback — not an AI model' : undefined}>
              {badgeLabel}
            </span>
          );
        })()}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Action buttons */}
        <button
          onClick={handleReset}
          disabled={loading || resetting}
          style={{
            background: 'transparent',
            color: 'rgba(248,113,113,0.65)',
            border: '1px solid rgba(248,113,113,0.18)',
            borderRadius: 6, padding: '4px 12px',
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, fontWeight: 500,
            cursor: 'pointer', transition: 'background 0.15s',
            opacity: (loading || resetting) ? 0.4 : 1,
          }}
          title="Reload scenario from backend with randomized link conditions"
        >
          Reset
        </button>
        <button
          onClick={refresh}
          disabled={loading || resetting}
          style={{
            background: 'rgba(255,255,255,0.04)',
            color: 'rgba(226,232,244,0.6)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 6, padding: '4px 12px',
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, fontWeight: 500,
            cursor: 'pointer', transition: 'background 0.15s',
            opacity: (loading || resetting) ? 0.4 : 1,
          }}
        >
          Refresh
        </button>
      </div>

      {/* ── Loading / error states ────────────────────────────────────────── */}
      {loading && (
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#080B11',
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, color: 'rgba(147,160,180,0.5)',
          letterSpacing: '0.02em',
        }}>
          Loading mission data…
        </div>
      )}

      {!loading && error && (
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#080B11',
          flexDirection: 'column', gap: 12,
        }}>
          <div style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 12, color: '#f87171',
            padding: '10px 20px', background: 'rgba(248,113,113,0.06)',
            border: '1px solid rgba(248,113,113,0.20)', borderRadius: 8,
          }}>
            Error: {error}
          </div>
          <button
            onClick={refresh}
            style={{
              background: 'rgba(76,141,255,0.08)',
              color: '#6EA8FF',
              border: '1px solid rgba(76,141,255,0.22)',
              borderRadius: 6, padding: '6px 16px',
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontSize: 12, fontWeight: 500, cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Legacy mode banner ───────────────────────────────────────────── */}
      {!loading && !error && !hasDataProducts && dataProductsCount === 0 && (
        <div style={{
          background: 'rgba(245,158,11,0.07)',
          borderBottom: '1px solid rgba(245,158,11,0.22)',
          padding: '10px 20px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          flexShrink: 0,
          zIndex: 50,
        }}>
          <span style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
            color: '#f59e0b', flexShrink: 0,
          }}>
            LIMITED DEMO MODE
          </span>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, color: 'rgba(147,160,180,0.8)', flex: 1,
          }}>
            {missionState ? `${missionState.current_event} — ` : ''}
            Legacy packet scenario active. High-volume AI prioritization, anomaly analysis, and spacecraft geometry are unavailable.
          </span>
          <button
            onClick={() => handleSwitchScenario('mission_data_v3.json')}
            disabled={scenarioSwitching}
            style={{
              padding: '5px 14px',
              background: 'rgba(76,141,255,0.10)',
              color: '#6EA8FF',
              border: '1px solid rgba(76,141,255,0.30)',
              borderRadius: 5, cursor: 'pointer', flexShrink: 0,
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontSize: 11, fontWeight: 600,
              opacity: scenarioSwitching ? 0.5 : 1,
            }}
          >
            {scenarioSwitching ? 'Switching…' : 'Switch to High-Volume Demo'}
          </button>
        </div>
      )}

      {/* ── Main 3-column layout ──────────────────────────────────────────── */}
      {!loading && !error && (
        <div style={{
          flex: 1,
          display: 'flex',
          overflow: 'hidden',
          minHeight: 0,
        }}>
          {/* LEFT: Navigation sidebar — always visible, even in focus mode */}
          <NavigationSidebar
            active={activeSection}
            onNavigate={setActiveSection}
          />

          {/* CENTER: 3D Mission Viewport — hidden in focus mode */}
          <div
            className="workspace-viewport"
            style={{
              flex: isFocus ? '0 0 0px' : 1,
              minWidth: 0,
              position: 'relative',
              overflow: 'hidden',
              opacity: isFocus ? 0 : 1,
              // Use visibility so the canvas stays mounted (preserves 3D state)
              // but takes no space in focus mode
              pointerEvents: isFocus ? 'none' : 'auto',
              width: isFocus ? 0 : undefined,
            }}
          >
            <MissionViewport
              linkState={linkState}
              missionState={missionState}
              distanceKm={distanceKm}
              approvalPhase={approvalPhase}
              showStarfield={viewSettings.showStarfield}
              showLabels={viewSettings.showLabels}
              showCommLink={viewSettings.showCommLink}
              smoothCamera={viewSettings.smoothCamera}
            />
          </div>

          {/* ── Drag divider — hidden in focus/expanded modes ── */}
          {!isFocus && !isExpanded && (
            <div
              className="workspace-divider"
              onMouseDown={handleDividerMouseDown}
              onDoubleClick={resetPanelWidth}
              title="Drag to resize · Double-click to reset"
              style={{
                width: 5,
                flexShrink: 0,
                background: 'transparent',
                borderLeft: '1px solid rgba(46,58,79,0.7)',
                cursor: 'col-resize',
                position: 'relative',
                zIndex: 30,
                transition: 'background 0.15s, border-color 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.background = 'rgba(76,141,255,0.12)';
                (e.currentTarget as HTMLDivElement).style.borderLeftColor = 'rgba(76,141,255,0.40)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.background = 'transparent';
                (e.currentTarget as HTMLDivElement).style.borderLeftColor = 'rgba(46,58,79,0.7)';
              }}
            >
              {/* grip dots */}
              <div style={{
                position: 'absolute',
                top: '50%', left: '50%',
                transform: 'translate(-50%, -50%)',
                display: 'flex', flexDirection: 'column', gap: 3,
                pointerEvents: 'none',
              }}>
                {[0,1,2].map((i) => (
                  <div key={i} style={{
                    width: 3, height: 3, borderRadius: '50%',
                    background: 'rgba(76,141,255,0.30)',
                  }} />
                ))}
              </div>
            </div>
          )}

          {/* RIGHT: Contextual control panel */}
          <RightPanel
            section={activeSection}
            panelWidth={panelWidth}
            panelDefaultWidth={DEFAULT_WIDTH}
            workspaceMode={workspaceMode}
            onSetWorkspaceMode={setWorkspaceMode}
            viewSettings={viewSettings}
            onUpdateSetting={updateViewSetting}
            onResetSettings={resetSettings}
            onResetPanelWidth={resetPanelWidth}
            linkState={linkState}
            missionState={missionState}
            distanceKm={distanceKm}
            propagationDelayS={propagationDelayS}
            roundTripTimeS={roundTripTimeS}
            availableCapacityBits={availableCapacityBits}
            queuedDataBits={queuedDataBits}
            dataProductsCount={dataProductsCount}
            anomalies={anomalies}
            queue={queue ?? {} as CandidatePlan}
            recommendation={recommendation}
            aiProvider={aiProvider}
            aiRequestedProvider={aiRequestedProvider}
            aiActualProvider={aiActualProvider}
            aiPrioritization={aiPrioritization}
            aiCandidateCount={aiCandidateCount}
            aiPrioritizationError={aiPrioritizationError}
            aiPrioritizationFallbackReason={aiPrioritizationFallbackReason}
            aiRecommendationFallbackReason={aiRecommendationFallbackReason}
            allPlans={allPlans}
            allEvaluations={displayEvals}
            activePlanId={activePlanId}
            approvalPhase={approvalPhase}
            approveResult={approveResult}
            whatIfEvals={whatIfEvals}
            whatIfSnr={whatIfSnr}
            recPlan={recPlan}
            recEval={recEval}
            activeEval={activeEval}
            activePlan={activePlan ?? {} as CandidatePlan}
            riskWeights={riskWeights}
            onApproved={handleApproved}
            onTransmitting={() => setApprovalPhase('transmitting')}
            onApprovalError={handleApprovalError}
            onWhatIfResult={handleWhatIfResult}
            onSelectPlan={setActivePlanId}
            decisionMode={decisionMode}
            onSelectDecisionMode={setDecisionMode}
            aiLifecycle={aiLifecycle}
            aiError={aiError}
            onRunAiAnalysis={runAiAnalysis}
            rawDataProducts={rawDataProducts}
            hasDataProducts={hasDataProducts}
            manualSelectedIds={manualSelectedIds}
            manualOrder={manualOrder}
            manualPlan={manualPlan}
            onToggleManualSelect={handleToggleManualSelect}
            onClearManualSelection={handleClearManualSelection}
            onManualReorder={handleManualReorder}
            availableScenarios={availableScenarios}
            activeScenarioPath={activeScenarioPath}
            scenarioSwitching={scenarioSwitching}
            onSwitchScenario={handleSwitchScenario}
            experienceManifest={experienceManifest}
            experienceAvailable={experienceAvailable}
            manualAssessment={manualAssessment}
            manualAssessmentLoading={manualAssessmentLoading}
            manualAssessmentError={manualAssessmentError}
            manualAssessmentStale={manualAssessmentStale}
            onManualEvaluate={handleManualEvaluate}
            onManualTransmit={handleManualTransmit}
            onApproveAiPlan={handleApproveAiPlan}
            onModifyAiPlan={handleModifyAiPlan}
            onRejectAiPlan={handleRejectAiPlan}
            executionId={executionId}
            playbackStartedAtMs={playbackStartedAtMs}
            onSetPlaybackStarted={setPlaybackStartedAtMs}
            aiRecommendationRejected={aiRecommendationRejected}
            sessionEvents={sessionEvents}
            choreographyActive={choreographyActive}
            pendingExecutionPlan={pendingExecutionPlan}
            onExecuteApproval={handleExecuteApproval}
            onChoreographyComplete={handleChoreographyComplete}
            onChoreographyError={(msg) => { setError(msg); setChoreographyActive(false); setApprovalPhase('ready'); }}
          />
        </div>
      )}
    </>
  );
}
