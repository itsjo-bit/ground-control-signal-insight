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
  :root {
    --font-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', monospace;
    --font-sans: 'IBM Plex Sans', system-ui, sans-serif;

    --bg: #05070d;
    --panel: #0b1220;
    --panel-alt: #0e1729;
    --border: #1a2540;
    --border-strong: #2a3a5c;
    --text: #dce6f5;
    --text-muted: #6f83a3;
    --text-dim: #3d4a63;

    --signal: #35e7b7;
    --warn: #ffb648;
    --critical: #ff4d5e;
    --ai: #7c9eff;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--font-sans);
    background:
      radial-gradient(ellipse 1200px 500px at 50% -8%, rgba(124,158,255,0.07), transparent 60%),
      repeating-linear-gradient(0deg, rgba(255,255,255,0.012) 0px, rgba(255,255,255,0.012) 1px, transparent 1px, transparent 24px),
      var(--bg);
    color: var(--text);
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
  }
  #root { display: flex; flex-direction: column; min-height: 100vh; }

  /* ---- header ---- */
  .mc-header {
    display: flex; align-items: center; gap: 14px;
    padding: 14px 20px; border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, rgba(13,19,32,0.7), transparent);
  }
  .mc-header h1 {
    font-size: 16px; font-weight: 600; flex: 1;
    letter-spacing: 0.01em; display: flex; align-items: baseline; gap: 10px;
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
  }
  .panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 16px;
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
    border-color: rgba(124,158,255,0.3);
    background: linear-gradient(180deg, rgba(124,158,255,0.05), var(--panel) 45%);
    box-shadow: 0 0 0 1px rgba(124,158,255,0.05), 0 10px 28px -14px rgba(124,158,255,0.25);
  }
  .ai-hero h2 { color: var(--ai); border-bottom-color: rgba(124,158,255,0.2); }

  /* ---- approval bar ---- */
  .approval-bar {
    grid-column: 1 / -1; background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 16px;
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

  /* ---- state indicators ---- */
  .spinner {
    color: var(--text-muted); padding: 48px; text-align: center;
    font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  }
  .error-banner {
    color: var(--critical); padding: 10px 20px; background: rgba(255,77,94,0.06);
    border-bottom: 1px solid rgba(255,77,94,0.3); font-family: var(--font-mono); font-size: 13px;
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
  if (error) return <div className="error-banner">Error: {error} <button onClick={refresh}>Retry</button>
</div>;
  if (!linkState || !missionState || !queue) return null;

  return (
    <>
<style>{styles}</style>

      {/* ── Header: title + simulated badge + refresh ── */}
      <header className="mc-header">
<h1>
<span className="live-dot" title="Live" />
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
