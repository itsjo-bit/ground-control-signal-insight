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
  MissionState,
  RankedProduct,
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

// ── Shared style tokens (V4.1 restrained dark workspace) ─────────────────────

const CARD: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #30363d',
  borderRadius: 4,
  padding: '10px 12px',
  marginBottom: 6,
  minWidth: 0,
  overflowX: 'hidden',
};

const LABEL: React.CSSProperties = {
  fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
  fontSize: 9,
  color: '#8b949e',
  letterSpacing: '0.05em',
  marginBottom: 3,
  textTransform: 'uppercase' as const,
};

const VALUE: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
  fontSize: 14,
  fontWeight: 700,
  lineHeight: 1,
  color: '#e6edf3',
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
  /** Phase 8B: whether manual mode was seeded from an AI recommendation via Modify */
  manualEditOrigin: 'manual' | 'ai_recommendation';
  /** Phase 8B: immutable snapshot of AI baseline deferred IDs at Modify time */
  aiBaselineDeferredIds: ReadonlySet<string>;
  /** Phase 8B.1: immutable snapshot of recommended plan packet ordering at Modify time */
  aiBaselinePlanOrder: readonly string[];
  onToggleManualSelect: (productId: string) => void;
  onClearManualSelection: () => void;
  onManualReorder: (newOrder: string[]) => void;
  // ── V3.5 props ───────────────────────────────────────────────────────────────
  workspaceMode?: WorkspaceMode;
  onSetWorkspaceMode?: (mode: WorkspaceMode) => void;
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
  /**
   * Navigate to the given section (e.g. 'ai') from within a panel.
   * Optional for backward compatibility; used by TransmissionSection.
   */
  onNavigateSection?: (section: NavSection) => void;
}

// ── StatGrid ──────────────────────────────────────────────────────────────────

