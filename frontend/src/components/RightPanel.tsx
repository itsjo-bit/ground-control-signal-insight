/**
 * RightPanel — GCSI V3.5 contextual main control panel (right side).
 *
 * V3.5 changes:
 * - Accepts workspaceMode: 'normal' | 'expanded' | 'focus'
 * - Panel width driven by workspaceMode (normal=manual, expanded=clamp(650,58vw,1100), focus=full)
 * - Workspace mode controls in panel header (⇔ Expand, ⛶ Focus, ↩ Normal)
 * - AI Copilot: tabbed workspace (Prioritization / Reasoning / Decision)
 * - Mission Log: tabbed workspace (Simulation / Narrative / Report)
 * - Data: responsive columns by workspace mode
 * - Focus mode: header shows "FOCUS MODE" indicator with EXIT button
 */
import { useState, useMemo, useCallback, useEffect } from 'react';
import { classifyProvider } from '../utils/providerClassification';
import type { NavSection } from './NavigationSidebar';
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
  MissionSourceMode,
  MissionState,
  RankedProduct,
  ScenarioInfo,
  WhatIfEvalResponse,
} from '../types/domain';
import type { ExperienceManifest } from '../types/experience';
import type { ManualAssessmentResult } from '../experience/missionExperienceReducer';
import type { ApprovalPhase } from './ApprovalBar';
import type { ViewSettings } from '../hooks/useViewSettings';
import type { WorkspaceMode } from '../MissionControl';
import { formatBitsAsDataVolume, formatDuration } from '../utils/formatters';
import { presentationLinkStatus, presentationSnrTrend } from '../experience/linkPresentation';
import { countUrgentProducts } from '../experience/urgentCandidates';

// Import existing panels (preserved as-is)
import { MissionStatePanel } from './MissionStatePanel';
import { CommBudgetBar } from './CommBudgetBar';
import { SignalGeometryBlock } from './SignalGeometryBlock';
import { LinkHealthPanel } from './LinkHealthPanel';
import { AIDecisionPanel } from './AIDecisionPanel';
import { MissionDecisionPanel } from './MissionDecisionPanel';
import { RecommendationPanel } from './RecommendationPanel';
import { ApprovalBar } from './ApprovalBar';
import { TransmissionSummaryPanel } from './TransmissionSummaryPanel';
import { TransmissionOutcomeBanner } from './TransmissionOutcomeBanner';
import { TransmissionQueuePanel } from './TransmissionQueuePanel';
import { SimulationPanel } from './SimulationPanel';
import { TransmissionNarrativePanel } from './TransmissionNarrativePanel';
import { MissionReportPanel } from './MissionReportPanel';
import { TransmissionSequencePanel } from './TransmissionSequencePanel';
import { GroundReceptionPanel } from './GroundReceptionPanel';
import { ManualVsAiPanel } from './ManualVsAiPanel';
import { SessionLogPanel } from './SessionLogPanel';
import { ResizableSection } from './ResizableSection';
import { ConfigPanel } from './ConfigPanel';
import type { SessionEvent } from '../experience/missionExperienceReducer';

// ── Shared style tokens (V4.0 light analytical workspace) ─────────────────────

const CARD: React.CSSProperties = {
  background: '#ffffff',
  border: '1px solid #dde1e8',
  borderRadius: 4,
  padding: '10px 12px',
  marginBottom: 6,
  minWidth: 0,
  overflowX: 'hidden',
};

const LABEL: React.CSSProperties = {
  fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
  fontSize: 9,
  color: '#7a8699',
  letterSpacing: '0.05em',
  marginBottom: 3,
  textTransform: 'uppercase' as const,
};

const VALUE: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
  fontSize: 14,
  fontWeight: 700,
  lineHeight: 1,
  color: '#1a2035',
};

/** Wraps a table in a horizontally scrollable container */
function TableScroll({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ overflowX: 'auto', overflowY: 'visible', minWidth: 0 }}>
      <div style={{ minWidth: 'max-content' }}>{children}</div>
    </div>
  );
}

// ── CommonProps — all data passed from MissionControl ──────────────────────────

interface CommonProps {
  linkState: LinkState | null;
  missionState: MissionState | null;
  distanceKm: number | null;
  propagationDelayS: number | null;
  roundTripTimeS: number | null;
  availableCapacityBits: number;
  queuedDataBits: number;
  dataProductsCount: number;
  anomalies: AnomalyEvent[];
  queue: CandidatePlan | null;
  recommendation: AIRecommendation | null;
  aiProvider: string | null;
  aiRequestedProvider: string | null;
  aiActualProvider: string | null;
  aiPrioritization: CandidatePrioritization | null;
  aiCandidateCount: number | null;
  aiPrioritizationError: string | null;
  aiPrioritizationFallbackReason: string | null;
  aiRecommendationFallbackReason: string | null;
  allPlans: CandidatePlan[];
  allEvaluations: EvaluationResult[];
  activePlanId: string;
  approvalPhase: ApprovalPhase;
  approveResult: ApproveResponse | null;
  whatIfEvals: EvaluationResult[] | null;
  whatIfSnr: number | null;
  recPlan: CandidatePlan | null;
  recEval: EvaluationResult | null;
  activeEval: EvaluationResult | null;
  activePlan: CandidatePlan;
  riskWeights: { w_deadline_miss: number; w_critical_deficit: number; w_window_pressure: number };
  onApproved: (result: ApproveResponse) => void;
  onTransmitting: () => void;
  onApprovalError: () => void;
  onWhatIfResult: (result: WhatIfEvalResponse, snrDb: number) => void;
  onSelectPlan: (planId: string) => void;
  // ── V3.4 props ───────────────────────────────────────────────────────────────
  decisionMode: DecisionMode;
  onSelectDecisionMode: (mode: DecisionMode) => void;
  aiLifecycle: AiLifecycle;
  aiError: string | null;
  onRunAiAnalysis: () => void;
  rawDataProducts: DataProduct[];
  hasDataProducts: boolean;
  manualSelectedIds: Set<string>;
  manualOrder: string[];
  manualPlan: CandidatePlan | null;
  onToggleManualSelect: (productId: string) => void;
  onClearManualSelection: () => void;
  onManualReorder: (newOrder: string[]) => void;
  availableScenarios: ScenarioInfo[];
  activeScenarioPath: string | null;
  scenarioSwitching: boolean;
  onSwitchScenario: (filename: string) => void;
  // Phase 6E-C7: source mode for historical context note in ConfigPanel
  sourceMode?: MissionSourceMode | null;
  // ── V3.5 props ───────────────────────────────────────────────────────────────
  workspaceMode: WorkspaceMode;
  onSetWorkspaceMode: (mode: WorkspaceMode) => void;
  // ── Phase 4.2F props ─────────────────────────────────────────────────────────
  experienceManifest: ExperienceManifest | null;
  experienceAvailable: boolean;
  manualAssessment: ManualAssessmentResult | null;
  manualAssessmentLoading: boolean;
  manualAssessmentError: string | null;
  manualAssessmentStale: boolean;
  onManualEvaluate: () => void;
  onManualTransmit: () => void;
  // ── Phase 4.2F4 props ─────────────────────────────────────────────────────────
  /** When true, TransmissionSequencePanel is active (choreography in progress). */
  choreographyActive: boolean;
  /** Plan queued for execution during choreography. */
  pendingExecutionPlan: CandidatePlan | null;
  /** Stable execution identifier — the single-shot approval guard. */
  executionId: string | null;
  /**
   * Phase 5.1G (WORKSTREAM A): Wall-clock ms when operator authorized the execution.
   * Passed through to TransmissionSequencePanel to anchor absolute-time early phase derivation.
   * Must be non-null when choreographyActive is true.
   */
  authorizedAtMs: number | null;
  /** Wall-clock ms when playback started (null if not yet started). */
  playbackStartedAtMs: number | null;
  /** Called to set playbackStartedAtMs in the coordinator (once per execution). */
  onSetPlaybackStarted: (ms: number) => void;
  /** Execute the actual backend approval call (single-shot by executionId). */
  onExecuteApproval: (executionId: string) => Promise<import('../types/domain').ApproveResponse>;
  /** Called when choreography sequence fully completes. */
  onChoreographyComplete: (result: import('../types/domain').ApproveResponse) => void;
  /** Called when choreography encounters an error. */
  onChoreographyError: (msg: string) => void;
  /** Called when a new attempt event pulse starts (for 3D visualization). Null clears. */
  onAttemptPulse: (pulse: import('./scene/CommunicationLink').ActivePulse | null) => void;
  /**
   * Phase 5.1E: Called when the choreography phase changes so the application level
   * can update pulseDirection (e.g. plan_uplink → earth_to_spacecraft).
   */
  onChoreographyPhaseChange: (phase: import('./TransmissionSequencePanel').TransmissionChoreographyPhase) => void;
  /**
   * Phase 5.1F: Application-level presentation phase for the active execution.
   * This is the authoritative source that TransmissionSection uses to set initialPhase,
   * so that remounting TransmissionSequencePanel resumes at the correct phase.
   */
  presentationPhase: import('./TransmissionSequencePanel').TransmissionChoreographyPhase;
  /**
   * Phase 5.1G FIX #1: authoritative execution result from MissionControl.executionResultRef.
   * When non-null, TransmissionSequencePanel hydrates approveResult/simResult on mount
   * so that navigation during TRANSMITTING / SIGNAL_TRANSIT / COMPLETE preserves playback.
   */
  executionResult?: import('../types/domain').ApproveResponse | null;
  // ── Phase 4.2F3 props ─────────────────────────────────────────────────────────
  /** Called when operator approves AI recommendation — does NOT execute transmission. */
  onApproveAiPlan: () => void;
  /** Called when operator wants to modify AI plan — seeds manual mode with AI plan IDs. */
  onModifyAiPlan: () => void;
  /** Called when operator rejects AI recommendation — no backend mutation. */
  onRejectAiPlan: () => void;
  /** Whether the AI recommendation was rejected this session. */
  aiRecommendationRejected: boolean;
  /** Session event log from missionExperienceReducer. */
  sessionEvents: SessionEvent[];
}

// ── StatGrid ──────────────────────────────────────────────────────────────────

function StatGrid({ items }: { items: { label: string; value: string; color: string }[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 10 }}>
      {items.map(({ label, value, color }) => (
        <div key={label} style={{
          background: '#f5f6f8',
          border: '1px solid #dde1e8',
          borderRadius: 3,
          padding: '7px 10px',
        }}>
          <div style={LABEL}>{label}</div>
          <div style={{ ...VALUE, color }}>{value}</div>
        </div>
      ))}
    </div>
  );
}

// ── V3.5: Reusable Tab Bar component ─────────────────────────────────────────

interface TabItem<T extends string> {
  id: T;
  label: string;
  badge?: string | number;
}

