/**
 * MissionControl — GCSI V3 primary layout.
 *
 * Three-column mission-control interface:
 *   LEFT:   NavigationSidebar (56px, persistent)
 *   CENTER: MissionViewport (3D Three.js scene, flex:1)
 *   RIGHT:  RightPanel (contextual control panel, 320px)
 *
 * All existing backend logic, API calls, state management, and mission
 * functionality is preserved. The visual architecture is redesigned around
 * the central 3D space visualization.
 *
 * State machine: IDLE → AI_ANALYZING → READY → TRANSMITTING → COMPLETE
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  getState,
  getQueue,
  getRecommendation,
  generatePlans,
  evaluatePlan,
  resetScenario,
} from './api/client';
import type {
  AIRecommendation,
  AnomalyEvent,
  ApproveResponse,
  CandidatePlan,
  CandidatePrioritization,
  EvaluationResult,
  LinkState,
  MissionState,
  WhatIfEvalResponse,
} from './types/domain';
import type { ApprovalPhase } from './components/ApprovalBar';
import { NavigationSidebar, type NavSection } from './components/NavigationSidebar';
import { MissionViewport } from './components/MissionViewport';
import { RightPanel } from './components/RightPanel';
import { useResizablePanel } from './hooks/useResizablePanel';
import { useViewSettings } from './hooks/useViewSettings';

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
    min-width: 0;
  }
  .approval-bar h2 {
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.01em;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  button { cursor: pointer; border: none; border-radius: 6px; margin: 0; font-family: var(--font-sans); }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  input[type=text] {
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    font-family: var(--font-mono);
  }
  input[type=range] { accent-color: var(--warn); cursor: pointer; }

  /* ── Action buttons ── */
  .btn-approve {
    background: var(--btn-primary-bg);
    color: var(--btn-primary-color);
    border: none !important;
    font-weight: 600;
    padding: 6px 16px;
    font-size: 12px;
    border-radius: 6px;
    transition: opacity 0.15s;
  }
  .btn-approve:hover:not(:disabled) { opacity: 0.85; }
  .btn-override {
    background: transparent;
    color: var(--warn);
    border: 1px solid rgba(245,158,11,0.35) !important;
    font-weight: 500;
    padding: 6px 16px;
    font-size: 12px;
    border-radius: 6px;
    transition: background 0.15s;
  }
  .btn-override:hover:not(:disabled) { background: rgba(245,158,11,0.07); }
  .btn-reset {
    background: transparent;
    color: var(--text-muted);
    border: 1px solid var(--border) !important;
    padding: 6px 12px;
    font-size: 12px;
    border-radius: 6px;
    transition: background 0.15s;
  }
  .btn-reset:hover:not(:disabled) { background: rgba(255,255,255,0.04); }

  /* ── Drag list (transmission queue) ── */
  .drag-list { display: flex; flex-direction: column; gap: 3px; max-height: 220px; overflow-y: auto; }
  .drag-item {
    display: flex; align-items: center; gap: 8px; padding: 5px 8px;
    background: rgba(255,255,255,0.025);
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: grab;
    user-select: none;
    transition: background 0.12s;
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .drag-item:hover { background: rgba(255,255,255,0.045); }
  .drag-item:active { cursor: grabbing; }
  .drag-handle { color: var(--text-dim); font-size: 13px; flex-shrink: 0; }
  .drag-rank { color: var(--text-dim); min-width: 16px; text-align: right; }
  .drag-id { min-width: 100px; }
  .drag-type { min-width: 70px; font-size: 10px; font-weight: 600; }
  .drag-crit { color: var(--text-muted); font-size: 10px; min-width: 56px; }
  .drag-size { color: var(--text-dim); font-size: 10px; }

  /* ── Simulation controls ── */
  .sim-ctrl {
    background: rgba(255,255,255,0.04);
    color: var(--text);
    border: 1px solid var(--border) !important;
    padding: 5px 10px;
    font-size: 13px;
    border-radius: 6px;
    transition: background 0.12s;
  }
  .sim-ctrl:hover { background: rgba(255,255,255,0.07); }
  .sim-timeline {
    position: relative;
    height: 4px;
    background: var(--border);
    border-radius: 3px;
    overflow: visible;
    margin: 0 0 6px;
  }
  .sim-timeline-fill { height: 100%; background: rgba(52,211,153,0.28); border-radius: 3px; }
  .sim-marker { position: absolute; top: 50%; width: 8px; height: 8px; border-radius: 50%; }

  /* ── Plan tabs ── */
  .plan-switcher { display: flex; gap: 4px; margin-bottom: 10px; flex-wrap: wrap; }
  .plan-tab {
    display: flex; align-items: center; gap: 5px; padding: 5px 11px;
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--border) !important;
    border-radius: 6px;
    color: var(--text-muted);
    font-family: var(--font-sans);
    font-size: 11px;
    cursor: pointer;
    transition: background 0.12s, border-color 0.12s, color 0.12s;
  }
  .plan-tab:hover { background: rgba(255,255,255,0.055); color: var(--text); }
  .plan-tab--active {
    background: rgba(129,140,248,0.10);
    color: var(--text);
    border-color: rgba(129,140,248,0.35) !important;
  }
  .plan-tab__label { font-weight: 600; }
  .plan-tab__ai-badge {
    background: rgba(129,140,248,0.12);
    color: var(--ai);
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 9px;
    font-weight: 600;
  }
  .plan-tab__risk { font-size: 10px; font-weight: 600; }

  /* ── Risk breakdown ── */
  .risk-breakdown {
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    padding: 12px 14px;
    margin: 8px 0;
  }
  .risk-breakdown__header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 10px;
    font-family: var(--font-sans);
    font-size: 11px;
    color: var(--text-muted);
  }
  .risk-breakdown__close {
    background: none; border: none !important; color: var(--text-dim);
    font-size: 14px; cursor: pointer; padding: 0 2px; border-radius: 3px;
  }
  .risk-breakdown__close:hover { color: var(--text); }
  .risk-breakdown__total {
    margin-top: 10px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
    border-top: 1px solid var(--border);
    padding-top: 8px;
  }
  .risk-row { margin-bottom: 8px; }
  .risk-row__header {
    display: flex; justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-muted);
    margin-bottom: 3px;
  }
  .risk-row__label { color: var(--text); }
  .risk-row__weight { color: var(--text-dim); margin: 0 3px; }
  .risk-row__contrib { color: var(--text); font-weight: 600; margin-left: 2px; }
  .risk-bar-track { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .risk-bar-fill { height: 100%; border-radius: 2px; }

  /* ── What-if section ── */
  .whatif-section { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
  .whatif-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .whatif-label {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    color: var(--text-muted);
  }
  .whatif-preview-badge {
    background: rgba(245,158,11,0.10);
    color: var(--warn);
    border: 1px solid rgba(245,158,11,0.30);
    border-radius: 4px;
    padding: 1px 6px;
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 600;
  }
  .whatif-slider { flex: 1; height: 3px; accent-color: var(--warn); cursor: pointer; }
  .whatif-reset {
    background: none;
    border: 1px solid var(--border) !important;
    color: var(--text-dim);
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 5px;
    cursor: pointer;
    transition: color 0.12s;
  }
  .whatif-reset:hover { color: var(--text); }

  /* ── Animations ── */
  .plan-content-fade { animation: fade-in 0.18s ease-out; }
  @keyframes fade-in { from { opacity: 0; transform: translateY(-2px); } to { opacity: 1; transform: none; } }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(52,211,153,0.50); }
    70%  { box-shadow: 0 0 0 6px rgba(52,211,153,0); }
    100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); }
  }
