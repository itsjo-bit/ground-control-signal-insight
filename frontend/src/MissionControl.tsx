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
  .ai-unavailable-panel { grid-column: 1 / -1; }
  .approve-banner { padding: 8px 16px; background: #0f2d0f; color: #22c55e; border-bottom: 1px solid #22c55e; }
`;

// ---------------------------------------------------------------------------
// MissionControl component
// ---------------------------------------------------------------------------

export default function MissionControl() {
  const [linkState, setLinkState] = useState<LinkState | null>(null);
  const [missionState, setMissionState] = useState<MissionState | null>(null);
  const [queue, setQueue] = useState<CandidatePlan | null>(null);
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
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

      // Recommendation is optional — Granite may not be configured.
      // Show an informational panel rather than silently hiding the area.
      try {
        const rec = await getRecommendation();
        setRecommendation(rec);
        setRecommendationError(null);
      } catch (recErr) {
        setRecommendation(null);
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
    // Reflect post-simulation state in all panels immediately.
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
        <button className="refresh-btn" onClick={refresh} disabled={loading}>
          ⟳ Refresh
        </button>
      </header>

      {/* ── Post-approval simulation result banner ── */}
      {approveResult && (
        <div className="approve-banner">
          ✓ Plan approved and simulated. Elapsed: {approveResult.simulation_result.elapsed_time_s.toFixed(1)} s
          &nbsp;|&nbsp; Delivered: {approveResult.simulation_result.delivered_packets.length}
          &nbsp;|&nbsp; Deferred: {approveResult.simulation_result.deferred_packets.length}
          &nbsp;|&nbsp; Failed: {approveResult.simulation_result.failed_packets.length}
        </div>
      )}

      {/*
        ── Story-driven panel order ──
        1. Mission state (risk + context)
        2. Link health
        3. Baseline transmission plan
        4. AI recommended order / baseline comparison  (or unavailable notice)
        5. AI reasoning + evidence
        6. Approve / Override
      */}
      <div className="mission-control">

        {/* Row 1: Mission context + link health */}
        <MissionStatePanel missionState={missionState} />
        <LinkHealthPanel linkState={linkState} />

        {/* Row 2: Baseline plan */}
        <TransmissionQueuePanel plan={queue} />

        {/* Row 3: AI comparison (full width) or unavailable notice */}
        {recommendation ? (
          <PlanComparisonPanel baseline={queue} recommendation={recommendation} />
        ) : (
          <section className="panel ai-unavailable-panel">
            <h2>AI Recommended Order</h2>
            <p style={{ color: '#8b949e' }}>
              <strong style={{ color: '#f97316' }}>AI Recommendation unavailable.</strong>
              &nbsp;
              {recommendationError
                ? `Granite API is not configured or unavailable. (${recommendationError})`
                : 'Granite API is not configured or unavailable.'}
            </p>
            <p style={{ color: '#57606a', fontSize: 12, marginTop: 6 }}>
              Set <code>GCSI_GRANITE_API_KEY</code> in your <code>.env</code> file and restart the backend
              to enable AI-powered transmission plan recommendations.
            </p>
          </section>
        )}

        {/* Row 4: AI reasoning + evidence (full width) */}
        {recommendation && (
          <RecommendationPanel recommendation={recommendation} />
        )}

        {/* Row 5: Approve / Override (full width) */}
        {recommendation && (
          <ApprovalBar
            recommendedPlanId={recommendation.recommended_plan_id}
            onApproved={handleApproved}
          />
        )}

      </div>
    </>
  );
}