function TabBar<T extends string>({
  tabs,
  active,
  onSelect,
}: {
  tabs: TabItem<T>[];
  active: T;
  onSelect: (id: T) => void;
}) {
  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        gap: 0,
        borderBottom: '1px solid #dde1e8',
        marginBottom: 0,
        flexShrink: 0,
        background: '#f5f6f8',
        padding: '0 8px',
      }}
    >
      {tabs.map((tab) => {
        const isActive = active === tab.id;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelect(tab.id)}
            style={{
              padding: '7px 11px',
              background: 'transparent',
              border: 'none',
              borderBottom: isActive ? '2px solid #1d4ed8' : '2px solid transparent',
              color: isActive ? '#1d4ed8' : '#7a8699',
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontSize: 11,
              fontWeight: isActive ? 600 : 400,
              cursor: 'pointer',
              letterSpacing: '0.01em',
              transition: 'color 0.12s, border-color 0.12s',
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              marginBottom: -1,
              outline: 'none',
            }}
            onFocus={(e) => { (e.currentTarget as HTMLButtonElement).style.outline = '1px solid rgba(29,78,216,0.35)'; }}
            onBlur={(e) => { (e.currentTarget as HTMLButtonElement).style.outline = 'none'; }}
          >
            {tab.label}
            {tab.badge !== undefined && tab.badge !== null && (
              <span style={{
                background: isActive ? 'rgba(29,78,216,0.10)' : '#e8eaee',
                color: isActive ? '#1d4ed8' : '#7a8699',
                borderRadius: 2,
                padding: '0 4px',
                fontSize: 9,
                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                fontWeight: 700,
              }}>
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ── ASTERIA Mission Hero ──────────────────────────────────────────────────────

function AsteriaMissionHero(props: CommonProps) {
  const { experienceManifest: m, linkState: ls, propagationDelayS, queuedDataBits, availableCapacityBits, anomalies } = props;
  if (!m || !ls) return null;

  const queuedVolume = formatBitsAsDataVolume(queuedDataBits);
  const capacityVolume = formatBitsAsDataVolume(availableCapacityBits);
  const queuePressure = availableCapacityBits > 0 ? (queuedDataBits / availableCapacityBits).toFixed(2) : '—';
  const fitFraction = availableCapacityBits > 0 && queuedDataBits > 0
    ? ((availableCapacityBits / queuedDataBits) * 100).toFixed(2)
    : '—';
  const uplink = m.schedule.plan_uplink_margin_s;
  const uplinkLabel = formatDuration(uplink);
  const oneWayLabel = propagationDelayS !== null ? formatDuration(propagationDelayS) : '—';
  const contactLabel = formatDuration(m.schedule.contact_duration_s);

  const snrTrend = presentationSnrTrend(ls.snr_db, m.snr_history);
  const linkStatus = presentationLinkStatus(ls);
  const linkColor = linkStatus === 'CRITICAL' ? '#f87171' : linkStatus === 'DEGRADED' ? '#f59e0b' : '#34d399';

  const thermalAnomaly = anomalies.find((a) => a.anomaly_id === 'ANOM-THERM-017') ?? anomalies[0] ?? null;
  const detectedMinutesAgo = thermalAnomaly ? Math.round(thermalAnomaly.detected_at_s / 60) : null;

  const heroMetrics: Array<{ label: string; value: string; color?: string }> = [
    { label: 'PLAN UPLINK MARGIN', value: uplinkLabel },
    { label: 'QUEUED DATA', value: queuedVolume, color: '#f59e0b' },
    { label: 'CONTACT CAPACITY', value: capacityVolume },
    { label: 'QUEUE PRESSURE', value: `~${queuePressure}×`, color: '#f87171' },
    { label: 'FIT FRACTION', value: `~${fitFraction}%`, color: '#f87171' },
    { label: 'ONE-WAY SIGNAL', value: oneWayLabel },
    { label: 'CONTACT DURATION', value: contactLabel },
    { label: 'ACTIVE EVENT', value: thermalAnomaly?.anomaly_id ?? 'NONE', color: thermalAnomaly ? '#f87171' : undefined },
  ];

  return (
    <div style={{ marginBottom: 12 }}>
      {/* Mission header — analytical title block */}
      <div style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid #e8eaee' }}>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 3 }}>
          Mission
        </div>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 17, fontWeight: 700, color: '#1a2035', letterSpacing: '0.02em' }}>
          {m.display.mission_name}
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#d97706', marginTop: 2 }}>
          {m.display.scenario_name}
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#b0bac9', marginTop: 3, lineHeight: 1.4 }}>
          {m.display.disclaimer}
        </div>
      </div>

      {/* Mission metrics — flat grid, no cards */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
          Situation
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px' }}>
          {heroMetrics.map(({ label, value, color }) => (
            <div key={label} style={{ paddingBottom: 6, borderBottom: '1px solid #e8eaee' }}>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#b0bac9', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 1 }}>
                {label}
              </div>
              <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: color ?? '#1a2035' }}>
                {value}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Spacecraft Health */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6, paddingBottom: 5, borderBottom: '1px solid #e8eaee' }}>
          Spacecraft Health
        </div>
        {Object.entries(m.subsystem_status).map(([key, ss]) => {
          const isGood = ss.status === 'nominal' || ss.status === 'stable';
          const color = ss.status === 'degraded' ? '#d97706' : ss.status === 'critical' ? '#dc2626' : '#16a34a';
          return (
            <div key={key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid #f0f1f3' }}>
              <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#4a5568' }}>
                {key.replace('_', ' ')}
              </span>
              <div style={{ textAlign: 'right' }}>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11, fontWeight: 600, color, display: 'block' }}>
                  {ss.label}
                </span>
                {ss.note && (
                  <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#b0bac9' }}>
                    {ss.note}
                  </span>
                )}
              </div>
              {isGood}
            </div>
          );
        })}
      </div>

      {/* Active Thermal Event */}
      {thermalAnomaly && (
        <div style={{
          ...CARD,
          border: '1px solid rgba(220,38,38,0.22)',
          background: 'rgba(220,38,38,0.04)',
          marginBottom: 8,
        }}>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#dc2626', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>
            Detected Event
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 12, fontWeight: 700, color: '#dc2626', marginBottom: 2 }}>
            THERMAL ANOMALY
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, color: '#d97706', marginBottom: 5 }}>
            {thermalAnomaly.anomaly_id}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 5, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ background: 'rgba(220,38,38,0.08)', color: '#dc2626', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9, padding: '1px 6px', borderRadius: 2, border: '1px solid rgba(220,38,38,0.22)' }}>
              ACTIVE
            </span>
            <span style={{ color: '#7a8699', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9 }}>
              SEVERITY {(thermalAnomaly.severity * 100).toFixed(0)}%
            </span>
            {detectedMinutesAgo !== null && (
              <span style={{ color: '#7a8699', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9 }}>
                ~{detectedMinutesAgo}m ago
              </span>
            )}
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#4a5568', lineHeight: 1.5 }}>
            {thermalAnomaly.description.slice(0, 200)}{thermalAnomaly.description.length > 200 ? '…' : ''}
          </div>
        </div>
      )}

      {/* Link Health summary — thin divider table */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6, paddingBottom: 5, borderBottom: '1px solid #e8eaee' }}>
          Communication Link
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '6px 8px' }}>
          {[
            { label: 'SNR', value: `${ls.snr_db.toFixed(1)} dB` },
            { label: 'Trend', value: snrTrend },
            { label: 'Stability', value: `${(ls.link_stability * 100).toFixed(0)}%` },
            { label: 'State', value: linkStatus, color: linkColor },
          ].map(({ label, value, color }) => (
            <div key={label}>
              <div style={{ color: '#b0bac9', fontSize: 8, fontFamily: '"IBM Plex Sans"', marginBottom: 1, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
              <div style={{ color: color ?? '#1a2035', fontSize: 11, fontFamily: '"IBM Plex Mono", ui-monospace', fontWeight: 600 }}>{value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Mission section ───────────────────────────────────────────────────────────

function MissionSection(props: CommonProps) {
  const ms = props.missionState;
  const ls = props.linkState;
  const dpCount = props.dataProductsCount;
  const anomCount = props.anomalies.length;

  // When ASTERIA experience is available, show the hero panel instead of the generic context
  if (props.experienceAvailable && props.experienceManifest) {
    return (
      <>
        <AsteriaMissionHero {...props} />
        <ResizableSection title="Mission State" icon="◉" accent="#1d4ed8">
          {ms ? (
            <TableScroll>
              <MissionStatePanel missionState={ms} />
            </TableScroll>
          ) : (
            <div style={{ color: 'rgba(147,160,180,0.5)', fontSize: 12 }}>No mission data</div>
          )}
        </ResizableSection>
        {ls && (
          <ResizableSection title="Comm Budget" icon="⌾" accent="#1d4ed8">
            <CommBudgetBar
              availableCapacityBits={props.availableCapacityBits}
              queuedDataBits={props.queuedDataBits}
              dataProductsCount={props.dataProductsCount}
              remainingWindowS={ls.remaining_window_s}
            />
          </ResizableSection>
        )}
      </>
    );
  }

  // Generic scenario fallback
  return (
    <>
      {ms && ls && (
        <StatGrid items={[
          { label: 'Window', value: `${ms.comm_window_remaining_s.toFixed(0)} s`, color: ms.comm_window_remaining_s < 60 ? '#dc2626' : '#16a34a' },
          { label: 'Risk', value: ms.risk_level, color: ms.risk_level === 'CRITICAL' ? '#dc2626' : ms.risk_level === 'HIGH' ? '#ea580c' : ms.risk_level === 'MEDIUM' ? '#d97706' : '#16a34a' },
          { label: 'SNR', value: `${ls.snr_db.toFixed(1)} dB`, color: ls.snr_db < 5 ? '#dc2626' : ls.snr_db < 10 ? '#d97706' : '#16a34a' },
          { label: 'Stability', value: `${(ls.link_stability * 100).toFixed(0)}%`, color: ls.link_stability < 0.5 ? '#dc2626' : ls.link_stability < 0.75 ? '#d97706' : '#16a34a' },
        ]} />
      )}

      {props.decisionMode === 'unselected' && dpCount > 0 && (
        <div style={{ ...CARD, marginBottom: 10 }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
            Mission Context
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#4a5568' }}>Data products</span>
              <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#1a2035' }}>{dpCount}</span>
            </div>
            {anomCount > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#4a5568' }}>Active anomalies</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#dc2626' }}>{anomCount}</span>
              </div>
            )}
            {ms && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#4a5568' }}>Comm window</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#16a34a' }}>{ms.comm_window_remaining_s.toFixed(0)} s</span>
              </div>
            )}
            {ls && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#4a5568' }}>Link status</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 12, fontWeight: 600, color: ls.link_stability > 0.7 ? '#16a34a' : '#d97706' }}>
                  {ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable'}
                </span>
              </div>
            )}
          </div>
          <div style={{ marginTop: 8, paddingTop: 7, borderTop: '1px solid #e8eaee', fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#7a8699', lineHeight: 1.5 }}>
            No transmission plan has been created yet. Choose a decision mode below or navigate to the AI or Data sections.
          </div>
        </div>
      )}

      <ResizableSection title="Mission State" icon="◉" accent="#1d4ed8">
        {ms ? (
          <TableScroll>
            <MissionStatePanel missionState={ms} />
          </TableScroll>
        ) : (
          <div style={{ color: '#7a8699', fontSize: 12 }}>No mission data</div>
        )}
      </ResizableSection>

      {props.linkState && (
        <ResizableSection title="Comm Budget" icon="⌾" accent="#1d4ed8">
          <CommBudgetBar
            availableCapacityBits={props.availableCapacityBits}
            queuedDataBits={props.queuedDataBits}
            dataProductsCount={props.dataProductsCount}
            remainingWindowS={props.linkState.remaining_window_s}
          />
        </ResizableSection>
      )}

      {props.anomalies.length > 0 && (
        <ResizableSection title="Anomalies" icon="⚠" accent="#dc2626">
          {props.anomalies.map((a) => (
            <div key={a.anomaly_id} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start',
              padding: '6px 0', borderBottom: '1px solid #e8eaee',
              minWidth: 0,
            }}>
              <span style={{ color: '#dc2626', flexShrink: 0, fontSize: 10, marginTop: 2 }}>
                {a.severity >= 0.75 ? '●' : '○'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  color: '#1a2035', fontWeight: 600, fontSize: 12,
                  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                  wordBreak: 'break-all',
                }}>{a.anomaly_id}</div>
                <div style={{ color: '#4a5568', fontSize: 11, marginTop: 2, lineHeight: 1.45 }}>{a.description}</div>
                <div style={{ color: '#7a8699', fontSize: 10, marginTop: 1 }}>
                  {a.subsystem} · severity {(a.severity * 100).toFixed(0)}%
                </div>
              </div>
            </div>
          ))}
        </ResizableSection>
      )}
    </>
  );
}

// ── Spacecraft section ────────────────────────────────────────────────────────

function SpacecraftSection(props: CommonProps) {
  return (
    <ResizableSection title="Spacecraft Geometry" icon="⬡" accent="#1d4ed8">
      <TableScroll>
        <SignalGeometryBlock
          distanceKm={props.distanceKm}
          propagationDelayS={props.propagationDelayS}
          roundTripTimeS={props.roundTripTimeS}
        />
      </TableScroll>
    </ResizableSection>
  );
}

// ── Communications section ────────────────────────────────────────────────────

function CommsSection(props: CommonProps) {
  if (!props.linkState) return (
    <div style={{ ...CARD }}>
      <div style={{ color: '#7a8699', fontSize: 12 }}>No link data available</div>
    </div>
  );
  return (
    <ResizableSection title="Link Health" icon="⌾" accent="#1d4ed8">
      <div style={{ minWidth: 0 }}>
        <LinkHealthPanel
          linkState={props.linkState}
          snrHistory={props.experienceManifest?.snr_history}
          onWhatIfResult={props.onWhatIfResult}
        />
      </div>
    </ResizableSection>
  );
}

// ── Data section — V3.5 workspace-aware data browser ─────────────────────────

const FILTER_LABELS: Record<string, string> = {
  all: 'All',
  critical: 'Critical',
  required: 'Required',
  anomaly: 'Anomaly',
  telemetry: 'Telemetry',
  diagnostic: 'Diagnostic',
  science: 'Science',
  image: 'Image',
  housekeeping: 'Housekeeping',
  navigation: 'Nav',
};

type SortKey = 'criticality' | 'deadline_s' | 'size_bits' | 'age_s' | 'mission_relevance';

const PAGE_SIZE = 20;

function DataSection(props: CommonProps) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<string>('all');
  const [sortKey, setSortKey] = useState<SortKey>('criticality');
  const [sortDesc, setSortDesc] = useState(true);
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const products = props.rawDataProducts;
  const hasDP = props.hasDataProducts;
  const isLegacy = !hasDP && (props.dataProductsCount === 0);
  const wm = props.workspaceMode;

  // Derive available filter categories from actual data
  const availableTypes = useMemo(() => {
    const types = new Set(products.map((p) => p.product_type));
    return Array.from(types);
  }, [products]);

  const hasAnomaly = useMemo(() => products.some((p) => p.anomaly_id), [products]);
  const hasRequired = useMemo(() => products.some((p) => p.delivery_requirement === 'required'), [products]);

  // Filtered + sorted products
  const filtered = useMemo(() => {
    let list = products;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) =>
        p.product_id.toLowerCase().includes(q) ||
        p.product_type.toLowerCase().includes(q) ||
        p.subsystem.toLowerCase().includes(q) ||
        (p.anomaly_id?.toLowerCase().includes(q) ?? false) ||
        p.description.toLowerCase().includes(q)
      );
    }
    if (filter !== 'all') {
      if (filter === 'critical') list = list.filter((p) => p.criticality >= 0.7);
      else if (filter === 'required') list = list.filter((p) => p.delivery_requirement === 'required');
      else if (filter === 'anomaly') list = list.filter((p) => p.anomaly_id !== null);
      else list = list.filter((p) => p.product_type === filter);
    }
    list = [...list].sort((a, b) => {
      const va = a[sortKey] as number;
      const vb = b[sortKey] as number;
      return sortDesc ? vb - va : va - vb;
    });
    return list;
  }, [products, search, filter, sortKey, sortDesc]);

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const paginated = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const handleSearch = useCallback((v: string) => { setSearch(v); setPage(0); }, []);
  const handleFilter = useCallback((v: string) => { setFilter(v); setPage(0); }, []);
  const handleSort = useCallback((key: SortKey) => {
    setSortKey((prev) => {
      if (prev === key) { setSortDesc((d) => !d); return key; }
      setSortDesc(true);
      return key;
    });
    setPage(0);
  }, []);

  if (isLegacy) {
    return (
      <div style={{ ...CARD, borderColor: 'rgba(217,119,6,0.22)', background: 'rgba(217,119,6,0.04)' }}>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 9, color: '#d97706', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
          Legacy Packet Scenario
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#4a5568', lineHeight: 1.55, marginBottom: 10 }}>
          This scenario uses the legacy packet model. AI data-product prioritization and high-volume manual planning are unavailable.
        </div>
        {props.queue && (
          <ResizableSection title="Transmission Queue" icon="▦" accent="#1d4ed8">
            <div style={{ overflowX: 'auto', minWidth: 0 }}>
              <TransmissionQueuePanel plan={props.queue} />
            </div>
          </ResizableSection>
        )}
      </div>
    );
  }

  const selectedCount = props.manualSelectedIds.size;
  const selectedBits = props.manualOrder.reduce((sum, id) => {
    const p = products.find((dp) => dp.product_id === id);
    return sum + (p?.size_bits ?? 0);
  }, 0);
  const capacityUsedPct = props.availableCapacityBits > 0
    ? Math.min(100, (selectedBits / props.availableCapacityBits) * 100)
    : 0;

  // Expanded/Focus: show inline summary bar instead of card
  const showExpandedColumns = wm === 'expanded' || wm === 'focus';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* Toolbar */}
      <div style={{ padding: '6px 0 5px', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 7, paddingBottom: 6, borderBottom: '1px solid #e8eaee' }}>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', fontWeight: 600 }}>
            Data Products
          </span>
          <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 15, fontWeight: 700, color: '#1a2035' }}>
            {products.length}
          </span>
        </div>
        <input
          type="text"
          placeholder="Search products, subsystem, anomaly…"
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
          style={{
            width: '100%', background: '#f5f6f8', border: '1px solid #dde1e8',
            color: '#1a2035', borderRadius: 3, padding: '5px 10px', fontSize: 12,
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace', boxSizing: 'border-box',
            marginBottom: 5,
          }}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginBottom: 5 }}>
          {['all', hasRequired && 'required', hasAnomaly && 'anomaly', 'critical', ...availableTypes]
            .filter(Boolean)
            .filter((v, i, a) => a.indexOf(v) === i)
            .slice(0, 10)
            .map((f) => {
              const fStr = f as string;
              const activeF = filter === fStr;
              return (
                <button key={fStr} onClick={() => handleFilter(fStr)} style={{
                  fontSize: 10, padding: '2px 7px',
                  background: activeF ? '#eef3fc' : '#f5f6f8',
                  color: activeF ? '#1d4ed8' : '#7a8699',
                  border: `1px solid ${activeF ? 'rgba(29,78,216,0.30)' : '#dde1e8'}`,
                  borderRadius: 2, cursor: 'pointer', fontFamily: '"IBM Plex Sans", system-ui',
                  fontWeight: activeF ? 600 : 400,
                }}>
                  {FILTER_LABELS[fStr] ?? fStr}
                </button>
              );
            })}
        </div>
        <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#b0bac9', marginRight: 2, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Sort</span>
          {(['criticality', 'deadline_s', 'size_bits', 'age_s', 'mission_relevance'] as SortKey[]).map((key) => {
            const labels: Record<SortKey, string> = { criticality: 'Crit', deadline_s: 'Deadline', size_bits: 'Size', age_s: 'Age', mission_relevance: 'Relevance' };
            const activeS = sortKey === key;
            return (
              <button key={key} onClick={() => handleSort(key)} style={{
                fontSize: 10, padding: '2px 7px',
                background: activeS ? '#eef3fc' : 'transparent',
                color: activeS ? '#1d4ed8' : '#7a8699',
                border: `1px solid ${activeS ? 'rgba(29,78,216,0.25)' : '#dde1e8'}`,
                borderRadius: 2, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
              }}>
                {labels[key]}{activeS ? (sortDesc ? ' ↓' : ' ↑') : ''}
              </button>
            );
          })}
          <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#b0bac9' }}>
            {filtered.length}/{products.length}
            {selectedCount > 0 && ` · ${selectedCount} sel`}
          </span>
        </div>
      </div>

      {/* Product list — scientific table style with thin row separators */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', flexDirection: 'column', paddingBottom: selectedCount > 0 ? 56 : 8 }}>
          {paginated.map((p) => {
            const isSelected = props.manualSelectedIds.has(p.product_id);
            const isExp = expandedId === p.product_id;
            const rank = props.manualOrder.indexOf(p.product_id);
            const critColor = p.criticality >= 0.85 ? '#dc2626' : p.criticality >= 0.7 ? '#d97706' : '#16a34a';
            return (
              <div key={p.product_id} style={{
                background: isSelected ? 'rgba(22,163,74,0.04)' : 'transparent',
                borderBottom: '1px solid #e8eaee',
                borderLeft: isSelected ? '2px solid #16a34a' : '2px solid transparent',
                overflow: 'hidden',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 6px', cursor: 'pointer' }}
                  onClick={() => setExpandedId(isExp ? null : p.product_id)}>
                  {props.decisionMode === 'manual' && (
                    <div
                      onClick={(e) => { e.stopPropagation(); props.onToggleManualSelect(p.product_id); }}
                      style={{
                        width: 13, height: 13, borderRadius: 3, flexShrink: 0, cursor: 'pointer',
                        background: isSelected ? '#16a34a' : 'transparent',
                        border: `1px solid ${isSelected ? '#16a34a' : '#c8cdd7'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}
                    >
                      {isSelected && <span style={{ color: '#fff', fontSize: 8, fontWeight: 700 }}>✓</span>}
                    </div>
                  )}
                  {isSelected && rank >= 0 && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#16a34a', minWidth: 16, textAlign: 'right', flexShrink: 0 }}>#{rank + 1}</span>
                  )}
                  {p.anomaly_id && (
                    <span style={{ color: '#dc2626', fontSize: 9, fontFamily: '"IBM Plex Mono"', fontWeight: 700, flexShrink: 0 }}>⚠</span>
                  )}
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#1a2035', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.product_id}
                  </span>
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#7a8699', flexShrink: 0, minWidth: 52, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {p.subsystem}
                    </span>
                  )}
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#7a8699', flexShrink: 0, minWidth: 40 }}>
                      {formatBitsAsDataVolume(p.size_bits)}
                    </span>
                  )}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: critColor, letterSpacing: '0.03em', whiteSpace: 'nowrap', fontWeight: 600 }}>
                      CRIT {p.criticality.toFixed(2)}
                    </span>
                    <div style={{ width: 24, height: 2, background: '#e8eaee', borderRadius: 1 }}>
                      <div style={{ width: `${p.criticality * 100}%`, height: '100%', borderRadius: 1, background: critColor }} />
                    </div>
                  </div>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: p.deadline_s < 120 ? '#dc2626' : '#7a8699', flexShrink: 0, minWidth: 36, textAlign: 'right' }}>
                    {p.deadline_s < 3600 ? `${p.deadline_s.toFixed(0)}s` : `${(p.deadline_s / 3600).toFixed(1)}h`}
                  </span>
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: p.mission_relevance > 0.7 ? '#16a34a' : '#b0bac9', flexShrink: 0, minWidth: 28 }}>
                      {(p.mission_relevance * 100).toFixed(0)}%
                    </span>
                  )}
                  <span style={{ color: '#b0bac9', fontSize: 9, flexShrink: 0 }}>{isExp ? '▲' : '▼'}</span>
                </div>
                {isExp && (
                  <div style={{ padding: '8px 10px 10px', borderTop: '1px solid #e8eaee', background: '#f5f6f8' }}>
                    <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#4a5568', lineHeight: 1.55, marginBottom: 8 }}>
                      {p.description || '—'}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 12px', fontSize: 10 }}>
                      {[
                        ['Type', p.product_type],
                        ['Subsystem', p.subsystem],
                        ['Size', formatBitsAsDataVolume(p.size_bits)],
                        ['Criticality', p.criticality.toFixed(2)],
                        ['Relevance', p.mission_relevance.toFixed(2)],
                        ['Delivery', p.delivery_requirement],
                        ...(p.anomaly_id ? [['Anomaly', p.anomaly_id]] : []),
                        ...(p.experiment_id ? [['Experiment', p.experiment_id]] : []),
                      ].map(([label, val]) => (
                        <div key={label} style={{ display: 'flex', gap: 4 }}>
                          <span style={{ color: '#7a8699', fontFamily: '"IBM Plex Sans"' }}>{label}</span>
                          <span style={{ color: '#1a2035', fontFamily: '"IBM Plex Mono"' }}>{val}</span>
                        </div>
                      ))}
                    </div>
                    {props.decisionMode === 'manual' && (
                      <button
                        onClick={() => props.onToggleManualSelect(p.product_id)}
                        style={{
                          marginTop: 8, fontSize: 11, padding: '4px 12px',
                          background: isSelected ? 'rgba(220,38,38,0.06)' : 'rgba(22,163,74,0.07)',
                          color: isSelected ? '#dc2626' : '#16a34a',
                          border: `1px solid ${isSelected ? 'rgba(220,38,38,0.22)' : 'rgba(22,163,74,0.22)'}`,
                          borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
                        }}
                      >
                        {isSelected ? 'Deselect' : 'Select for transmission'}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {paginated.length === 0 && (
            <div style={{ color: '#7a8699', fontSize: 12, padding: '12px 0', textAlign: 'center' }}>
              No products match the current filter.
            </div>
          )}
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: '6px 0', borderTop: '1px solid #e8eaee', flexShrink: 0 }}>
          <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
            style={{ fontSize: 10, padding: '3px 8px', background: '#f5f6f8', border: '1px solid #dde1e8', borderRadius: 3, color: '#4a5568', cursor: 'pointer' }}>
            ←
          </button>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#7a8699' }}>
            {page + 1} / {totalPages}
          </span>
          <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}
            style={{ fontSize: 10, padding: '3px 8px', background: '#f5f6f8', border: '1px solid #dde1e8', borderRadius: 3, color: '#4a5568', cursor: 'pointer' }}>
            →
          </button>
        </div>
      )}

      {/* Sticky selection summary bar — manual mode with selection */}
      {props.decisionMode === 'manual' && selectedCount > 0 && (
        <div style={{
          borderTop: '1px solid rgba(22,163,74,0.25)',
          background: '#ffffff',
          padding: '7px 10px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexShrink: 0,
        }}>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#16a34a', fontWeight: 700 }}>{selectedCount}</span>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#4a5568' }}>selected</span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#7a8699' }}>{formatBitsAsDataVolume(selectedBits)}</span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: capacityUsedPct > 90 ? '#dc2626' : '#7a8699' }}>{capacityUsedPct.toFixed(0)}% cap</span>
          <button
            onClick={props.onClearManualSelection}
            style={{ marginLeft: 'auto', fontSize: 10, padding: '3px 8px', background: 'transparent', color: '#7a8699', border: '1px solid #dde1e8', borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"' }}
          >
            Clear
          </button>
        </div>
      )}
    </div>
  );
}

// ── F3: Human Decision Panel (Approve / Modify / Reject) ─────────────────────

function AiHumanDecisionPanel({ props }: { props: CommonProps }) {
  const rec = props.recommendation;
  const recEval = props.recEval;
  const recPlan = props.recPlan;
  const approvalPhase = props.approvalPhase;
  const isTransmitting = approvalPhase === 'transmitting';
  const isComplete = approvalPhase === 'complete';

  if (!rec) return null;

  const planPayloadBits = (recPlan?.packets ?? []).reduce((s, p) => s + p.size_bits, 0);
  const deferredCount = recEval?.deferred_packets.length ?? 0;
  const selectedCount = (recPlan?.packets.length ?? 0);

  const riskLevel = recEval?.risk_level ?? rec.risk_level;
  const riskColor =
    riskLevel === 'LOW' ? '#34d399' :
    riskLevel === 'MEDIUM' ? '#f59e0b' :
    riskLevel === 'HIGH' ? '#fb923c' : '#f87171';

  // Required delivery rate from evaluation
  const reqDeliveryRate = recEval
    ? recEval.critical_packets_delivered / Math.max(1, recEval.total_critical_packets)
    : null;

  // Anomaly-linked packet coverage from plan packets
  const anomalyIds = new Set(props.anomalies.map((a) => a.anomaly_id));
  const anomalyLinkedPackets = (recPlan?.packets ?? []).filter((pkt) => {
    const dp = props.rawDataProducts.find((p) => p.product_id === pkt.packet_id);
    return dp?.anomaly_id && anomalyIds.has(dp.anomaly_id);
  });
  const totalAnomalyLinked = props.rawDataProducts.filter((p) => p.anomaly_id && anomalyIds.has(p.anomaly_id)).length;
  const anomalyCoverage = totalAnomalyLinked > 0
    ? (anomalyLinkedPackets.length / totalAnomalyLinked)
    : null;

  return (
    <div style={{
      background: '#ffffff',
      border: '1px solid #dde1e8',
      borderRadius: 4, padding: '12px 14px',
      marginBottom: 12,
    }}>
      {/* Header: Mission Decision */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6, paddingBottom: 7, borderBottom: '1px solid #e8eaee' }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', fontWeight: 600 }}>
          Mission Decision
        </div>
        {rec.recommended_plan_id && (
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#b0bac9', letterSpacing: '0.05em' }}>
            {rec.recommended_plan_id.toUpperCase()}
          </div>
        )}
      </div>
      <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#7a8699', marginBottom: 10, lineHeight: 1.5 }}>
        Stage 1: semantic candidate prioritization → Stage 2: final plan selection → Human approval
      </div>

      {/* Metrics — flat rows */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px', marginBottom: 14 }}>
        {[
          { label: 'PRIORITIZED QUEUE', value: `${selectedCount} products`, color: '#1d4ed8' },
          { label: 'PROJECTED THIS CONTACT', value: deferredCount < selectedCount ? `${selectedCount - deferredCount} products` : `${selectedCount} products`, color: '#16a34a' },
          { label: 'PRIORITY PAYLOAD', value: formatBitsAsDataVolume(planPayloadBits), color: '#1a2035' },
          { label: 'CONTACT CAPACITY', value: formatBitsAsDataVolume(props.availableCapacityBits), color: '#7a8699' },
          { label: 'PLAN RISK', value: riskLevel, color: riskColor },
          { label: 'PROJECTED DEFERRED', value: `${deferredCount}`, color: deferredCount > 0 ? '#d97706' : '#16a34a' },
          ...(reqDeliveryRate !== null ? [{ label: 'REQ. DELIVERY', value: `${(reqDeliveryRate * 100).toFixed(0)}%`, color: reqDeliveryRate >= 0.8 ? '#16a34a' : '#d97706' }] : []),
          ...(anomalyCoverage !== null ? [{ label: 'ANOMALY COVERAGE', value: `${(anomalyCoverage * 100).toFixed(0)}%`, color: anomalyCoverage >= 0.8 ? '#16a34a' : anomalyCoverage >= 0.5 ? '#d97706' : '#dc2626' }] : []),
        ].map(({ label, value, color }) => (
          <div key={label} style={{ paddingBottom: 5, borderBottom: '1px solid #f0f1f3' }}>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#b0bac9', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 1 }}>{label}</div>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Decision buttons — human authority hierarchy */}
      {!isComplete && !isTransmitting && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button
            onClick={props.onApproveAiPlan}
            disabled={isTransmitting}
            style={{
              width: '100%', padding: '9px 0', fontSize: 12, fontWeight: 600,
              fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
              background: '#1d4ed8', color: '#ffffff',
              border: '1px solid #1d4ed8', borderRadius: 3,
              letterSpacing: '0.01em',
            }}
          >
            ✓ APPROVE TRANSMISSION
          </button>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={props.onModifyAiPlan}
              style={{
                flex: 1, padding: '7px 0', fontSize: 11, fontWeight: 500,
                fontFamily: '"IBM Plex Sans"', cursor: 'pointer',
                background: '#f5f6f8', color: '#4a5568',
                border: '1px solid #c8cdd7', borderRadius: 3,
              }}
            >
              ✎ Modify Plan
            </button>
            <button
              onClick={props.onRejectAiPlan}
              style={{
                flex: 1, padding: '7px 0', fontSize: 11, fontWeight: 500,
                fontFamily: '"IBM Plex Sans"', cursor: 'pointer',
                background: 'rgba(220,38,38,0.06)', color: '#dc2626',
                border: '1px solid rgba(220,38,38,0.22)', borderRadius: 3,
              }}
            >
              ✕ Reject
            </button>
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#b0bac9', textAlign: 'center', marginTop: 2 }}>
            Approve authorizes transmission · Modify seeds manual planning · Reject does not transmit
          </div>
        </div>
      )}

      {isTransmitting && (
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#1d4ed8', textAlign: 'center', padding: '8px 0' }}>
          Transmission in progress…
        </div>
      )}
      {isComplete && (
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#16a34a', textAlign: 'center', padding: '8px 0' }}>
          ✓ Transmission complete — see Log section
        </div>
      )}
    </div>
  );
}

// ── V3.5 / F3: AI Mission Triage helpers ─────────────────────────────────────

/** Determine provider-aware triage heading using shared classifier */
function triageHeading(providerName: string | null, fallbackReason: string | null): {
  title: string; subtitle: string | null; isLocal: boolean;
} {
  const classification = classifyProvider(providerName);
  const hasFallback = !!fallbackReason;

  if (classification.kind === 'local_deterministic' || hasFallback) {
    return {
      title: 'DETERMINISTIC MISSION TRIAGE',
      subtitle: 'LOCAL FALLBACK',
      isLocal: true,
    };
  }
  if (classification.kind === 'unknown') {
    return {
      title: 'ADVISORY MISSION TRIAGE',
      subtitle: 'PROVIDER UNKNOWN',
      isLocal: false,
    };
  }
  return {
    title: 'AI MISSION TRIAGE',
    subtitle: null,
    isLocal: false,
  };
}

/** WHY THIS MATTERS panel — shows RankedProduct reason + DataProduct evidence */
function WhyThisMatters({
  rp,
  dataProduct,
  anomaly,
}: {
  rp: RankedProduct;
  dataProduct: DataProduct | undefined;
  anomaly: AnomalyEvent | undefined;
}) {
  const AI_COLOR = '#1d4ed8';
  const MUTED = '#4a5568';
  const DIM = '#b0bac9';

  return (
    <div style={{
      background: '#ffffff',
      border: '1px solid rgba(29,78,216,0.18)',
      borderRadius: 4, padding: '10px 12px',
      marginBottom: 8,
    }}>
      {/* WHY THIS MATTERS header */}
      <div style={{
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9,
        color: '#1d4ed8', letterSpacing: '0.07em', marginBottom: 6,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <span>◈</span> WHY THIS MATTERS
        <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>
          Rank #{rp.priority}
        </span>
      </div>

      {/* Product ID */}
      <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: AI_COLOR, marginBottom: 4 }}>
        {rp.product_id}
      </div>

      {/* AI reasoning — labelled advisory */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM, letterSpacing: '0.08em', marginBottom: 3 }}>
          AI INTERPRETATION · ADVISORY · UNCALIBRATED
        </div>
        <div style={{
          fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: MUTED,
          fontStyle: 'italic', lineHeight: 1.5,
          background: '#f5f6f8', borderRadius: 3, padding: '5px 8px',
          borderLeft: '2px solid rgba(29,78,216,0.18)',
        }}>
          "{rp.reason}"
        </div>
      </div>

      {/* Authoritative evidence from DataProduct */}
      {dataProduct && (
        <div>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#16a34a', letterSpacing: '0.08em', marginBottom: 5 }}>
            ● AUTHORITATIVE EVIDENCE
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 10px', fontSize: 10 }}>
            {[
              ['Active event', anomaly ? anomaly.anomaly_id : (dataProduct.anomaly_id ?? '—')],
              ['Subsystem', dataProduct.subsystem],
              ['Severity', anomaly ? `${(anomaly.severity * 100).toFixed(0)}%` : '—'],
              ['Criticality', dataProduct.criticality.toFixed(2)],
              ['Mission relevance', dataProduct.mission_relevance.toFixed(2)],
              ['Delivery req.', dataProduct.delivery_requirement],
              ['Payload', formatBitsAsDataVolume(dataProduct.size_bits)],
              ['Contact deadline', dataProduct.deadline_s < 3600 ? `${dataProduct.deadline_s.toFixed(0)} s` : `${(dataProduct.deadline_s / 3600).toFixed(1)} h`],
            ].map(([label, val]) => (
              <div key={label} style={{ display: 'flex', gap: 4 }}>
                <span style={{ color: '#b0bac9', fontFamily: '"IBM Plex Sans"' }}>{label}</span>
                <span style={{ color: '#1a2035', fontFamily: '"IBM Plex Mono"', wordBreak: 'break-all' }}>{val}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** AI Funnel display: 1,284 → 50 → 23 → N */
function AiFunnel({
  totalQueued,
  semanticCandidates,
  urgentCount,
  projectedFit,
}: {
  totalQueued: number;
  semanticCandidates: number;
  urgentCount: number;
  projectedFit: number | null;
}) {
  const DIM = '#b0bac9';
  const rows: Array<{ count: number; label: string; color: string }> = [
    { count: totalQueued, label: 'QUEUED PRODUCTS', color: '#d97706' },
    { count: semanticCandidates, label: 'SEMANTIC CANDIDATES', color: '#1d4ed8' },
    { count: urgentCount, label: 'URGENT / OPERATIONALLY RELEVANT', color: '#dc2626' },
    { count: projectedFit ?? 0, label: 'PROJECTED TO FIT CONTACT', color: '#16a34a' },
  ];

  return (
    <div style={{
      background: '#ffffff',
      border: '1px solid #dde1e8',
      borderRadius: 4, padding: '10px 12px',
      marginBottom: 10,
    }}>
      <div style={{
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9,
        color: '#7a8699', letterSpacing: '0.07em', marginBottom: 8,
        textTransform: 'uppercase',
      }}>
        Mission Triage Funnel
      </div>
      {rows.map((row, i) => (
        <div key={row.label}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 20, fontWeight: 700, color: row.color, minWidth: 56, textAlign: 'right' }}>
              {row.count > 0 || i === 3 ? row.count.toLocaleString() : '—'}
            </span>
            <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#7a8699', letterSpacing: '0.04em', flexShrink: 0 }}>
              {row.label}
            </span>
          </div>
          {i < rows.length - 1 && (
            <div style={{ marginLeft: 24, color: DIM, fontSize: 11, lineHeight: '14px' }}>↓</div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── V3.5: AI Copilot section — Tabbed workspace ───────────────────────────────

type AiTab = 'prioritization' | 'reasoning' | 'decision';

function AiSection(props: CommonProps) {
  const [activeTab, setActiveTab] = useState<AiTab>('prioritization');
  const [selectedRpId, setSelectedRpId] = useState<string | null>(null);

  const lc = props.aiLifecycle;
  const isStandby = lc === 'standby';
  const isAnalyzing = lc === 'analyzing';
  const isReady = lc === 'ready';
  const isError = lc === 'error';
  const isStale = lc === 'stale';
  const hasResult = isReady || isStale;
  const dp = props.dataProductsCount;
  const anomCount = props.anomalies.length;
  const ms = props.missionState;
  const ls = props.linkState;

  const statusColor = isAnalyzing ? '#1d4ed8' : isReady ? '#16a34a' : isError ? '#dc2626' : isStale ? '#d97706' : '#c8cdd7';
  const statusLabel = isAnalyzing ? 'ANALYZING' : isReady ? 'READY' : isError ? 'FAILED' : isStale ? 'STALE' : 'STANDBY';
  const notInAiMode = props.decisionMode !== 'ai';

  const rankedCount = props.aiPrioritization?.ranked_products.length ?? null;

  // ── Derived funnel counts ────────────────────────────────────────────────────
  const funnelData = useMemo(() => {
    const totalQueued = props.rawDataProducts.length > 0 ? props.rawDataProducts.length : props.dataProductsCount;
    const semanticCandidates = props.aiCandidateCount ?? props.aiPrioritization?.candidate_count ?? 0;

    // Urgent count: apply production predicate to candidates
    // Candidates are products that appear in ranked_products
    const rankedIds = new Set((props.aiPrioritization?.ranked_products ?? []).map((r) => r.product_id));
    const candidateProducts = props.rawDataProducts.filter((p) => rankedIds.has(p.product_id));
    const anomalyIds = new Set(props.anomalies.map((a) => a.anomaly_id));
    const effectiveWindowS = ms?.comm_window_remaining_s ?? 0;
    const urgentCount = countUrgentProducts(candidateProducts, anomalyIds, effectiveWindowS);

    // Projected fit: ai_plan packets that are NOT deferred
    const recPlanPackets = props.recPlan?.packets ?? [];
    const deferredSet = new Set(props.recEval?.deferred_packets ?? []);
    const projectedFit = recPlanPackets.filter((pkt) => !deferredSet.has(pkt.packet_id)).length;

    return { totalQueued, semanticCandidates, urgentCount, projectedFit: projectedFit > 0 ? projectedFit : null };
  }, [props.rawDataProducts, props.dataProductsCount, props.aiCandidateCount, props.aiPrioritization, props.anomalies, ms, props.recPlan, props.recEval]);

  // ── Derived provider heading ─────────────────────────────────────────────────
  const triageInfo = useMemo(
    () => triageHeading(
      props.aiActualProvider ?? props.aiProvider,
      props.aiRecommendationFallbackReason ?? props.aiPrioritizationFallbackReason,
    ),
    [props.aiActualProvider, props.aiProvider, props.aiRecommendationFallbackReason, props.aiPrioritizationFallbackReason],
  );

  // ── WHY THIS MATTERS lookup ─────────────────────────────────────────────────
  const productById = useMemo(() => {
    const m = new Map<string, DataProduct>();
    for (const p of props.rawDataProducts) m.set(p.product_id, p);
    return m;
  }, [props.rawDataProducts]);

  const anomalyById = useMemo(() => {
    const m = new Map<string, AnomalyEvent>();
    for (const a of props.anomalies) m.set(a.anomaly_id, a);
    return m;
  }, [props.anomalies]);

  // Auto-select first ranked product when AI result arrives
  useEffect(() => {
    if (hasResult && props.aiPrioritization && !selectedRpId) {
      const first = props.aiPrioritization.ranked_products.find((r) => r.priority === 1)
        ?? props.aiPrioritization.ranked_products[0];
      if (first) setSelectedRpId(first.product_id);
    }
  }, [hasResult, props.aiPrioritization, selectedRpId]);

  const selectedRp: RankedProduct | null = useMemo(() => {
    if (!selectedRpId || !props.aiPrioritization) return null;
    return props.aiPrioritization.ranked_products.find((r) => r.product_id === selectedRpId) ?? null;
  }, [selectedRpId, props.aiPrioritization]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* AI Copilot status card — always visible */}
      <div style={{
        ...CARD,
        background: isAnalyzing ? '#eef3fc' : isReady ? 'rgba(22,163,74,0.04)' : isError ? 'rgba(220,38,38,0.04)' : isStale ? 'rgba(217,119,6,0.04)' : '#ffffff',
        borderColor: isAnalyzing ? 'rgba(29,78,216,0.20)' : isReady ? 'rgba(22,163,74,0.18)' : isError ? 'rgba(220,38,38,0.20)' : isStale ? 'rgba(217,119,6,0.20)' : '#dde1e8',
        marginBottom: 6,
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: hasResult ? 8 : 0 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', display: 'inline-block', background: statusColor, flexShrink: 0 }} />
          <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, fontWeight: 600, color: '#1a2035' }}>
            {hasResult ? triageInfo.title : `AI · ${statusLabel}`}
          </span>
          {hasResult && triageInfo.subtitle && (
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#d97706', background: 'rgba(217,119,6,0.07)', border: '1px solid rgba(217,119,6,0.22)', borderRadius: 2, padding: '1px 5px' }}>
              {triageInfo.subtitle}
            </span>
          )}
          {props.aiProvider && (isReady || isStale) && (
            <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: triageInfo.isLocal ? '#d97706' : '#1d4ed8', flexShrink: 0 }}>
              {props.aiProvider}
            </span>
          )}
        </div>

        {/* Compact summary row when ready */}
        {hasResult && props.aiPrioritization && (
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#b0bac9', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Queued</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#1a2035' }}>
                {funnelData.totalQueued.toLocaleString()}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#b0bac9', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Candidates</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#1d4ed8' }}>
                {funnelData.semanticCandidates}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#b0bac9', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Urgent</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#dc2626' }}>
                {funnelData.urgentCount}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#b0bac9', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Fit Contact</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#16a34a' }}>
                {funnelData.projectedFit ?? '—'}
              </div>
            </div>
            {isStale && (
              <span style={{ alignSelf: 'center', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#d97706', background: 'rgba(217,119,6,0.07)', border: '1px solid rgba(217,119,6,0.22)', borderRadius: 2, padding: '2px 5px' }}>
                STALE
              </span>
            )}
          </div>
        )}

        {/* Context summary — standby/stale/not-AI mode */}
        {(isStandby || isStale || notInAiMode) && !hasResult && (
          <div style={{ marginTop: 4 }}>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 5 }}>Mission Context</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {dp > 0 && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568' }}>Data products</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#1a2035' }}>{dp}</span>
                </div>
              )}
              {anomCount > 0 && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568' }}>Active anomalies</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#dc2626' }}>{anomCount}</span>
                </div>
              )}
              {ms && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568' }}>Comm window</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, color: '#16a34a' }}>{ms.comm_window_remaining_s.toFixed(0)} s</span>
                </div>
              )}
              {ls && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568' }}>Link status</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: ls.link_stability > 0.7 ? '#16a34a' : '#d97706' }}>
                    {ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable'}
                  </span>
                </div>
              )}
            </div>
            {isStandby && !isAnalyzing && (
              <div style={{ marginTop: 7, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#7a8699', lineHeight: 1.5 }}>
                AI has not made any recommendation. No analysis has been requested.
              </div>
            )}
          </div>
        )}

        {/* Analyzing progress */}
        {isAnalyzing && (
          <div style={{ marginTop: 6 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {[
                { done: true, label: 'Mission state loaded' },
                { done: true, label: `${dp} data products ready` },
                { done: true, label: `${anomCount} anomalies identified` },
                { done: true, label: 'Communication constraints evaluated' },
                { done: false, label: `Requesting AI analysis (${props.aiProvider ?? 'provider'})…` },
              ].map(({ done, label }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: done ? '#b0bac9' : '#1d4ed8' }}>
                  <span style={{ flexShrink: 0 }}>{done ? '✓' : '●'}</span>
                  {label}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error state */}
        {isError && (
          <div style={{ background: 'rgba(220,38,38,0.04)', border: '1px solid rgba(220,38,38,0.20)', borderRadius: 3, padding: '8px 10px', marginTop: 8 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, fontWeight: 700, color: '#dc2626', marginBottom: 4 }}>⚠ ANALYSIS FAILED</div>
            {props.aiProvider && (
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#7a8699', marginBottom: 3 }}>Provider: {props.aiProvider}</div>
            )}
            {props.aiError && (
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#dc2626', wordBreak: 'break-all', lineHeight: 1.4 }}>
                {props.aiError.slice(0, 200)}
              </div>
            )}
            <div style={{ marginTop: 6, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#7a8699' }}>
              Mission operations remain available. Use Manual mode if needed.
            </div>
          </div>
        )}

        {/* Rejected state */}
        {props.aiRecommendationRejected && (
          <div style={{ background: 'rgba(220,38,38,0.04)', border: '1px solid rgba(220,38,38,0.18)', borderRadius: 3, padding: '8px 10px', marginTop: 8 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, fontWeight: 700, color: '#dc2626', marginBottom: 3 }}>AI RECOMMENDATION REJECTED</div>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#7a8699', lineHeight: 1.5, marginBottom: 8 }}>
              No transmission was initiated.
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => props.onSelectDecisionMode('manual')}
                style={{ flex: 1, padding: '6px 0', fontSize: 11, fontFamily: '"IBM Plex Sans"', fontWeight: 600, cursor: 'pointer', background: 'rgba(22,163,74,0.07)', color: '#16a34a', border: '1px solid rgba(22,163,74,0.25)', borderRadius: 3 }}
              >
                Return to Manual Planning
              </button>
              <button
                onClick={() => { props.onRunAiAnalysis(); }}
                style={{ flex: 1, padding: '6px 0', fontSize: 11, fontFamily: '"IBM Plex Sans"', fontWeight: 600, cursor: 'pointer', background: '#eef3fc', color: '#1d4ed8', border: '1px solid rgba(29,78,216,0.25)', borderRadius: 3 }}
              >
                Re-run Analysis
              </button>
            </div>
          </div>
        )}

        {/* Action button */}
        {(isStandby || isStale || isError) && !props.aiRecommendationRejected && (
          <button
            onClick={() => {
              if (props.decisionMode !== 'ai') props.onSelectDecisionMode('ai');
              props.onRunAiAnalysis();
            }}
            disabled={isAnalyzing}
            style={{
              width: '100%', padding: '8px 0', marginTop: 8,
              background: '#1d4ed8',
              color: '#ffffff',
              border: '1px solid #1d4ed8',
              borderRadius: 3, cursor: 'pointer',
              fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, fontWeight: 600,
              transition: 'background 0.15s',
            }}
          >
            {isStale ? 'Re-analyze Mission' : isError ? 'Retry AI Analysis' : 'Analyze Mission with AI'}
          </button>
        )}
        {isAnalyzing && (
          <button disabled style={{
            width: '100%', padding: '8px 0', marginTop: 8,
            background: '#eef3fc',
            color: '#1d4ed8',
            border: '1px solid rgba(29,78,216,0.25)',
            borderRadius: 3,
            fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            cursor: 'not-allowed',
          }}>
            Analyzing…
          </button>
        )}
      </div>

      {/* Tabbed AI result workspace — shown when results are ready */}
      {hasResult && (
        <div style={{
          display: 'flex', flexDirection: 'column',
          border: '1px solid #dde1e8', borderRadius: 4,
          marginTop: 4,
        }}>
          {/* Tab bar */}
          <TabBar<AiTab>
            tabs={[
              { id: 'prioritization', label: 'Prioritization', badge: rankedCount ?? undefined },
              { id: 'reasoning', label: 'Reasoning' },
              { id: 'decision', label: 'Decision' },
            ]}
            active={activeTab}
            onSelect={setActiveTab}
          />

          {/* Tab content — natural height, Main Control scrolls */}
          <div style={{ padding: '10px', overflowX: 'hidden' }}>
            {activeTab === 'prioritization' && (
              <>
                {/* AI funnel */}
                <AiFunnel
                  totalQueued={funnelData.totalQueued}
                  semanticCandidates={funnelData.semanticCandidates}
                  urgentCount={funnelData.urgentCount}
                  projectedFit={funnelData.projectedFit}
                />

                {/* WHY THIS MATTERS for selected product */}
                {selectedRp && (
                  <WhyThisMatters
                    rp={selectedRp}
                    dataProduct={productById.get(selectedRp.product_id)}
                    anomaly={selectedRp.anomaly_ids.length > 0 ? anomalyById.get(selectedRp.anomaly_ids[0]) : undefined}
                  />
                )}

                {/* Ranked product selector */}
                {props.aiPrioritization && props.aiPrioritization.ranked_products.length > 0 && (
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#b0bac9', letterSpacing: '0.06em', marginBottom: 5, textTransform: 'uppercase' }}>
                      Select product to view evidence
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, maxHeight: 200, overflowY: 'auto' }}>
                      {props.aiPrioritization.ranked_products
                        .slice()
                        .sort((a, b) => a.priority - b.priority)
                        .map((rp) => {
                          const isSelected = rp.product_id === selectedRpId;
                          return (
                            <button
                              key={rp.product_id}
                              onClick={() => setSelectedRpId(rp.product_id)}
                              style={{
                                display: 'flex', alignItems: 'center', gap: 8,
                                padding: '5px 8px', textAlign: 'left', cursor: 'pointer',
                                background: isSelected ? '#eef3fc' : 'transparent',
                                border: `1px solid ${isSelected ? 'rgba(29,78,216,0.28)' : '#e8eaee'}`,
                                borderRadius: 3,
                                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                              }}
                            >
                              <span style={{ fontSize: 9, color: '#b0bac9', minWidth: 20, textAlign: 'right' }}>
                                #{rp.priority}
                              </span>
                              <span style={{ fontSize: 11, color: isSelected ? '#1d4ed8' : '#4a5568', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {rp.product_id}
                              </span>
                              {rp.anomaly_ids.length > 0 && (
                                <span style={{ color: '#dc2626', fontSize: 9, flexShrink: 0 }}>⚠</span>
                              )}
                            </button>
                          );
                        })}
                    </div>
                  </div>
                )}

                {/* Full AI prioritization detail */}
                <AIDecisionPanel
                  prioritization={props.aiPrioritization}
                  providerName={props.aiActualProvider ?? props.aiProvider}
                  requestedProviderName={props.aiRequestedProvider}
                  candidateCount={props.aiCandidateCount}
                  totalProducts={props.dataProductsCount > 0 ? props.dataProductsCount : null}
                  prioritizationFallbackReason={props.aiPrioritizationFallbackReason}
                  recommendationFallbackReason={props.aiRecommendationFallbackReason}
                />
              </>
            )}
            {activeTab === 'reasoning' && (
              <RecommendationPanel
                recommendation={props.recommendation}
                providerName={props.aiActualProvider ?? props.aiProvider}
                requestedProviderName={props.aiRequestedProvider}
                recommendationFallbackReason={props.aiRecommendationFallbackReason}
                evaluation={props.recEval}
                riskWeights={props.riskWeights}
              />
            )}
            {activeTab === 'decision' && (
              <>
                {/* Human decision panel */}
                <AiHumanDecisionPanel props={props} />

                {/* Full decision chain */}
                <MissionDecisionPanel
                  prioritization={props.aiPrioritization}
                  recommendation={props.recommendation}
                  allPlans={props.allPlans}
                  recEval={props.recEval}
                  linkState={props.linkState}
                  providerName={props.aiActualProvider ?? props.aiProvider}
                  prioritizationError={props.aiPrioritizationError}
                  candidateCount={props.aiCandidateCount}
                />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Decision Mode selector ─────────────────────────────────────────────────────

function DecisionModeSelector(props: CommonProps) {
  const dp = props.dataProductsCount;
  const isLegacy = !props.hasDataProducts;

  if (isLegacy) {
    return (
      <div style={{ ...CARD, borderColor: 'rgba(217,119,6,0.22)', background: 'rgba(217,119,6,0.04)' }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#d97706', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>Legacy Packet Scenario</div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568', lineHeight: 1.55 }}>
          This scenario uses the legacy packet model. AI prioritization and high-volume manual planning are not available.
        </div>
      </div>
    );
  }

  return (
    <>
      <div style={{ ...CARD, marginBottom: 10 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #e8eaee' }}>
          Decision Workflow
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568', lineHeight: 1.55, marginBottom: 12 }}>
          <strong style={{ color: '#1a2035' }}>{dp} data products</strong> are awaiting downlink.
          Communication resources are limited. Choose how to build the transmission plan.
        </div>

        <div style={{
          border: `1px solid ${props.decisionMode === 'manual' ? 'rgba(22,163,74,0.28)' : '#dde1e8'}`,
          borderRadius: 3, padding: '10px 12px', marginBottom: 8,
          background: props.decisionMode === 'manual' ? 'rgba(22,163,74,0.04)' : '#f5f6f8',
        }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600, color: props.decisionMode === 'manual' ? '#16a34a' : '#1a2035', marginBottom: 5 }}>
            Manual Decision
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#4a5568', lineHeight: 1.5, marginBottom: 8 }}>
            Review and prioritize mission data yourself. Browse all {dp} products, apply filters, select what to transmit.
          </div>
          <button
            onClick={() => props.onSelectDecisionMode('manual')}
            style={{
              width: '100%', padding: '6px 0',
              background: props.decisionMode === 'manual' ? 'rgba(22,163,74,0.10)' : '#ffffff',
              color: props.decisionMode === 'manual' ? '#16a34a' : '#4a5568',
              border: `1px solid ${props.decisionMode === 'manual' ? 'rgba(22,163,74,0.28)' : '#c8cdd7'}`,
              borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            }}
          >
            {props.decisionMode === 'manual' ? '✓ Manual Mode Active' : 'Start Manual Planning'}
          </button>
        </div>

        <div style={{
          border: `1px solid ${props.decisionMode === 'ai' ? 'rgba(29,78,216,0.28)' : '#dde1e8'}`,
          borderRadius: 3, padding: '10px 12px',
          background: props.decisionMode === 'ai' ? '#eef3fc' : '#f5f6f8',
        }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600, color: props.decisionMode === 'ai' ? '#1d4ed8' : '#1a2035', marginBottom: 5 }}>
            AI Assisted
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#4a5568', lineHeight: 1.5, marginBottom: 8 }}>
            Ask the AI Copilot to analyze the mission context, anomalies, deadlines, and constraints — then recommend a prioritized transmission plan.
          </div>
          <button
            onClick={() => props.onSelectDecisionMode('ai')}
            style={{
              width: '100%', padding: '6px 0',
              background: props.decisionMode === 'ai' ? '#1d4ed8' : '#ffffff',
              color: props.decisionMode === 'ai' ? '#ffffff' : '#4a5568',
              border: `1px solid ${props.decisionMode === 'ai' ? '#1d4ed8' : '#c8cdd7'}`,
              borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            }}
          >
            {props.decisionMode === 'ai' ? '✓ AI Mode Active' : 'Use AI Assistant'}
          </button>
        </div>
      </div>

      {props.decisionMode === 'ai' && <AiSection {...props} />}

      {props.decisionMode === 'manual' && (
        <div style={{ ...CARD, borderColor: 'rgba(22,163,74,0.18)' }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#4a5568', lineHeight: 1.5 }}>
            Manual mode active. Navigate to the <strong style={{ color: '#16a34a' }}>Data</strong> section to browse and select data products.
          </div>
          {props.manualSelectedIds.size > 0 && (
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#16a34a' }}>
              {props.manualSelectedIds.size} products selected
            </div>
          )}
        </div>
      )}
    </>
  );
}

// ── Transmission section ──────────────────────────────────────────────────────

function TransmissionSection(props: CommonProps) {
  const activeTxPlan = props.decisionMode === 'manual' && props.manualPlan
    ? props.manualPlan
    : (props.recPlan ?? props.activePlan);
  const activeTxEval = props.decisionMode === 'manual'
    ? null
    : (props.recEval ?? props.activeEval);

  // Show choreography sequence panel when active (survives navigation — executionId persists)
  // Phase 5.1F (WORKSTREAM A): Pass the application-level presentationPhase as initialPhase.
  // This ensures remounts resume at the correct phase instead of restarting at plan_uplink.
  if (props.choreographyActive && props.executionId && props.authorizedAtMs !== null) {
    return (
      <TransmissionSequencePanel
        initialPhase={props.presentationPhase}
        pendingPlan={props.pendingExecutionPlan}
        playbackConfig={props.experienceManifest?.playback ?? null}
        propagationDelayS={props.propagationDelayS}
        availableCapacityBits={props.availableCapacityBits}
        executionId={props.executionId}
        authorizedAtMs={props.authorizedAtMs}
        playbackStartedAtMs={props.playbackStartedAtMs}
        onSetPlaybackStarted={props.onSetPlaybackStarted}
        onExecuteApproval={props.onExecuteApproval}
        executionResult={props.executionResult}
        onComplete={props.onChoreographyComplete}
        onError={props.onChoreographyError}
        onAttemptPulse={props.onAttemptPulse}
        onPhaseChange={props.onChoreographyPhaseChange}
      />
    );
  }

  // AI Assisted mode: show "AWAITING AUTHORIZATION" until operator approves in Decision
  const isAiMode = props.decisionMode === 'ai';
  const isTransmissionComplete = props.approvalPhase === 'complete';

  return (
    <>
      <ResizableSection title="Transmission Summary" icon="↗" accent="#1d4ed8">
        <div style={{ minWidth: 0 }}>
          <TransmissionSummaryPanel
            plan={activeTxPlan}
            evaluation={activeTxEval}
            availableCapacityBits={props.availableCapacityBits}
          />
        </div>
      </ResizableSection>

      {/* AI mode: single authorization point gate */}
      {isAiMode && !isTransmissionComplete && (
        <ResizableSection title="Authorization Required" icon="◉" accent="#1d4ed8">
          <div style={{
            background: '#ffffff',
            border: '1px solid rgba(29,78,216,0.18)',
            borderRadius: 4, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#7a8699', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              Awaiting Operator Authorization
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#4a5568', lineHeight: 1.6, marginBottom: 14 }}>
              Review the final recommendation in <strong style={{ color: '#1d4ed8' }}>AI Copilot → Decision</strong>.
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#7a8699', marginBottom: 12, lineHeight: 1.5 }}>
              Use <strong>✓ Approve Transmission</strong> in the Decision tab to authorize a single authoritative execution.
              Once approved, this Transmission panel will show execution status and playback.
            </div>
            <button
              onClick={() => props.onSelectDecisionMode('ai')}
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
                background: '#1d4ed8', color: '#ffffff',
                border: '1px solid #1d4ed8', borderRadius: 3,
              }}
            >
              Go to Decision
            </button>
          </div>
        </ResizableSection>
      )}

      {/* Manual mode: keep Approval bar with EVALUATE SELECTION / TRANSMIT SELECTED */}
      {!isAiMode && (
      <ResizableSection title="Approval" icon="◉" accent="#1d4ed8">
        <div style={{ minWidth: 0 }}>
          <ApprovalBar
            recommendedPlanId={props.recommendation ? props.recommendation.recommended_plan_id : null}
            recommendedPlan={
              props.decisionMode === 'manual' && props.manualPlan
                ? props.manualPlan
                : props.recPlan
            }
            baselinePlan={props.queue}
            approvalPhase={props.approvalPhase}
            onApproved={props.onApproved}
            onTransmitting={props.onTransmitting}
            onApprovalError={props.onApprovalError}
            decisionMode={props.decisionMode}
            manualOrder={props.manualOrder}
            rawDataProducts={props.rawDataProducts}
            manualEvaluation={props.manualAssessment?.evaluation ?? null}
            onManualEvaluate={props.onManualEvaluate}
            onManualTransmit={props.onManualTransmit}
            availableCapacityBits={props.availableCapacityBits}
          />
          {/* Show manual assessment details when available */}
          {props.decisionMode === 'manual' && props.manualAssessment && (
            <div style={{
              marginTop: 8,
              background: props.manualAssessmentStale ? 'rgba(217,119,6,0.05)' : 'rgba(22,163,74,0.04)',
              border: `1px solid ${props.manualAssessmentStale ? 'rgba(217,119,6,0.25)' : 'rgba(22,163,74,0.18)'}`,
              borderRadius: 4, padding: '8px 12px',
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11,
            }}>
              {props.manualAssessmentStale && (
                <div style={{ color: '#d97706', marginBottom: 4, fontSize: 10 }}>⚠ STALE — Re-evaluate to update</div>
              )}
              <div style={{ color: '#7a8699', marginBottom: 4, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Manual Plan Assessment</div>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ color: '#b0bac9', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>SELECTED</div>
                  <div style={{ color: '#1a2035', fontSize: 12, fontWeight: 700 }}>{props.manualAssessment.capacity_summary.selected_count}</div>
                </div>
                <div>
                  <div style={{ color: '#b0bac9', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>PAYLOAD</div>
                  <div style={{ color: '#1a2035', fontSize: 12, fontWeight: 700 }}>{formatBitsAsDataVolume(props.manualAssessment.capacity_summary.selected_bits)}</div>
                </div>
                <div>
                  <div style={{ color: '#b0bac9', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>RISK</div>
                  <div style={{
                    fontSize: 12, fontWeight: 700,
                    color: props.manualAssessment.evaluation.risk_level === 'LOW' ? '#16a34a' :
                           props.manualAssessment.evaluation.risk_level === 'MEDIUM' ? '#d97706' :
                           props.manualAssessment.evaluation.risk_level === 'HIGH' ? '#ea580c' : '#dc2626',
                  }}>
                    {props.manualAssessment.evaluation.risk_level}
                  </div>
                </div>
                <div>
                  <div style={{ color: '#b0bac9', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>DEFERRED</div>
                  <div style={{ color: '#1a2035', fontSize: 12, fontWeight: 700 }}>{props.manualAssessment.evaluation.deferred_packets.length}</div>
                </div>
              </div>
            </div>
          )}
          {props.decisionMode === 'manual' && props.manualAssessmentLoading && (
            <div style={{ marginTop: 6, color: '#7a8699', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 11 }}>
              Evaluating…
            </div>
          )}
          {props.decisionMode === 'manual' && props.manualAssessmentError && (
            <div style={{ marginTop: 6, color: '#dc2626', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 11 }}>
              Assessment error: {props.manualAssessmentError}
            </div>
          )}
        </div>
      </ResizableSection>
      )}

      <TransmissionOutcomeBanner
        approvalPhase={props.approvalPhase}
        simulationResult={props.approveResult?.simulation_result ?? null}
        isAiRecommendedPlan={
          props.approveResult?.simulation_result?.plan_id !== undefined &&
          props.approveResult.simulation_result.plan_id !== 'operator-override' &&
          props.approveResult.simulation_result.plan_id !== 'operator-manual'
        }
      />
    </>
  );
}

// ── V3.5 / F5: Log section — Tabbed workspace ────────────────────────────────

type LogTab = 'simulation' | 'narrative' | 'ground' | 'comparison' | 'sessionlog' | 'report';

function LogSection(props: CommonProps) {
  const hasResult = !!props.approveResult;
  const hasManualAssessment = !!props.manualAssessment && !props.manualAssessmentStale;
  const hasAiEval = !!props.recEval && !!props.recPlan;
  const showComparison = hasManualAssessment && hasAiEval;

  const [activeTab, setActiveTab] = useState<LogTab>(hasResult ? 'ground' : 'sessionlog');

  const isAiPlan = props.approveResult
    ? (props.approveResult.simulation_result.plan_id !== undefined &&
       props.approveResult.simulation_result.plan_id !== 'operator-override' &&
       props.approveResult.simulation_result.plan_id !== 'operator-manual')
    : false;

  const tabs: TabItem<LogTab>[] = [
    { id: 'ground', label: 'Reception', badge: hasResult ? '✓' : undefined },
    { id: 'simulation', label: 'Simulation' },
    { id: 'narrative', label: 'Narrative' },
    ...(showComparison ? [{ id: 'comparison' as const, label: 'Manual vs AI' }] : []),
    { id: 'sessionlog', label: 'Session Log', badge: props.sessionEvents.length > 0 ? props.sessionEvents.length : undefined },
    { id: 'report', label: 'Report' },
  ];

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      border: '1px solid #dde1e8', borderRadius: 4,
    }}>
      {/* Tab bar */}
      <TabBar<LogTab>
        tabs={tabs}
        active={activeTab}
        onSelect={setActiveTab}
      />

      {/* Tab content — natural height, Main Control scrolls */}
      <div style={{ padding: '10px', overflowX: 'hidden' }}>
        {activeTab === 'ground' && (
          <>
            {hasResult ? (
              <GroundReceptionPanel
                approveResult={props.approveResult!}
                anomalies={props.anomalies}
                groundInformationObjectives={props.experienceManifest?.ground_information_objectives ?? null}
                groundStationName={props.experienceManifest?.display.ground_station_name}
                queueTotal={props.dataProductsCount}
                queueDataBits={props.queuedDataBits}
                availableCapacityBits={props.availableCapacityBits}
                decisionMode={props.decisionMode}
              />
            ) : (
              <div style={{ color: '#b0bac9', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                No ground reception data yet. Complete a transmission to see the ground station reception.
              </div>
            )}
          </>
        )}
        {activeTab === 'simulation' && (
          <>
            {hasResult ? (
              <SimulationPanel
                approveResult={props.approveResult!}
                propagationDelayS={props.propagationDelayS}
              />
            ) : (
              <div style={{ color: '#b0bac9', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                No simulation data yet. Approve a transmission plan to run the simulation.
              </div>
            )}
          </>
        )}
        {activeTab === 'narrative' && (
          <>
            {hasResult ? (
              <TransmissionNarrativePanel
                prioritization={props.aiPrioritization}
                simulationResult={props.approveResult!.simulation_result}
                anomalies={props.anomalies}
                isAiRecommendedPlan={isAiPlan}
              />
            ) : (
              <div style={{ color: '#b0bac9', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                No transmission narrative yet. Complete a transmission to generate the mission narrative.
              </div>
            )}
          </>
        )}
        {activeTab === 'comparison' && showComparison && (
          <ManualVsAiPanel
            manualAssessment={props.manualAssessment!}
            aiEval={props.recEval!}
            aiPlanPayloadBits={(props.recPlan?.packets ?? []).reduce((s, p) => s + p.size_bits, 0)}
            aiPlanPacketCount={props.recPlan?.packets.length ?? 0}
            availableCapacityBits={props.availableCapacityBits}
          />
        )}
        {activeTab === 'sessionlog' && (
          <SessionLogPanel events={props.sessionEvents} />
        )}
        {activeTab === 'report' && (
          <MissionReportPanel
            approvalPhase={props.approvalPhase}
            missionState={props.missionState}
            recommendation={props.recommendation}
            aiPrioritization={props.aiPrioritization}
            aiProvider={props.aiProvider}
            simulationResult={props.approveResult?.simulation_result ?? null}
            anomalies={props.anomalies}
            distanceKm={props.distanceKm}
            propagationDelayS={props.propagationDelayS}
            roundTripTimeS={props.roundTripTimeS}
          />
        )}
      </div>
    </div>
  );
}

// ── Section headings ──────────────────────────────────────────────────────────

const SECTION_HEADINGS: Record<NavSection, { icon: string; title: string }> = {
  mission:      { icon: '◉', title: 'Mission Control' },
  spacecraft:   { icon: '⬡', title: 'Spacecraft' },
  comms:        { icon: '⌾', title: 'Link Health' },
  data:         { icon: '▦', title: 'Data Products' },
  ai:           { icon: '◈', title: 'AI Copilot' },
  transmission: { icon: '↗', title: 'Transmission' },
  log:          { icon: '≡', title: 'Mission Log' },
  config:       { icon: '⚙', title: 'Configuration' },
};

// ── V3.5: Workspace mode control buttons ──────────────────────────────────────

function WorkspaceModeControls({
  mode,
  onSet,
}: {
  mode: WorkspaceMode;
  onSet: (m: WorkspaceMode) => void;
}) {
  const btnBase: React.CSSProperties = {
    background: 'transparent',
    border: '1px solid #dde1e8',
    borderRadius: 3,
    color: '#a0aab8',
    cursor: 'pointer',
    fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
    fontSize: 11,
    padding: '2px 6px',
    lineHeight: 1,
    transition: 'color 0.12s, border-color 0.12s, background 0.12s',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
  };
  const btnActive: React.CSSProperties = {
    ...btnBase,
    color: '#1d4ed8',
    border: '1px solid rgba(29,78,216,0.35)',
    background: '#eef3fc',
  };

  if (mode === 'focus') {
    return (
      <button
        style={{ ...btnBase, color: '#dc2626', border: '1px solid rgba(220,38,38,0.28)', background: 'rgba(220,38,38,0.05)' }}
        onClick={() => onSet('normal')}
        title="Exit focus mode (Esc)"
        aria-label="Exit focus mode"
      >
        ↩ Exit Focus
      </button>
    );
  }

  return (
    <div style={{ display: 'flex', gap: 3 }}>
      {mode === 'expanded' && (
        <button
          style={btnActive}
          onClick={() => onSet('normal')}
          title="Return to normal mode"
          aria-label="Return to normal workspace"
        >
          ↔ Normal
        </button>
      )}
      {mode === 'normal' && (
        <button
          style={btnBase}
          onClick={() => onSet('expanded')}
          title="Expand workspace"
          aria-label="Expand workspace"
        >
          ⇔
        </button>
      )}
      <button
        style={btnBase}
        onClick={() => onSet('focus')}
        title="Focus workspace — full panel (Ctrl+Shift+F)"
        aria-label="Enter focus workspace"
      >
        ⛶
      </button>
    </div>
  );
}

// ── RightPanelProps ───────────────────────────────────────────────────────────

interface RightPanelProps extends CommonProps {
  section: NavSection;
  viewSettings: ViewSettings;
  onUpdateSetting: <K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) => void;
  onResetSettings: () => void;
  onResetPanelWidth: () => void;
  panelWidth: number;
  panelDefaultWidth: number;
}

// ── RightPanel ────────────────────────────────────────────────────────────────

export function RightPanel({
  section,
  viewSettings,
  onUpdateSetting,
  onResetSettings,
  onResetPanelWidth,
  panelWidth,
  panelDefaultWidth,
  workspaceMode,
  onSetWorkspaceMode,
  ...props
}: RightPanelProps) {
  const heading = SECTION_HEADINGS[section];
  const isFocus = workspaceMode === 'focus';
  const isExpanded = workspaceMode === 'expanded';

  // Compute effective panel width from workspace mode
  const effectiveWidth = isFocus ? undefined : isExpanded ? 'clamp(650px, 58vw, 1100px)' : panelWidth;

  // V3.5.2: purge obsolete per-section height keys written by the old ResizableSection
  useEffect(() => {
    try {
      const stale = Object.keys(localStorage).filter((k) => k.startsWith('GCSI_SEC_H_'));
      stale.forEach((k) => localStorage.removeItem(k));
    } catch { /* ignore */ }
  }, []);

  // AI lifecycle badge for section header
  const aiStatusBadge = section === 'ai' && props.aiLifecycle !== 'standby' && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: props.aiLifecycle === 'ready' ? 'rgba(22,163,74,0.08)' :
                  props.aiLifecycle === 'analyzing' ? '#eef3fc' :
                  props.aiLifecycle === 'error' ? 'rgba(220,38,38,0.08)' :
                  props.aiLifecycle === 'stale' ? 'rgba(217,119,6,0.08)' : 'transparent',
      color: props.aiLifecycle === 'ready' ? '#16a34a' :
             props.aiLifecycle === 'analyzing' ? '#1d4ed8' :
             props.aiLifecycle === 'error' ? '#dc2626' :
             props.aiLifecycle === 'stale' ? '#d97706' : 'transparent',
      border: `1px solid ${props.aiLifecycle === 'ready' ? 'rgba(22,163,74,0.22)' :
              props.aiLifecycle === 'analyzing' ? 'rgba(29,78,216,0.22)' :
              props.aiLifecycle === 'error' ? 'rgba(220,38,38,0.22)' :
              props.aiLifecycle === 'stale' ? 'rgba(217,119,6,0.22)' : 'transparent'}`,
      borderRadius: 2, padding: '1px 5px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      letterSpacing: '0.05em',
    }}>
      {props.aiLifecycle.toUpperCase()}
    </span>
  );

  const dataBadge = section === 'data' && props.dataProductsCount > 0 && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: '#f5f6f8',
      color: '#7a8699',
      border: '1px solid #dde1e8',
      borderRadius: 2, padding: '1px 5px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
    }}>
      {props.dataProductsCount}
    </span>
  );

  // Focus mode indicator
  const focusBadge = isFocus && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: 'rgba(220,38,38,0.06)',
      color: '#dc2626',
      border: '1px solid rgba(220,38,38,0.22)',
      borderRadius: 2, padding: '1px 5px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      letterSpacing: '0.05em',
    }}>
      FOCUS
    </span>
  );

  // Expanded mode indicator
  const expandedBadge = isExpanded && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: '#eef3fc',
      color: '#1d4ed8',
      border: '1px solid rgba(29,78,216,0.22)',
      borderRadius: 2, padding: '1px 5px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      letterSpacing: '0.05em',
    }}>
      EXPANDED
    </span>
  );

  return (
    <div
      className="workspace-right-panel"
      style={{
        ...(isFocus ? { flex: 1 } : { width: effectiveWidth, flexShrink: 0 }),
        minWidth: isFocus ? 0 : 340,
        maxWidth: isFocus ? undefined : isExpanded ? 1100 : 680,
        background: '#f5f6f8',
        borderLeft: '1px solid #dde1e8',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Panel header — sticky, flat */}
      <div style={{
        padding: '9px 14px 8px',
        borderBottom: '1px solid #dde1e8',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: '#ffffff',
      }}>
        <span style={{ fontSize: 11, color: '#a0aab8', lineHeight: 1 }}>{heading.icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, fontWeight: 600,
          color: '#1a2035',
          letterSpacing: '0.01em',
          flex: 1,
          minWidth: 0,
        }}>
          {heading.title}
          {aiStatusBadge}
          {dataBadge}
          {focusBadge}
          {expandedBadge}
        </span>
        {/* V3.5: Workspace mode controls */}
        <WorkspaceModeControls mode={workspaceMode} onSet={onSetWorkspaceMode} />
      </div>

      {/* Primary vertical scroll container — single scrollbar for all content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '10px',
        minWidth: 0,
        minHeight: 0,
      }}>
        {section === 'mission'      && <MissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'spacecraft'   && <SpacecraftSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'comms'        && <CommsSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'data'         && <DataSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'ai'           && <AiSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'transmission' && (
          <>
            {props.decisionMode === 'unselected' && (
              <DecisionModeSelector {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />
            )}
            {props.decisionMode !== 'unselected' && <TransmissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
            {props.decisionMode === 'unselected' && <TransmissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
          </>
        )}
        {section === 'log'          && <LogSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'config'       && (
          <ConfigPanel
            settings={viewSettings}
            onUpdate={onUpdateSetting}
            onResetSettings={onResetSettings}
            onResetPanelWidth={onResetPanelWidth}
            panelWidth={panelWidth}
            panelDefaultWidth={panelDefaultWidth}
            availableScenarios={props.availableScenarios}
            activeScenarioPath={props.activeScenarioPath}
            scenarioSwitching={props.scenarioSwitching}
            onSwitchScenario={props.onSwitchScenario}
            sourceMode={props.sourceMode}
          />
        )}
      </div>
    </div>
  );
}