`;

// ── MissionControl ─────────────────────────────────────────────────────────────

export default function MissionControl() {
  // ── Resize + settings hooks ────────────────────────────────────────────────
  const { width: panelWidth, handleMouseDown: handleDividerMouseDown, resetWidth: resetPanelWidth, DEFAULT_WIDTH } = useResizablePanel();
  const { settings: viewSettings, update: updateViewSetting, resetSettings } = useViewSettings();

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
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
  const [aiProvider, setAiProvider] = useState<string | null>(null);
  const [aiPrioritization, setAiPrioritization] = useState<CandidatePrioritization | null>(null);
  const [aiCandidateCount, setAiCandidateCount] = useState<number | null>(null);
  const [aiPrioritizationError, setAiPrioritizationError] = useState<string | null>(null);
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

  // ── Navigation ─────────────────────────────────────────────────────────────
  const [activeSection, setActiveSection] = useState<NavSection>('mission');

  // ── Load / refresh ─────────────────────────────────────────────────────────

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setApproveResult(null);
    setWhatIfEvals(null);
    setWhatIfSnr(null);
    setApprovalPhase('ai_analyzing');
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
        setAllPlans(plans);
        const evals = await Promise.all(plans.map((p) => evaluatePlan(p)));
        setAllEvaluations(evals);
        setActivePlanId(plans[0]?.plan_id ?? 'baseline');
      } catch {
        setAllPlans([]);
        setAllEvaluations([]);
      }
      let recOk = false;
      try {
        const resp = await getRecommendation();
        setRecommendation(resp.recommendation);
        setAiProvider(resp.provider);
        setAiPrioritization(resp.prioritization ?? null);
        setAiCandidateCount(resp.candidate_count ?? null);
        setAiPrioritizationError(resp.prioritization_error ?? null);
        recOk = true;
      } catch (recErr) {
        setRecommendation(null);
        setAiProvider(null);
        setAiPrioritization(null);
        setAiCandidateCount(null);
        setAiPrioritizationError(null);
        console.warn('AI recommendation unavailable:', recErr);
      }
      setApprovalPhase(recOk ? 'ready' : 'idle');
    } catch (err) {
      setError(String(err));
      setApprovalPhase('idle');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleReset = useCallback(async () => {
    setResetting(true);
    setError(null);
    try {
      await resetScenario();
      totalWindowRef.current = null;
    } catch {}
    finally {
      setResetting(false);
    }
    await refresh();
  }, [refresh]);

  useEffect(() => {
    handleReset();
  }, [handleReset]);

  // ── Approval handlers ──────────────────────────────────────────────────────

  function handleApproved(result: ApproveResponse) {
    setApproveResult(result);
    setLinkState(result.simulation_result.link_state);
    setMissionState(result.simulation_result.mission_state);
    setApprovalPhase('complete');
    // Auto-navigate to log after successful transmission
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

  // ── Render ────────────────────────────────────────────────────────────────

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

        {aiProvider && (
          <span style={{
            padding: '2px 8px',
            background: 'rgba(129,140,248,0.08)',
            color: '#818cf8',
            border: '1px solid rgba(129,140,248,0.22)',
            borderRadius: 4,
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9, fontWeight: 600,
            flexShrink: 0,
          }}>
            AI · {aiProvider}
          </span>
        )}

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

      {/* ── Main 3-column layout ──────────────────────────────────────────── */}
      {!loading && !error && (
        <div style={{
          flex: 1,
          display: 'flex',
          overflow: 'hidden',
          minHeight: 0,
        }}>
          {/* LEFT: Navigation sidebar */}
          <NavigationSidebar
            active={activeSection}
            onNavigate={setActiveSection}
          />

          {/* CENTER: 3D Mission Viewport */}
          <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
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

          {/* ── Drag divider — resizes Main Control width ── */}
          <div
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

          {/* RIGHT: Contextual control panel — always visible (config view when no mission data) */}
          <RightPanel
            section={activeSection}
            panelWidth={panelWidth}
            panelDefaultWidth={DEFAULT_WIDTH}
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
            aiPrioritization={aiPrioritization}
            aiCandidateCount={aiCandidateCount}
            aiPrioritizationError={aiPrioritizationError}
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
          />
        </div>
      )}
    </>
  );
}
