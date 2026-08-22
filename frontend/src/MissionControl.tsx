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
import { RecommendationPanel } from './components/RecommendationPanel';
import { ApprovalBar } from './components/ApprovalBar';

// ---------------------------------------------------------------------------
// Styles (inline — single-file CSS-in-JS for MVP)
// ---------------------------------------------------------------------------

const styles = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0d1117; color: #e6edf3; font-size: 14px; }
  h1 { font-size: 20px; padding: 12px 16px; border-bottom: 1px solid #30363d; }
  h1 small { font-weight: 400; color: #8b949e; font-size: 13px; margin-left: 8px; }
  #root { display: flex; flex-direction: column; min-height: 100vh; }
  .mission-control { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; flex: 1; }
  .panel { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
  .panel h2 { font-size: 14px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  .panel h3 { font-size: 13px; color: #8b949e; margin: 8px 0 4px; }
  .panel p { margin-bottom: 6px; line-height: 1.5; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 4px 8px; text-align: left; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 500; font-size: 11px; text-transform: uppercase; }
  code { background: #21262d; border-radius: 3px; padding: 1px 5px; font-size: 12px; }
  .approval-bar { grid-column: 1 / -1; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
  .approval-bar h2 { font-size: 14px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  button { cursor: pointer; border: none; border-radius: 4px; margin: 0 4px; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  input[type=text] { background: #0d1117; border: 1px solid #30363d; color: #e6edf3; border-radius: 4px; padding: 4px 8px; font-size: 13px; }
  .spinner { color: #8b949e; padding: 24px; text-align: center; }
  .error-banner { color: #ef4444; padding: 8px 16px; background: #2d1117; border-bottom: 1px solid #ef4444; }
`;

// ---------------------------------------------------------------------------
// MissionControl component
// ---------------------------------------------------------------------------

export default function MissionControl() {
  const [linkState, setLinkState] = useState<LinkState | null>(null);
  const [missionState, setMissionState] = useState<MissionState | null>(null);
  const [queue, setQueue] = useState<CandidatePlan | null>(null);
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stateData, queueData] = await Promise.all([getState(), getQueue()]);
      setLinkState(stateData.link_state);
      setMissionState(stateData.mission_state);
      setQueue(queueData);
      // Recommendation is optional — don't fail if Granite is unavailable.
      try {
        const rec = await getRecommendation();
        setRecommendation(rec);
      } catch {
        setRecommendation(null);
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
    // Update state from simulation result.
    setLinkState(result.simulation_result.link_state);
    setMissionState(result.simulation_result.mission_state);
  }

  if (loading) return <div className="spinner">Loading mission data…</div>;
  if (error) return <div className="error-banner">Error: {error} <button onClick={refresh}>Retry</button></div>;
  if (!linkState || !missionState || !queue) return null;

  return (
    <>
      <style>{styles}</style>
      <h1>GCSI — Ground Control Signal Insight <small>Mission Control</small></h1>
      {approveResult && (
        <div style={{ padding: '8px 16px', background: '#0f2d0f', color: '#22c55e', borderBottom: '1px solid #22c55e' }}>
          ✓ Plan approved. Elapsed: {approveResult.simulation_result.elapsed_time_s.toFixed(1)} s |
          Delivered: {approveResult.simulation_result.delivered_packets.length} |
          Deferred: {approveResult.simulation_result.deferred_packets.length}
        </div>
      )}
      <div className="mission-control">
        <LinkHealthPanel linkState={linkState} />
        <MissionStatePanel missionState={missionState} />
        <TransmissionQueuePanel plan={queue} />
        {recommendation && <RecommendationPanel recommendation={recommendation} />}
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