function StatGrid({ items }: { items: { label: string; value: string; color: string }[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 10 }}>
      {items.map(({ label, value, color }) => (
        <div key={label} style={{
          background: '#21262d',
          border: '1px solid #30363d',
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
        borderBottom: '1px solid #30363d',
        marginBottom: 0,
        flexShrink: 0,
        background: '#161b22',
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
              borderBottom: isActive ? '2px solid #2f81f7' : '2px solid transparent',
              color: isActive ? '#2f81f7' : '#8b949e',
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
            onFocus={(e) => { (e.currentTarget as HTMLButtonElement).style.outline = '1px solid rgba(47,129,247,0.35)'; }}
            onBlur={(e) => { (e.currentTarget as HTMLButtonElement).style.outline = 'none'; }}
          >
            {tab.label}
            {tab.badge !== undefined && tab.badge !== null && (
              <span style={{
                background: isActive ? 'rgba(47,129,247,0.14)' : '#21262d',
                color: isActive ? '#2f81f7' : '#8b949e',
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
      <div style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid #30363d' }}>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 3 }}>
          Mission
        </div>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 17, fontWeight: 700, color: '#e6edf3', letterSpacing: '0.02em' }}>
          {m.display.mission_name}
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#d29922', marginTop: 2 }}>
          {m.display.scenario_name}
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#656d76', marginTop: 3, lineHeight: 1.4 }}>
          {m.display.disclaimer}
        </div>
      </div>

      {/* Mission metrics — flat grid, no cards */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
          Situation
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px' }}>
          {heroMetrics.map(({ label, value, color }) => (
            <div key={label} style={{ paddingBottom: 6, borderBottom: '1px solid #30363d' }}>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#656d76', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 1 }}>
                {label}
              </div>
              <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: color ?? '#e6edf3' }}>
                {value}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Spacecraft Health */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6, paddingBottom: 5, borderBottom: '1px solid #30363d' }}>
          Spacecraft Health
        </div>
        {Object.entries(m.subsystem_status).map(([key, ss]) => {
          const isGood = ss.status === 'nominal' || ss.status === 'stable';
          const color = ss.status === 'degraded' ? '#d29922' : ss.status === 'critical' ? '#f85149' : '#3fb950';
          return (
            <div key={key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid #30363d' }}>
              <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#8b949e' }}>
                {key.replace('_', ' ')}
              </span>
              <div style={{ textAlign: 'right' }}>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11, fontWeight: 600, color, display: 'block' }}>
                  {ss.label}
                </span>
                {ss.note && (
                  <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#656d76' }}>
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
          border: '1px solid rgba(248,81,73,0.25)',
          background: 'rgba(248,81,73,0.06)',
          marginBottom: 8,
        }}>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#f85149', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>
            Detected Event
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 12, fontWeight: 700, color: '#f85149', marginBottom: 2 }}>
            THERMAL ANOMALY
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, color: '#d29922', marginBottom: 5 }}>
            {thermalAnomaly.anomaly_id}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 5, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ background: 'rgba(248,81,73,0.10)', color: '#f85149', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9, padding: '1px 6px', borderRadius: 2, border: '1px solid rgba(248,81,73,0.25)' }}>
              ACTIVE
            </span>
            <span style={{ color: '#8b949e', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9 }}>
              SEVERITY {(thermalAnomaly.severity * 100).toFixed(0)}%
            </span>
            {detectedMinutesAgo !== null && (
              <span style={{ color: '#8b949e', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9 }}>
                ~{detectedMinutesAgo}m ago
              </span>
            )}
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
            {thermalAnomaly.description.slice(0, 200)}{thermalAnomaly.description.length > 200 ? '…' : ''}
          </div>
        </div>
      )}

      {/* Link Health summary — thin divider table */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6, paddingBottom: 5, borderBottom: '1px solid #30363d' }}>
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
              <div style={{ color: '#656d76', fontSize: 8, fontFamily: '"IBM Plex Sans"', marginBottom: 1, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
              <div style={{ color: color ?? '#e6edf3', fontSize: 11, fontFamily: '"IBM Plex Mono", ui-monospace', fontWeight: 600 }}>{value}</div>
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
        <ResizableSection title="Mission State" icon="◉" accent="#2f81f7">
            {ms ? (
              <TableScroll>
                <MissionStatePanel missionState={ms} />
              </TableScroll>
            ) : (
              <div style={{ color: '#656d76', fontSize: 12 }}>No mission data</div>
            )}
          </ResizableSection>
          {ls && (
            <ResizableSection title="Comm Budget" icon="⌾" accent="#2f81f7">
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
          { label: 'Window', value: `${ms.comm_window_remaining_s.toFixed(0)} s`, color: ms.comm_window_remaining_s < 60 ? '#f85149' : '#3fb950' },
          { label: 'Risk', value: ms.risk_level, color: ms.risk_level === 'CRITICAL' ? '#f85149' : ms.risk_level === 'HIGH' ? '#f85149' : ms.risk_level === 'MEDIUM' ? '#d29922' : '#3fb950' },
          { label: 'SNR', value: `${ls.snr_db.toFixed(1)} dB`, color: ls.snr_db < 5 ? '#f85149' : ls.snr_db < 10 ? '#d29922' : '#3fb950' },
          { label: 'Stability', value: `${(ls.link_stability * 100).toFixed(0)}%`, color: ls.link_stability < 0.5 ? '#f85149' : ls.link_stability < 0.75 ? '#d29922' : '#3fb950' },
        ]} />
      )}

      {props.decisionMode === 'unselected' && dpCount > 0 && (
        <div style={{ ...CARD, marginBottom: 10 }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
            Mission Context
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e' }}>Data products</span>
              <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#e6edf3' }}>{dpCount}</span>
            </div>
            {anomCount > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e' }}>Active anomalies</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#f85149' }}>{anomCount}</span>
              </div>
            )}
            {ms && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e' }}>Comm window</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#3fb950' }}>{ms.comm_window_remaining_s.toFixed(0)} s</span>
              </div>
            )}
            {ls && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e' }}>Link status</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 12, fontWeight: 600, color: ls.link_stability > 0.7 ? '#3fb950' : '#d29922' }}>
                  {ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable'}
                </span>
              </div>
            )}
          </div>
          <div style={{ marginTop: 8, paddingTop: 7, borderTop: '1px solid #30363d', fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
            No transmission plan has been created yet. Choose a decision mode below or navigate to the AI or Data sections.
          </div>
        </div>
      )}

      <ResizableSection title="Mission State" icon="◉" accent="#2f81f7">
        {ms ? (
          <TableScroll>
            <MissionStatePanel missionState={ms} />
          </TableScroll>
        ) : (
          <div style={{ color: '#8b949e', fontSize: 12 }}>No mission data</div>
        )}
      </ResizableSection>

      {props.linkState && (
        <ResizableSection title="Comm Budget" icon="⌾" accent="#2f81f7">
          <CommBudgetBar
            availableCapacityBits={props.availableCapacityBits}
            queuedDataBits={props.queuedDataBits}
            dataProductsCount={props.dataProductsCount}
            remainingWindowS={props.linkState.remaining_window_s}
          />
        </ResizableSection>
      )}

      {props.anomalies.length > 0 && (
        <ResizableSection title="Anomalies" icon="⚠" accent="#f85149">
          {props.anomalies.map((a) => (
            <div key={a.anomaly_id} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start',
              padding: '6px 0', borderBottom: '1px solid #30363d',
              minWidth: 0,
            }}>
              <span style={{ color: '#f85149', flexShrink: 0, fontSize: 10, marginTop: 2 }}>
                {a.severity >= 0.75 ? '●' : '○'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  color: '#e6edf3', fontWeight: 600, fontSize: 12,
                  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                  wordBreak: 'break-all',
                }}>{a.anomaly_id}</div>
                <div style={{ color: '#8b949e', fontSize: 11, marginTop: 2, lineHeight: 1.45 }}>{a.description}</div>
                <div style={{ color: '#656d76', fontSize: 10, marginTop: 1 }}>
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
    <ResizableSection title="Spacecraft Geometry" icon="⬡" accent="#2f81f7">
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
      <div style={{ color: '#8b949e', fontSize: 12 }}>No link data available</div>
    </div>
  );
  return (
    <ResizableSection title="Link Health" icon="⌾" accent="#2f81f7">
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

type SortKey = 'criticality' | 'deadline_s' | 'size_bits' | 'age_s' | 'mission_relevance' | 'plan_order' | 'selected';

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

  // Phase 8B.1: conditions for the two new sort modes
  const isAiModifyMode = props.decisionMode === 'manual' && props.manualEditOrigin === 'ai_recommendation';
  const showPlanOrderSort = isAiModifyMode && props.aiBaselinePlanOrder.length > 0;
  const showSelectedSort = props.decisionMode === 'manual';

  // Phase 8B.1: when Modify is clicked (new non-empty plan order arrives), default to Plan Order.
  useEffect(() => {
    if (showPlanOrderSort) {
      setSortKey('plan_order');
      setSortDesc(true);
      setPage(0);
    }
    // Only trigger when plan order provenance changes (new Modify session).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.aiBaselinePlanOrder]);

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

    if (sortKey === 'plan_order') {
      // Phase 8B.1: Display in the order captured from recPlan.packets at Modify time.
      // Products not in the baseline plan order (e.g. added later) go to the end.
      const planIdx = new Map<string, number>(
        props.aiBaselinePlanOrder.map((id, i) => [id, i])
      );
      list = [...list].sort((a, b) => {
        const ia = planIdx.get(a.product_id) ?? props.aiBaselinePlanOrder.length;
        const ib = planIdx.get(b.product_id) ?? props.aiBaselinePlanOrder.length;
        return sortDesc ? ia - ib : ib - ia;
      });
    } else if (sortKey === 'selected') {
      // Phase 8B.1: Selected products first (sortDesc) or last (sortAsc).
      // Group 1 (selected): ordered by manualOrder.
      // Group 2 (unselected): ordered by aiBaselinePlanOrder if available, else stable original.
      const manualIdx = new Map<string, number>(
        props.manualOrder.map((id, i) => [id, i])
      );
      const planIdx = new Map<string, number>(
        props.aiBaselinePlanOrder.map((id, i) => [id, i])
      );
      const originalIdx = new Map<string, number>(
        products.map((p, i) => [p.product_id, i])
      );
      list = [...list].sort((a, b) => {
        const aSelected = props.manualSelectedIds.has(a.product_id);
        const bSelected = props.manualSelectedIds.has(b.product_id);
        if (aSelected !== bSelected) {
          // sortDesc = selected first; sortAsc = unselected first
          return sortDesc ? (aSelected ? -1 : 1) : (aSelected ? 1 : -1);
        }
        if (aSelected) {
          // Both selected: order by manualOrder
          return (manualIdx.get(a.product_id) ?? 0) - (manualIdx.get(b.product_id) ?? 0);
        }
        // Both unselected: prefer aiBaselinePlanOrder, then original data order
        const ia = planIdx.has(a.product_id)
          ? (planIdx.get(a.product_id) as number)
          : (originalIdx.get(a.product_id) ?? 0) + props.aiBaselinePlanOrder.length;
        const ib = planIdx.has(b.product_id)
          ? (planIdx.get(b.product_id) as number)
          : (originalIdx.get(b.product_id) ?? 0) + props.aiBaselinePlanOrder.length;
        return ia - ib;
      });
    } else {
      list = [...list].sort((a, b) => {
        const va = a[sortKey as keyof typeof a] as number;
        const vb = b[sortKey as keyof typeof b] as number;
        return sortDesc ? vb - va : va - vb;
      });
    }
    return list;
  }, [products, search, filter, sortKey, sortDesc, props.aiBaselinePlanOrder, props.manualOrder, props.manualSelectedIds]);

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
      <div style={{ ...CARD, borderColor: 'rgba(210,153,34,0.25)', background: 'rgba(210,153,34,0.06)' }}>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 9, color: '#d29922', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
          Legacy Packet Scenario
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e', lineHeight: 1.55, marginBottom: 10 }}>
          This scenario uses the legacy packet model. AI data-product prioritization and high-volume manual planning are unavailable.
        </div>
        {props.queue && (
          <ResizableSection title="Transmission Queue" icon="▦" accent="#2f81f7">
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
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 7, paddingBottom: 6, borderBottom: '1px solid #30363d' }}>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', fontWeight: 600 }}>
            Data Products
          </span>
          <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 15, fontWeight: 700, color: '#e6edf3' }}>
            {products.length}
          </span>
        </div>
        <input
          type="text"
          placeholder="Search products, subsystem, anomaly…"
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
          style={{
            width: '100%', background: '#21262d', border: '1px solid #30363d',
            color: '#e6edf3', borderRadius: 3, padding: '5px 10px', fontSize: 12,
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
                  background: activeF ? 'rgba(47,129,247,0.14)' : '#21262d',
                  color: activeF ? '#2f81f7' : '#8b949e',
                  border: `1px solid ${activeF ? 'rgba(47,129,247,0.35)' : '#30363d'}`,
                  borderRadius: 2, cursor: 'pointer', fontFamily: '"IBM Plex Sans", system-ui',
                  fontWeight: activeF ? 600 : 400,
                }}>
                  {FILTER_LABELS[fStr] ?? fStr}
                </button>
              );
            })}
        </div>
        <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#656d76', marginRight: 2, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Sort</span>
          {(['criticality', 'deadline_s', 'size_bits', 'age_s', 'mission_relevance'] as SortKey[]).map((key) => {
            const labels: Record<SortKey, string> = { criticality: 'Crit', deadline_s: 'Deadline', size_bits: 'Size', age_s: 'Age', mission_relevance: 'Relevance', plan_order: 'Plan Order', selected: 'Selected' };
            const activeS = sortKey === key;
            return (
              <button key={key} onClick={() => handleSort(key)} style={{
                fontSize: 10, padding: '2px 7px',
                background: activeS ? 'rgba(47,129,247,0.12)' : 'transparent',
                color: activeS ? '#2f81f7' : '#8b949e',
                border: `1px solid ${activeS ? 'rgba(47,129,247,0.30)' : '#30363d'}`,
                borderRadius: 2, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
              }}>
                {labels[key]}{activeS ? (sortDesc ? ' ↓' : ' ↑') : ''}
              </button>
            );
          })}
          {/* Phase 8B.1: Selected sort — shown when manual selection is active */}
          {showSelectedSort && (() => {
            const activeS = sortKey === 'selected';
            return (
              <button onClick={() => handleSort('selected')} style={{
                fontSize: 10, padding: '2px 7px',
                background: activeS ? 'rgba(47,129,247,0.12)' : 'transparent',
                color: activeS ? '#2f81f7' : '#8b949e',
                border: `1px solid ${activeS ? 'rgba(47,129,247,0.30)' : '#30363d'}`,
                borderRadius: 2, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
              }}>
                {'Selected'}{activeS ? (sortDesc ? ' ↓' : ' ↑') : ''}
              </button>
            );
          })()}
          {/* Phase 8B.1: Plan Order sort — shown only when Modify-AI provenance exists */}
          {showPlanOrderSort && (() => {
            const activeS = sortKey === 'plan_order';
            return (
              <button onClick={() => handleSort('plan_order')} style={{
                fontSize: 10, padding: '2px 7px',
                background: activeS ? 'rgba(47,129,247,0.12)' : 'transparent',
                color: activeS ? '#2f81f7' : '#8b949e',
                border: `1px solid ${activeS ? 'rgba(47,129,247,0.30)' : '#30363d'}`,
                borderRadius: 2, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
              }}>
                {'Plan Order'}{activeS ? (sortDesc ? ' ↓' : ' ↑') : ''}
              </button>
            );
          })()}
          <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#656d76' }}>
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
            const critColor = p.criticality >= 0.85 ? '#f85149' : p.criticality >= 0.7 ? '#d29922' : '#3fb950';
            // Phase 8B: AI baseline deferred status (only relevant in Modify-AI mode)
            const wasAiBaselineDeferred = isAiModifyMode && props.aiBaselineDeferredIds.has(p.product_id);
            return (
              <div key={p.product_id} style={{
                background: isSelected ? 'rgba(63,185,80,0.06)' : wasAiBaselineDeferred && !isSelected ? 'rgba(210,153,34,0.04)' : 'transparent',
                borderBottom: '1px solid #30363d',
                borderLeft: isSelected ? '2px solid #3fb950' : wasAiBaselineDeferred && !isSelected ? '2px solid rgba(210,153,34,0.4)' : '2px solid transparent',
                overflow: 'hidden',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 6px', cursor: 'pointer' }}
                  onClick={() => setExpandedId(isExp ? null : p.product_id)}>
                  {props.decisionMode === 'manual' && (
                    <div
                      onClick={(e) => { e.stopPropagation(); props.onToggleManualSelect(p.product_id); }}
                      style={{
                        width: 13, height: 13, borderRadius: 3, flexShrink: 0, cursor: 'pointer',
                        background: isSelected ? '#3fb950' : 'transparent',
                        border: `1px solid ${isSelected ? '#3fb950' : '#444c56'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}
                    >
                      {isSelected && <span style={{ color: '#0d1117', fontSize: 8, fontWeight: 700 }}>✓</span>}
                    </div>
                  )}
                  {isSelected && rank >= 0 && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#3fb950', minWidth: 16, textAlign: 'right', flexShrink: 0 }}>#{rank + 1}</span>
                  )}
                  {/* Phase 8B: AI baseline status badge — only shown in Modify-AI mode */}
                  {isAiModifyMode && !isSelected && wasAiBaselineDeferred && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#d29922', letterSpacing: '0.05em', flexShrink: 0, whiteSpace: 'nowrap' }}>AI DEFERRED</span>
                  )}
                  {p.anomaly_id && (
                    <span style={{ color: '#f85149', fontSize: 9, fontFamily: '"IBM Plex Mono"', fontWeight: 700, flexShrink: 0 }}>⚠</span>
                  )}
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#e6edf3', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.product_id}
                  </span>
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#8b949e', flexShrink: 0, minWidth: 52, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {p.subsystem}
                    </span>
                  )}
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#8b949e', flexShrink: 0, minWidth: 40 }}>
                      {formatBitsAsDataVolume(p.size_bits)}
                    </span>
                  )}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: critColor, letterSpacing: '0.03em', whiteSpace: 'nowrap', fontWeight: 600 }}>
                      CRIT {p.criticality.toFixed(2)}
                    </span>
                    <div style={{ width: 24, height: 2, background: '#30363d', borderRadius: 1 }}>
                      <div style={{ width: `${p.criticality * 100}%`, height: '100%', borderRadius: 1, background: critColor }} />
                    </div>
                  </div>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: p.deadline_s < 120 ? '#f85149' : '#8b949e', flexShrink: 0, minWidth: 36, textAlign: 'right' }}>
                    {p.deadline_s < 3600 ? `${p.deadline_s.toFixed(0)}s` : `${(p.deadline_s / 3600).toFixed(1)}h`}
                  </span>
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: p.mission_relevance > 0.7 ? '#3fb950' : '#656d76', flexShrink: 0, minWidth: 28 }}>
                      {(p.mission_relevance * 100).toFixed(0)}%
                    </span>
                  )}
                  <span style={{ color: '#656d76', fontSize: 9, flexShrink: 0 }}>{isExp ? '▲' : '▼'}</span>
                </div>
                {isExp && (
                  <div style={{ padding: '8px 10px 10px', borderTop: '1px solid #30363d', background: '#21262d' }}>
                    <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.55, marginBottom: 8 }}>
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
                          <span style={{ color: '#8b949e', fontFamily: '"IBM Plex Sans"' }}>{label}</span>
                          <span style={{ color: '#e6edf3', fontFamily: '"IBM Plex Mono"' }}>{val}</span>
                        </div>
                      ))}
                    </div>
                    {props.decisionMode === 'manual' && (
                      <button
                        onClick={() => props.onToggleManualSelect(p.product_id)}
                        style={{
                          marginTop: 8, fontSize: 11, padding: '4px 12px',
                          background: isSelected ? 'rgba(248,81,73,0.08)' : 'rgba(63,185,80,0.08)',
                          color: isSelected ? '#f85149' : '#3fb950',
                          border: `1px solid ${isSelected ? 'rgba(248,81,73,0.25)' : 'rgba(63,185,80,0.25)'}`,
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
            <div style={{ color: '#8b949e', fontSize: 12, padding: '12px 0', textAlign: 'center' }}>
              No products match the current filter.
            </div>
          )}
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: '6px 0', borderTop: '1px solid #30363d', flexShrink: 0 }}>
          <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
            style={{ fontSize: 10, padding: '3px 8px', background: '#21262d', border: '1px solid #30363d', borderRadius: 3, color: '#8b949e', cursor: 'pointer' }}>
            ←
          </button>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#8b949e' }}>
            {page + 1} / {totalPages}
          </span>
          <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}
            style={{ fontSize: 10, padding: '3px 8px', background: '#21262d', border: '1px solid #30363d', borderRadius: 3, color: '#8b949e', cursor: 'pointer' }}>
            →
          </button>
        </div>
      )}

      {/* Sticky selection summary bar — manual mode with selection */}
      {props.decisionMode === 'manual' && selectedCount > 0 && (
        <div style={{
          borderTop: '1px solid rgba(63,185,80,0.28)',
          background: '#161b22',
          padding: '7px 10px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexShrink: 0,
        }}>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#3fb950', fontWeight: 700 }}>{selectedCount}</span>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e' }}>selected</span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#8b949e' }}>{formatBitsAsDataVolume(selectedBits)}</span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: capacityUsedPct > 90 ? '#f85149' : '#8b949e' }}>{capacityUsedPct.toFixed(0)}% cap</span>
          <button
            onClick={props.onClearManualSelection}
            style={{ marginLeft: 'auto', fontSize: 10, padding: '3px 8px', background: 'transparent', color: '#8b949e', border: '1px solid #30363d', borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"' }}
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
      background: '#161b22',
      border: '1px solid #30363d',
      borderRadius: 4, padding: '12px 14px',
      marginBottom: 12,
    }}>
      {/* Header: Mission Decision */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6, paddingBottom: 7, borderBottom: '1px solid #30363d' }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', fontWeight: 600 }}>
          Mission Decision
        </div>
        {rec.recommended_plan_id && (
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#656d76', letterSpacing: '0.05em' }}>
            {rec.recommended_plan_id.toUpperCase()}
          </div>
        )}
      </div>
      <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#8b949e', marginBottom: 10, lineHeight: 1.5 }}>
        Stage 1: semantic candidate prioritization → Stage 2: final plan selection → Human approval
      </div>

      {/* Metrics — flat rows */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px', marginBottom: 14 }}>
        {[
          { label: 'PRIORITIZED QUEUE', value: `${selectedCount} products`, color: '#2f81f7' },
          { label: 'PROJECTED THIS CONTACT', value: deferredCount < selectedCount ? `${selectedCount - deferredCount} products` : `${selectedCount} products`, color: '#3fb950' },
          { label: 'PRIORITY PAYLOAD', value: formatBitsAsDataVolume(planPayloadBits), color: '#e6edf3' },
          { label: 'CONTACT CAPACITY', value: formatBitsAsDataVolume(props.availableCapacityBits), color: '#8b949e' },
          { label: 'PLAN RISK', value: riskLevel, color: riskColor },
          { label: 'PROJECTED DEFERRED', value: `${deferredCount}`, color: deferredCount > 0 ? '#d29922' : '#3fb950' },
          ...(reqDeliveryRate !== null ? [{ label: 'REQ. DELIVERY', value: `${(reqDeliveryRate * 100).toFixed(0)}%`, color: reqDeliveryRate >= 0.8 ? '#3fb950' : '#d29922' }] : []),
          ...(anomalyCoverage !== null ? [{ label: 'ANOMALY COVERAGE', value: `${(anomalyCoverage * 100).toFixed(0)}%`, color: anomalyCoverage >= 0.8 ? '#3fb950' : anomalyCoverage >= 0.5 ? '#d29922' : '#f85149' }] : []),
        ].map(({ label, value, color }) => (
          <div key={label} style={{ paddingBottom: 5, borderBottom: '1px solid #30363d' }}>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#656d76', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 1 }}>{label}</div>
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
              background: '#2f81f7', color: '#ffffff',
              border: '1px solid #2f81f7', borderRadius: 3,
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
                background: '#21262d', color: '#8b949e',
                border: '1px solid #444c56', borderRadius: 3,
              }}
            >
              ✎ Modify Plan
            </button>
            <button
              onClick={props.onRejectAiPlan}
              style={{
                flex: 1, padding: '7px 0', fontSize: 11, fontWeight: 500,
                fontFamily: '"IBM Plex Sans"', cursor: 'pointer',
                background: 'rgba(248,81,73,0.08)', color: '#f85149',
                border: '1px solid rgba(248,81,73,0.25)', borderRadius: 3,
              }}
            >
              ✕ Reject
            </button>
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#656d76', textAlign: 'center', marginTop: 2 }}>
            Approve authorizes transmission · Modify seeds manual planning · Reject does not transmit
          </div>
        </div>
      )}

      {isTransmitting && (
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#2f81f7', textAlign: 'center', padding: '8px 0' }}>
          Transmission in progress…
        </div>
      )}
      {isComplete && (
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#3fb950', textAlign: 'center', padding: '8px 0' }}>
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
  const AI_COLOR = '#2f81f7';
  const MUTED = '#8b949e';
  const DIM = '#656d76';

  return (
    <div style={{
      background: '#161b22',
      border: '1px solid rgba(47,129,247,0.2)',
      borderRadius: 4, padding: '10px 12px',
      marginBottom: 8,
    }}>
      {/* WHY THIS MATTERS header */}
      <div style={{
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9,
        color: '#2f81f7', letterSpacing: '0.07em', marginBottom: 6,
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
          background: '#21262d', borderRadius: 3, padding: '5px 8px',
          borderLeft: '2px solid rgba(47,129,247,0.25)',
        }}>
          "{rp.reason}"
        </div>
      </div>

      {/* Authoritative evidence from DataProduct */}
      {dataProduct && (
        <div>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#3fb950', letterSpacing: '0.08em', marginBottom: 5 }}>
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
                <span style={{ color: '#656d76', fontFamily: '"IBM Plex Sans"' }}>{label}</span>
                <span style={{ color: '#e6edf3', fontFamily: '"IBM Plex Mono"', wordBreak: 'break-all' }}>{val}</span>
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
  const DIM = '#656d76';
  const rows: Array<{ count: number; label: string; color: string }> = [
    { count: totalQueued, label: 'QUEUED PRODUCTS', color: '#d29922' },
    { count: semanticCandidates, label: 'SEMANTIC CANDIDATES', color: '#2f81f7' },
    { count: urgentCount, label: 'URGENT / OPERATIONALLY RELEVANT', color: '#f85149' },
    { count: projectedFit ?? 0, label: 'PROJECTED TO FIT CONTACT', color: '#3fb950' },
  ];

  return (
    <div style={{
      background: '#161b22',
      border: '1px solid #30363d',
      borderRadius: 4, padding: '10px 12px',
      marginBottom: 10,
    }}>
      <div style={{
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9,
        color: '#8b949e', letterSpacing: '0.07em', marginBottom: 8,
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
            <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: '#8b949e', letterSpacing: '0.04em', flexShrink: 0 }}>
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

  const statusColor = isAnalyzing ? '#2f81f7' : isReady ? '#3fb950' : isError ? '#f85149' : isStale ? '#d29922' : '#444c56';
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
        background: isAnalyzing ? 'rgba(47,129,247,0.08)' : isReady ? 'rgba(63,185,80,0.06)' : isError ? 'rgba(248,81,73,0.06)' : isStale ? 'rgba(210,153,34,0.06)' : '#161b22',
        borderColor: isAnalyzing ? 'rgba(47,129,247,0.28)' : isReady ? 'rgba(63,185,80,0.22)' : isError ? 'rgba(248,81,73,0.25)' : isStale ? 'rgba(210,153,34,0.25)' : '#30363d',
        marginBottom: 6,
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: hasResult ? 8 : 0 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', display: 'inline-block', background: statusColor, flexShrink: 0 }} />
          <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, fontWeight: 600, color: '#e6edf3' }}>
            {hasResult ? triageInfo.title : `AI · ${statusLabel}`}
          </span>
          {hasResult && triageInfo.subtitle && (
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#d29922', background: 'rgba(210,153,34,0.10)', border: '1px solid rgba(210,153,34,0.28)', borderRadius: 2, padding: '1px 5px' }}>
              {triageInfo.subtitle}
            </span>
          )}
        </div>

        {/* Compact summary row when ready */}
        {hasResult && props.aiPrioritization && (
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#656d76', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Queued</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#e6edf3' }}>
                {funnelData.totalQueued.toLocaleString()}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#656d76', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Candidates</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#2f81f7' }}>
                {funnelData.semanticCandidates}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#656d76', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Urgent</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#f85149' }}>
                {funnelData.urgentCount}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#656d76', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Fit Contact</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#3fb950' }}>
                {funnelData.projectedFit ?? '—'}
              </div>
            </div>
            {isStale && (
              <span style={{ alignSelf: 'center', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#d29922', background: 'rgba(210,153,34,0.10)', border: '1px solid rgba(210,153,34,0.28)', borderRadius: 2, padding: '2px 5px' }}>
                STALE
              </span>
            )}
          </div>
        )}

        {/* Context summary — standby/stale/not-AI mode */}
        {(isStandby || isStale || notInAiMode) && !hasResult && (
          <div style={{ marginTop: 4 }}>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 5 }}>Mission Context</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {dp > 0 && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e' }}>Data products</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#e6edf3' }}>{dp}</span>
                </div>
              )}
              {anomCount > 0 && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e' }}>Active anomalies</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#f85149' }}>{anomCount}</span>
                </div>
              )}
              {ms && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e' }}>Comm window</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, color: '#3fb950' }}>{ms.comm_window_remaining_s.toFixed(0)} s</span>
                </div>
              )}
              {ls && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e' }}>Link status</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: ls.link_stability > 0.7 ? '#3fb950' : '#d29922' }}>
                    {ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable'}
                  </span>
                </div>
              )}
            </div>
            {isStandby && !isAnalyzing && (
              <div style={{ marginTop: 7, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
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
                { done: false, label: 'Requesting AI analysis…' },
              ].map(({ done, label }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: done ? '#656d76' : '#2f81f7' }}>
                  <span style={{ flexShrink: 0 }}>{done ? '✓' : '●'}</span>
                  {label}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error state */}
        {isError && (
          <div style={{ background: 'rgba(248,81,73,0.06)', border: '1px solid rgba(248,81,73,0.25)', borderRadius: 3, padding: '8px 10px', marginTop: 8 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, fontWeight: 700, color: '#f85149', marginBottom: 4 }}>⚠ ANALYSIS FAILED</div>
            {props.aiError && (
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#f85149', wordBreak: 'break-all', lineHeight: 1.4 }}>
                {props.aiError.slice(0, 200)}
              </div>
            )}
            <div style={{ marginTop: 6, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e' }}>
              Mission operations remain available. Use Manual mode if needed.
            </div>
          </div>
        )}

        {/* Rejected state */}
        {props.aiRecommendationRejected && (
          <div style={{ background: 'rgba(248,81,73,0.06)', border: '1px solid rgba(248,81,73,0.22)', borderRadius: 3, padding: '8px 10px', marginTop: 8 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, fontWeight: 700, color: '#f85149', marginBottom: 3 }}>AI RECOMMENDATION REJECTED</div>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5, marginBottom: 8 }}>
              No transmission was initiated.
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => props.onSelectDecisionMode('manual')}
                style={{ flex: 1, padding: '6px 0', fontSize: 11, fontFamily: '"IBM Plex Sans"', fontWeight: 600, cursor: 'pointer', background: 'rgba(63,185,80,0.08)', color: '#3fb950', border: '1px solid rgba(63,185,80,0.25)', borderRadius: 3 }}
              >
                Return to Manual Planning
              </button>
              <button
                onClick={() => { props.onRunAiAnalysis(); }}
                style={{ flex: 1, padding: '6px 0', fontSize: 11, fontFamily: '"IBM Plex Sans"', fontWeight: 600, cursor: 'pointer', background: 'rgba(47,129,247,0.12)', color: '#2f81f7', border: '1px solid rgba(47,129,247,0.30)', borderRadius: 3 }}
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
              background: '#2f81f7',
              color: '#ffffff',
              border: '1px solid #2f81f7',
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
            background: 'rgba(47,129,247,0.12)',
            color: '#2f81f7',
            border: '1px solid rgba(47,129,247,0.30)',
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
          border: '1px solid #30363d', borderRadius: 4,
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
                    <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#656d76', letterSpacing: '0.06em', marginBottom: 5, textTransform: 'uppercase' }}>
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
                                background: isSelected ? 'rgba(47,129,247,0.12)' : 'transparent',
                                border: `1px solid ${isSelected ? 'rgba(47,129,247,0.30)' : '#30363d'}`,
                                borderRadius: 3,
                                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                              }}
                            >
                              <span style={{ fontSize: 9, color: '#656d76', minWidth: 20, textAlign: 'right' }}>
                                #{rp.priority}
                              </span>
                              <span style={{ fontSize: 11, color: isSelected ? '#2f81f7' : '#8b949e', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {rp.product_id}
                              </span>
                              {rp.anomaly_ids.length > 0 && (
                                <span style={{ color: '#f85149', fontSize: 9, flexShrink: 0 }}>⚠</span>
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
                {/* Decision chain detail — for reference */}
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
                {/* Approve/Modify/Reject is in the Decision/Outcome panel (right) — single authoritative location */}
                <div style={{ marginTop: 10, padding: '8px 10px', background: 'rgba(47,129,247,0.06)', border: '1px solid rgba(47,129,247,0.18)', borderRadius: 3, fontFamily: '"IBM Plex Sans", system-ui, sans-serif', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
                  Approve, Modify, or Reject the recommendation in the <strong style={{ color: '#2f81f7' }}>Decision / Evidence</strong> panel on the right.
                </div>
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
      <div style={{ ...CARD, borderColor: 'rgba(210,153,34,0.25)', background: 'rgba(210,153,34,0.06)' }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#d29922', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>Legacy Packet Scenario</div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e', lineHeight: 1.55 }}>
          This scenario uses the legacy packet model. AI prioritization and high-volume manual planning are not available.
        </div>
      </div>
    );
  }

  return (
    <>
      <div style={{ ...CARD, marginBottom: 10 }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #30363d' }}>
          Decision Workflow
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e', lineHeight: 1.55, marginBottom: 12 }}>
          <strong style={{ color: '#e6edf3' }}>{dp} data products</strong> are awaiting downlink.
          Communication resources are limited. Choose how to build the transmission plan.
        </div>

        <div style={{
          border: `1px solid ${props.decisionMode === 'manual' ? 'rgba(63,185,80,0.28)' : '#30363d'}`,
          borderRadius: 3, padding: '10px 12px', marginBottom: 8,
          background: props.decisionMode === 'manual' ? 'rgba(63,185,80,0.06)' : '#21262d',
        }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600, color: props.decisionMode === 'manual' ? '#3fb950' : '#e6edf3', marginBottom: 5 }}>
            Manual Decision
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5, marginBottom: 8 }}>
            Review and prioritize mission data yourself. Browse all {dp} products, apply filters, select what to transmit.
          </div>
          <button
            onClick={() => props.onSelectDecisionMode('manual')}
            style={{
              width: '100%', padding: '6px 0',
              background: props.decisionMode === 'manual'
                ? 'rgba(63,185,80,0.12)'
                : '#238636',
              color: props.decisionMode === 'manual' ? '#3fb950' : '#ffffff',
              border: `1px solid ${props.decisionMode === 'manual' ? 'rgba(63,185,80,0.30)' : '#2ea043'}`,
              borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            }}
          >
            {props.decisionMode === 'manual' ? '✓ Manual Mode Active' : 'Start Manual Planning'}
          </button>
        </div>

        <div style={{
          border: `1px solid ${props.decisionMode === 'ai' ? 'rgba(47,129,247,0.30)' : '#30363d'}`,
          borderRadius: 3, padding: '10px 12px',
          background: props.decisionMode === 'ai' ? 'rgba(47,129,247,0.10)' : '#21262d',
        }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600, color: props.decisionMode === 'ai' ? '#2f81f7' : '#e6edf3', marginBottom: 5 }}>
            AI Assisted
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5, marginBottom: 8 }}>
            Ask the AI Copilot to analyze the mission context, anomalies, deadlines, and constraints — then recommend a prioritized transmission plan.
          </div>
          <button
            onClick={() => props.onSelectDecisionMode('ai')}
            style={{
              width: '100%', padding: '6px 0',
              background: props.decisionMode === 'ai' ? '#2f81f7' : '#1f6feb',
              color: '#ffffff',
              border: `1px solid ${props.decisionMode === 'ai' ? '#2f81f7' : '#388bfd'}`,
              borderRadius: 3, cursor: 'pointer', fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            }}
          >
            {props.decisionMode === 'ai' ? '✓ AI Mode Active' : 'Use AI Assistant'}
          </button>
        </div>
      </div>

      {props.decisionMode === 'ai' && <AiSection {...props} />}

      {props.decisionMode === 'manual' && (
        <div style={{ ...CARD, borderColor: 'rgba(63,185,80,0.22)' }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: '#8b949e', lineHeight: 1.5 }}>
            Manual mode active. Navigate to the <strong style={{ color: '#3fb950' }}>Data</strong> section to browse and select data products.
          </div>
          {props.manualSelectedIds.size > 0 && (
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#3fb950' }}>
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

  const isAiMode = props.decisionMode === 'ai';
  const isTransmissionComplete = props.approvalPhase === 'complete';

  // ── AI lifecycle-aware gate ─────────────────────────────────────────────────
  // Only shown when AI mode is selected and transmission is not yet complete.
  // The gate varies by aiLifecycle so the user is never sent to a dead-end.
  let aiGateSection: React.ReactNode = null;
  if (isAiMode && !isTransmissionComplete) {
    const lc = props.aiLifecycle;

    if (lc === 'standby') {
      // STATE A — no analysis yet; prompt user to run it right here
      aiGateSection = (
        <ResizableSection title="AI Analysis Required" icon="◈" accent="#d29922">
          <div style={{
            background: '#21262d',
            border: '1px solid rgba(210,153,34,0.22)',
            borderRadius: 4, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#d29922', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              No Analysis Yet
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e', lineHeight: 1.6, marginBottom: 14 }}>
              AI has not analyzed this mission yet.
              Run mission analysis before reviewing or authorizing a transmission.
            </div>
            <button
              onClick={() => props.onRunAiAnalysis()}
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
                background: '#d29922', color: '#0d1117',
                border: '1px solid #d29922', borderRadius: 3,
              }}
            >
              Analyze Mission with AI
            </button>
          </div>
        </ResizableSection>
      );
    } else if (lc === 'analyzing') {
      // STATE B — analysis in progress
      aiGateSection = (
        <ResizableSection title="AI Analysis In Progress" icon="◈" accent="#2f81f7">
          <div style={{
            background: '#21262d',
            border: '1px solid rgba(47,129,247,0.22)',
            borderRadius: 4, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#2f81f7', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              Analyzing…
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e', lineHeight: 1.6, marginBottom: 14 }}>
              AI analysis is running. Authorization will become available once analysis completes.
            </div>
            <button
              disabled
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'not-allowed',
                background: 'rgba(47,129,247,0.18)', color: '#2f81f7',
                border: '1px solid rgba(47,129,247,0.30)', borderRadius: 3,
                opacity: 0.7,
              }}
            >
              Analyzing…
            </button>
          </div>
        </ResizableSection>
      );
    } else if (lc === 'error') {
      // STATE C — analysis failed; offer retry
      aiGateSection = (
        <ResizableSection title="AI Analysis Failed" icon="◈" accent="#f85149">
          <div style={{
            background: '#21262d',
            border: '1px solid rgba(248,81,73,0.22)',
            borderRadius: 4, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#f85149', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              Analysis Failed
            </div>
            {props.aiError && (
              <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, color: '#f85149', marginBottom: 10, lineHeight: 1.4, wordBreak: 'break-word' }}>
                {props.aiError}
              </div>
            )}
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e', lineHeight: 1.6, marginBottom: 14 }}>
              AI analysis could not complete. Retry to generate a recommendation before authorizing transmission.
            </div>
            <button
              onClick={() => props.onRunAiAnalysis()}
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
                background: 'rgba(248,81,73,0.12)', color: '#f85149',
                border: '1px solid rgba(248,81,73,0.35)', borderRadius: 3,
              }}
            >
              Retry AI Analysis
            </button>
          </div>
        </ResizableSection>
      );
    } else if (lc === 'stale') {
      // STATE D — previous result exists but is no longer current
      aiGateSection = (
        <ResizableSection title="AI Analysis Stale" icon="◈" accent="#d29922">
          <div style={{
            background: '#21262d',
            border: '1px solid rgba(210,153,34,0.22)',
            borderRadius: 4, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#d29922', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              Analysis Out of Date
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e', lineHeight: 1.6, marginBottom: 14 }}>
              Mission context or data has changed since the last analysis.
              Re-run analysis to get a current recommendation before authorizing transmission.
            </div>
            <button
              onClick={() => props.onRunAiAnalysis()}
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
                background: '#d29922', color: '#0d1117',
                border: '1px solid #d29922', borderRadius: 3,
              }}
            >
              Re-run AI Analysis
            </button>
          </div>
        </ResizableSection>
      );
    } else {
      // STATE E — ready (aiLifecycle === 'ready' and recommendation exists)
      aiGateSection = (
        <ResizableSection title="Authorization Required" icon="◉" accent="#2f81f7">
          <div style={{
            background: '#21262d',
            border: '1px solid rgba(47,129,247,0.22)',
            borderRadius: 4, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              Awaiting Operator Authorization
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: '#8b949e', lineHeight: 1.6, marginBottom: 14 }}>
              AI analysis is ready. Review the Decision tab in AI Copilot before authorizing transmission.
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', marginBottom: 12, lineHeight: 1.5 }}>
              Use <strong>✓ Approve Transmission</strong> in the Decision tab to authorize a single authoritative execution.
              Once approved, this Transmission panel will show execution status and playback.
            </div>
            <button
              onClick={() => props.onNavigateSection?.('ai')}
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
                background: '#2f81f7', color: '#ffffff',
                border: '1px solid #2f81f7', borderRadius: 3,
              }}
            >
              Open AI Copilot
            </button>
          </div>
        </ResizableSection>
      );
    }
  }

  return (
    <>
      <ResizableSection title="Transmission Summary" icon="↗" accent="#2f81f7">
        <div style={{ minWidth: 0 }}>
          <TransmissionSummaryPanel
            plan={activeTxPlan}
            evaluation={activeTxEval}
            availableCapacityBits={props.availableCapacityBits}
          />
        </div>
      </ResizableSection>

      {/* AI mode lifecycle-aware gate */}
      {aiGateSection}

      {/* Manual mode: keep Approval bar with EVALUATE SELECTION / TRANSMIT SELECTED */}
      {!isAiMode && (
      <ResizableSection title="Approval" icon="◉" accent="#2f81f7">
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
              background: props.manualAssessmentStale ? 'rgba(210,153,34,0.06)' : 'rgba(63,185,80,0.06)',
              border: `1px solid ${props.manualAssessmentStale ? 'rgba(210,153,34,0.28)' : 'rgba(63,185,80,0.22)'}`,
              borderRadius: 4, padding: '8px 12px',
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11,
            }}>
              {props.manualAssessmentStale && (
                <div style={{ color: '#d29922', marginBottom: 4, fontSize: 10 }}>⚠ STALE — Re-evaluate to update</div>
              )}
              <div style={{ color: '#8b949e', marginBottom: 4, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Manual Plan Assessment</div>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ color: '#656d76', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>SELECTED</div>
                  <div style={{ color: '#e6edf3', fontSize: 12, fontWeight: 700 }}>{props.manualAssessment.capacity_summary.selected_count}</div>
                </div>
                <div>
                  <div style={{ color: '#656d76', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>PAYLOAD</div>
                  <div style={{ color: '#e6edf3', fontSize: 12, fontWeight: 700 }}>{formatBitsAsDataVolume(props.manualAssessment.capacity_summary.selected_bits)}</div>
                </div>
                <div>
                  <div style={{ color: '#656d76', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>RISK</div>
                  <div style={{
                    fontSize: 12, fontWeight: 700,
                    color: props.manualAssessment.evaluation.risk_level === 'LOW' ? '#3fb950' :
                           props.manualAssessment.evaluation.risk_level === 'MEDIUM' ? '#d29922' :
                           props.manualAssessment.evaluation.risk_level === 'HIGH' ? '#f85149' : '#f85149',
                  }}>
                    {props.manualAssessment.evaluation.risk_level}
                  </div>
                </div>
                <div>
                  <div style={{ color: '#656d76', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>DEFERRED</div>
                  <div style={{ color: '#e6edf3', fontSize: 12, fontWeight: 700 }}>{props.manualAssessment.evaluation.deferred_packets.length}</div>
                </div>
              </div>
            </div>
          )}
          {props.decisionMode === 'manual' && props.manualAssessmentLoading && (
            <div style={{ marginTop: 6, color: '#8b949e', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 11 }}>
              Evaluating…
            </div>
          )}
          {props.decisionMode === 'manual' && props.manualAssessmentError && (
            <div style={{ marginTop: 6, color: '#f85149', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 11 }}>
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
      border: '1px solid #30363d', borderRadius: 4,
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
              <div style={{ color: '#656d76', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
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
              <div style={{ color: '#656d76', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
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
              <div style={{ color: '#656d76', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
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
    border: '1px solid #30363d',
    borderRadius: 3,
    color: '#656d76',
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
    color: '#2f81f7',
    border: '1px solid rgba(47,129,247,0.40)',
    background: 'rgba(47,129,247,0.12)',
  };

  if (mode === 'focus') {
    return (
      <button
        style={{ ...btnBase, color: '#f85149', border: '1px solid rgba(248,81,73,0.28)', background: 'rgba(248,81,73,0.06)' }}
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
  workspaceMode = 'normal',
  onSetWorkspaceMode = () => {},
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
      background: props.aiLifecycle === 'ready' ? 'rgba(63,185,80,0.10)' :
                  props.aiLifecycle === 'analyzing' ? 'rgba(47,129,247,0.12)' :
                  props.aiLifecycle === 'error' ? 'rgba(248,81,73,0.10)' :
                  props.aiLifecycle === 'stale' ? 'rgba(210,153,34,0.10)' : 'transparent',
      color: props.aiLifecycle === 'ready' ? '#3fb950' :
             props.aiLifecycle === 'analyzing' ? '#2f81f7' :
             props.aiLifecycle === 'error' ? '#f85149' :
             props.aiLifecycle === 'stale' ? '#d29922' : 'transparent',
      border: `1px solid ${props.aiLifecycle === 'ready' ? 'rgba(63,185,80,0.28)' :
              props.aiLifecycle === 'analyzing' ? 'rgba(47,129,247,0.30)' :
              props.aiLifecycle === 'error' ? 'rgba(248,81,73,0.28)' :
              props.aiLifecycle === 'stale' ? 'rgba(210,153,34,0.28)' : 'transparent'}`,
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
      background: '#21262d',
      color: '#8b949e',
      border: '1px solid #30363d',
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
      background: 'rgba(248,81,73,0.08)',
      color: '#f85149',
      border: '1px solid rgba(248,81,73,0.25)',
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
      background: 'rgba(47,129,247,0.12)',
      color: '#2f81f7',
      border: '1px solid rgba(47,129,247,0.30)',
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
        background: '#0d1117',
        borderLeft: '1px solid #30363d',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Panel header — sticky, flat */}
      <div style={{
        padding: '9px 14px 8px',
        borderBottom: '1px solid #30363d',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: '#161b22',
      }}>
        <span style={{ fontSize: 11, color: '#484f58', lineHeight: 1 }}>{heading.icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, fontWeight: 600,
          color: '#e6edf3',
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
          />
        )}
      </div>
    </div>
  );
}

// ── DecisionPanel — lower-right zone of the four-zone layout ─────────────────
//
// Per-section decision / evidence / outcome content.
// "What should the operator do, why, and what happened?"
//
// IMPORTANT: Approve/Modify/Reject/Execute controls exist ONLY here.
// AnalysisPanel does not render duplicate decision controls.

function DecisionMission(props: CommonProps) {
  const ms = props.missionState;
  const ls = props.linkState;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ ...CARD }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #30363d' }}>
          Operational Context
        </div>
        {ms && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {[
              { label: 'Phase', value: ms.mission_phase ?? '—' },
              { label: 'Event', value: ms.current_event ?? '—' },
              { label: 'Risk', value: ms.risk_level, color: ms.risk_level === 'CRITICAL' || ms.risk_level === 'HIGH' ? '#f85149' : ms.risk_level === 'MEDIUM' ? '#d29922' : '#3fb950' },
              ...(ls ? [{ label: 'Link', value: ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable', color: ls.link_stability > 0.75 ? '#3fb950' : '#d29922' }] : []),
            ].map(({ label, value, color }) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '3px 0', borderBottom: '1px solid #21262d' }}>
                <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e' }}>{label}</span>
                <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, fontWeight: 600, color: color ?? '#e6edf3' }}>{value}</span>
              </div>
            ))}
          </div>
        )}
        {!ms && (
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#656d76' }}>Loading…</div>
        )}
        <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#656d76', lineHeight: 1.5 }}>
          To act on this mission, select a decision mode in the AI or Data sections, then approve a transmission plan.
        </div>
      </div>
    </div>
  );
}

function DecisionConfig() {
  return (
    <div style={{ ...CARD }}>
      <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
        System Context
      </div>
      <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
        Configuration changes take effect on next scenario load.
        Use Reset or Refresh in the header to apply changes to mission data.
      </div>
    </div>
  );
}

function DecisionLog(props: CommonProps) {
  const hasResult = !!props.approveResult;
  if (!hasResult) {
    return (
      <div style={{ ...CARD }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
          Mission Outcome
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#656d76', lineHeight: 1.5 }}>
          No transmission has been completed yet.
          Complete a transmission to see the outcome summary here.
        </div>
      </div>
    );
  }
  const sim = props.approveResult!.simulation_result;
  const delivered = sim.delivered_packets.length;
  const failed = sim.failed_packets.length;
  const deferred = sim.deferred_packets.length;
  return (
    <div style={{ ...CARD }}>
      <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #30363d' }}>
        Transmission Outcome
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {[
          { label: 'Delivered', value: `${delivered}`, color: '#3fb950' },
          { label: 'Failed', value: `${failed}`, color: failed > 0 ? '#f85149' : '#3fb950' },
          { label: 'Deferred', value: `${deferred}`, color: deferred > 0 ? '#d29922' : '#3fb950' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '3px 0', borderBottom: '1px solid #21262d' }}>
            <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e' }}>{label}</span>
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color }}>{value}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#656d76' }}>
        See Log section for full reception details, narrative, and report.
      </div>
    </div>
  );
}

function DecisionTransmission(props: CommonProps) {
  // The authoritative Approve/Modify/Reject and choreography belong here
  // when decision mode is 'ai' (AI recommended plan).
  // Manual TRANSMIT button remains in the Data section (lower-left) per spec.
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* AI authorization — primary decision widget */}
      {props.decisionMode === 'ai' && (
        <AiHumanDecisionPanel props={props} />
      )}
      {/* Outcome banner */}
      <TransmissionOutcomeBanner
        approvalPhase={props.approvalPhase}
        simulationResult={props.approveResult?.simulation_result ?? null}
        isAiRecommendedPlan={
          props.approveResult?.simulation_result?.plan_id !== undefined &&
          props.approveResult.simulation_result.plan_id !== 'operator-override' &&
          props.approveResult.simulation_result.plan_id !== 'operator-manual'
        }
      />
      {/* Manual mode: shows accounting only — TRANSMIT button stays in analysis (TransmissionSection) */}
      {props.decisionMode === 'manual' && (
        <div style={{ ...CARD }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
            Manual Authorization
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
            Review your selected products in the Transmission section (left), then use
            <strong style={{ color: '#3fb950' }}> Transmit Selected</strong> to authorize a single execution.
          </div>
          {props.manualAssessment && !props.manualAssessmentStale && (
            <div style={{ marginTop: 8, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {[
                { label: 'SELECTED', value: `${props.manualAssessment.capacity_summary.selected_count}`, color: '#e6edf3' },
                { label: 'RISK', value: props.manualAssessment.evaluation.risk_level, color: props.manualAssessment.evaluation.risk_level === 'LOW' ? '#3fb950' : props.manualAssessment.evaluation.risk_level === 'MEDIUM' ? '#d29922' : '#f85149' },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 8, color: '#656d76', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 1 }}>{label}</div>
                  <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color }}>{value}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {props.decisionMode === 'unselected' && (
        <div style={{ ...CARD }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
            Authorization
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#656d76', lineHeight: 1.5 }}>
            Select a decision mode (AI Assisted or Manual) to begin planning a transmission.
          </div>
        </div>
      )}
    </div>
  );
}

function DecisionData(props: CommonProps) {
  // When a product is selected (manual mode), show selection context.
  // Otherwise show concise mission bottleneck / context.
  const hasSelection = props.manualSelectedIds.size > 0;
  const hasAssessment = !!props.manualAssessment && !props.manualAssessmentStale;

  // Phase 8B: distinguish Modify-AI from fresh manual planning
  const isAiModifyOrigin = props.manualEditOrigin === 'ai_recommendation';
  // Count of originally-deferred products that the operator has NOT re-selected.
  const aiBaselineDeferredCount = props.aiBaselineDeferredIds.size;
  // Detect whether operator has diverged from the initial AI seeded selection.
  const operatorModified = isAiModifyOrigin && hasSelection && (() => {
    // The operator has modified if the current selection differs from the initial AI seeding.
    // We detect this by checking if any selected item was originally AI-deferred (added by operator)
    // or if any originally-scheduled item is no longer selected (removed by operator).
    for (const id of props.aiBaselineDeferredIds) {
      if (props.manualSelectedIds.has(id)) return true;
    }
    return false;
  })();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {hasSelection ? (
        <div style={{ ...CARD, borderColor: isAiModifyOrigin ? 'rgba(47,129,247,0.25)' : 'rgba(63,185,80,0.22)' }}>
          {/* Phase 8B: header distinguishes Modify-AI from fresh manual */}
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: isAiModifyOrigin ? '#2f81f7' : '#3fb950', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6, paddingBottom: 6, borderBottom: `1px solid ${isAiModifyOrigin ? 'rgba(47,129,247,0.18)' : 'rgba(63,185,80,0.18)'}` }}>
            {isAiModifyOrigin ? 'Modified AI Plan' : 'Manual Selection'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '3px 0', borderBottom: '1px solid #21262d' }}>
              {/* Phase 8B: use "Scheduled / Selected" terminology in AI-modify mode */}
              <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e' }}>
                {isAiModifyOrigin ? 'Scheduled / Selected' : 'Selected'}
              </span>
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#3fb950' }}>{props.manualSelectedIds.size}</span>
            </div>
            {/* Phase 8B: show AI baseline deferred count in Modify-AI mode */}
            {isAiModifyOrigin && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '3px 0', borderBottom: '1px solid #21262d' }}>
                <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e' }}>
                  {operatorModified ? 'Original AI Baseline Deferred' : 'AI Baseline Deferred'}
                </span>
                <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, fontWeight: 700, color: aiBaselineDeferredCount > 0 ? '#d29922' : '#3fb950' }}>{aiBaselineDeferredCount}</span>
              </div>
            )}
            {hasAssessment && (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '3px 0', borderBottom: '1px solid #21262d' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e' }}>Plan risk</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, fontWeight: 700, color: props.manualAssessment!.evaluation.risk_level === 'LOW' ? '#3fb950' : props.manualAssessment!.evaluation.risk_level === 'MEDIUM' ? '#d29922' : '#f85149' }}>
                    {props.manualAssessment!.evaluation.risk_level}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '3px 0', borderBottom: '1px solid #21262d' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#8b949e' }}>
                    {isAiModifyOrigin ? 'Current Plan Deferred' : 'Deferred'}
                  </span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, fontWeight: 700, color: '#d29922' }}>{props.manualAssessment!.evaluation.deferred_packets.length}</span>
                </div>
              </>
            )}
          </div>
          <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#656d76', lineHeight: 1.5 }}>
            {isAiModifyOrigin
              ? 'Seeded from AI recommendation. Navigate to Transmit to evaluate your modified plan.'
              : 'Navigate to Transmit section to evaluate and authorize your plan.'}
          </div>
        </div>
      ) : (
        <div style={{ ...CARD }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
            Mission Context
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#8b949e', lineHeight: 1.5 }}>
            Browse the data products in the table and select items to build a manual transmission plan.
            Or navigate to the AI section to use AI-assisted prioritization.
          </div>
          {props.dataProductsCount > 0 && props.availableCapacityBits > 0 && (
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#d29922' }}>
              {props.dataProductsCount} products · {(props.queuedDataBits / props.availableCapacityBits).toFixed(0)}× queue pressure
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DecisionAi(props: CommonProps) {
  // AI: recommendation, reasoning, risk, Approve/Modify/Reject
  const hasResult = props.aiLifecycle === 'ready' || props.aiLifecycle === 'stale';
  if (!hasResult) {
    return (
      <div style={{ ...CARD }}>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
          AI Decision
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: '#656d76', lineHeight: 1.5 }}>
          {props.aiLifecycle === 'analyzing' ? 'AI analysis in progress…' : 'Run AI analysis from the left panel to receive a recommendation.'}
        </div>
        {props.aiLifecycle === 'error' && props.aiError && (
          <div style={{ marginTop: 6, fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#f85149' }}>
            {props.aiError.slice(0, 120)}
          </div>
        )}
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Authoritative decision widget — Approve / Modify / Reject */}
      <AiHumanDecisionPanel props={props} />

      {/* Outcome banner */}
      <TransmissionOutcomeBanner
        approvalPhase={props.approvalPhase}
        simulationResult={props.approveResult?.simulation_result ?? null}
        isAiRecommendedPlan={
          props.approveResult?.simulation_result?.plan_id !== undefined &&
          props.approveResult.simulation_result.plan_id !== 'operator-override' &&
          props.approveResult.simulation_result.plan_id !== 'operator-manual'
        }
      />
    </div>
  );
}

export interface DecisionPanelOuterProps extends CommonProps {
  section: NavSection;
  workspaceMode: WorkspaceMode;
  onSetWorkspaceMode: (mode: WorkspaceMode) => void;
}

const DECISION_HEADINGS: Record<NavSection, { icon: string; title: string }> = {
  mission:      { icon: '◉', title: 'Operational Context' },
  spacecraft:   { icon: '⬡', title: 'Spacecraft Context' },
  comms:        { icon: '⌾', title: 'Link Context' },
  data:         { icon: '▦', title: 'Selection Summary' },
  ai:           { icon: '◈', title: 'Decision / Evidence' },
  transmission: { icon: '↗', title: 'Authorization' },
  log:          { icon: '≡', title: 'Outcome Summary' },
  config:       { icon: '⚙', title: 'System Context' },
};

export function DecisionPanel({
  section,
  workspaceMode,
  onSetWorkspaceMode,
  ...props
}: DecisionPanelOuterProps) {
  const heading = DECISION_HEADINGS[section];
  const isFocus = workspaceMode === 'focus';

  return (
    <div
      data-testid={`decision-panel-${section}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: '#0d1117',
        borderLeft: '1px solid #30363d',
        overflow: 'hidden',
        minWidth: 0,
      }}
    >
      {/* Zone header */}
      <div style={{
        padding: '7px 12px 6px',
        borderBottom: '1px solid #30363d',
        flexShrink: 0,
        background: '#161b22',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <span style={{ fontSize: 10, color: '#484f58' }}>{heading.icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 10,
          fontWeight: 600,
          color: '#e6edf3',
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          flex: 1,
        }}>
          {heading.title}
        </span>
        {/* Focus/expand mode controls — appear in decision panel header */}
        <WorkspaceModeControls mode={workspaceMode} onSet={onSetWorkspaceMode} />
      </div>

      {/* Scrollable content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '10px',
        minHeight: 0,
      }}>
        {section === 'mission'    && <DecisionMission {...props} />}
        {section === 'spacecraft' && <DecisionMission {...props} />}
        {section === 'comms'      && <DecisionMission {...props} />}
        {section === 'data'       && <DecisionData {...props} />}
        {section === 'ai'         && <DecisionAi {...props} />}
        {section === 'transmission' && <DecisionTransmission {...props} />}
        {section === 'log'        && <DecisionLog {...props} />}
        {section === 'config'     && <DecisionConfig />}
        {isFocus && (
          <div style={{ marginTop: 4, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#444c56' }}>
            Press Esc or click Exit Focus to return to normal layout.
          </div>
        )}
      </div>
    </div>
  );
}

// ── AnalysisPanel — lower-left zone of the four-zone layout ──────────────────
//
// Contextual analytical workbench driven by the selected navigation section.
// Reuses the existing section rendering from RightPanel.
// Does NOT contain Approve / Modify / Reject controls (those are in DecisionPanel).

export interface AnalysisPanelProps extends CommonProps {
  section: NavSection;
  viewSettings: ViewSettings;
  onUpdateSetting: <K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) => void;
  onResetSettings: () => void;
  onResetPanelWidth: () => void;
  panelWidth: number;
  panelDefaultWidth: number;
  workspaceMode: WorkspaceMode;
  onSetWorkspaceMode: (mode: WorkspaceMode) => void;
}

const ANALYSIS_HEADINGS: Record<NavSection, { icon: string; title: string }> = {
  mission:      { icon: '◉', title: 'Mission Analysis' },
  spacecraft:   { icon: '⬡', title: 'Spacecraft' },
  comms:        { icon: '⌾', title: 'Link Health & What-If' },
  data:         { icon: '▦', title: 'Data Products' },
  ai:           { icon: '◈', title: 'AI Analysis' },
  transmission: { icon: '↗', title: 'Transmission' },
  log:          { icon: '≡', title: 'Mission Log' },
  config:       { icon: '⚙', title: 'Configuration' },
};

export function AnalysisPanel({
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
}: AnalysisPanelProps) {
  const heading = ANALYSIS_HEADINGS[section];

  const aiStatusBadge = section === 'ai' && props.aiLifecycle !== 'standby' && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: props.aiLifecycle === 'ready' ? 'rgba(63,185,80,0.10)' :
                  props.aiLifecycle === 'analyzing' ? 'rgba(47,129,247,0.12)' :
                  props.aiLifecycle === 'error' ? 'rgba(248,81,73,0.10)' :
                  props.aiLifecycle === 'stale' ? 'rgba(210,153,34,0.10)' : 'transparent',
      color: props.aiLifecycle === 'ready' ? '#3fb950' :
             props.aiLifecycle === 'analyzing' ? '#2f81f7' :
             props.aiLifecycle === 'error' ? '#f85149' :
             props.aiLifecycle === 'stale' ? '#d29922' : 'transparent',
      border: `1px solid ${props.aiLifecycle === 'ready' ? 'rgba(63,185,80,0.28)' :
              props.aiLifecycle === 'analyzing' ? 'rgba(47,129,247,0.30)' :
              props.aiLifecycle === 'error' ? 'rgba(248,81,73,0.28)' :
              props.aiLifecycle === 'stale' ? 'rgba(210,153,34,0.28)' : 'transparent'}`,
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
      background: '#21262d', color: '#8b949e',
      border: '1px solid #30363d',
      borderRadius: 2, padding: '1px 5px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
    }}>
      {props.dataProductsCount}
    </span>
  );

  return (
    <div
      data-testid={`analysis-panel-${section}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: '#0d1117',
        overflow: 'hidden',
        minWidth: 0,
      }}
    >
      {/* Zone header */}
      <div style={{
        padding: '7px 12px 6px',
        borderBottom: '1px solid #30363d',
        flexShrink: 0,
        background: '#161b22',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <span style={{ fontSize: 10, color: '#484f58' }}>{heading.icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 10,
          fontWeight: 600,
          color: '#e6edf3',
          letterSpacing: '0.01em',
          flex: 1,
          minWidth: 0,
        }}>
          {heading.title}
          {aiStatusBadge}
          {dataBadge}
        </span>
      </div>

      {/* Scrollable content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '10px',
        minWidth: 0,
        minHeight: 0,
      }}>
        {section === 'mission'    && <MissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'spacecraft' && <SpacecraftSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'comms'      && <CommsSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'data'       && <DataSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'ai'         && <AiSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'transmission' && (
          <>
            {props.decisionMode === 'unselected' && (
              <DecisionModeSelector {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />
            )}
            <TransmissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />
          </>
        )}
        {section === 'log'    && <LogSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'config' && (
          <ConfigPanel
            settings={viewSettings}
            onUpdate={onUpdateSetting}
            onResetSettings={onResetSettings}
            onResetPanelWidth={onResetPanelWidth}
            panelWidth={panelWidth}
            panelDefaultWidth={panelDefaultWidth}
          />
        )}
      </div>
    </div>
  );
}
