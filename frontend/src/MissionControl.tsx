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
  ApproveResponse,
  CandidatePlan,
  EvaluationResult,
  LinkState,
  MissionState,
  WhatIfEvalResponse,
} from './types/domain';
import { LinkHealthPanel } from './components/LinkHealthPanel';
import { MissionStatePanel } from './components/MissionStatePanel';
import { TransmissionQueuePanel } from './components/TransmissionQueuePanel';
import { PlanComparisonPanel } from './components/PlanComparisonPanel';
import { RecommendationPanel } from './components/RecommendationPanel';
import { ApprovalBar } from './components/ApprovalBar';
import { SimulationPanel } from './components/SimulationPanel';
import { PlanSwitcher } from './components/PlanSwitcher';
import { OrbitBackground } from './components/OrbitBackground';

// ---------------------------------------------------------------------------
// Styles (inline — single-file CSS-in-JS for MVP)
// ---------------------------------------------------------------------------

const styles = `
  /* ------------------------------------------------------------------
   * Local token aliases — keep panel-alt / panel for components that
   * reference them directly; theme.css owns the canonical values.
   * ------------------------------------------------------------------ */
  :root {
    --panel: #0b1220;
    --panel-alt: #0e1729;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--font-sans);
    background:
      var(--glow-tl),
      var(--glow-br),
      var(--bg);
    color: var(--text);
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
  }
  #root { display: flex; flex-direction: column; min-height: 100vh; position: relative; }

  /* ---- orbit background (Feature 6) ---- */
  .orbit-bg-wrap {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    z-index: 0; pointer-events: none; overflow: hidden;
  }
  .orbit-bg {
    width: 100%; height: 100%;
    opacity: 0.22;
  }
  .orbit-path {
    fill: none; stroke: rgba(124,158,255,0.35); stroke-width: 1;
    stroke-dasharray: 4 6;
  }
  .orbit-arc-travelled {
    fill: none; stroke: rgba(53,231,183,0.5); stroke-width: 1.5;
  }
  .orbit-dot {
    fill: rgba(53,231,183,0.9);
  }
  .orbit-dot--los {
    fill: rgba(255,77,94,0.7);
    animation: los-pulse 1.4s ease-out forwards;
  }
  @keyframes los-pulse {
    0% { opacity: 1; r: 5; }
    60% { opacity: 0.6; r: 9; }
    100% { opacity: 0.3; r: 5; }
  }
  .orbit-earth {
    fill: rgba(30,50,100,0.7); stroke: rgba(124,158,255,0.4); stroke-width: 1;
  }
  .orbit-earth-glow {
    fill: none; stroke: rgba(124,158,255,0.12); stroke-width: 1;
  }

  /* ---- header ---- */
  .mc-header {
    display: flex; align-items: center; gap: 14px;
    padding: 14px 20px; border-bottom: 1px solid var(--border);
    background: rgba(5,7,13,0.85);
    position: relative; z-index: 10;
    backdrop-filter: blur(2px);
  }
  .mc-header h1 {
    font-size: 16px; font-weight: 600; flex: 1;
    letter-spacing: 0.01em; display: flex; align-items: baseline; gap: 10px;
  }
  /* Gradient text for the app title only — not section headers or data values */
  .mc-title-gradient {
    background: linear-gradient(90deg, #38bdf8 0%, #818cf8 60%, #c084fc 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .mc-header h1 small {
    font-weight: 500; color: var(--text-muted); font-size: 11px; margin-left: 2px;
    font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.1em;
  }
  .live-dot {
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    background: var(--signal); flex-shrink: 0;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(53,231,183,0.55); }
    70% { box-shadow: 0 0 0 8px rgba(53,231,183,0); }
    100% { box-shadow: 0 0 0 0 rgba(53,231,183,0); }
  }
  .sim-badge {
    display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
    background: rgba(255,182,72,0.08); color: var(--warn);
    border: 1px solid rgba(255,182,72,0.35); border-radius: 3px;
    font-family: var(--font-mono); font-size: 11px; font-weight: 600; letter-spacing: 0.05em;
  }
  .provider-badge {
    display: inline-block; padding: 3px 10px;
    background: rgba(124,158,255,0.08); color: var(--ai);
    border: 1px solid rgba(124,158,255,0.35); border-radius: 3px;
    font-family: var(--font-mono); font-size: 11px; font-weight: 600; letter-spacing: 0.03em;
  }
  .refresh-btn {
    background: var(--panel-alt); color: var(--text); border: 1px solid var(--border);
    border-radius: 3px; padding: 5px 14px; font-size: 12px; font-family: var(--font-mono);
    cursor: pointer; transition: background 0.15s, border-color 0.15s;
  }
  .refresh-btn:hover { background: var(--border); border-color: var(--border-strong); }
  .refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ---- layout ---- */
  .mission-control {
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 14px; padding: 18px; flex: 1;
    max-width: 1360px; width: 100%; margin: 0 auto;
    position: relative; z-index: 10;
  }
  .panel {
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: var(--panel-radius); padding: 16px;
  }
  .panel-full { grid-column: 1 / -1; }
  .panel h2 {
    font-family: var(--font-mono); font-size: 11px; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.1em;
    margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--border);
  }
  .panel h3 {
    font-family: var(--font-mono); font-size: 10px; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.08em; margin: 12px 0 6px;
  }
  .panel p { margin-bottom: 8px; line-height: 1.6; }

  .waveform-wrap {
    background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
    padding: 4px 8px; margin-bottom: 12px;
  }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.04); }
  th {
    color: var(--text-dim); font-weight: 500; font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.06em; font-family: var(--font-mono);
  }
  td { font-family: var(--font-mono); font-size: 13px; }
  td:first-child { font-family: var(--font-sans); color: var(--text-muted); font-size: 13px; }
  code {
    background: rgba(124,158,255,0.08); color: var(--ai);
    border-radius: 3px; padding: 2px 6px; font-size: 12px; font-family: var(--font-mono);
  }

  /* ---- AI hero panel ---- */
  .ai-hero {
    border-color: var(--ai-panel-border);
    background: rgba(6,10,18,0.96);
    box-shadow: var(--ai-panel-glow);
  }
  .ai-hero h2 { color: var(--ai); border-bottom-color: rgba(124,158,255,0.2); }

  /* ---- approval bar ---- */
  .approval-bar {
    grid-column: 1 / -1; background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: var(--panel-radius); padding: 16px;
  }
  .approval-bar h2 {
    font-family: var(--font-mono); font-size: 11px; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 12px;
  }
  button { cursor: pointer; border: none; border-radius: 4px; margin: 0 4px; font-family: var(--font-mono); }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  input[type=text] {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 3px; padding: 6px 10px; font-size: 13px; font-family: var(--font-mono);
  }

  /* ---- approve / override buttons ---- */
  /* Primary action — gradient fill + glow, one per view */
  .btn-approve {
    background: var(--btn-primary-bg); color: var(--btn-primary-color);
    border: none !important;
    font-weight: 700; padding: 5px 16px; font-size: 12px;
    box-shadow: var(--btn-primary-glow);
    transition: opacity 0.15s, box-shadow 0.15s;
  }
  .btn-approve:hover:not(:disabled) { opacity: 0.88; box-shadow: 0 0 26px rgba(99,102,241,0.55); }
  /* Secondary actions — ghost/outline only, no gradient, no glow */
  .btn-override {
    background: transparent; color: var(--warn);
    border: 1px solid rgba(255,182,72,0.4) !important;
    font-weight: 600; padding: 5px 16px; font-size: 12px;
    transition: background 0.15s;
  }
  .btn-override:hover:not(:disabled) { background: rgba(255,182,72,0.08); }
  .btn-reset {
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border) !important;
    padding: 5px 12px; font-size: 12px;
    transition: background 0.15s;
  }
  .btn-reset:hover:not(:disabled) { background: rgba(255,255,255,0.04); }

  /* ---- drag-to-reorder (Feature 3) ---- */
  .drag-list {
    display: flex; flex-direction: column; gap: 2px;
    max-height: 280px; overflow-y: auto;
  }
  .drag-item {
    display: flex; align-items: center; gap: 10px;
    padding: 5px 8px; background: var(--panel-alt);
    border: 1px solid var(--border); border-radius: 3px;
    cursor: grab; user-select: none;
    transition: background 0.12s;
    font-family: var(--font-mono); font-size: 12px;
  }
  .drag-item:hover { background: var(--border); }
  .drag-item:active { cursor: grabbing; }
  .drag-handle { color: var(--text-dim); font-size: 14px; flex-shrink: 0; }
  .drag-rank { color: var(--text-dim); min-width: 18px; text-align: right; }
  .drag-id { min-width: 120px; }
  .drag-type { min-width: 80px; font-size: 11px; font-weight: 600; }
  .drag-crit { color: var(--text-muted); font-size: 11px; min-width: 65px; }
  .drag-size { color: var(--text-dim); font-size: 11px; }

  /* ---- simulation playback (Feature 4) ---- */
  .sim-ctrl {
    background: var(--panel-alt); color: var(--text);
    border: 1px solid var(--border) !important;
    padding: 4px 10px; font-size: 14px;
    transition: background 0.12s;
  }
  .sim-ctrl:hover { background: var(--border); }
  .sim-timeline {
    position: relative; height: 6px; background: var(--border);
    border-radius: 3px; overflow: visible; margin: 0 0 6px;
  }
  .sim-timeline-fill {
    height: 100%; background: rgba(53,231,183,0.25);
    border-radius: 3px;
  }
  .sim-marker {
    position: absolute; top: 50%; width: 10px; height: 10px;
    border-radius: 50%;
  }

  /* ---- plan switcher tabs (Feature 1) ---- */
  .plan-switcher {
    display: flex; gap: 4px; margin-bottom: 10px; flex-wrap: wrap;
  }
  .plan-tab {
    display: flex; align-items: center; gap: 6px;
    padding: 5px 12px; background: var(--panel-alt);
    border: 1px solid var(--border) !important;
    border-radius: 3px; color: var(--text-muted);
    font-family: var(--font-mono); font-size: 11px; cursor: pointer;
    transition: background 0.12s, border-color 0.12s, color 0.12s;
  }
  .plan-tab:hover { background: var(--border); color: var(--text); }
  .plan-tab--active {
    background: rgba(124,158,255,0.10); color: var(--text);
    border-color: rgba(124,158,255,0.45) !important;
    box-shadow: var(--tab-active-glow);
  }
  .plan-tab__label { font-weight: 600; }
  .plan-tab__ai-badge {
    background: rgba(124,158,255,0.15); color: var(--ai);
    border-radius: 2px; padding: 1px 5px;
    font-size: 9px; font-weight: 700; letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .plan-tab__risk { font-size: 11px; font-weight: 600; }

  /* ---- risk breakdown (Feature 2) ---- */
  .risk-breakdown {
    background: var(--panel-alt); border: 1px solid var(--border-strong);
    border-radius: 4px; padding: 12px; margin: 8px 0;
    animation: fade-in 0.15s ease-out;
  }
  @keyframes fade-in {
    from { opacity: 0; transform: translateY(-4px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .risk-breakdown__header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 10px; font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted);
  }
  .risk-breakdown__close {
    background: none; border: none !important; color: var(--text-dim);
    font-size: 13px; cursor: pointer; padding: 0 2px;
  }
  .risk-breakdown__close:hover { color: var(--text); }
  .risk-breakdown__total {
    margin-top: 10px; font-family: var(--font-mono); font-size: 12px;
    color: var(--text-muted); border-top: 1px solid var(--border);
    padding-top: 8px;
  }
  .risk-row { margin-bottom: 8px; }
  .risk-row__header {
    display: flex; justify-content: space-between;
    font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted); margin-bottom: 3px;
  }
  .risk-row__label { color: var(--text); }
  .risk-row__weight { color: var(--text-dim); margin: 0 2px; }
  .risk-row__contrib { color: var(--text); font-weight: 600; margin-left: 2px; }
  .risk-bar-track {
    height: 4px; background: var(--border); border-radius: 2px; overflow: hidden;
  }
  .risk-bar-fill { height: 100%; border-radius: 2px; }

  /* ---- what-if slider (Feature 5) ---- */
  .whatif-section {
    margin-top: 12px; padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .whatif-header {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 6px;
  }
  .whatif-label {
    font-family: var(--font-mono); font-size: 10px; font-weight: 600;
    color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em;
  }
  .whatif-preview-badge {
    background: rgba(255,182,72,0.12); color: var(--warn);
    border: 1px solid rgba(255,182,72,0.4); border-radius: 2px;
    padding: 1px 6px; font-family: var(--font-mono); font-size: 10px;
    font-weight: 700; letter-spacing: 0.06em;
  }
  .whatif-slider {
    flex: 1; height: 3px; accent-color: var(--warn);
    cursor: pointer;
  }
  .whatif-reset {
    background: none; border: 1px solid var(--border) !important;
    color: var(--text-dim); font-size: 11px; padding: 2px 6px;
    border-radius: 3px; cursor: pointer;
    transition: color 0.12s;
  }
  .whatif-reset:hover { color: var(--text); }

  /* ---- plan comparison fade transition ---- */
  .plan-content-fade {
    animation: fade-in 0.2s ease-out;
  }

  /* ---- reset button — ghost/outline, no glow ---- */
  .reset-btn {
    background: transparent; color: var(--critical);
    border: 1px solid rgba(255,77,94,0.35) !important;
    border-radius: 3px; padding: 5px 14px; font-size: 12px; font-family: var(--font-mono);
    cursor: pointer; transition: background 0.15s;
  }
  .reset-btn:hover:not(:disabled) { background: rgba(255,77,94,0.08); }
  .reset-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ---- state indicators ---- */
  .spinner {
    color: var(--text-muted); padding: 48px; text-align: center;
    font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.08em;
    text-transform: uppercase; position: relative; z-index: 10;
  }
  .error-banner {
    color: var(--critical); padding: 10px 20px; background: rgba(255,77,94,0.06);
    border-bottom: 1px solid rgba(255,77,94,0.3); font-family: var(--font-mono);
    font-size: 13px; position: relative; z-index: 10;
  }
`;

