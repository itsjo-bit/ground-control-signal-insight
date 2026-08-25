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
import { LinkHealthPanel } from './components/LinkHealthPanel';
import { MissionStatePanel } from './components/MissionStatePanel';
import { CommBudgetBar } from './components/CommBudgetBar';
import { SignalGeometryBlock } from './components/SignalGeometryBlock';
import { TransmissionQueuePanel } from './components/TransmissionQueuePanel';
import { PlanComparisonPanel } from './components/PlanComparisonPanel';
import { RecommendationPanel } from './components/RecommendationPanel';
import { AIDecisionPanel } from './components/AIDecisionPanel';
import { MissionDecisionPanel } from './components/MissionDecisionPanel';
import { ApprovalBar } from './components/ApprovalBar';
import type { ApprovalPhase } from './components/ApprovalBar';
import { SimulationPanel } from './components/SimulationPanel';
import { TransmissionSummaryPanel } from './components/TransmissionSummaryPanel';
import { TransmissionNarrativePanel } from './components/TransmissionNarrativePanel';
import { TransmissionOutcomeBanner } from './components/TransmissionOutcomeBanner';
import { MissionReportPanel } from './components/MissionReportPanel';
import { PlanSwitcher } from './components/PlanSwitcher';
import { OrbitBackground } from './components/OrbitBackground';
import { usePanelLayout } from './hooks/usePanelLayout';
import type { PanelId, LayoutPreset } from './hooks/usePanelLayout';

const RESIZABLE_PANEL_IDS = new Set<PanelId>([
  'mission-state',
  'link-health',
  'baseline-plan',
  'plan-comparison',
  'ai-decision',
  'ai-reasoning',
  'mission-decision',
  'simulation',
  'mission-report',
]);

