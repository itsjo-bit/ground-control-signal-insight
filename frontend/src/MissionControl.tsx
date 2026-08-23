import { useEffect, useState, useCallback } from 'react';
import {
  getState,
  getQueue,
  getRecommendation,
} from './api/client';
import type {
  AIRecommendation,
  ApproveResponse,
  CandidatePlan,
  LinkState,
  MissionState,
} from './types/domain';
import { LinkHealthPanel } from './components/LinkHealthPanel';
import { MissionStatePanel } from './components/MissionStatePanel';
import { TransmissionQueuePanel } from './components/TransmissionQueuePanel';
import { PlanComparisonPanel } from './components/PlanComparisonPanel';
import { RecommendationPanel } from './components/RecommendationPanel';
import { ApprovalBar } from './components/ApprovalBar';
import { SimulationPanel } from './components/SimulationPanel';

// ---------------------------------------------------------------------------
// Styles (inline — single-file CSS-in-JS for MVP)
// ---------------------------------------------------------------------------

const styles = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0d1117; color: #e6edf3; font-size: 14px; }
  #root { display: flex; flex-direction: column; min-height: 100vh; }

  /* ---- header ---- */
  .mc-header {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 16px; border-bottom: 1px solid #30363d;
  }
  .mc-header h1 { font-size: 18px; flex: 1; }
  .mc-header h1 small { font-weight: 400; color: #8b949e; font-size: 13px; margin-left: 8px; }
  .sim-badge {
    display: inline-block; padding: 2px 10px;
    background: #2d1c0a; color: #f97316;
    border: 1px solid #f97316; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
  }
  .refresh-btn {
    background: #21262d; color: #e6edf3; border: 1px solid #30363d;
    border-radius: 4px; padding: 4px 12px; font-size: 13px;
    cursor: pointer;
  }
  .refresh-btn:hover { background: #30363d; }
  .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  /* ---- layout ---- */
  .mission-control {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px; padding: 12px; flex: 1;
  }
  .panel { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
  .panel-full { grid-column: 1 / -1; }
  .panel h2 { font-size: 13px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  .panel h3 { font-size: 12px; color: #8b949e; margin: 8px 0 4px; }
  .panel p { margin-bottom: 6px; line-height: 1.5; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 4px 8px; text-align: left; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 500; font-size: 11px; text-transform: uppercase; }
  code { background: #21262d; border-radius: 3px; padding: 1px 5px; font-size: 12px; }

  /* ---- approval bar ---- */
  .approval-bar { grid-column: 1 / -1; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
  .approval-bar h2 { font-size: 13px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  button { cursor: pointer; border: none; border-radius: 4px; margin: 0 4px; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  input[type=text] { background: #0d1117; border: 1px solid #30363d; color: #e6edf3; border-radius: 4px; padding: 4px 8px; font-size: 13px; }

  /* ---- state indicators ---- */
  .spinner { color: #8b949e; padding: 24px; text-align: center; }
  .error-banner { color: #ef4444; padding: 8px 16px; background: #2d1117; border-bottom: 1px solid #ef4444; }

  /* ---- provider badge ---- */
  .provider-badge {
    display: inline-block; padding: 2px 8px;
    background: #1a2332; color: #58a6ff;
    border: 1px solid #1f6feb; border-radius: 4px;
    font-size: 11px; font-weight: 600;
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
  const [error, setError] = useState<string | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setApproveResult(null);
    try {
      const [stateData, queueData] = await Promise.all([getState(), getQueue()]);
      setLinkState(stateData.link_state);
      setMissionState(stateData.mission_state);
      setQueue(queueData);

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

  useEffect(() => {
    refresh();
  }, [refresh]);

  function handleApproved(result: ApproveResponse) {
    setApproveResult(result);
    // Reflect post-simulation state in the link/mission panels immediately.
    setLinkState(result.simulation_result.link_state);
    setMissionState(result.simulation_result.mission_state);
  }

  if (loading) return <div className="spinner">Loading mission data…</div>;
  if (error) return <div className="error-banner">Error: {error} <button onClick={refresh}>Retry</button></div>;
  if (!linkState || !missionState || !queue) return null;

  return (
    <>
      <style>{styles}</style>

      {/* ── Header: title + simulated badge + refresh ── */}
      <header className="mc-header">
        <h1>
          GCSI — Ground Control Signal Insight
          <small>Mission Control</small>
        </h1>
        <span className="sim-badge">⚠ SIMULATED SCENARIO</span>
        {aiProvider && (
          <span className="provider-badge">AI: {aiProvider}</span>
        )}
        <button className="refresh-btn" onClick={refresh} disabled={loading}>
          ⟳ Refresh
        </button>
      </header>

      {/*
        ── Complete story — all 7 sections always rendered ──
        1. Mission State
        2. Link Health
        3. Baseline Plan
        4. AI Recommended Order   (unavailable state only if backend fails entirely)
        5. AI Reasoning + Evidence (unavailable state only if backend fails entirely)
        6. Approval / Override    (disabled state if no recommendation)
        7. Simulation             (placeholder until approval; real data after)
      */}
      <div className="mission-control">

        {/* 1 + 2: Mission context + link health */}
        <MissionStatePanel missionState={missionState} />
        <LinkHealthPanel linkState={linkState} />

        {/* 3: Baseline plan */}
        <TransmissionQueuePanel plan={queue} />

        {/* 4: AI comparison (full width) */}
        {recommendation ? (
          <PlanComparisonPanel baseline={queue} recommendation={recommendation} />
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

        {/* 5: AI reasoning + evidence (full width) — null-safe, renders unavailable state */}
        <RecommendationPanel recommendation={recommendation} providerName={aiProvider} />

        {/* 6: Approve / Override (full width) — null-safe, renders disabled state */}
        <ApprovalBar
          recommendedPlanId={recommendation ? recommendation.recommended_plan_id : null}
          onApproved={handleApproved}
        />

        {/* 7: Simulation results (full width) — placeholder until approval */}
        <SimulationPanel approveResult={approveResult} />

      </div>
    </>
  );
}
