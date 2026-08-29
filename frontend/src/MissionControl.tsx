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
import { classifyProvider, buildProviderBadgeLabel } from './utils/providerClassification';
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
  getSources,
  selectSource,
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
  MissionSourceInfo,
  MissionState,
  ScenarioInfo,
  SourceSummary,
  WhatIfEvalResponse,
} from './types/domain';
import type { ExperienceManifest } from './types/experience';
import type { ApprovalPhase } from './components/ApprovalBar';
import { SourceContextBanner } from './components/SourceContextBanner';
import { ScenarioSwitcher } from './components/ScenarioSwitcher';
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
    background: #f5f6f8;
    color: #1a2035;
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
  ::-webkit-scrollbar-thumb { background: rgba(29,78,216,0.18); border-radius: 2px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(29,78,216,0.32); }

  /* ── V4.0 Scientific Mission Analysis design tokens ── */
  :root {
    /* shell */
    --bg:            #f5f6f8;
    --shell-bg:      #ffffff;
    --shell-border:  #dde1e8;
    --panel-bg:      #ffffff;
    --panel-border:  #dde1e8;
    --panel-radius:  4px;
    --section-border: #e8eaee;
    /* text */
    --text:          #1a2035;
    --text-secondary: #4a5568;
    --text-muted:    #7a8699;
    --text-dim:      #b0bac9;
    /* accent */
    --accent:        #1d4ed8;
    --accent-light:  #eef3fc;
    --accent-mid:    rgba(29,78,216,0.12);
    /* borders */
    --border:        #dde1e8;
    --border-strong: #c8cdd7;
    /* semantic */
    --signal:   #16a34a;
    --warn:     #d97706;
    --critical: #dc2626;
    --ai:       #1d4ed8;
    /* fonts */
    --font-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', monospace;
    --font-sans: 'IBM Plex Sans', system-ui, sans-serif;
    /* risk */
    --risk-low-bg:       rgba(22,163,74,0.07);
    --risk-low-color:    #16a34a;
    --risk-low-border:   rgba(22,163,74,0.25);
    --risk-low-glow:     none;
    --risk-medium-bg:    rgba(217,119,6,0.08);
    --risk-medium-color: #d97706;
    --risk-medium-border: rgba(217,119,6,0.28);
    --risk-medium-glow:  none;
    --risk-high-bg:      rgba(234,88,12,0.08);
    --risk-high-color:   #ea580c;
    --risk-high-border:  rgba(234,88,12,0.28);
    --risk-high-glow:    none;
    --risk-critical-bg:      rgba(220,38,38,0.08);
    --risk-critical-color:   #dc2626;
    --risk-critical-border:  rgba(220,38,38,0.28);
    --risk-critical-glow:    none;
    --btn-primary-bg:    #1d4ed8;
    --btn-primary-color: #ffffff;
    --btn-primary-glow:  none;
    --tab-active-glow:   none;
    --ai-panel-border:   rgba(29,78,216,0.18);
    --ai-panel-glow:     none;
  }

  /* ── Panel base — flat, minimal rounding ── */
  .panel {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: var(--panel-radius);
    padding: 14px 16px;
    margin-bottom: 8px;
    min-width: 0;
    box-sizing: border-box;
    overflow-x: hidden;
  }
  .panel h2 {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 10px;
    padding-bottom: 8px;
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
    margin: 10px 0 5px;
  }
  .panel p {
    margin-bottom: 8px;
    line-height: 1.6;
    font-size: 12.5px;
    color: var(--text-secondary);
  }
  .waveform-wrap {
    background: rgba(0,0,0,0.03);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 6px 10px;
    margin-bottom: 12px;
  }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td {
    padding: 5px 8px;
    text-align: left;
    border-bottom: 1px solid var(--section-border);
  }
  th {
    color: var(--text-muted);
    font-weight: 500;
    font-size: 10px;
    letter-spacing: 0.05em;
    font-family: var(--font-sans);
    text-transform: uppercase;
  }
  td { font-family: var(--font-mono); font-size: 12px; color: var(--text); }
  td:first-child { font-family: var(--font-sans); color: var(--text-secondary); font-size: 12px; }
  code {
    background: var(--accent-light);
    color: var(--accent);
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 11px;
    font-family: var(--font-mono);
  }
  .ai-hero {
    border-color: var(--ai-panel-border);
    background: #fafbfd;
  }
  .ai-hero h2 { color: var(--accent); border-bottom-color: rgba(29,78,216,0.12); }
  .approval-bar {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: var(--panel-radius);
    padding: 12px 14px;
  }

  /* ── ApprovalBar buttons ── */
  .btn-approve {
    background: var(--btn-primary-bg);
    color: var(--btn-primary-color);
    border: 1px solid var(--btn-primary-bg);
    border-radius: 3px;
    padding: 7px 18px;
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.01em;
  }
  .btn-approve:hover { background: #1e40af; border-color: #1e40af; }
  .btn-override {
    background: #f5f6f8;
    color: var(--text-secondary);
    border: 1px solid var(--border-strong);
    border-radius: 3px;
    padding: 7px 14px;
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
  }
  .btn-override:hover { background: #e8eaee; }
  .btn-reset {
    background: transparent;
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 7px 12px;
    font-family: var(--font-sans);
    font-size: 12px;
    cursor: pointer;
  }
  .btn-reset:hover { background: #f5f6f8; }

  /* ── ApprovalBar drag list ── */
  .drag-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 280px;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 4px;
    background: #f5f6f8;
  }
  .drag-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: grab;
    font-size: 11px;
    min-width: 0;
  }
  .drag-item:active { cursor: grabbing; }
  .drag-handle { color: var(--text-dim); font-size: 13px; flex-shrink: 0; cursor: grab; }
  .drag-rank { color: var(--text-dim); font-family: var(--font-mono); font-size: 10px; min-width: 20px; text-align: right; flex-shrink: 0; }
  .drag-id { font-family: var(--font-mono); font-size: 11px; color: var(--text); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; background: none; }
  .drag-type { font-family: var(--font-sans); font-size: 10px; flex-shrink: 0; }
  .drag-crit { font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); flex-shrink: 0; }
  .drag-size { font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); flex-shrink: 0; }

  /* ── LinkHealthPanel whatif ── */
  .whatif-section {
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .whatif-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-muted);
  }
  .whatif-label { font-size: 11px; color: var(--text-secondary); }
  .whatif-preview-badge {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    background: rgba(217,119,6,0.08);
    color: var(--warn);
    border: 1px solid rgba(217,119,6,0.25);
    border-radius: 2px;
    padding: 1px 5px;
    letter-spacing: 0.06em;
  }
  .whatif-slider {
    flex: 1;
    accent-color: var(--accent);
  }
  .whatif-reset {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 11px;
    padding: 2px 7px;
  }
  .whatif-reset:hover { background: #f5f6f8; }

  /* ── Section header — analytical workspace style ── */
  .section-hd {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    padding-bottom: 7px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 8px;
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
    50% { opacity: 0.4; }
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
  // ── Phase 6E-C7: Source provenance summary ────────────────────────────────
  const [sourceSummary, setSourceSummary] = useState<SourceSummary | null>(null);
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

  // ── Phase 7: Mission source switcher ──────────────────────────────────────
  const [missionSources, setMissionSources] = useState<MissionSourceInfo[]>([]);
  const [activeSourceId, setActiveSourceId] = useState<string | null>(null);
  const [sourceSwitching, setSourceSwitching] = useState(false);
  const [sourceSwitchError, setSourceSwitchError] = useState<string | null>(null);

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

  // ── Phase 5.1E: Application-level execution coordinator ──────────────────
  // INVARIANTS:
  //   E1 One operator authorization creates exactly one executionId.
  //   E2 Exactly one backend approval request exists per executionId.
  //   E3 The approval Promise is registered BEFORE awaiting its resolution.
  //   E4 The approval request is dispatched at authorization time (not from a child timer).
  //   E5 Unmounting TransmissionSequencePanel cannot cancel/prevent an already-dispatched execution.
  //   E7 Browser backgrounding cannot postpone first backend dispatch.
  //   E8 The authoritative ApproveResponse remains available after navigation/remount.

  /** Monotonic counter to generate unique execution IDs. */
  const executionCounter = useRef(0);

  /** Current execution ID. Null when idle. */
  const [executionId, setExecutionId] = useState<string | null>(null);

  /**
   * Wall-clock ms at which operator authorized the current execution.
   * Phase 5.1G (WORKSTREAM A): passed to TransmissionSequencePanel as absolute time anchor.
   * anchors the presentation phase timeline for navigation-resilient phase derivation.
   */
  const [authorizedAtMs, setAuthorizedAtMs] = useState<number | null>(null);

  /**
   * Phase 5.1F: Application-level presentation phase for the active execution.
   * This is the AUTHORITATIVE source of the current choreography phase.
   * TransmissionSequencePanel is a VIEW that must not reset this on remount.
   *
   * When non-null it represents the furthest phase the execution has reached,
   * ensuring navigation away and back shows the correct current phase.
   */
  const [presentationPhase, setPresentationPhase] = useState<import('./components/TransmissionSequencePanel').TransmissionChoreographyPhase>('plan_uplink');

  /**
   * Phase 5.1F: Wall-clock ms when the approval result was received.
   * Stored at application level so it is available after panel remounts.
   */
  const approvalResultReceivedAtMsRef = useRef<number | null>(null);

  /**
   * Frozen snapshot of the plan/mode chosen at authorization time.
   * These cannot drift even if later UI state changes recommendation/manualOrder.
   * (INVARIANT E14: execution snapshot immutability)
   */
  const executionSnapshotRef = useRef<{
    plan: CandidatePlan;
    mode: 'ai' | 'custom';
    recommendedPlanId: string | null;
    scenarioPath: string | null;
  } | null>(null);

  /**
   * The Promise for the in-flight or completed approval request. Keyed by executionId.
   * Once an entry exists, NO second dispatch is ever made for that id.
   */
  const executionPromiseRef = useRef<Map<string, Promise<ApproveResponse>>>(new Map());

  /**
   * The resolved approval result keyed by executionId.
   * Populated immediately when the Promise resolves, regardless of panel mount state.
   * Used to deliver result back to UI even after navigation/remount.
   */
  const executionResultRef = useRef<Map<string, ApproveResponse>>(new Map());

  /** Wall-clock ms at which the current execution's visual playback began. */
  const [playbackStartedAtMs, setPlaybackStartedAtMs] = useState<number | null>(null);

  /** Current active 3D transmission pulse — driven by authoritative attempt_events. */
  const [activePulse, setActivePulse] = useState<import('./components/scene/CommunicationLink').ActivePulse | null>(null);

  /** Current choreography phase — drives 3D pulse direction from above navigation. */
  const [choreographyPhase, setChoreographyPhase] = useState<import('./components/TransmissionSequencePanel').TransmissionChoreographyPhase>('plan_uplink');

  // ── Phase 4.2F4: Transmission choreography ────────────────────────────────
  /** When true, TransmissionSequencePanel is active in TransmissionSection. */
  const [choreographyActive, setChoreographyActive] = useState<boolean>(false);
  /** Frozen plan snapshot for the active execution (set at authorization time). */
  const [pendingExecutionPlan, setPendingExecutionPlan] = useState<CandidatePlan | null>(null);
  /** Whether to use /approve (ai) or /approve/custom (manual/modified). Frozen at authorization. */
  // pendingExecutionMode: kept for potential future use; was used in removed fallback
  const [_pendingExecutionMode, setPendingExecutionMode] = useState<'ai' | 'custom'>('custom');

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

  // ── Phase 5.1E: Assessment invalidation effect (replaces setTimeout side-effect) ──
  // Runs when manualOrder changes. Marks assessment stale if it exists.
  // This is a clean useEffect — no setState-inside-setState, no setTimeout.
  useEffect(() => {
    if (manualAssessment !== null) {
      setManualAssessmentStale(true);
      setManualAssessmentOrderFingerprint(manualOrder.join(','));
    }
  // manualAssessment is intentionally not in deps — we only want to react to manualOrder changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manualOrder]);

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
      // Phase 6E-C7: source provenance from GET /state — no additional request
      setSourceSummary(stateData.source ?? null);
      // Phase 7: sync active source from GET /sources (best effort — no separate request)
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
    setChoreographyPhase('plan_uplink');
    // Reset execution coordinator
    setExecutionId(null);
    setAuthorizedAtMs(null);
    setPlaybackStartedAtMs(null);
    setActivePulse(null);
    setPresentationPhase('plan_uplink');
    approvalResultReceivedAtMsRef.current = null;
    executionPromiseRef.current.clear();
    executionResultRef.current.clear();
    executionSnapshotRef.current = null;
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

  // ── Phase 7: Load mission source catalog ─────────────────────────────────
  const loadSources = useCallback(async () => {
    try {
      const resp = await getSources();
      setMissionSources(resp.sources);
      setActiveSourceId(resp.active_source_id);
    } catch { /* informational — switcher degrades gracefully */ }
  }, []);

  // ── Phase 7: Handle source selection ─────────────────────────────────────
  const handleSelectSource = useCallback(async (sourceId: string) => {
    if (sourceId === activeSourceId) return; // no-op for same source
    setSourceSwitching(true);
    setSourceSwitchError(null);
    // Clear all stale AI/planning state before switching
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
    setAllPlans((prev) => prev.filter((p) => p.plan_id !== 'ai-prioritized'));
    setAllEvaluations((prev) => prev.filter((e) => e.plan_id !== 'ai-prioritized'));
    setManualOrder([]);
    clearManualAssessmentState();
    setAiRecommendationRejected(false);
    setChoreographyActive(false);
    setPendingExecutionPlan(null);
    setChoreographyPhase('plan_uplink');
    setExecutionId(null);
    setAuthorizedAtMs(null);
    setPlaybackStartedAtMs(null);
    setActivePulse(null);
    setPresentationPhase('plan_uplink');
    approvalResultReceivedAtMsRef.current = null;
    executionPromiseRef.current.clear();
    executionResultRef.current.clear();
    executionSnapshotRef.current = null;
    setExperienceManifest(null);
    setExperienceAvailable(false);
    aiRequestInFlight.current = false;
    totalWindowRef.current = null;
    try {
      const resp = await selectSource(sourceId);
      setActiveSourceId(resp.active_source_id);
      // Re-load full mission data and experience after switch
      await loadMissionData(false);
      await loadExperience();
    } catch (err) {
      // Keep old source selected — show error, do not blank the application
      setSourceSwitchError('Failed to switch scenario. Current scenario remains active.');
      // Refresh sources in case active_source_id drifted
      await loadSources();
    } finally {
      setSourceSwitching(false);
    }
  }, [activeSourceId, loadMissionData, loadExperience, loadSources, clearManualAssessmentState]);

  // ── V3.4: Initial load — NO AI ────────────────────────────────────────────
  useEffect(() => {
    const init = async () => {
      setLoading(true);
      try {
        const scenList = await listScenarios();
        setAvailableScenarios(scenList.scenarios);
        setActiveScenarioPath(scenList.active_scenario_path);
      } catch { /* informational */ }
      // Phase 7: load source catalog
      await loadSources();
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
    setChoreographyPhase('plan_uplink');
    // Reset execution coordinator
    setExecutionId(null);
    setAuthorizedAtMs(null);
    setPlaybackStartedAtMs(null);
    setActivePulse(null);
    setPresentationPhase('plan_uplink');
    approvalResultReceivedAtMsRef.current = null;
    executionPromiseRef.current.clear();
    executionResultRef.current.clear();
    executionSnapshotRef.current = null;
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
   * Assessment invalidation is handled via a separate useEffect (not setTimeout).
   */
  function handleToggleManualSelect(productId: string) {
    setManualOrder((prev) => {
      if (prev.includes(productId)) {
        // Deselect: remove ALL occurrences (guard against prior corruption)
        return prev.filter((id) => id !== productId);
      } else {
        // Select: append — idempotent guard
        return [...prev, productId];
      }
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
    const nowMs = Date.now();

    // ── INVARIANT E4: Dispatch approval immediately at authorization time ──
    // This happens BEFORE any presentation timer fires. The backend is called
    // as soon as the operator clicks TRANSMIT SELECTED — not when CONTACT_WAIT
    // presentation stage ends. Presentation choreography observes this execution.
    const promise = approveCustomPlan(planToExecute, 'operator transmission');
    executionPromiseRef.current.set(newId, promise);
    // Resolve result into ref for navigation-resilient retrieval (INVARIANT E8)
    // Phase 5.1F: also record approvalResultReceivedAtMs at resolution time (not panel mount)
    promise.then(
      (result) => {
        executionResultRef.current.set(newId, result);
        if (approvalResultReceivedAtMsRef.current === null) {
          approvalResultReceivedAtMsRef.current = Date.now();
        }
      },
      () => { /* error handled in TransmissionSequencePanel */ }
    );

    // Freeze execution snapshot (INVARIANT E14)
    executionSnapshotRef.current = {
      plan: planToExecute,
      mode: 'custom',
      recommendedPlanId: null,
      scenarioPath: activeScenarioPath,
    };

    setExecutionId(newId);
    setAuthorizedAtMs(nowMs);
    setPlaybackStartedAtMs(null);
    setPresentationPhase('plan_uplink');
    approvalResultReceivedAtMsRef.current = null;
    setPendingExecutionPlan(planToExecute);
    setPendingExecutionMode('custom');
    setChoreographyPhase('plan_uplink');
    setChoreographyActive(true);
    setApprovalPhase('transmitting');
    addSessionEvent('plan_uplink_started', `manual:${manualOrder.length} products`);
    setActiveSection('transmission');
  }, [manualAssessment, manualAssessmentStale, manualOrder, rawDataProducts, addSessionEvent, activeScenarioPath]);

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

  /**
   * Approve: start choreography with AI plan.
   * INVARIANT E4: approval is dispatched IMMEDIATELY at authorization — not from a presentation timer.
   */
  const handleApproveAiPlan = useCallback(() => {
    if (!recPlan) return;
    const newId = `exec-${++executionCounter.current}`;
    const nowMs = Date.now();

    // ── INVARIANT E4: Dispatch approval immediately at authorization time ──
    const promise = approvePlan(recommendation!.recommended_plan_id, recPlan);
    executionPromiseRef.current.set(newId, promise);
    // Phase 5.1F: record approvalResultReceivedAtMs at resolution time (not panel mount)
    promise.then(
      (result) => {
        executionResultRef.current.set(newId, result);
        if (approvalResultReceivedAtMsRef.current === null) {
          approvalResultReceivedAtMsRef.current = Date.now();
        }
      },
      () => { /* error handled in TransmissionSequencePanel */ }
    );

    // Freeze execution snapshot (INVARIANT E14)
    executionSnapshotRef.current = {
      plan: recPlan,
      mode: 'ai',
      recommendedPlanId: recommendation!.recommended_plan_id,
      scenarioPath: activeScenarioPath,
    };

    setExecutionId(newId);
    setAuthorizedAtMs(nowMs);
    setPlaybackStartedAtMs(null);
    setPresentationPhase('plan_uplink');
    approvalResultReceivedAtMsRef.current = null;
    setAiRecommendationRejected(false);
    setPendingExecutionPlan(recPlan);
    setPendingExecutionMode('ai');
    setChoreographyPhase('plan_uplink');
    setChoreographyActive(true);
    setApprovalPhase('transmitting');
    addSessionEvent('recommendation_approved', `plan=${recPlan.plan_id}`);
    setActiveSection('transmission');
  }, [recPlan, recommendation, addSessionEvent, activeScenarioPath]);

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
   * Return the execution Promise for a given executionId.
   * Called by TransmissionSequencePanel to await the result.
   *
   * Phase 5.1F (WORKSTREAM B — FAIL CLOSED):
   * This function is a RETRIEVAL OPERATION ONLY.
   * The approval Promise was already dispatched at authorization time in
   * handleManualTransmit / handleApproveAiPlan. This function ONLY returns it.
   *
   * If the Promise is missing, that is a frontend invariant violation — it means
   * authorization never ran or the execution coordinator lost its state. We throw
   * a typed error rather than silently creating a second backend request.
   *
   * DO NOT add a fallback approvePlan / approveCustomPlan call here.
   * Backend one-shot semantics must remain protected.
   *
   * INVARIANT E2: one backend call per executionId.
   * INVARIANT E4: dispatch happens at authorization, not from a presentation timer.
   * INVARIANT F07: missing Promise fails closed, no secondary dispatch.
   */
  const handleExecuteApproval = useCallback(async (activeExecutionId: string): Promise<ApproveResponse> => {
    const promise = executionPromiseRef.current.get(activeExecutionId);
    if (!promise) {
      // Execution coordinator invariant violation: the Promise must always exist before
      // TransmissionSequencePanel is mounted. This indicates a programming error.
      throw new Error(
        `Execution coordinator invariant violation: no approval request exists for execution ${activeExecutionId}. ` +
        `This is a frontend programming error — authorization must register the Promise before the panel mounts.`
      );
    }
    return promise;
  }, []);

  /**
   * Phase 5.1F (WORKSTREAM D): Production scenario stale-result guard.
   * Checks whether the result belongs to the current scenario before committing it.
   * Called before any state update from a resolved approval Promise.
   *
   * Uses executionSnapshotRef (frozen at authorization) to compare scenario identity
   * against the current active scenario path. This is a ref-based check that does not
   * depend on stale React closure values.
   */
  const currentActiveScenarioPathRef = useRef<string | null>(null);

  // Keep currentActiveScenarioPathRef up to date with active scenario
  useEffect(() => {
    currentActiveScenarioPathRef.current = activeScenarioPath;
  }, [activeScenarioPath]);

  /** Called when TransmissionSequencePanel completes the full sequence. */
  const handleChoreographyComplete = useCallback((result: ApproveResponse) => {
    // Phase 5.1F (WORKSTREAM D): Validate scenario identity before committing result.
    // If the operator switched scenarios after authorization, the old result must not
    // overwrite the new scenario's UI state.
    const snapshot = executionSnapshotRef.current;
    if (snapshot && snapshot.scenarioPath !== currentActiveScenarioPathRef.current) {
      // Stale result — log diagnostic and discard without touching current UI.
      console.warn(
        `[GCSI] Stale execution result discarded: result for scenario "${snapshot.scenarioPath}" ` +
        `arrived after switch to "${currentActiveScenarioPathRef.current}". ` +
        `This is expected behavior when the operator switches scenarios during execution.`
      );
      return;
    }
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

      {/* ── Top bar — flat scientific mission header ──────────────────────── */}
      <div style={{
        height: 44,
        background: '#ffffff',
        borderBottom: '1px solid #dde1e8',
        display: 'flex',
        alignItems: 'center',
        paddingLeft: 16,
        paddingRight: 12,
        gap: 0,
        flexShrink: 0,
        zIndex: 100,
        position: 'relative',
      }}>
        {/* Identity — GCSI wordmark */}
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', marginRight: 20, flexShrink: 0 }}>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 13, fontWeight: 700, letterSpacing: '0.03em',
            color: '#1a2035',
            lineHeight: 1.1,
          }}>
            GCSI
          </span>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 9, color: '#7a8699',
            fontWeight: 400,
            letterSpacing: '0.01em',
            lineHeight: 1.2,
          }}>
            Ground Control Signal Insight
          </span>
        </div>

        {/* Thin divider */}
        <div style={{ width: 1, height: 22, background: '#dde1e8', marginRight: 16, flexShrink: 0 }} />

        {/* Source mode indicator — semantic only */}
        {(() => {
          const isHistorical = sourceSummary?.mode === 'historical_replay';
          return (
            <span
              data-testid="source-mode-badge"
              style={{
                padding: '2px 6px',
                background: isHistorical ? '#eef3fc' : 'rgba(217,119,6,0.08)',
                color: isHistorical ? '#1d4ed8' : '#d97706',
                border: `1px solid ${isHistorical ? 'rgba(29,78,216,0.22)' : 'rgba(217,119,6,0.25)'}`,
                borderRadius: 3,
                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                flexShrink: 0,
                marginRight: 10,
              }}
              title={isHistorical ? 'Historical replay — not live telemetry' : 'Simulated synthetic scenario'}
            >
              {isHistorical ? 'HIST' : 'SIM'}
            </span>
          );
        })()}

        {/* What-if indicator — operational context */}
        {whatIfEvals !== null && (
          <span style={{
            padding: '2px 6px',
            background: 'rgba(217,119,6,0.07)',
            color: '#d97706',
            border: '1px solid rgba(217,119,6,0.25)',
            borderRadius: 3,
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9, fontWeight: 700,
            flexShrink: 0,
            marginRight: 8,
          }}>
            WHAT-IF · {whatIfSnr?.toFixed(1)} dB
          </span>
        )}

        {/* AI lifecycle — only show non-standby states, keep minimal */}
        {aiLifecycle !== 'standby' && ((): React.ReactNode => {
          const providerClass = classifyProvider(aiActualProvider ?? aiProvider);
          const badgeLabel = buildProviderBadgeLabel(aiActualProvider ?? aiProvider, aiLifecycle);
          const isLocal = providerClass.kind === 'local_deterministic';
          const isError = aiLifecycle === 'error';
          const isReady = aiLifecycle === 'ready';
          const isAnalyzing = aiLifecycle === 'analyzing';
          const titleText = isLocal
            ? 'Deterministic local fallback — not an AI model'
            : undefined;
          const color = isError ? '#dc2626' : isLocal ? '#d97706' : isAnalyzing ? '#1d4ed8' : isReady ? '#16a34a' : '#7a8699';
          const bg = isError ? 'rgba(220,38,38,0.07)' : isLocal ? 'rgba(217,119,6,0.07)' : isAnalyzing ? '#eef3fc' : isReady ? 'rgba(22,163,74,0.07)' : 'transparent';
          const bdr = isError ? 'rgba(220,38,38,0.25)' : isLocal ? 'rgba(217,119,6,0.25)' : isAnalyzing ? 'rgba(29,78,216,0.22)' : isReady ? 'rgba(22,163,74,0.22)' : '#dde1e8';
          return (
            <span style={{
              padding: '2px 6px', background: bg, color,
              border: `1px solid ${bdr}`, borderRadius: 3,
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
              fontSize: 9, fontWeight: 700, flexShrink: 0, marginRight: 8,
            }} title={titleText}>
              AI · {badgeLabel}
            </span>
          );
        })()}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Phase 7: Scenario/source switcher */}
        <ScenarioSwitcher
          sources={missionSources}
          activeSourceId={activeSourceId}
          switching={sourceSwitching}
          onSelectSource={handleSelectSource}
          error={sourceSwitchError}
        />

        {/* Thin divider */}
        <div style={{ width: 1, height: 22, background: '#dde1e8', marginLeft: 12, marginRight: 8, flexShrink: 0 }} />

        {/* Action buttons — flat */}
        <button
          onClick={handleReset}
          disabled={loading || resetting}
          style={{
            background: 'transparent',
            color: '#7a8699',
            border: '1px solid #dde1e8',
            borderRadius: 3, padding: '4px 11px',
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, fontWeight: 500,
            cursor: 'pointer', transition: 'background 0.12s, color 0.12s',
            opacity: (loading || resetting) ? 0.4 : 1,
            marginRight: 4,
          }}
          title="Reload scenario from backend with randomized link conditions"
          onMouseEnter={(e) => { if (!loading && !resetting) { (e.currentTarget as HTMLButtonElement).style.color = '#dc2626'; (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(220,38,38,0.35)'; } }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = '#7a8699'; (e.currentTarget as HTMLButtonElement).style.borderColor = '#dde1e8'; }}
        >
          Reset
        </button>
        <button
          onClick={refresh}
          disabled={loading || resetting}
          style={{
            background: 'transparent',
            color: '#4a5568',
            border: '1px solid #dde1e8',
            borderRadius: 3, padding: '4px 11px',
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, fontWeight: 500,
            cursor: 'pointer', transition: 'background 0.12s',
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
          background: '#f5f6f8',
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, color: '#7a8699',
          letterSpacing: '0.02em',
        }}>
          Loading mission data…
        </div>
      )}

      {!loading && error && (
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#f5f6f8',
          flexDirection: 'column', gap: 12,
        }}>
          <div style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 12, color: '#dc2626',
            padding: '10px 20px', background: 'rgba(220,38,38,0.05)',
            border: '1px solid rgba(220,38,38,0.18)', borderRadius: 4,
          }}>
            Error: {error}
          </div>
          <button
            onClick={refresh}
            style={{
              background: '#eef3fc',
              color: '#1d4ed8',
              border: '1px solid rgba(29,78,216,0.22)',
              borderRadius: 3, padding: '6px 16px',
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontSize: 12, fontWeight: 500, cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Phase 6E-C7: Source context banner ───────────────────────────── */}
      {!loading && !error && (
        <SourceContextBanner
          source={sourceSummary}
          missionId={missionState?.mission_id ?? null}
        />
      )}

      {/* ── Legacy mode banner ───────────────────────────────────────────── */}
      {!loading && !error && !hasDataProducts && dataProductsCount === 0 && (
        <div style={{
          background: 'rgba(217,119,6,0.05)',
          borderBottom: '1px solid rgba(217,119,6,0.18)',
          padding: '8px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          flexShrink: 0,
          zIndex: 50,
        }}>
          <span style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9, fontWeight: 700, letterSpacing: '0.07em',
            color: '#d97706', flexShrink: 0,
          }}>
            LIMITED DEMO MODE
          </span>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, color: '#4a5568', flex: 1,
          }}>
            {missionState ? `${missionState.current_event} — ` : ''}
            Legacy packet scenario active. High-volume AI prioritization, anomaly analysis, and spacecraft geometry are unavailable.
          </span>
          <button
            onClick={() => handleSwitchScenario('mission_data_v3.json')}
            disabled={scenarioSwitching}
            style={{
              padding: '4px 12px',
              background: '#eef3fc',
              color: '#1d4ed8',
              border: '1px solid rgba(29,78,216,0.28)',
              borderRadius: 3, cursor: 'pointer', flexShrink: 0,
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
              activePulse={activePulse}
              pulseDirection={
                choreographyActive
                  ? (choreographyPhase === 'plan_uplink'
                    ? 'earth_to_spacecraft'
                    : 'spacecraft_to_earth')
                  : 'idle'
              }
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
                width: 4,
                flexShrink: 0,
                background: 'transparent',
                borderLeft: '1px solid #dde1e8',
                cursor: 'col-resize',
                position: 'relative',
                zIndex: 30,
                transition: 'background 0.15s, border-color 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.background = 'rgba(29,78,216,0.06)';
                (e.currentTarget as HTMLDivElement).style.borderLeftColor = 'rgba(29,78,216,0.35)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.background = 'transparent';
                (e.currentTarget as HTMLDivElement).style.borderLeftColor = '#dde1e8';
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
                    width: 2, height: 2, borderRadius: '50%',
                    background: 'rgba(29,78,216,0.25)',
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
            sourceMode={sourceSummary?.mode ?? null}
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
            authorizedAtMs={authorizedAtMs}
            playbackStartedAtMs={playbackStartedAtMs}
            onSetPlaybackStarted={setPlaybackStartedAtMs}
            aiRecommendationRejected={aiRecommendationRejected}
            sessionEvents={sessionEvents}
            choreographyActive={choreographyActive}
            pendingExecutionPlan={pendingExecutionPlan}
            onExecuteApproval={handleExecuteApproval}
            executionResult={executionId ? (executionResultRef.current.get(executionId) ?? null) : null}
            onChoreographyComplete={handleChoreographyComplete}
            onChoreographyError={(msg) => {
              // Phase 5.1F (WORKSTREAM D): Check scenario identity before committing error state.
              const snap = executionSnapshotRef.current;
              if (snap && snap.scenarioPath !== currentActiveScenarioPathRef.current) {
                console.warn('[GCSI] Stale execution error discarded (scenario switched).');
                return;
              }
              setError(msg);
              setChoreographyActive(false);
              setApprovalPhase('ready');
            }}
            onAttemptPulse={setActivePulse}
            onChoreographyPhaseChange={(phase) => {
              setChoreographyPhase(phase);
              // Phase 5.1F: keep application-level presentationPhase in sync.
              // This ensures navigation away and back shows the correct current phase.
              // Phase only ever advances forward — never regresses.
              setPresentationPhase((prev) => {
                const order: import('./components/TransmissionSequencePanel').TransmissionChoreographyPhase[] =
                  ['plan_uplink', 'contact_wait', 'transmitting', 'signal_transit', 'complete'];
                const prevIdx = order.indexOf(prev);
                const newIdx = order.indexOf(phase);
                return newIdx > prevIdx ? phase : prev;
              });
            }}
            presentationPhase={presentationPhase}
          />
        </div>
      )}
    </>
  );
}