const styles = `
  :root { --panel: #0b1220; --panel-alt: #0e1729; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font-sans);
    background: var(--glow-tl), var(--glow-br), var(--bg);
    color: var(--text); font-size: 14px; -webkit-font-smoothing: antialiased;
  }
  #root { display: flex; flex-direction: column; min-height: 100vh; position: relative; }
  .orbit-bg-wrap { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; overflow: hidden; }
  .orbit-bg { width: 100%; height: 100%; opacity: 0.22; }
  .orbit-path { fill: none; stroke: rgba(124,158,255,0.35); stroke-width: 1; stroke-dasharray: 4 6; }
  .orbit-arc-travelled { fill: none; stroke: rgba(53,231,183,0.5); stroke-width: 1.5; }
  .orbit-dot { fill: rgba(53,231,183,0.9); }
  .orbit-dot--los { fill: rgba(255,77,94,0.7); animation: los-pulse 1.4s ease-out forwards; }
  @keyframes los-pulse { 0% { opacity: 1; r: 5; } 60% { opacity: 0.6; r: 9; } 100% { opacity: 0.3; r: 5; } }
  .orbit-dot--complete-success { fill: rgba(53,231,183,1.0); }
  .orbit-dot--complete-warning { fill: rgba(255,182,72,0.9); }
  .orbit-dot--complete-neutral { fill: rgba(124,158,255,0.7); }
  .orbit-earth { fill: rgba(30,50,100,0.7); stroke: rgba(124,158,255,0.4); stroke-width: 1; }
  .orbit-earth-glow { fill: none; stroke: rgba(124,158,255,0.12); stroke-width: 1; }
  .orbit-beam-track { fill: none; stroke: rgba(53,231,183,0.12); stroke-width: 1; }
  .orbit-beam-pulse { fill: none; stroke: rgba(53,231,183,0.65); stroke-width: 1.5; stroke-linecap: round; }
  .orbit-beam-label { fill: rgba(53,231,183,0.55); font-family: 'IBM Plex Mono', monospace; font-size: 9px; letter-spacing: 0.06em; }
  .mc-header {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 12px 20px; border-bottom: 1px solid var(--border);
    background: rgba(5,7,13,0.9); position: sticky; top: 0; z-index: 200; backdrop-filter: blur(4px);
  }
  .mc-header h1 {
    font-size: 16px; font-weight: 600; flex: 1; min-width: 0;
    letter-spacing: 0.01em; display: flex; align-items: baseline; gap: 10px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mc-title-gradient {
    background: linear-gradient(90deg, #38bdf8 0%, #818cf8 60%, #c084fc 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  }
  .mc-header h1 small {
    font-weight: 500; color: var(--text-muted); font-size: 11px; margin-left: 2px;
    font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.1em; flex-shrink: 0;
  }
  .live-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--signal); flex-shrink: 0; animation: pulse 2s infinite; }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(53,231,183,0.55); } 70% { box-shadow: 0 0 0 8px rgba(53,231,183,0); } 100% { box-shadow: 0 0 0 0 rgba(53,231,183,0); } }
  .sim-badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; background: rgba(255,182,72,0.08); color: var(--warn); border: 1px solid rgba(255,182,72,0.35); border-radius: 3px; font-family: var(--font-mono); font-size: 11px; font-weight: 600; letter-spacing: 0.05em; white-space: nowrap; }
  .provider-badge { display: inline-block; padding: 3px 10px; background: rgba(124,158,255,0.08); color: var(--ai); border: 1px solid rgba(124,158,255,0.35); border-radius: 3px; font-family: var(--font-mono); font-size: 11px; font-weight: 600; letter-spacing: 0.03em; white-space: nowrap; }
  .refresh-btn { background: var(--panel-alt); color: var(--text); border: 1px solid var(--border); border-radius: 3px; padding: 5px 14px; font-size: 12px; font-family: var(--font-mono); cursor: pointer; transition: background 0.15s, border-color 0.15s; white-space: nowrap; }
  .refresh-btn:hover { background: var(--border); border-color: var(--border-strong); }
  .refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .mission-control {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px; padding: 18px; flex: 1; max-width: 1920px; width: 100%; margin: 0 auto;
    position: relative; z-index: 10; align-items: start;
  }
  @media (max-width: 700px) { .mission-control { grid-template-columns: 1fr; padding: 10px; gap: 10px; } }
  @media (min-width: 1400px) { .mission-control { padding: 20px 28px; gap: 16px; } }
  @media (min-width: 1800px) { .mission-control { padding: 24px 36px; gap: 18px; } }
  .panel { background: var(--panel-bg); border: 1px solid var(--panel-border); border-radius: var(--panel-radius); padding: 16px; min-width: 0; height: 100%; box-sizing: border-box; overflow-y: auto; overflow-x: hidden; }
  .dnd-section { position: relative; min-height: 60px; }
  .panel-col-2 { grid-column: span 2; }
  @media (max-width: 700px) { .panel-col-2 { grid-column: span 1; } }
  .panel h2 { font-family: var(--font-mono); font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--border); display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }
  .panel h3 { font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; margin: 12px 0 6px; }
  .panel p { margin-bottom: 8px; line-height: 1.6; }
  .waveform-wrap { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.04); }
  th { color: var(--text-dim); font-weight: 500; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; font-family: var(--font-mono); }
  td { font-family: var(--font-mono); font-size: 13px; }
  td:first-child { font-family: var(--font-sans); color: var(--text-muted); font-size: 13px; }
  code { background: rgba(124,158,255,0.08); color: var(--ai); border-radius: 3px; padding: 2px 6px; font-size: 12px; font-family: var(--font-mono); }
  .ai-hero { border-color: var(--ai-panel-border); background: rgba(6,10,18,0.96); box-shadow: var(--ai-panel-glow); }
  .ai-hero h2 { color: var(--ai); border-bottom-color: rgba(124,158,255,0.2); }
  .approval-bar { background: var(--panel-bg); border: 1px solid var(--panel-border); border-radius: var(--panel-radius); padding: 16px; min-width: 0; }
  .approval-bar h2 { font-family: var(--font-mono); font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 12px; }
  button { cursor: pointer; border: none; border-radius: 4px; margin: 0 4px; font-family: var(--font-mono); }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  input[type=text] { background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 3px; padding: 6px 10px; font-size: 13px; font-family: var(--font-mono); }
  .btn-approve { background: var(--btn-primary-bg); color: var(--btn-primary-color); border: none !important; font-weight: 700; padding: 5px 16px; font-size: 12px; box-shadow: var(--btn-primary-glow); transition: opacity 0.15s, box-shadow 0.15s; }
  .btn-approve:hover:not(:disabled) { opacity: 0.88; box-shadow: 0 0 26px rgba(99,102,241,0.55); }
  .btn-override { background: transparent; color: var(--warn); border: 1px solid rgba(255,182,72,0.4) !important; font-weight: 600; padding: 5px 16px; font-size: 12px; transition: background 0.15s; }
  .btn-override:hover:not(:disabled) { background: rgba(255,182,72,0.08); }
  .btn-reset { background: transparent; color: var(--text-muted); border: 1px solid var(--border) !important; padding: 5px 12px; font-size: 12px; transition: background 0.15s; }
  .btn-reset:hover:not(:disabled) { background: rgba(255,255,255,0.04); }
  .drag-list { display: flex; flex-direction: column; gap: 2px; max-height: 280px; overflow-y: auto; }
  .drag-item { display: flex; align-items: center; gap: 10px; padding: 5px 8px; background: var(--panel-alt); border: 1px solid var(--border); border-radius: 3px; cursor: grab; user-select: none; transition: background 0.12s; font-family: var(--font-mono); font-size: 12px; }
  .drag-item:hover { background: var(--border); }
  .drag-item:active { cursor: grabbing; }
  .drag-handle { color: var(--text-dim); font-size: 14px; flex-shrink: 0; }
  .drag-rank { color: var(--text-dim); min-width: 18px; text-align: right; }
  .drag-id { min-width: 120px; }
  .drag-type { min-width: 80px; font-size: 11px; font-weight: 600; }
  .drag-crit { color: var(--text-muted); font-size: 11px; min-width: 65px; }
  .drag-size { color: var(--text-dim); font-size: 11px; }
  .sim-ctrl { background: var(--panel-alt); color: var(--text); border: 1px solid var(--border) !important; padding: 4px 10px; font-size: 14px; transition: background 0.12s; }
  .sim-ctrl:hover { background: var(--border); }
  .sim-timeline { position: relative; height: 6px; background: var(--border); border-radius: 3px; overflow: visible; margin: 0 0 6px; }
  .sim-timeline-fill { height: 100%; background: rgba(53,231,183,0.25); border-radius: 3px; }
  .sim-marker { position: absolute; top: 50%; width: 10px; height: 10px; border-radius: 50%; }
  .plan-switcher { display: flex; gap: 4px; margin-bottom: 10px; flex-wrap: wrap; }
  .plan-tab { display: flex; align-items: center; gap: 6px; padding: 5px 12px; background: var(--panel-alt); border: 1px solid var(--border) !important; border-radius: 3px; color: var(--text-muted); font-family: var(--font-mono); font-size: 11px; cursor: pointer; transition: background 0.12s, border-color 0.12s, color 0.12s; }
  .plan-tab:hover { background: var(--border); color: var(--text); }
  .plan-tab--active { background: rgba(124,158,255,0.10); color: var(--text); border-color: rgba(124,158,255,0.45) !important; box-shadow: var(--tab-active-glow); }
  .plan-tab__label { font-weight: 600; }
  .plan-tab__ai-badge { background: rgba(124,158,255,0.15); color: var(--ai); border-radius: 2px; padding: 1px 5px; font-size: 9px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
  .plan-tab__risk { font-size: 11px; font-weight: 600; }
  .risk-breakdown { background: var(--panel-alt); border: 1px solid var(--border-strong); border-radius: 4px; padding: 12px; margin: 8px 0; animation: fade-in 0.15s ease-out; }
  @keyframes fade-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
  .risk-breakdown__header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
  .risk-breakdown__close { background: none; border: none !important; color: var(--text-dim); font-size: 13px; cursor: pointer; padding: 0 2px; }
  .risk-breakdown__close:hover { color: var(--text); }
  .risk-breakdown__total { margin-top: 10px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); border-top: 1px solid var(--border); padding-top: 8px; }
  .risk-row { margin-bottom: 8px; }
  .risk-row__header { display: flex; justify-content: space-between; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin-bottom: 3px; }
  .risk-row__label { color: var(--text); }
  .risk-row__weight { color: var(--text-dim); margin: 0 2px; }
  .risk-row__contrib { color: var(--text); font-weight: 600; margin-left: 2px; }
  .risk-bar-track { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .risk-bar-fill { height: 100%; border-radius: 2px; }
  .whatif-section { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
  .whatif-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .whatif-label { font-family: var(--font-mono); font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; }
  .whatif-preview-badge { background: rgba(255,182,72,0.12); color: var(--warn); border: 1px solid rgba(255,182,72,0.4); border-radius: 2px; padding: 1px 6px; font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }
  .whatif-slider { flex: 1; height: 3px; accent-color: var(--warn); cursor: pointer; }
  .whatif-reset { background: none; border: 1px solid var(--border) !important; color: var(--text-dim); font-size: 11px; padding: 2px 6px; border-radius: 3px; cursor: pointer; transition: color 0.12s; }
  .whatif-reset:hover { color: var(--text); }
  .plan-content-fade { animation: fade-in 0.2s ease-out; }
  .reset-btn { background: transparent; color: var(--critical); border: 1px solid rgba(255,77,94,0.35) !important; border-radius: 3px; padding: 5px 14px; font-size: 12px; font-family: var(--font-mono); cursor: pointer; transition: background 0.15s; white-space: nowrap; }
  .reset-btn:hover:not(:disabled) { background: rgba(255,77,94,0.08); }
  .reset-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .spinner { color: var(--text-muted); padding: 48px; text-align: center; font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; position: relative; z-index: 10; }
  .error-banner { color: var(--critical); padding: 10px 20px; background: rgba(255,77,94,0.06); border-bottom: 1px solid rgba(255,77,94,0.3); font-family: var(--font-mono); font-size: 13px; position: relative; z-index: 10; }
  .mc-header-controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-left: auto; }
  .mc-ctrl-btn { background: var(--panel-alt); color: var(--text-muted); border: 1px solid var(--border); border-radius: 3px; padding: 5px 12px; font-size: 11px; font-family: var(--font-mono); cursor: pointer; transition: background 0.15s, color 0.15s, border-color 0.15s; white-space: nowrap; letter-spacing: 0.04em; }
  .mc-ctrl-btn:hover { background: var(--border); color: var(--text); border-color: var(--border-strong); }
  .mc-ctrl-btn--active { background: rgba(124,158,255,0.10); color: var(--ai); border-color: rgba(124,158,255,0.4); }
  .mc-dropdown { position: relative; display: inline-block; }
  .mc-dropdown-menu { position: absolute; top: calc(100% + 6px); right: 0; background: #0b1220; border: 1px solid var(--border-strong); border-radius: 6px; padding: 6px 0; z-index: 300; min-width: 180px; box-shadow: 0 8px 32px rgba(0,0,0,0.55); animation: fade-in 0.12s ease-out; }
  .mc-dropdown-menu--left { right: auto; left: 0; }
  .mc-dropdown-title { padding: 4px 14px 6px; font-family: var(--font-mono); font-size: 10px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.1em; border-bottom: 1px solid var(--border); margin-bottom: 4px; }
  .mc-dropdown-item { display: flex; align-items: center; gap: 8px; padding: 6px 14px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); cursor: pointer; user-select: none; transition: background 0.1s, color 0.1s; white-space: nowrap; }
  .mc-dropdown-item:hover { background: rgba(255,255,255,0.05); color: var(--text); }
  .mc-dropdown-item--active { color: var(--ai); }
  .mc-dropdown-check { width: 14px; height: 14px; border: 1px solid var(--border-strong); border-radius: 3px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 9px; background: var(--bg); }
  .mc-dropdown-check--on { background: rgba(124,158,255,0.15); border-color: rgba(124,158,255,0.5); color: var(--ai); }
  .mc-dropdown-divider { height: 1px; background: var(--border); margin: 6px 0; }
  .panel-slot { display: contents; }
  .panel-drag-handle { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; margin-right: 4px; color: var(--text-dim); font-size: 13px; cursor: grab; opacity: 0.5; transition: opacity 0.12s; flex-shrink: 0; vertical-align: middle; }
  .panel-drag-handle:hover { opacity: 1; }
  .panel--dragging { opacity: 0.4; outline: 2px dashed rgba(124,158,255,0.5); outline-offset: 2px; }
  .panel--drag-over { outline: 2px solid rgba(53,231,183,0.5); outline-offset: 2px; }
  .resize-handle { position: absolute; right: 0; bottom: 0; width: 18px; height: 18px; cursor: ns-resize; touch-action: none; z-index: 6; }
  .resize-handle::after { content: ''; position: absolute; right: 4px; bottom: 4px; width: 8px; height: 8px; border-right: 2px solid var(--text-dim); border-bottom: 2px solid var(--text-dim); opacity: 0.5; transition: opacity 0.15s, border-color 0.15s; }
  .resize-handle:hover::after, .resize-handle--active::after { opacity: 1; border-color: var(--ai); }
  @media (max-width: 900px) { .mc-header h1 small { display: none; } .sim-badge { display: none; } }
  @media (max-width: 600px) { .mc-header { padding: 10px 12px; gap: 6px; } .mc-header h1 { font-size: 14px; } .mc-ctrl-btn { padding: 4px 8px; font-size: 10px; } .panel { padding: 12px; } }
`;