// ---------------------------------------------------------------------------
// MissionControl component
// ---------------------------------------------------------------------------

export default function MissionControl() {
  const [linkState, setLinkState] = useState<LinkState | null>(null);
  const [missionState, setMissionState] = useState<MissionState | null>(null);
  const [queue, setQueue] = useState<CandidatePlan | null>(null);
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
  const [aiProvider, setAiProvider] = useState<string | null>(null);
  const [recommendationError, setRecommendationError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);

  // Feature 1: all 4 plans + their evaluations
  const [allPlans, setAllPlans] = useState<CandidatePlan[]>([]);
  const [allEvaluations, setAllEvaluations] = useState<EvaluationResult[]>([]);
  const [activePlanId, setActivePlanId] = useState<string>('baseline');

  // Feature 5: what-if overrides — replace evaluations when slider is used
  const [whatIfEvals, setWhatIfEvals] = useState<EvaluationResult[] | null>(null);
  const [whatIfSnr, setWhatIfSnr] = useState<number | null>(null);

  // Total window duration for orbit background (Feature 6) — captured on first load
  const totalWindowRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setApproveResult(null);
    setWhatIfEvals(null);
    setWhatIfSnr(null);
    try {
      const [stateData, queueData] = await Promise.all([getState(), getQueue()]);
      setLinkState(stateData.link_state);
      setMissionState(stateData.mission_state);
      setQueue(queueData);

      // Capture total window on first load
      if (totalWindowRef.current === null) {
        totalWindowRef.current = stateData.mission_state.comm_window_remaining_s;
      }

      // Feature 1: generate all 4 plans and evaluate each
      try {
        const plans = await generatePlans();
        setAllPlans(plans);
        const evals = await Promise.all(plans.map((p) => evaluatePlan(p)));
        setAllEvaluations(evals);
        // Default active plan to baseline
        setActivePlanId(plans[0]?.plan_id ?? 'baseline');
      } catch {
        // Non-fatal — plan switcher just stays empty
        setAllPlans([]);
        setAllEvaluations([]);
      }

      // Recommendation: always attempt; components handle the null case.
      try {
        const resp = await getRecommendation();
        setRecommendation(resp.recommendation);
        setAiProvider(resp.provider);
        setRecommendationError(null);
      } catch (recErr) {
        setRecommendation(null);
        setAiProvider(null);
        setRecommendationError(String(recErr));
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // Feature 0: auto-reset on mount so a fresh browser session never inherits
  // a previous tester's consumed state.  Falls back gracefully if the reset
  // endpoint returns 503 (no scenario loaded yet — backend may be starting up).
  const handleReset = useCallback(async () => {
    setResetting(true);
    setError(null);
    try {
      await resetScenario();
      // Reset the orbit background total-window reference so it re-anchors
      // to the freshly-restored window duration.
      totalWindowRef.current = null;
    } catch {
      // 503 = no scenario loaded; any other error is non-fatal for reset.
    } finally {
      setResetting(false);
    }
    await refresh();
  }, [refresh]);

  useEffect(() => {
    // Auto-reset on initial mount, then refresh.
    handleReset();
  }, [handleReset]);

  function handleApproved(result: ApproveResponse) {
    setApproveResult(result);
    setLinkState(result.simulation_result.link_state);
    setMissionState(result.simulation_result.mission_state);
  }

  // Feature 5: what-if results callback
  function handleWhatIfResult(result: WhatIfEvalResponse, snrDb: number) {
    if (result.evaluations.length === 0) {
      // Reset signal from slider
      setWhatIfEvals(null);
      setWhatIfSnr(null);
    } else {
      setWhatIfEvals(result.evaluations);
      setWhatIfSnr(snrDb);
    }
  }

  if (loading) return <div className="spinner">Loading mission data…</div>;
  if (error) return <div className="error-banner">Error: {error} <button onClick={refresh}>Retry</button></div>;
  if (!linkState || !missionState || !queue) return null;

  // Determine which evaluations to display (real or what-if preview)
  const displayEvals = whatIfEvals ?? allEvaluations;
  const isWhatIfPreview = whatIfEvals !== null;

  // Find the currently active plan + its evaluation
  const activePlan = allPlans.find((p) => p.plan_id === activePlanId) ?? queue;
  const activeEval = displayEvals.find((e) => e.plan_id === activePlanId) ?? null;

  // Find the evaluation for the AI-recommended plan
  const recEval = recommendation
    ? (displayEvals.find((e) => e.plan_id === recommendation.recommended_plan_id) ?? null)
    : null;

  // Risk weights — extract from first what-if response if available, else use defaults
  const riskWeights = {
    w_deadline_miss: 0.40,
    w_critical_deficit: 0.40,
    w_window_pressure: 0.20,
  };

  return (
    <>
<style>{styles}</style>

      {/* Feature 6: Orbit background — fixed, behind everything, pointer-events none */}
      <div className="orbit-bg-wrap">
        <OrbitBackground
          commWindowRemainingS={missionState.comm_window_remaining_s}
          totalWindowS={totalWindowRef.current ?? missionState.comm_window_remaining_s}
        />
      </div>

      {/* ── Header ── */}
      <header className="mc-header">
<h1>
<span className="live-dot" title="Live" />
          <span className="mc-title-gradient">GCSI — Ground Control Signal Insight</span>
          <small>Mission Control</small>
</h1>
<span className="sim-badge">⚠ SIMULATED SCENARIO</span>
        {isWhatIfPreview && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px',
            background: 'rgba(255,182,72,0.12)', color: 'var(--warn)',
            border: '1px solid rgba(255,182,72,0.5)', borderRadius: 3,
            fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700,
          }}>
            WHAT-IF PREVIEW · SNR {whatIfSnr?.toFixed(1)} dB
          </span>
        )}
        {aiProvider && (
          <span className="provider-badge">AI: {aiProvider}</span>
        )}
        <button
          className="reset-btn"
          onClick={handleReset}
          disabled={loading || resetting}
          title="Reload original scenario, discarding post-simulation state mutations"
        >
          ↺ Reset
        </button>
        <button className="refresh-btn" onClick={refresh} disabled={loading || resetting}>
          ⟳ Refresh
        </button>
</header>

      <div className="mission-control">

        {/* 1 + 2: Mission context + link health (with what-if slider) */}
        <MissionStatePanel missionState={missionState} />
        <LinkHealthPanel
          linkState={linkState}
          onWhatIfResult={handleWhatIfResult}
        />

        {/* 3: Baseline plan */}
        <TransmissionQueuePanel plan={queue} />

        {/* Feature 1: Plan switcher + comparison panel */}
        {allPlans.length > 0 && (
          <section className="panel panel-full" style={{ paddingBottom: 0 }}>
            <h2>
              Plan Comparison
              {isWhatIfPreview && (
                <span style={{
                  marginLeft: 8, color: 'var(--warn)', fontSize: 11,
                  textTransform: 'none', letterSpacing: 0, fontWeight: 600,
                }}>
                  — WHAT-IF PREVIEW (SNR {whatIfSnr?.toFixed(1)} dB)
                </span>
              )}
            </h2>
            <PlanSwitcher
              plans={allPlans}
              evaluations={displayEvals}
              activePlanId={activePlanId}
              aiRecommendedPlanId={recommendation?.recommended_plan_id ?? null}
              onSelect={setActivePlanId}
            />
          </section>
        )}

        {/* AI comparison panel */}
        {recommendation ? (
          <div key={activePlanId} className="plan-content-fade" style={{ gridColumn: '1 / -1' }}>
            <PlanComparisonPanel
              activePlan={activePlan}
              recommendation={recommendation}
              evaluation={activeEval}
            />
          </div>
        ) : (
          <section className="panel panel-full">
<h2>AI Recommended Order</h2>
<p style={{ color: '#8b949e' }}>
<strong style={{ color: '#f97316' }}>AI Recommendation unavailable.</strong>
              &nbsp;
              {recommendationError
                ? `The backend returned an error. (${recommendationError})`
                : 'The AI provider could not be reached.'}
            </p>
<p style={{ color: '#57606a', fontSize: 12, marginTop: 6 }}>
              Ensure the backend has a scenario loaded and restart it to enable AI recommendations.
            </p>
</section>
        )}

        {/* AI reasoning + evidence — with risk breakdown (Feature 2) */}
        <RecommendationPanel
          recommendation={recommendation}
          providerName={aiProvider}
          evaluation={recEval}
          riskWeights={riskWeights}
        />

        {/* Approval + drag-to-reorder (Feature 3) */}
        <ApprovalBar
          recommendedPlanId={recommendation ? recommendation.recommended_plan_id : null}
          baselinePlan={queue}
          onApproved={handleApproved}
        />

        {/* Simulation playback (Feature 4) */}
        <SimulationPanel approveResult={approveResult} />
</div>
</>
  );
}