const PRESET_LABELS: Record<LayoutPreset, string> = {
  'mission-control': 'Mission Control',
  'ai-analysis': 'AI Analysis',
  'minimal': 'Minimal',
};

function PanelsMenu({
  panels,
  onToggle,
}: {
  panels: Array<{ id: PanelId; label: string; visible: boolean }>;
  onToggle: (id: PanelId) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div className="mc-dropdown" ref={ref}>
      <button
        className={`mc-ctrl-btn${open ? ' mc-ctrl-btn--active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        title="Show / hide dashboard panels"
      >
        Panels {open ? '\u25b2' : '\u25be'}
      </button>
      {open && (
        <div className="mc-dropdown-menu">
          <div className="mc-dropdown-title">Visible Panels</div>
          {panels.map((p) => (
            <div key={p.id} className="mc-dropdown-item" onClick={() => onToggle(p.id)}>
              <span className={`mc-dropdown-check${p.visible ? ' mc-dropdown-check--on' : ''}`}>
                {p.visible ? '\u2713' : ''}
              </span>
              {p.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PresetsMenu({
  current,
  onApply,
}: {
  current: LayoutPreset;
  onApply: (p: LayoutPreset) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const presets: LayoutPreset[] = ['mission-control', 'ai-analysis', 'minimal'];

  return (
    <div className="mc-dropdown" ref={ref}>
      <button
        className={`mc-ctrl-btn${open ? ' mc-ctrl-btn--active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        title="Switch layout preset"
      >
        Layout {open ? '\u25b2' : '\u25be'}
      </button>
      {open && (
        <div className="mc-dropdown-menu mc-dropdown-menu--left">
          <div className="mc-dropdown-title">Layout Presets</div>
          {presets.map((p) => (
            <div
              key={p}
              className={`mc-dropdown-item${p === current ? ' mc-dropdown-item--active' : ''}`}
              onClick={() => { onApply(p); setOpen(false); }}
            >
              {p === current ? '\u25cf ' : '\u25cb '}
              {PRESET_LABELS[p]}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const MIN_PANEL_HEIGHT = 140;
const MAX_PANEL_HEIGHT = 720;

function ResizeHandle({ onResizeDelta }: { onResizeDelta: (deltaY: number) => void }) {
  const dragging = useRef(false);
  const lastY = useRef(0);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    e.preventDefault();
    dragging.current = true;
    lastY.current = e.clientY;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    e.stopPropagation();
    const delta = e.clientY - lastY.current;
    lastY.current = e.clientY;
    if (delta !== 0) onResizeDelta(delta);
  };
  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  return (
    <div
      className="resize-handle"
      draggable={false}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      title="Drag to resize panel height"
      role="separator"
      aria-orientation="horizontal"
    />
  );
}

function DraggableSection({
  id, colSpan, heightPx, resizable,
  onDragStart, onDragOver, onDrop, onResize, children,
}: {
  id: PanelId; colSpan: 1 | 2; heightPx: number | null; resizable?: boolean;
  onDragStart: (id: PanelId) => void; onDragOver: (id: PanelId) => void;
  onDrop: (id: PanelId) => void; onResize?: (id: PanelId, heightPx: number) => void;
  children: React.ReactNode;
}) {
  const [isDragging, setIsDragging] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const sectionRef = useRef<HTMLDivElement>(null);

  const colClass = colSpan === 2 ? 'panel-col-2' : '';
  const dragClass = isDragging ? 'panel--dragging' : '';
  const overClass = isDragOver ? 'panel--drag-over' : '';
  const style: React.CSSProperties = {};
  if (heightPx !== null) style.height = heightPx;

  const handleResizeDelta = (deltaY: number) => {
    if (!onResize) return;
    const current = sectionRef.current?.getBoundingClientRect().height ?? heightPx ?? MIN_PANEL_HEIGHT;
    const next = Math.max(MIN_PANEL_HEIGHT, Math.min(MAX_PANEL_HEIGHT, current + deltaY));
    onResize(id, next);
  };

  return (
    <div
      ref={sectionRef}
      className={`dnd-section ${colClass} ${dragClass} ${overClass}`.trim()}
      style={style}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', id);
        setIsDragging(true);
        onDragStart(id);
      }}
      onDragEnd={() => { setIsDragging(false); setIsDragOver(false); }}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setIsDragOver(true); onDragOver(id); }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={(e) => { e.preventDefault(); setIsDragOver(false); onDrop(id); }}
    >
      {children}
      {resizable && <ResizeHandle onResizeDelta={handleResizeDelta} />}
    </div>
  );
}

export default function MissionControl() {
  const [linkState, setLinkState] = useState<LinkState | null>(null);
  const [missionState, setMissionState] = useState<MissionState | null>(null);
  // Phase 2E-C2: communication budget from GET /state (C1 fields)
  const [availableCapacityBits, setAvailableCapacityBits] = useState<number>(0);
  const [queuedDataBits, setQueuedDataBits] = useState<number>(0);
  const [dataProductsCount, setDataProductsCount] = useState<number>(0);
  const [anomalies, setAnomalies] = useState<AnomalyEvent[]>([]);
  // Phase 2E-C3-D: spacecraft communication geometry (null for legacy scenarios)
  const [distanceKm, setDistanceKm] = useState<number | null>(null);
  const [propagationDelayS, setPropagationDelayS] = useState<number | null>(null);
  const [roundTripTimeS, setRoundTripTimeS] = useState<number | null>(null);
  const [queue, setQueue] = useState<CandidatePlan | null>(null);
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
  const [aiProvider, setAiProvider] = useState<string | null>(null);
  const [aiPrioritization, setAiPrioritization] = useState<CandidatePrioritization | null>(null);
  const [aiCandidateCount, setAiCandidateCount] = useState<number | null>(null);
  const [aiPrioritizationError, setAiPrioritizationError] = useState<string | null>(null);
  const [recommendationError, setRecommendationError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);
  // Phase 2E-D4: operator approval state machine
  // IDLE → AI_ANALYZING → READY → TRANSMITTING → COMPLETE
  const [approvalPhase, setApprovalPhase] = useState<ApprovalPhase>('idle');
  const [allPlans, setAllPlans] = useState<CandidatePlan[]>([]);
  const [allEvaluations, setAllEvaluations] = useState<EvaluationResult[]>([]);
  const [activePlanId, setActivePlanId] = useState<string>('baseline');
  const [whatIfEvals, setWhatIfEvals] = useState<EvaluationResult[] | null>(null);
  const [whatIfSnr, setWhatIfSnr] = useState<number | null>(null);
  const totalWindowRef = useRef<number | null>(null);

  const { prefs, togglePanel, applyPreset, reorderPanels, setPanelHeight, resetLayout } = usePanelLayout();
  const dragSourceId = useRef<PanelId | null>(null);

  function handlePanelDragStart(id: PanelId) { dragSourceId.current = id; }
  function handlePanelDragOver(_id: PanelId) {}
  function handlePanelDrop(targetId: PanelId) {
    if (dragSourceId.current && dragSourceId.current !== targetId) {
      reorderPanels(dragSourceId.current, targetId);
    }
    dragSourceId.current = null;
  }

  const refresh = useCallback(async () => {
    setLoading(true); setError(null); setApproveResult(null); setWhatIfEvals(null); setWhatIfSnr(null);
    setApprovalPhase('ai_analyzing');
    try {
      const [stateData, queueData] = await Promise.all([getState(), getQueue()]);
      setLinkState(stateData.link_state);
      setMissionState(stateData.mission_state);
      // Phase 2E-C2: store communication budget fields
      setAvailableCapacityBits(stateData.available_capacity_bits ?? 0);
      setQueuedDataBits(stateData.queued_data_bits ?? 0);
      setDataProductsCount(stateData.data_products_count ?? 0);
      setAnomalies(stateData.anomalies ?? []);
      // Phase 2E-C3-D: store spacecraft communication geometry fields
      setDistanceKm(stateData.distance_km ?? null);
      setPropagationDelayS(stateData.propagation_delay_s ?? null);
      setRoundTripTimeS(stateData.round_trip_time_s ?? null);
      setQueue(queueData);
      if (totalWindowRef.current === null) totalWindowRef.current = stateData.mission_state.comm_window_remaining_s;
      try {
        const plans = await generatePlans();
        setAllPlans(plans);
        const evals = await Promise.all(plans.map((p) => evaluatePlan(p)));
        setAllEvaluations(evals);
        setActivePlanId(plans[0]?.plan_id ?? 'baseline');
      } catch { setAllPlans([]); setAllEvaluations([]); }
      let recOk = false;
      try {
        const resp = await getRecommendation();
        setRecommendation(resp.recommendation);
        setAiProvider(resp.provider);
        setAiPrioritization(resp.prioritization ?? null);
        setAiCandidateCount(resp.candidate_count ?? null);
        setAiPrioritizationError(resp.prioritization_error ?? null);
        setRecommendationError(null);
        recOk = true;
      } catch (recErr) {
        setRecommendation(null);
        setAiProvider(null);
        setAiPrioritization(null);
        setAiCandidateCount(null);
        setAiPrioritizationError(null);
        setRecommendationError(String(recErr));
      }
      // Phase 2E-D4: advance state machine once AI analysis is done
      setApprovalPhase(recOk ? 'ready' : 'idle');
    } catch (err) { setError(String(err)); setApprovalPhase('idle'); }
    finally { setLoading(false); }
  }, []);

  const handleReset = useCallback(async () => {
    setResetting(true); setError(null);
    try { await resetScenario(); totalWindowRef.current = null; } catch {}
    finally { setResetting(false); }
    await refresh();
  }, [refresh]);

  useEffect(() => { handleReset(); }, [handleReset]);

  function handleApproved(result: ApproveResponse) {
    setApproveResult(result);
    setLinkState(result.simulation_result.link_state);
    setMissionState(result.simulation_result.mission_state);
    setApprovalPhase('complete');
  }

  // Phase 2E-D3 (P0-2): reset to 'ready' on approval failure so operator can retry.
  function handleApprovalError() {
    setApprovalPhase('ready');
  }

  function handleWhatIfResult(result: WhatIfEvalResponse, snrDb: number) {
    if (result.evaluations.length === 0) { setWhatIfEvals(null); setWhatIfSnr(null); }
    else { setWhatIfEvals(result.evaluations); setWhatIfSnr(snrDb); }
  }

  if (loading) return <div className="spinner">Loading mission data...</div>;
  if (error) return <div className="error-banner">Error: {error} <button onClick={refresh}>Retry</button></div>;
  if (!linkState || !missionState || !queue) return null;

  const displayEvals = whatIfEvals ?? allEvaluations;
  const isWhatIfPreview = whatIfEvals !== null;
  const activePlan = allPlans.find((p) => p.plan_id === activePlanId) ?? queue;
  const activeEval = displayEvals.find((e) => e.plan_id === activePlanId) ?? null;
  const recEval = recommendation ? (displayEvals.find((e) => e.plan_id === recommendation.recommended_plan_id) ?? null) : null;
  const riskWeights = { w_deadline_miss: 0.40, w_critical_deficit: 0.40, w_window_pressure: 0.20 };

  // Resolve the AI-recommended plan from the generated plan list.
  // This is the plan the deterministic scheduler created from AI-ordered packets.
  // It is NOT the baseline queue — this is the P0 fix.
  const recPlan = recommendation
    ? (allPlans.find((p) => p.plan_id === recommendation.recommended_plan_id) ?? null)
    : null;

  const panelRenderers: Record<PanelId, React.ReactNode> = {
    'mission-state': (
      <>
        <MissionStatePanel missionState={missionState} />
        <section className="panel" style={{ paddingTop: 8, paddingBottom: 10 }}>
          <CommBudgetBar
            availableCapacityBits={availableCapacityBits}
            queuedDataBits={queuedDataBits}
            dataProductsCount={dataProductsCount}
            remainingWindowS={linkState.remaining_window_s}
          />
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', marginTop: 12 }} />
          <SignalGeometryBlock
            distanceKm={distanceKm}
            propagationDelayS={propagationDelayS}
            roundTripTimeS={roundTripTimeS}
          />
        </section>
      </>
    ),
    'link-health': <LinkHealthPanel linkState={linkState} onWhatIfResult={handleWhatIfResult} />,
    'baseline-plan': <TransmissionQueuePanel plan={queue} />,
    'ai-decision': (
      <AIDecisionPanel
        prioritization={aiPrioritization}
        providerName={aiProvider}
        candidateCount={aiCandidateCount}
        prioritizationError={aiPrioritizationError}
      />
    ),
    'plan-comparison': allPlans.length > 0 ? (
      <section className="panel">
        <h2>
          Plan Comparison
          {isWhatIfPreview && (
            <span style={{ marginLeft: 8, color: 'var(--warn)', fontSize: 11, textTransform: 'none', letterSpacing: 0, fontWeight: 600 }}>
              — WHAT-IF PREVIEW (SNR {whatIfSnr?.toFixed(1)} dB)
            </span>
          )}
        </h2>
        <PlanSwitcher plans={allPlans} evaluations={displayEvals} activePlanId={activePlanId} aiRecommendedPlanId={recommendation?.recommended_plan_id ?? null} onSelect={setActivePlanId} />
      </section>
    ) : null,
    'mission-decision': (
      <MissionDecisionPanel
        prioritization={aiPrioritization}
        recommendation={recommendation}
        allPlans={allPlans}
        recEval={recEval}
        linkState={linkState}
        providerName={aiProvider}
        prioritizationError={aiPrioritizationError}
        candidateCount={aiCandidateCount}
      />
    ),
    'ai-order': recommendation ? (
      <div key={activePlanId} className="plan-content-fade">
        <PlanComparisonPanel activePlan={activePlan} recommendation={recommendation} evaluation={activeEval} />
      </div>
    ) : (
      <section className="panel">
        <h2>AI Recommended Order</h2>
        <p style={{ color: '#8b949e' }}>
          <strong style={{ color: '#f97316' }}>AI Recommendation unavailable.</strong>
          &nbsp;{recommendationError ? `The backend returned an error. (${recommendationError})` : 'The AI provider could not be reached.'}
        </p>
        <p style={{ color: '#57606a', fontSize: 12, marginTop: 6 }}>Ensure the backend has a scenario loaded and restart it to enable AI recommendations.</p>
      </section>
    ),
    'ai-reasoning': <RecommendationPanel recommendation={recommendation} providerName={aiProvider} evaluation={recEval} riskWeights={riskWeights} />,
    'approval': (
      <>
        {/* Phase 2E-D3 (D3-E): always show the AI-recommended plan summary.
            Falls back to activePlan only when no recommendation is available. */}
        <TransmissionSummaryPanel
          plan={recPlan ?? activePlan}
          evaluation={recEval ?? activeEval}
          availableCapacityBits={availableCapacityBits}
        />
        <ApprovalBar
          recommendedPlanId={recommendation ? recommendation.recommended_plan_id : null}
          recommendedPlan={recPlan}
          baselinePlan={queue}
          approvalPhase={approvalPhase}
          onApproved={handleApproved}
          onTransmitting={() => setApprovalPhase('transmitting')}
          onApprovalError={handleApprovalError}
        />
        {/* Phase 2E-D5: compact post-transmission outcome banner.
            isAiRecommendedPlan is true only when the simulated plan_id matches
            the AI recommendation.  For operator-override plans the plan_id is
            'operator-override' (set in ApprovalBar.handleOverride) so this
            evaluates to false, showing OPERATOR OVERRIDE instead of AI RECOMMENDED.
            We use plan_id comparison rather than a separate boolean flag so the
            badge remains correct even if MissionControl state is stale. */}
        <TransmissionOutcomeBanner
          approvalPhase={approvalPhase}
          simulationResult={approveResult?.simulation_result ?? null}
          isAiRecommendedPlan={
            approveResult?.simulation_result?.plan_id !== undefined &&
            approveResult.simulation_result.plan_id !== 'operator-override'
          }
        />
      </>
    ),
    'simulation': (
      <>
        <SimulationPanel approveResult={approveResult} propagationDelayS={propagationDelayS} />
        {approveResult && (
          <TransmissionNarrativePanel
            prioritization={aiPrioritization}
            simulationResult={approveResult.simulation_result}
            anomalies={anomalies}
            isAiRecommendedPlan={
              approveResult.simulation_result.plan_id !== undefined &&
              approveResult.simulation_result.plan_id !== 'operator-override'
            }
          />
        )}
      </>
    ),
    'mission-report': (
      <MissionReportPanel
        approvalPhase={approvalPhase}
        missionState={missionState}
        recommendation={recommendation}
        aiPrioritization={aiPrioritization}
        aiProvider={aiProvider}
        simulationResult={approveResult?.simulation_result ?? null}
        anomalies={anomalies}
        distanceKm={distanceKm}
        propagationDelayS={propagationDelayS}
        roundTripTimeS={roundTripTimeS}
      />
    ),
  };

  const visiblePanels = prefs.panels.filter((p) => p.visible);

  return (
    <>
      <style>{styles}</style>
      <div className="orbit-bg-wrap">
        <OrbitBackground
          commWindowRemainingS={missionState.comm_window_remaining_s}
          totalWindowS={totalWindowRef.current ?? missionState.comm_window_remaining_s}
          distanceKm={distanceKm}
          approvalPhase={approvalPhase}
          simulationResult={approveResult?.simulation_result ?? null}
        />
      </div>
      <header className="mc-header">
        <h1>
          <span className="live-dot" title="Live" />
          <span className="mc-title-gradient">GCSI \u2014 Ground Control Signal Insight</span>
          <small>Mission Control</small>
        </h1>
        <span className="sim-badge">SIMULATED</span>
        {isWhatIfPreview && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', background: 'rgba(255,182,72,0.12)', color: 'var(--warn)', border: '1px solid rgba(255,182,72,0.5)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>
            WHAT-IF \u00b7 SNR {whatIfSnr?.toFixed(1)} dB
          </span>
        )}
        {aiProvider && <span className="provider-badge">AI: {aiProvider}</span>}
        <div className="mc-header-controls">
          <PanelsMenu panels={prefs.panels.map((p) => ({ id: p.id, label: p.label, visible: p.visible }))} onToggle={togglePanel} />
          <PresetsMenu current={prefs.preset} onApply={applyPreset} />
          <button className="mc-ctrl-btn" onClick={resetLayout} title="Restore default panel visibility, order, and sizes (does not reset the scenario)">Reset Layout</button>
          <button className="reset-btn" onClick={handleReset} disabled={loading || resetting} title="Reload scenario from backend with randomized link conditions">Reset Scenario</button>
          <button className="refresh-btn" onClick={refresh} disabled={loading || resetting}>Refresh</button>
        </div>
      </header>
      <div className="mission-control">
        {visiblePanels.map((panelCfg) => {
          const content = panelRenderers[panelCfg.id];
          if (content === null || content === undefined) return null;
          return (
            <DraggableSection key={panelCfg.id} id={panelCfg.id} colSpan={panelCfg.span} heightPx={panelCfg.heightPx} resizable={RESIZABLE_PANEL_IDS.has(panelCfg.id)} onDragStart={handlePanelDragStart} onDragOver={handlePanelDragOver} onDrop={handlePanelDrop} onResize={setPanelHeight}>
              {content}
            </DraggableSection>
          );
        })}
      </div>
    </>
  );
}
